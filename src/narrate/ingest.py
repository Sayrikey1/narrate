"""Turning a raw script into chunks, anchors and effect slots.

One function, used by the CLI, the API and the tests, because this is the step
where markers are stripped. Duplicating it would mean duplicating the guarantee
that nothing bracketed reaches a billed request — and a guarantee that exists
in three copies is one that eventually holds in two.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from narrate.chunking import MARKER_RE, chunk_script, sentence_starts
from narrate.chunking import Chunk as ChunkObj
from narrate.db.models import Chunk, Script, Take
from narrate.effects import sync_slots_from_script
from narrate.lint import lint_chunks
from narrate.registry import ModelSpec
from narrate.script_parse import ParsedScript, SpeakerTurn, parse_script

# A script is prose. These are the extensions that hold prose as text; a
# `.docx` or `.pdf` would need parsing, and PDF in particular loses the
# paragraph breaks the chunker splits on.
SCRIPT_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".text"})

# Generous for prose: a 30-minute episode is roughly 30 KB. The cap exists to
# reject a file that is plainly not a script before it reaches the chunker.
MAX_SCRIPT_BYTES = 1_000_000


class ScriptHasTakes(RuntimeError):
    """Re-chunking would orphan generated audio and its cost records."""


class UnsupportedScript(ValueError):
    """The uploaded file is not something we can read as a script."""


def decode_script(filename: str, data: bytes) -> str:
    """Validate and decode an uploaded script, or say precisely what is wrong.

    Checked in this order: extension, size, then that the bytes are actually
    UTF-8 text. **The decode is the check that matters** — an extension proves
    nothing, and undecodable bytes reaching the chunker produce nonsense that
    costs money to discover.
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in SCRIPT_SUFFIXES:
        allowed = ", ".join(sorted(SCRIPT_SUFFIXES))
        raise UnsupportedScript(f"{filename!r} is not a text script. Upload one of: {allowed}.")
    if len(data) > MAX_SCRIPT_BYTES:
        raise UnsupportedScript(
            f"{filename!r} is {len(data) / 1_000_000:.1f} MB. Scripts are prose — "
            f"the limit is {MAX_SCRIPT_BYTES // 1_000_000} MB."
        )
    if not data.strip():
        raise UnsupportedScript(f"{filename!r} is empty.")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsupportedScript(
            f"{filename!r} is not UTF-8 text (byte {exc.start} is not decodable). "
            "Re-save it as UTF-8 plain text or Markdown."
        ) from exc
    # A NUL byte means binary content wearing a text extension.
    if "\x00" in text:
        raise UnsupportedScript(f"{filename!r} contains binary data, not text.")
    return text


@dataclass
class IngestResult:
    chunks: list[ChunkObj] = field(default_factory=list)
    slots_created: int = 0
    warnings: list[str] = field(default_factory=list)
    parsed: ParsedScript | None = None
    # How many chunks were given a voice from the cast, and who spoke.
    turns_assigned: int = 0
    speakers: list[str] = field(default_factory=list)
    # True when the chunks were packed as dialogue groups rather than one
    # chunk per turn.
    dialogue: bool = False
    chapters_named: int = 0


