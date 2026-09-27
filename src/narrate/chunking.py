"""Splitting a script into orderable, generatable segments (PRD F1).

Priority of boundaries, best first:

    explicit markers  ->  the writer said where to cut, so cut there
    paragraph breaks  ->  a natural pause the listener already expects
    sentence ends     ->  audible, but a clean full stop
    clause marks      ->  a comma or dash; noticeable
    hard split        ->  mid-word; only when one clause exceeds the ceiling

Two rules the rest of the pipeline depends on:

* Prefix tags are applied *before* measuring. They are billable characters and
  they count against the model's per-request limit, so a chunk sized without
  them can be rejected at submit time or silently overrun the estimate.
* Nothing is ever emitted above the model's ceiling. `ModelSpec.chunk_ceiling`
  already carries a safety margin below the published limit.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from narrate.registry import ModelSpec

# `## [CHUNK 3]`, `### [chunk]`, or a bare `[CHUNK 3]` on its own line.
MARKER_RE = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?\[chunk(?:[ \t]+\d+)?\][ \t]*$", re.IGNORECASE | re.MULTILINE
)

PARAGRAPH_RE = re.compile(r"\n[ \t]*\n+")

# A sentence end is terminal punctuation, any closing quotes or brackets that
# belong to it, then whitespace. Whether it is a *real* boundary is decided by
# `_is_sentence_boundary` — this only proposes candidates.
_SENTENCE_CANDIDATE_RE = re.compile(r"(?<=[.!?])([\"'”’\)\]]*)(\s+)")

# Clause-level fallback, used only inside a sentence that is too long on its own.
_CLAUSE_RE = re.compile(r"(?<=[,;:—–])(\s+)")

# Words that end in a period without ending a sentence. Kept deliberately small
# and narration-shaped: a script is prose, not legal text, and every entry here
# is a chance to *miss* a real boundary, so the list earns its length.
# fmt: off
_ABBREVIATIONS = frozenset({
    "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.", "rev.", "hon.",
    "vs.", "etc.", "e.g.", "i.e.", "cf.", "al.", "esp.", "approx.", "est.",
    "inc.", "ltd.", "co.", "corp.", "dept.", "univ.",
    "u.s.", "u.k.", "u.n.", "e.u.",
    "a.m.", "p.m.", "b.c.", "a.d.",
    "no.", "vol.", "ch.", "fig.", "pp.", "ed.",
    "jan.", "feb.", "mar.", "apr.", "jun.", "jul.", "aug.", "sep.", "sept.",
    "oct.", "nov.", "dec.",
    "ave.", "blvd.", "rd.", "mt.",
})
# fmt: on

# What a new sentence may begin with: a capital, an opening quote or bracket, a
# digit, or an audio tag such as `[whispers]`.
_SENTENCE_START_RE = re.compile(r"[\"'“‘\(\[A-Z0-9—]")


@dataclass(frozen=True)
class Chunk:
    """One generatable segment of a script."""

    ordinal: int
    text: str
    source: str
    prefix_tags: str = ""

    # Offset into the parsed script this chunk begins at, when that is exactly
    # known — the start of the script, and the start of every forced boundary
    # segment. `None` elsewhere, because subdivision strips whitespace and an
    # approximate offset is worse than an absent one for placing effects.
    start_offset: int | None = None

    @property
    def submitted_text(self) -> str:
        """Exactly what gets sent to the provider — tags included."""
        return apply_prefix(self.text, self.prefix_tags)

    @property
    def char_count(self) -> int:
        """Billable length. Always measured on the submitted text."""
        return len(self.submitted_text)

    @property
    def tag_chars(self) -> int:
        """How many of `char_count` the prefix tags account for."""
        return self.char_count - len(self.text)


def apply_prefix(text: str, prefix_tags: str) -> str:
    tags = prefix_tags.strip()
    if not tags:
        return text
    return f"{tags} {text}"


# ---------------------------------------------------------------------------
# Sentence and clause splitting
# ---------------------------------------------------------------------------


def _is_sentence_boundary(text: str, punct_index: int, next_index: int) -> bool:
    """Decide whether the terminal punctuation at `punct_index` ends a sentence."""
    char = text[punct_index]

    # What follows has to look like the start of a sentence. A lowercase word
    # after a full stop means we are mid-sentence (an abbreviation we do not
    # know, most often).
    if next_index < len(text) and not _SENTENCE_START_RE.match(text[next_index]):
        return False

    # `!` and `?` are unambiguous — nothing abbreviates with them.
    if char != ".":
        return True

    # Walk back over the token that owns this period.
    start = punct_index
    while start > 0 and (text[start - 1].isalnum() or text[start - 1] == "."):
        start -= 1
    token = text[start : punct_index + 1].lower()

    if token in _ABBREVIATIONS:
        return False

    # A single letter before the period is an initial — "J. R. R. Tolkien".
    if len(token) == 2 and token[0].isalpha():
        return False

    return True


def sentence_starts(text: str) -> list[int]:
    """Offsets at which a new sentence begins, including 0 and len(text).

    Used to snap a forced chunk boundary onto a real sentence end, so an effect
    marker dropped mid-sentence does not cut the narration in half.
    """
    starts = [0]
    for match in _SENTENCE_CANDIDATE_RE.finditer(text):
        punct_index = match.start() - 1
        if _is_sentence_boundary(text, punct_index, match.end()):
            starts.append(match.end())
    starts.append(len(text))
    return sorted(set(starts))


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences, keeping terminal punctuation attached."""
    if not text.strip():
        return []

    out: list[str] = []
    start = 0
    for match in _SENTENCE_CANDIDATE_RE.finditer(text):
        # The lookbehind consumed the terminal punctuation just before match.start().
        punct_index = match.start() - 1
        if not _is_sentence_boundary(text, punct_index, match.end()):
            continue
        piece = text[start : match.end(1)].strip()
        if piece:
            out.append(piece)
        start = match.end()

    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


