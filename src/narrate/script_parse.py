"""Extracting timeline markers from a script before anything is billed.

**This module is a correctness gate.** Chunk text is passed straight into the
TTS request, so a marker left inline would be read aloud and charged for — and
on `eleven_v3` a bracketed phrase is additionally interpreted as an audio tag,
so `[SFX: thunder]` could change the delivery of the surrounding narration.
Parsing happens before chunking, and the cleaned text is the only thing that
ever reaches a provider.

Grammar, deliberately narrow so ordinary prose cannot trip it:

    [@ 02:15]                     timeline anchor — a target start time
    [@ 1:02:15.500]               hours and milliseconds both optional
    [SFX: wind howling]           effect slot, provider picks the duration
    [SFX: wind howling, 4s]       effect slot with an explicit duration
    [SFX: soft rain, 30s, loop]   looping ambience bed
    [CAST] Morag = <voice_id>     declare who is in this script
    [VOICE: Morag]                everything after this is Morag speaking
    Morag: the line she speaks    the same thing, in script form
    <!-- a note to yourself -->   never narrated, never billed

**A speaker prefix only counts if the name is cast.** `Morag:` becomes a
speaker change when Morag is a declared cast member and stays ordinary prose
otherwise, so `Note:`, `Warning:` and `12:30` cannot be mistaken for dialogue.
No heuristic about capitalisation is involved, because a heuristic would
eventually edit somebody's narration.

Markdown syntax is stripped for the same reason. A script written as `.md`
carries headings, emphasis and links that are *layout*, not speech — left in
place, `# The Keeper's Log` is narrated and billed as characters, and on
`eleven_v3` stray asterisks land in the same bracket-and-symbol territory the
audio tags occupy. Formatting is removed first, so marker offsets are recorded
against text that no longer contains any.

Offsets are recorded against the *cleaned* text, so they stay valid after the
markers are removed and can be handed to the chunker as forced boundaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

# Sound effects are billed per second and the API rejects anything outside
# this range (`duration_seconds` must be "at least 0.5 and at most 30").
MIN_EFFECT_SECONDS = 0.5
MAX_EFFECT_SECONDS = 30.0

_MARKER_RE = re.compile(
    r"\[[ \t]*(?:"
    # An anchor may carry a name: `[@ 03:00 The Employee Trap]`. Naming one
    # makes it a chapter as well as a target, which is worth having because a
    # writer who marks where a section begins has already done the work.
    r"@[ \t]*(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\.\d{1,3})?)"
    r"(?:[ \t]+(?P<label>[^\]]+?))?"
    r"|(?:SFX|FX)[ \t]*:[ \t]*(?P<sfx>[^\]]+?)"
    r"|(?:VOICE|SPEAKER)[ \t]*:[ \t]*(?P<voice>[^\]]+?)"
    r"|(?:CHAPTER|CH)[ \t]*:[ \t]*(?P<chapter>[^\]]+?)"
    r")[ \t]*\]",
    re.IGNORECASE,
)

# A chapter name is a label in a list, not a sentence. Long enough for a real
# heading, short enough that a paragraph pasted by mistake is obviously wrong.
MAX_CHAPTER_TITLE = 100

# Which heading levels become chapters. `#` is the episode's own title — a
# chapter named after the episode is noise — and `####` and deeper are
# sub-notes, where a forty-entry chapter list is worse than none.
CHAPTER_HEADING_LEVELS = (2, 3)

# `[CAST] Ada = voice_id · Bo = other_id`, or one pair per line beneath it.
# Consumed before the marker pass so the names are known when the speaker
# prefixes are read.
_CAST_BLOCK_RE = re.compile(r"^[ \t]*\[[ \t]*CAST[ \t]*\][ \t]*(?P<body>.*)$", re.IGNORECASE)
_CAST_PAIR_RE = re.compile(r"(?P<name>[^=·,;\n]+?)[ \t]*=[ \t]*(?P<voice>[A-Za-z0-9_-]{1,64})")

# A line-leading `Name:`. Matching is generous here on purpose — the *narrow*
# rule is applied afterwards, by requiring the name to be in the cast.
_SPEAKER_LINE_RE = re.compile(r"^[ \t]*(?P<name>[^\n:]{1,40}?)[ \t]*:[ \t]+(?=\S)", re.MULTILINE)

# Three or more newlines: a paragraph break with a removed marker in it.
_BLANKS_RE = re.compile(r"\n[ \t]*\n[ \t]*\n+")

_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)[ \t]*s(?:ec(?:onds?)?)?$", re.IGNORECASE)

# Markdown that would otherwise be spoken. Every pattern here is deliberately
# conservative: prose containing a lone asterisk or an underscore inside a word
# must survive untouched, because a false positive silently edits narration.
# HTML comments. Markdown-native, invisible when rendered, and therefore the
# natural way to annotate a script — which is exactly why they must not be
# narrated. `DOTALL` because a comment spanning lines is the useful kind.
# One flanking space is absorbed, so a comment removed from mid-sentence leaves
# one space rather than two. A stranded space is a billed character.
_COMMENT_RE = re.compile(r"[ \t]?<!--.*?-->[ \t]?", re.DOTALL)

_FENCE_RE = re.compile(r"^[ \t]{0,3}(?:```|~~~)")
_HEADING_RE = re.compile(r"^[ \t]{0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_RULE_RE = re.compile(r"^[ \t]{0,3}(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$")
_SETEXT_RE = re.compile(r"^[ \t]{0,3}=+[ \t]*$")
_QUOTE_RE = re.compile(r"^[ \t]{0,3}>[ \t]?")
_BULLET_RE = re.compile(r"^([ \t]*)(?:[-*+]|\d{1,3}[.)])[ \t]+")

_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)\s]*(?:[ \t]+\"[^\"]*\")?\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)\s]*(?:[ \t]+\"[^\"]*\")?\)")
_CODE_RE = re.compile(r"`+([^`]+?)`+")
_STRONG_RE = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
_STRIKE_RE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~", re.DOTALL)
_STAR_EM_RE = re.compile(r"\*(?=\S)([^*\n]+?)(?<=\S)\*")
# `_` only counts as emphasis at a word edge, so `output_format` is left alone.
_UNDER_EM_RE = re.compile(r"(?<![A-Za-z0-9_])_(?=\S)([^_\n]+?)(?<=\S)_(?![A-Za-z0-9_])")
_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!>~|])")
# A backslash-escaped character has to be hidden from the emphasis patterns
# before they run, or `\*not italic\*` is read as emphasis and loses both
# asterisks. NUL is safe as the sentinel because `ingest.decode_script`
# rejects any input containing one.
_STASH_RE = re.compile(r"\x00(\d+)\x00")


def _promote_heading(words: str, level: int) -> tuple[str, str]:
    """What a heading line becomes: a marker, a chapter, or nothing.

    Three cases, ordered by how literally the writer asked for something.

    * **A bracketed body** — `## [CHUNK 2]`, `## [SFX: rain]`, `## [@ 02:15]` —
      is emitted verbatim, because the brackets are a marker the writer wrote and
      the heading round them is presentation. This is also a bug fix: the
      `## [CHUNK n]` form is documented in `PRD §F1` and advertised by the
      chunker, but it was swallowed here as a heading and never reached the
      chunker at all, so it silently did nothing.
    * **A plain heading at a chapter level** becomes a `[CHAPTER: ...]` marker,
      so the pass that follows records its position using machinery that already
      works — rather than carrying an offset through four passes that each delete
      text out from under it.
    * **Anything else** is dropped and reported, as every heading used to be.

    A body containing `]` is never promoted: it would truncate the marker and
    silently rename the chapter. Rewriting a writer's words to fit a regex is the
    one thing this module refuses to do anywhere else, and it will not start
    here.
    """
    if not words:
        return "", "empty"
    if words.startswith("[") and words.endswith("]") and "]" not in words[1:-1]:
        return words, "marker"
    if level in CHAPTER_HEADING_LEVELS and "]" not in words:
        return f"[CHAPTER: {words[:MAX_CHAPTER_TITLE]}]", "chapter"
    return "", "dropped"


def _name_some(titles: list[str], limit: int = 3) -> str:
    shown = ", ".join(f"{t!r}" for t in titles[:limit])
    more = f" and {len(titles) - limit} more" if len(titles) > limit else ""
    return f"{shown}{more}"


def strip_formatting(text: str) -> tuple[str, list[str]]:
    """Remove Markdown syntax, returning the prose and what was dropped.

    Headings go entirely rather than losing their `#`. A heading is a label for
    a reader — the episode title, an act break — and narrating it is wrong in a
    way that keeping the words is not: the voice would announce "The Keeper's
    Log" and then begin the episode that is already called that. Anything
    dropped is named in a warning, so nothing disappears quietly.
    """
    notes: list[str] = []
    dropped: list[str] = []
    promoted: list[str] = []

    # Comments go first, before anything else looks at the text. A script
    # written from the downloadable template is mostly guidance in comments,
    # and left in place every word of it would be read aloud and billed for.
    text, comment_notes = _strip_comments(text)
    notes += comment_notes

    lines = text.split("\n")
    out: list[str] = []
    in_fence = False

    for line in lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            # Kept, minus the fence. A fenced block in a narration script is
            # more likely a quoted passage than code, and dropping words is
            # the one mistake that cannot be undone downstream.
            out.append(line)
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            words = _inline(heading.group(2)).strip()
            emitted, outcome = _promote_heading(words, len(heading.group(1)))
            if outcome == "chapter":
                promoted.append(words)
            elif outcome == "dropped":
                dropped.append(words)
            out.append(emitted)
            continue

        if _RULE_RE.match(line) or (_SETEXT_RE.match(line) and out and out[-1].strip()):
            out.append("")
            continue

        line = _QUOTE_RE.sub("", line)
        line = _BULLET_RE.sub(r"\1", line)
        out.append(_inline(line))

    if promoted:
        notes.append(
            f"{len(promoted)} heading(s) became chapter markers "
            f"({_name_some(promoted)}) — named in the publish pack, still not narrated."
        )
    if dropped:
        levels = "-".join(str(n) for n in CHAPTER_HEADING_LEVELS)
        notes.append(
            f"Dropped {len(dropped)} Markdown heading(s) from the narration "
            f"({_name_some(dropped)}) — a heading is a label, not something to read "
            f"aloud. Use a level {levels} heading to make one a chapter instead."
        )

    clean = "\n".join(out)
    # Dropped headings and rules leave blank lines behind; collapse them so the
    # chunker does not see an extra paragraph break where a title used to be.
    clean = re.sub(r"\n[ \t]*\n[ \t]*\n+", "\n\n", clean).strip()
    return clean, notes


def _strip_comments(text: str) -> tuple[str, list[str]]:
    """Remove `<!-- ... -->`, and refuse to guess about an unterminated one.

    An unterminated comment is the interesting case. HTML says it swallows
    everything to the end of the document, and a Markdown renderer will do
    exactly that — which here would silently delete the rest of the episode.
    Losing words is the one mistake that cannot be undone downstream, so a
    stray `<!--` is left in place and reported instead. Narrating four odd
    characters is recoverable; narrating nothing at all is not.
    """

    def swap(match: re.Match[str]) -> str:
        # Whitespace on both sides means the comment sat inside a sentence, so
        # one space has to survive. Otherwise it was on its own, and nothing
        # should be left behind.
        raw = match.group(0)
        return " " if raw[:1] in " \t" and raw[-1:] in " \t" else ""

    cleaned = _COMMENT_RE.sub(swap, text)
    if "<!--" not in cleaned:
        return cleaned, []
    return cleaned, [
        "An unterminated `<!--` is left in the narration rather than guessed at — "
        "closing it with `-->` would delete everything after it. Close the comment "
        "or remove it."
    ]


def _inline(line: str) -> str:
    """Inline formatting removed, leaving the words it decorated."""
    kept: list[str] = []

    def stash(match: re.Match[str]) -> str:
        kept.append(match.group(1))
        return f"\x00{len(kept) - 1}\x00"

    line = _ESCAPE_RE.sub(stash, line)
    line = _IMAGE_RE.sub("", line)
    line = _LINK_RE.sub(r"\1", line)
    line = _CODE_RE.sub(r"\1", line)
    line = _STRONG_RE.sub(r"\2", line)
    line = _STRIKE_RE.sub(r"\1", line)
    line = _STAR_EM_RE.sub(r"\1", line)
    line = _UNDER_EM_RE.sub(r"\1", line)
    return _STASH_RE.sub(lambda m: kept[int(m.group(1))], line)


@dataclass(frozen=True)
class Anchor:
    """A `[@ MM:SS]` target start time for whatever follows it.

    `label` is the optional name in `[@ 03:00 The Employee Trap]`. An anchor and
    a chapter mark the same thing from two directions — where a section begins —
    so naming an anchor produces a chapter too, rather than asking for the
    position to be written twice.
    """

    offset: int
    target_s: float
    raw: str
    label: str = ""


@dataclass(frozen=True)
class ParsedChapter:
    """A named division of the episode, for a YouTube chapter list.

    Sibling to `ParsedSlot`: an offset plus what belongs there. The *time* is
    deliberately absent — a chapter's timestamp is whatever the audio turns out
    to be, read off the measured timeline, never what the script hoped for.
    """

    offset: int
    title: str
    raw: str


@dataclass(frozen=True)
class ParsedSlot:
    """A `[SFX: ...]` request for an effect at a point in the script."""

    offset: int
    description: str
    duration_s: float | None
    loop: bool
    raw: str

    @property
    def slug(self) -> str:
        """Stable, filesystem-safe name for the effect this slot asks for."""
        base = re.sub(r"[^a-z0-9]+", "-", self.description.lower()).strip("-")[:48]
        parts = [base or "effect"]
        if self.duration_s is not None:
            parts.append(f"{self.duration_s:g}s")
        if self.loop:
            parts.append("loop")
        return "-".join(parts)


@dataclass(frozen=True)
class SpeakerTurn:
    """Where a named speaker takes over, and who they are.

    `offset` is measured against the cleaned text, like every other marker, so
    it can be handed to the chunker as a forced boundary — which is what makes
    each turn a chunk of its own with its own voice.
    """

    offset: int
    speaker: str
    voice_id: str
    raw: str


@dataclass(frozen=True)
class ParsedScript:
    text: str
    anchors: list[Anchor] = field(default_factory=list)
    slots: list[ParsedSlot] = field(default_factory=list)
    turns: list[SpeakerTurn] = field(default_factory=list)
    chapters: list[ParsedChapter] = field(default_factory=list)
    # Names declared by a `[CAST]` block, mapped to voice ids.
    cast: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def boundaries(self) -> list[int]:
        """Offsets where a chunk must start, so effects land on a known time.

        An effect placed at a chunk edge has an exact timeline position, since
        the edge time is the sum of the preceding takes' measured durations.
        Placing one mid-chunk would mean interpolating, which is a guess.

        Speaker changes join them for the same reason turned inside out: a turn
        that shares a chunk with the previous speaker cannot be given its own
        voice, because a chunk is one request with one voice.

        A chapter joins them for the third version of the same reason: a
        chapter's timestamp is the start of its first chunk, and a chapter
        beginning mid-chunk could only be timed by interpolating within a take.
        """
        marks = {s.offset for s in self.slots}
        marks |= {t.offset for t in self.turns}
        marks |= {c.offset for c in self.chapters}
        return sorted(o for o in marks if 0 < o < len(self.text))

    @property
    def has_markers(self) -> bool:
        return bool(self.anchors or self.slots or self.turns or self.chapters)

    @property
    def speakers(self) -> list[str]:
        """Distinct speakers, in the order they first appear."""
        seen: dict[str, None] = {}
        for turn in self.turns:
            seen.setdefault(turn.speaker, None)
        return list(seen)

    def voice_at(self, offset: int) -> str | None:
        """Whose voice applies at a point in the text.

        The last turn at or before the offset wins, so text following a speaker
        change belongs to that speaker until the next one.
        """
        voice: str | None = None
        for turn in self.turns:
            if turn.offset <= offset:
                voice = turn.voice_id
            else:
                break
        return voice


def parse_time(raw: str) -> float:
    """`MM:SS`, `HH:MM:SS`, either with optional `.mmm`, to seconds."""
    head, _, millis = raw.partition(".")
    parts = [int(p) for p in head.split(":")]
    if len(parts) == 2:
        hours, minutes, seconds = 0, parts[0], parts[1]
    else:
        hours, minutes, seconds = parts
    total = float(hours * 3600 + minutes * 60 + seconds)
    if millis:
        total += int(millis.ljust(3, "0")) / 1000
    return total


def format_time(seconds: float) -> str:
    """Seconds to `HH:MM:SS.mmm`, the form the plan and filenames use."""
    if seconds < 0:
        seconds = 0.0
    whole = int(seconds)
    millis = round((seconds - whole) * 1000)
    if millis == 1000:  # rounding carried into the next second
        whole += 1
        millis = 0
    return f"{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}.{millis:03d}"


def format_youtube_time(seconds: float) -> str:
    """Seconds as a YouTube chapter stamp: `0:00`, `4:31`, `1:02:03`.

    Three differences from `format_time`, each one required rather than
    cosmetic. No milliseconds, because a chapter line is parsed as whole
    seconds. No hour field under an hour, and no zero-padding on the leading
    unit, because `00:04:31` is not accepted where `4:31` is.

    And it **truncates** where `format_time` rounds. A chapter at 59.6s must
    render `0:59`: rounding it to `1:00` would move it past a chapter that
    genuinely starts at 60s, and YouTube discards a chapter list whose stamps
    are not strictly increasing. Losing up to a second of precision is the
    cheaper error by far.
    """
    whole = max(0, int(seconds))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _parse_sfx_body(body: str) -> tuple[str, float | None, bool]:
    """Split `wind howling, 4s, loop` into description, duration and loop flag.

    Parsed from the right, because a description may legitimately contain
    commas — "footsteps on gravel, then a door opens" is one description, not
    two fields.
    """
    parts = [p.strip() for p in body.split(",")]
    duration: float | None = None
    loop = False

    while len(parts) > 1:
        last = parts[-1]
        if last.lower() == "loop":
            loop = True
            parts.pop()
            continue
        match = _DURATION_RE.match(last)
        if match and duration is None:
            duration = float(match.group(1))
            parts.pop()
            continue
        break

    return ", ".join(p for p in parts if p).strip(), duration, loop


def extract_cast(text: str) -> tuple[str, dict[str, str], list[str]]:
    """Pull `[CAST]` declarations out of a script.

    Returns the text with the block removed, the name→voice map, and any
    warnings. The block is consumed first because the speaker-prefix rule
    depends on knowing the names: without it, deciding whether `Morag:` is
    dialogue or prose would need a guess.

    Two shapes are accepted, because both read naturally:

        [CAST] Morag = 21m00Tcm4TlvDq8ikWAM · Keeper = pNInz6obpgDQGcFmaJgB
        [CAST]
        Morag = 21m00Tcm4TlvDq8ikWAM
        Keeper = pNInz6obpgDQGcFmaJgB
    """
    cast: dict[str, str] = {}
    warnings: list[str] = []
    kept: list[str] = []
    in_block = False

    for line in text.split("\n"):
        header = _CAST_BLOCK_RE.match(line)
        if header:
            in_block = True
            _absorb_pairs(header.group("body"), cast, warnings)
            continue

        # A blank line, or a line that is not a `name = voice` pair, ends the
        # block. Otherwise a whole script could be swallowed by a stray header.
        if in_block:
            if not line.strip():
                in_block = False
                kept.append(line)
                continue
            if _CAST_PAIR_RE.fullmatch(line.strip()):
                _absorb_pairs(line, cast, warnings)
                continue
            in_block = False

        kept.append(line)

    return "\n".join(kept), cast, warnings


def _absorb_pairs(body: str, cast: dict[str, str], warnings: list[str]) -> None:
    for match in _CAST_PAIR_RE.finditer(body):
        name = match.group("name").strip()
        if not name:
            continue
        voice = match.group("voice")
        if name in cast and cast[name] != voice:
            warnings.append(
                f"{name!r} is cast twice, as {cast[name]!r} and {voice!r}; the later one wins."
            )
        cast[name] = voice


def _find_turns(
    text: str, cast: dict[str, str], warnings: list[str]
) -> tuple[str, list[SpeakerTurn], list[tuple[int, int]]]:
    """Strip `Name:` prefixes for cast members, recording where each turn began.

    **A prefix is only a speaker if the name is cast.** That single rule is what
    makes this safe to run over ordinary prose: `Note:`, `Warning:` and a
    timestamp such as `12:30` have no cast entry, so they are left exactly as
    written. Recognising speakers by their capitalisation instead would
    eventually delete a word from somebody's narration, and silently.

    The prefix itself must never survive: left inline it would be spoken as
    "Morag colon" and billed for, which is the same failure the markers and the
    Markdown syntax are stripped to avoid.

    Returns the cleaned text, the turns, and the `(position, length)` cuts made
    — the caller needs those to move the anchor and slot offsets it already
    holds, which were measured against the text passed in here.
    """
    if not cast:
        return text, [], []

    # Case-insensitive lookup, so `MORAG:` and `Morag:` are the same person,
    # while the declared spelling is what gets reported.
    lookup = {name.casefold(): name for name in cast}

    out: list[str] = []
    turns: list[SpeakerTurn] = []
    cuts: list[tuple[int, int]] = []
    clean_len = 0
    cursor = 0
    unknown: set[str] = set()

    for match in _SPEAKER_LINE_RE.finditer(text):
        name = match.group("name").strip()
        declared = lookup.get(name.casefold())
        if declared is None:
            # Not cast, so not a speaker. Track it only to mention plausible
            # near-misses once at the end.
            if name and name[:1].isupper() and " " not in name.strip():
                unknown.add(name)
            continue

        before = text[cursor : match.start()]
        out.append(before)
        clean_len += len(before)
        turns.append(
            SpeakerTurn(
                offset=clean_len,
                speaker=declared,
                voice_id=cast[declared],
                raw=match.group(0),
            )
        )
        cuts.append((match.start(), match.end() - match.start()))
        cursor = match.end()

    # Reported whether or not anything matched. A script where *nothing*
    # matched is the confusing case — a cast was set up and a name misspelled —
    # so staying silent there would be the worst of the options.
    if unknown:
        shown = ", ".join(sorted(unknown)[:4])
        warnings.append(
            f"Left as narration because they are not in the cast: {shown}. "
            "Add them with `narrate cast set` if they are speakers."
        )

    if not turns:
        return text, [], []

    out.append(text[cursor:])
    return "".join(out), turns, cuts


def _shift(offset: int, cuts: list[tuple[int, int]]) -> int:
    """Move an offset from before a set of removals to after them.

    Every cut that lies wholly before `offset` moves it left by its length.
    Without this, an offset recorded in one pass points at the wrong words once
    a later pass has deleted text ahead of it — which is how a speaker change
    ends up four characters from the end of a paragraph.
    """
    for position, length in cuts:
        if position + length <= offset:
            offset -= length
        elif position < offset:
            # The offset sits inside a removed run; the text it named is gone,
            # so the start of the removal is the closest honest answer.
            offset = position
    return max(0, offset)


def parse_script(text: str, cast: dict[str, str] | None = None) -> ParsedScript:
    """Strip formatting and markers, returning clean text and what was removed.

    `cast` is the project's standing name→voice map. Names declared inline by a
    `[CAST]` block are merged over it, so a script can introduce a one-off
    character without editing the project.
    """
    anchors: list[Anchor] = []
    slots: list[ParsedSlot] = []
    chapters: list[ParsedChapter] = []

    # Formatting first: marker offsets are recorded against the cleaned text
    # and handed to the chunker, so they have to be measured against the same
    # string the chunker will see.
    text, warnings = strip_formatting(text)

    # Then the cast, because whether `Morag:` is a speaker or ordinary prose
    # depends entirely on whether Morag is cast.
    text, declared, cast_warnings = extract_cast(text)
    warnings += cast_warnings
    roster = {**(cast or {}), **declared}

    turns: list[SpeakerTurn] = []

    out: list[str] = []
    clean_len = 0
    cursor = 0

    for match in _MARKER_RE.finditer(text):
        before = text[cursor : match.start()]
        out.append(before)
        clean_len += len(before)

        # Removing an inline marker leaves whitespace on both sides. Collapse
        # to one space so the narration reads normally and the character count
        # (which is billed) does not carry the gap.
        trailing = len(before) - len(before.rstrip(" \t"))
        after = text[match.end() :]
        leading_space = after[:1] in (" ", "\t")
        if trailing and leading_space:
            out[-1] = before.rstrip(" \t")
            clean_len -= trailing

        offset = clean_len
        cursor = match.end()

        # A marker that began a line leaves the following text starting on a
        # space. Nothing collapses it above — there is no trailing whitespace
        # on a `before` that ends in a newline — so it is dropped here. One
        # stranded character is a billed character, and it also means an
        # offset recorded at this point names a space rather than a word.
        if not before.rstrip(" \t").endswith(" ") and (not before or before.endswith("\n")):
            skip = len(after) - len(after.lstrip(" \t"))
            cursor += skip

        if match.group("time") is not None:
            raw = match.group("time")
            label = (match.group("label") or "").strip()[:MAX_CHAPTER_TITLE]
            anchors.append(
                Anchor(offset=offset, target_s=parse_time(raw), raw=match.group(0), label=label)
            )
            # A named anchor marks a section start twice over — a time to aim at
            # and a name for it — so it yields a chapter without the position
            # having to be written again as a separate marker.
            if label:
                chapters.append(ParsedChapter(offset=offset, title=label, raw=match.group(0)))
            continue

        if match.group("chapter") is not None:
            title = match.group("chapter").strip()[:MAX_CHAPTER_TITLE]
            if not title:
                warnings.append(f"Ignored a chapter marker with no title: {match.group(0)!r}")
                continue
            chapters.append(ParsedChapter(offset=offset, title=title, raw=match.group(0)))
            continue

        if match.group("voice") is not None:
            name = match.group("voice").strip()
            declared_name = next(
                (n for n in roster if n.casefold() == name.casefold()),
                None,
            )
            if declared_name is None:
                warnings.append(
                    f"{match.group(0)!r} names a speaker who is not in the cast; "
                    "the narration continues in the current voice."
                )
                continue
            turns.append(
                SpeakerTurn(
                    offset=offset,
                    speaker=declared_name,
                    voice_id=roster[declared_name],
                    raw=match.group(0),
                )
            )
            continue

        description, duration, loop = _parse_sfx_body(match.group("sfx"))
        if not description:
            warnings.append(f"Ignored an effect marker with no description: {match.group(0)!r}")
            continue
        if duration is not None and not (MIN_EFFECT_SECONDS <= duration <= MAX_EFFECT_SECONDS):
            warnings.append(
                f"{match.group(0)!r}: {duration:g}s is outside the provider's "
                f"{MIN_EFFECT_SECONDS:g}-{MAX_EFFECT_SECONDS:g}s range; "
                "the duration will be left for the provider to choose."
            )
            duration = None
        slots.append(
            ParsedSlot(
                offset=offset,
                description=description,
                duration_s=duration,
                loop=loop,
                raw=match.group(0),
            )
        )

    out.append(text[cursor:])
    clean = "".join(out)

    # An anchor or effect on its own line leaves a stranded blank line; three
    # or more newlines would otherwise read as an extra paragraph break.
    #
    # This shortens the text, so it moves every offset recorded above — which
    # it silently did not, before. The error was small and the boundary snapping
    # absorbed it for effects, but a speaker change has to land on a word, and
    # a two-character drift put one in the middle of "Seven".
    blank_cuts: list[tuple[int, int]] = []

    def _collapse(match: re.Match[str]) -> str:
        # The first two newlines survive; everything after them is the cut.
        blank_cuts.append((match.start() + 2, len(match.group(0)) - 2))
        return "\n\n"

    clean = _BLANKS_RE.sub(_collapse, clean)
    if blank_cuts:
        anchors = [replace(a, offset=_shift(a.offset, blank_cuts)) for a in anchors]
        slots = [replace(s, offset=_shift(s.offset, blank_cuts)) for s in slots]
        turns = [replace(t, offset=_shift(t.offset, blank_cuts)) for t in turns]
        chapters = [replace(c, offset=_shift(c.offset, blank_cuts)) for c in chapters]

    # Speaker prefixes come *after* the markers, deliberately. Both passes
    # delete text, and offsets recorded by one are meaningless to the other
    # unless they share a coordinate system — so this pass runs last, on the
    # marker-free text, and everything already recorded is moved to match by
    # the cuts it reports. Doing it the other way round put a speaker change
    # four characters from the end of a paragraph.
    clean, prefix_turns, cuts = _find_turns(clean, roster, warnings)
    if cuts:
        anchors = [replace(a, offset=_shift(a.offset, cuts)) for a in anchors]
        slots = [replace(s, offset=_shift(s.offset, cuts)) for s in slots]
        turns = [replace(t, offset=_shift(t.offset, cuts)) for t in turns]
        chapters = [replace(c, offset=_shift(c.offset, cuts)) for c in chapters]
    turns += prefix_turns

    lead = len(clean) - len(clean.lstrip())
    if lead:
        clean = clean[lead:]
        anchors = [replace(a, offset=max(0, a.offset - lead)) for a in anchors]
        slots = [replace(s, offset=max(0, s.offset - lead)) for s in slots]
        turns = [replace(t, offset=max(0, t.offset - lead)) for t in turns]
        chapters = [replace(c, offset=max(0, c.offset - lead)) for c in chapters]

    # A `[VOICE:]` marker and a `Name:` prefix are found by different passes,
    # so the combined list is only in document order once both have run.
    turns.sort(key=lambda t: t.offset)

    return ParsedScript(
        text=clean,
        anchors=anchors,
        slots=slots,
        turns=turns,
        chapters=chapters,
        cast=roster,
        warnings=warnings,
    )


def strip_markers(text: str) -> str:
    """The cleaned narration alone — what is safe to send and to bill for."""
    return parse_script(text).text


# An audio tag: `[fast-paced]`, `[whispers]`, `[building energy]`. Short, on one
# line, and — after `parse_script` — never one of the structural markers, which
# are gone by then.
_AUDIO_TAG_RE = re.compile(r"\[[^\]\n]{1,60}\]")


def spoken_text(text: str, *, audio_tags: bool) -> str:
    """What a chunk should actually *sound* like.

    Different from what was sent. On a model that honours audio tags,
    `[fast-paced]` is a performance direction and is never spoken, so anything
    comparing the audio against the text has to remove it first — or every tag
    would be reported as a missing word.

    On a model that does not honour them, the brackets are left in, because
    that model will read them out: the honest expectation is the embarrassing
    one, and the lint at ingest is where that gets caught before any spend.
    """
    if not audio_tags:
        return text
    return " ".join(_AUDIO_TAG_RE.sub(" ", text).split())