def ingest_script(
    session: Session,
    script: Script,
    spec: ModelSpec,
    prefix_tags: str = "",
    cast: dict[str, str] | None = None,
    dialogue: bool = False,
) -> IngestResult:
    """Parse, chunk, and record anchors, effect slots and speaker turns.

    Markers are removed before chunking, so the stored chunk text — the only
    thing ever sent to a provider — contains none of them.

    `cast` maps speaker names to voice ids. It is what makes `Morag:` a speaker
    change rather than prose, and it is passed in rather than looked up here so
    this stays the one function both the CLI and the API call.

    Two ways to perform a multi-speaker script:

    * **One chunk per turn** (the default). Each turn becomes its own chunk with
      its own `voice_id`, which the runner already prefers over the script's and
      the project's. Nothing downstream changes: every turn is separately
      re-rollable and separately costed.
    * **Dialogue groups** (`dialogue=True`, `eleven_v3` only). Consecutive turns
      are packed up to the dialogue endpoint's 2,000-character ceiling and each
      group becomes one chunk carrying its turns, so the model hears the whole
      exchange.
    """
    existing = list(session.scalars(select(Chunk).where(Chunk.script_id == script.id)).all())
    if existing:
        has_takes = session.scalar(
            select(Take.id).where(Take.chunk_id.in_([c.id for c in existing])).limit(1)
        )
        if has_takes:
            raise ScriptHasTakes(
                "This script already has generated takes. Re-chunking would orphan them "
                "and their cost records. Create a new script instead."
            )
        for row in existing:
            session.delete(row)
        session.flush()

    chunk_plan = plan_chunks(script.source_text, spec, prefix_tags, cast, dialogue)
    parsed, planned = chunk_plan.parsed, chunk_plan.planned
    result = IngestResult(warnings=list(chunk_plan.warnings), parsed=parsed)
    result.speakers = parsed.speakers
    result.dialogue = chunk_plan.dialogue
    result.chunks = [p.chunk for p in planned]

    offsets: dict[int, int] = {}
    assigned = 0
    for plan in planned:
        c = plan.chunk
        row = Chunk(
            script_id=script.id,
            ordinal=c.ordinal,
            text=c.text,
            source=c.source,
            start_offset=c.start_offset,
        )
        # Set inline rather than in a second pass over offsets: a subdivided
        # chunk has no `start_offset` at all — subdivision strips whitespace, so
        # an approximate one would be worse than none — and a chunk with no
        # voice is a chunk that cannot be generated.
        if plan.turns:
            row.turns_json = json.dumps(plan.turns)
            row.source = "dialogue"
        if plan.voice_id:
            row.voice_id = plan.voice_id
            if not plan.turns and c.source == "marker":
                row.source = "speaker"
            assigned += 1

        if c.start_offset is not None:
            offsets[c.ordinal] = c.start_offset
        session.add(row)
    session.flush()

    result.turns_assigned = assigned
    _attach_anchors(session, script.id, parsed, offsets)
    result.chapters_named = _attach_chapters(session, script.id, parsed, offsets, result.warnings)
    result.slots_created = len(sync_slots_from_script(session, script.id, parsed, offsets))
    # Last, so it sees the chunks as they will actually be sent.
    result.warnings += lint_chunks([(c.ordinal, c.text) for c in result.chunks], spec)
    return result


@dataclass
class ChunkPlan:
    """A script's chunks as they would be written, before anything is."""

    parsed: ParsedScript
    planned: list[_Planned]
    dialogue: bool
    warnings: list[str]


def plan_chunks(
    source_text: str,
    spec: ModelSpec,
    prefix_tags: str = "",
    cast: dict[str, str] | None = None,
    dialogue: bool = False,
    keep: list[str] | None = None,
) -> ChunkPlan:
    """Parse and chunk a script without writing anything.

    The half of ingest a replacement needs on its own: the new chunks have to be
    known before any old one is touched, so the ones that did not change can
    keep their takes.

    `keep` is the old chunks' text. Where one still appears in the new script,
    its edges become cuts, so the chunker cannot re-pack it with its
    neighbours: without that, cutting one paragraph from an unmarked script
    shifts every later chunk's contents, and every one of them is paid for
    again. A script with `[CHUNK n]` markers needs none of this — the writer's
    cuts do not move when a neighbour is edited — and gets none, so a kept edge
    can never override one.
    """
    parsed = parse_script(source_text, cast)
    warnings = list(parsed.warnings)
    edges: set[int] = set()
    if keep and not MARKER_RE.search(parsed.text):
        for span in locate_chunks(keep, parsed.text):
            if span is not None:
                edges.update(span)

    as_dialogue = dialogue and bool(parsed.turns)
    if dialogue and not spec.dialogue:
        warnings.append(
            f"{spec.label} cannot generate dialogue — only models with a dialogue "
            "endpoint can. Falling back to one chunk per speaker turn, which works "
            "on every model."
        )
        as_dialogue = False

    def oversize(size: int, ceiling: int) -> None:
        warnings.append(
            f"a marked segment of {size:,} chars exceeds the {ceiling:,} ceiling and was subdivided"
        )

    if as_dialogue:
        planned = _dialogue_chunks(parsed, spec, prefix_tags, oversize, starts=edges)
    else:
        # Turn offsets reach the chunker as cuts, which is what gives each turn
        # a chunk of its own — a chunk is one request with one voice, so a turn
        # sharing a chunk with the previous speaker could not be cast at all.
        # Exact cuts, not snapped ones: a turn begins where its words do, and
        # the nearest sentence start can lie back inside the previous line —
        # one ending "—" or "…", or followed by a `[CHUNK]` marker — which gave
        # the next speaker's words to the previous speaker's voice.
        #
        # A chunk after the first of a subdivided part has no offset of its
        # own, but it is the same turn continuing, so it keeps the voice its
        # part began with — not the narrator's, which is what None would mean.
        turns = {t.offset for t in parsed.turns}
        planned = []
        voice: str | None = None
        for c in chunk_script(
            parsed.text,
            spec,
            prefix_tags,
            on_oversize=oversize,
            boundaries=[o for o in parsed.boundaries if o not in turns],
            exact=sorted(edges | turns),
        ):
            if parsed.turns and c.start_offset is not None:
                voice = parsed.voice_at(c.start_offset)
            planned.append(_Planned(chunk=c, voice_id=voice))
    return ChunkPlan(parsed=parsed, planned=planned, dialogue=as_dialogue, warnings=warnings)