def snap_to_sentence(text: str, offset: int) -> int:
    """Move `offset` to the nearest sentence start."""
    starts = sentence_starts(text)
    return min(starts, key=lambda s: (abs(s - offset), s))


def split_clauses(text: str) -> list[str]:
    """Last resort before a hard split: break on commas, semicolons and dashes."""
    pieces = [p.strip() for p in _CLAUSE_RE.split(text)]
    # `re.split` with a capturing group interleaves the separators; drop the
    # whitespace groups and keep the text.
    return [p for p in pieces if p and not p.isspace()]


def _hard_split(text: str, size: int) -> list[str]:
    """Split on whitespace nearest each `size` boundary, mid-word only if forced."""
    out: list[str] = []
    remaining = text
    while len(remaining) > size:
        window = remaining[:size]
        cut = window.rfind(" ")
        if cut <= 0:
            cut = size
        out.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        out.append(remaining)
    return out


# ---------------------------------------------------------------------------
# Packing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Segment:
    text: str
    source: str


def _subdivide(unit: str, target_max: int, ceiling: int) -> list[_Segment]:
    """Break one oversized unit down, taking the cleanest cut available."""
    sentences = split_sentences(unit)
    if len(sentences) > 1:
        return _pack(sentences, " ", target_max, ceiling, "sentence")

    clauses = split_clauses(unit)
    if len(clauses) > 1:
        return _pack(clauses, " ", target_max, ceiling, "clause")

    return [_Segment(piece, "hard") for piece in _hard_split(unit, ceiling)]


def _pack(
    units: list[str],
    joiner: str,
    target_max: int,
    ceiling: int,
    source: str,
) -> list[_Segment]:
    """Greedily fill segments up to `target_max`, never exceeding `ceiling`."""
    out: list[_Segment] = []
    buf: list[str] = []
    buf_len = 0
    jlen = len(joiner)

    def flush() -> None:
        nonlocal buf, buf_len
        if buf:
            out.append(_Segment(joiner.join(buf), source))
            buf = []
            buf_len = 0

    for unit in units:
        ulen = len(unit)

        # Too big to ever fit — cut it apart on its own terms.
        if ulen > ceiling:
            flush()
            out.extend(_subdivide(unit, target_max, ceiling))
            continue

        prospective = buf_len + jlen + ulen if buf else ulen
        if buf and prospective > target_max:
            flush()
            buf, buf_len = [unit], ulen
            continue

        buf.append(unit)
        buf_len = prospective

    flush()
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def find_markers(text: str) -> list[str]:
    """Segments delimited by explicit `## [CHUNK n]` markers, if any are present."""
    if not MARKER_RE.search(text):
        return []
    parts = [p.strip() for p in MARKER_RE.split(text)]
    return [p for p in parts if p]