def locate_chunks(texts: list[str], text: str) -> list[tuple[int, int] | None]:
    """Where each chunk sits in a script's text, as (start, start of what follows).

    By its words, whitespace aside: the chunker tidies the spacing it stores,
    and regeneration can join a paragraph break, so a chunk is not always a
    byte-exact slice of its script. In order, each search starting where the
    last match ended, so two chunks with the same words find their own places;
    a chunk found only earlier than that — a paragraph moved up — is taken
    there, provided nothing else already claimed the spot.

    A match counts only where a chunk could have begun and ended: at a line
    break or a sentence start on both sides. The same words inside a longer
    paragraph are not that chunk, and cutting there would split a paragraph
    the writer did not break. None for a chunk not found.
    """
    starts = set(sentence_starts(text))

    def edge(start: int, end: int) -> tuple[int, int] | None:
        before = text[:start].rstrip(" \t")
        if not (before == "" or before.endswith("\n") or start in starts):
            return None
        after = end + (len(text[end:]) - len(text[end:].lstrip()))
        if not (after == len(text) or "\n" in text[end:after] or after in starts):
            return None
        return start, after

    claimed: list[tuple[int, int]] = []

    def find(pattern: re.Pattern[str], position: int) -> tuple[int, int] | None:
        for found in pattern.finditer(text, position):
            span = edge(found.start(), found.end())
            if span and not any(a < span[1] and span[0] < b for a, b in claimed):
                return span
        return None

    out: list[tuple[int, int] | None] = []
    position = 0
    for chunk in texts:
        words = chunk.split()
        if not words:
            out.append(None)
            continue
        pattern = re.compile(r"\s+".join(map(re.escape, words)))
        span = find(pattern, position)
        if span is not None:
            position = span[1]
        else:
            span = find(pattern, 0)
        if span is not None:
            claimed.append(span)
        out.append(span)
    return out


@dataclass
class _Planned:
    """A chunk about to be written, with what it is going to be generated as."""

    chunk: ChunkObj
    voice_id: str | None = None
    # `[{"speaker", "voice_id", "text"}]` for a dialogue chunk; None otherwise.
    turns: list[dict[str, str]] | None = None


def _dialogue_chunks(
    parsed: ParsedScript,
    spec: ModelSpec,
    prefix_tags: str,
    on_oversize: Callable[[int, int], None],
    starts: set[int] | None = None,
) -> list[_Planned]:
    """Chunks for a dialogue run: one per group of turns that fits one request.

    Built directly rather than by handing group starts to `chunk_script` as
    boundaries, which was the first attempt and was wrong twice over. The
    chunker enforces the *model's* ceiling — 5,000 characters on v3 — while the
    dialogue endpoint accepts 2,000 across all of its inputs; and it snaps every
    boundary to the nearest sentence start, so a passage without sentence
    punctuation collapses several groups into one. Between them those produced a
    3,506-character chunk aimed at a 2,000-character endpoint.

    The groups already fit by construction, so there is nothing left for
    sentence packing to decide. `starts` are turns that must open a group — a
    kept chunk's first turn, and the turn after its last — so cutting one line
    does not regroup, and re-bill, every line after it.
    """
    groups = _pack_dialogue(parsed, spec.dialogue_max_chars, starts or set())
    planned: list[_Planned] = []
    ordinal = 1

    for index, (start, turns) in enumerate(groups):
        end = groups[index + 1][0] if index + 1 < len(groups) else len(parsed.text)
        pieces = _split_turns(parsed.text, turns, end)
        span = parsed.text[start:end].strip()
        if not span:
            continue

        total = sum(len(text) for _, text in pieces)
        if len(turns) > 1 and total <= spec.dialogue_max_chars:
            planned.append(
                _Planned(
                    chunk=ChunkObj(
                        ordinal=ordinal,
                        text=span,
                        source="dialogue",
                        prefix_tags=prefix_tags,
                        start_offset=start,
                    ),
                    voice_id=turns[0].voice_id,
                    turns=[
                        {"speaker": t.speaker, "voice_id": t.voice_id, "text": text}
                        for t, text in pieces
                    ],
                )
            )
            ordinal += 1
            continue

        # One speaker, or a single turn too long for the dialogue endpoint.
        # Either way it is ordinary speech in that speaker's voice, subdivided
        # by the chunker if it needs to be. Degrading to one voice is the honest
        # outcome; a truncated request would not be.
        if len(turns) == 1 and total > spec.dialogue_max_chars:
            on_oversize(total, spec.dialogue_max_chars)
        voice = turns[0].voice_id if turns else parsed.voice_at(start)
        for position, piece in enumerate(
            chunk_script(span, spec, prefix_tags, on_oversize=on_oversize)
        ):
            planned.append(
                _Planned(
                    chunk=ChunkObj(
                        ordinal=ordinal,
                        text=piece.text,
                        source=piece.source,
                        prefix_tags=prefix_tags,
                        start_offset=start if position == 0 else None,
                    ),
                    voice_id=voice,
                )
            )
            ordinal += 1

    return planned


def _pack_dialogue(
    parsed: ParsedScript, ceiling: int, starts: set[int] | None = None
) -> list[tuple[int, list[SpeakerTurn]]]:
    """Group consecutive turns into requests that fit the dialogue ceiling.

    The endpoint takes at most 2,000 characters across all of its inputs, which
    is tighter than the model's own request limit — so this is its own packing
    problem rather than something the chunker can be asked to do. Returns
    `(start_offset, turns)` per group.
    """
    groups: list[tuple[int, list[SpeakerTurn]]] = []
    current: list[SpeakerTurn] = []
    start = 0
    size = 0

    for turn, length in _turn_spans(parsed):
        # A single turn longer than the ceiling still gets its own group; the
        # caller degrades it to ordinary speech rather than truncating it.
        if current and (size + length > ceiling or turn.offset in (starts or ())):
            groups.append((start, current))
            current, size = [], 0
        if not current:
            start = turn.offset
        current.append(turn)
        size += length

    if current:
        groups.append((start, current))
    return groups


def _turn_spans(parsed: ParsedScript) -> list[tuple[SpeakerTurn, int]]:
    """Each turn with the length of the text it covers."""
    spans: list[tuple[SpeakerTurn, int]] = []
    for index, turn in enumerate(parsed.turns):
        end = parsed.turns[index + 1].offset if index + 1 < len(parsed.turns) else len(parsed.text)
        spans.append((turn, max(0, end - turn.offset)))
    return spans


def _split_turns(
    full_text: str, turns: list[SpeakerTurn], group_end: int
) -> list[tuple[SpeakerTurn, str]]:
    """Each turn paired with its own words.

    Sliced from `full_text` by offset rather than searched for in the chunk,
    because two speakers can say exactly the same thing and a search would
    attribute both lines to whichever came first.
    """
    out: list[tuple[SpeakerTurn, str]] = []
    for index, turn in enumerate(turns):
        end = turns[index + 1].offset if index + 1 < len(turns) else group_end
        text = full_text[turn.offset : end].strip()
        if text:
            out.append((turn, text))
    return out


def _attach_anchors(
    session: Session, script_id: int, parsed: ParsedScript, offsets: dict[int, int]
) -> None:
    """Give each `[@ MM:SS]` anchor the chunk it applies to.

    Matched on nearest start rather than exact offset: a boundary snaps to the
    nearest sentence, so an anchor's raw position rarely equals a chunk start
    to the character. The anchor belongs to whichever chunk begins closest to
    where the writer put it.
    """
    if not parsed.anchors or not offsets:
        return
    rows = {
        c.ordinal: c
        for c in session.scalars(select(Chunk).where(Chunk.script_id == script_id)).all()
    }
    for anchor in parsed.anchors:
        ordinal = min(offsets.items(), key=lambda pair: (abs(pair[1] - anchor.offset), pair[0]))[0]
        row = rows.get(ordinal)
        if row is not None and row.target_start_s is None:
            row.target_start_s = anchor.target_s


def _attach_chapters(
    session: Session,
    script_id: int,
    parsed: ParsedScript,
    offsets: dict[int, int],
    warnings: list[str],
) -> int:
    """Name the chunk each chapter begins, and say so when two collide.

    Nearest start, exactly as `_attach_anchors` matches: a chapter offset is a
    forced boundary, so it normally *is* a chunk start, but the chunker snaps to
    the nearest sentence and a subdivided segment can move it a little.

    Two chapters landing on one chunk is reported rather than silently resolved.
    They would share a timestamp, and YouTube discards a chapter list whose
    stamps are not strictly increasing — so the writer needs to know that one of
    them will not appear, and which.
    """
    if not parsed.chapters or not offsets:
        return 0
    rows = {
        c.ordinal: c
        for c in session.scalars(select(Chunk).where(Chunk.script_id == script_id)).all()
    }
    named = 0
    for chapter in parsed.chapters:
        ordinal = min(offsets.items(), key=lambda pair: (abs(pair[1] - chapter.offset), pair[0]))[0]
        row = rows.get(ordinal)
        if row is None:
            continue
        if row.chapter_title is not None:
            warnings.append(
                f"Chapter {chapter.title!r} lands on the same chunk as "
                f"{row.chapter_title!r}, so only the first will appear. Move one, or "
                "add words between them."
            )
            continue
        row.chapter_title = chapter.title
        named += 1
    return named