def chunk_script(
    text: str,
    spec: ModelSpec,
    prefix_tags: str = "",
    on_oversize: Callable[[int, int], None] | None = None,
    boundaries: list[int] | None = None,
    exact: list[int] | None = None,
) -> list[Chunk]:
    """Split `text` into chunks that fit `spec`, with `prefix_tags` accounted for.

    Explicit `[CHUNK n]` markers win over automatic splitting, but a marked
    segment that exceeds the model's ceiling is still subdivided — honouring
    the writer's intent cannot extend past what the API will accept.

    `boundaries` are source offsets where a chunk *must* begin, supplied by
    the script parser for effect markers. Splitting there is what gives an
    effect an exact timeline position: it sits at a chunk edge, and an edge
    time is the sum of the preceding takes' measured durations rather than an
    interpolation. Each boundary snaps to the nearest sentence start so a
    marker dropped mid-sentence cannot cut the narration in half.

    `exact` are cuts taken where they are, unsnapped — the edges of chunks a
    replacement keeps. Those are already where a chunk began, so there is
    nothing to snap to; and snapping one that sits after a line ending in "—"
    or "…", which no sentence rule recognises, would pull it back inside the
    chunk and split the very chunk it was there to keep.
    """
    prefix_len = len(apply_prefix("", prefix_tags))
    ceiling = spec.chunk_ceiling - prefix_len
    _, target_max = spec.chunk_target
    target_max = max(1, target_max - prefix_len)

    if ceiling <= 0:
        raise ValueError(
            f"Prefix tags ({prefix_len} chars) leave no room under "
            f"{spec.model_id}'s {spec.chunk_ceiling}-character ceiling."
        )

    # Forced boundaries partition the script first; everything else then runs
    # independently inside each part.
    cuts = sorted(
        set(_snapped_cuts(text, boundaries)) | {o for o in exact or [] if 0 < o < len(text)}
    )
    parts = [(cuts[i], text[cuts[i] : cuts[i + 1]]) for i in range(len(cuts) - 1)]

    chunks: list[Chunk] = []
    ordinal = 1
    for offset, part in parts:
        if not part.strip():
            continue
        segments = _segment(part, spec, target_max, ceiling, on_oversize)
        for position, seg in enumerate(segments):
            if not seg.text.strip():
                continue
            chunks.append(
                Chunk(
                    ordinal=ordinal,
                    text=seg.text,
                    source=seg.source,
                    prefix_tags=prefix_tags,
                    # Only the first chunk of a part begins at a known offset:
                    # subdivision strips whitespace, so anything after it would
                    # be approximate, and an approximate offset is worse than
                    # none when it is used to place an effect.
                    start_offset=offset if position == 0 else None,
                )
            )
            ordinal += 1
    return chunks


def _snapped_cuts(text: str, boundaries: list[int] | None) -> list[int]:
    """Sorted partition points for the script, including 0 and its end."""
    cuts = {0, len(text)}
    for raw in boundaries or []:
        snapped = snap_to_sentence(text, raw)
        if 0 < snapped < len(text):
            cuts.add(snapped)
    return sorted(cuts)


def _segment(
    text: str,
    spec: ModelSpec,
    target_max: int,
    ceiling: int,
    on_oversize: Callable[[int, int], None] | None,
) -> list[_Segment]:
    """Chunk one part of a script: `[CHUNK n]` markers if present, else paragraphs."""
    # Tested on the marker itself rather than on what it delimits: a part cut
    # between two kept chunks can hold nothing but a `[CHUNK n]` line, and that
    # line is not a paragraph to be read aloud.
    if MARKER_RE.search(text):
        marked = find_markers(text)
        segments: list[_Segment] = []
        for part in marked:
            if len(part) > ceiling:
                if on_oversize is not None:
                    on_oversize(len(part), ceiling)
                segments.extend(_subdivide(part, target_max, ceiling))
            else:
                segments.append(_Segment(part, "marker"))
        return segments

    paragraphs = [p.strip() for p in PARAGRAPH_RE.split(text) if p.strip()]
    return _pack(paragraphs, "\n\n", target_max, ceiling, "paragraph")


def merge_chunks(chunks: list[Chunk], first: int, second: int) -> list[Chunk]:
    """Merge two adjacent chunks (F2), renumbering the result."""
    if abs(first - second) != 1:
        raise ValueError(f"Chunks {first} and {second} are not adjacent.")
    lo, hi = sorted((first, second))
    by_ordinal = {c.ordinal: c for c in chunks}
    if lo not in by_ordinal or hi not in by_ordinal:
        raise ValueError(f"No such chunk: {lo if lo not in by_ordinal else hi}.")

    merged = Chunk(
        ordinal=lo,
        text=f"{by_ordinal[lo].text}\n\n{by_ordinal[hi].text}",
        source="manual",
        prefix_tags=by_ordinal[lo].prefix_tags,
    )
    kept = [c for c in chunks if c.ordinal not in (lo, hi)]
    kept.append(merged)
    return _renumber(kept)


def split_chunk(chunks: list[Chunk], ordinal: int, at: int) -> list[Chunk]:
    """Split one chunk at a character offset (F2), renumbering the result."""
    by_ordinal = {c.ordinal: c for c in chunks}
    if ordinal not in by_ordinal:
        raise ValueError(f"No such chunk: {ordinal}.")
    target = by_ordinal[ordinal]
    if not 0 < at < len(target.text):
        raise ValueError(f"Split point {at} is outside chunk {ordinal} (0–{len(target.text)}).")

    head, tail = target.text[:at].strip(), target.text[at:].strip()
    if not head or not tail:
        raise ValueError(f"Split point {at} would produce an empty chunk.")

    kept = [c for c in chunks if c.ordinal != ordinal]
    kept.extend(
        [
            Chunk(ordinal, head, "manual", target.prefix_tags),
            Chunk(ordinal + 0.5, tail, "manual", target.prefix_tags),  # type: ignore[arg-type]
        ]
    )
    return _renumber(kept)


def _renumber(chunks: list[Chunk]) -> list[Chunk]:
    ordered = sorted(chunks, key=lambda c: c.ordinal)
    return [Chunk(i, c.text, c.source, c.prefix_tags) for i, c in enumerate(ordered, start=1)]
