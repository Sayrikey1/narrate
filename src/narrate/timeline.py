"""The running order — what plays when.

Timings are **derived from measured durations**, never guessed. Every take
records the runtime `ffprobe` read off its file, so a chunk's start is simply
the sum of everything before it. That is why the plan can be trusted against
the audio.

Two things follow from the design decisions in this phase:

* **Effects are overlays, not inserts.** They carry a timeline position but do
  not advance the running time and are not mixed into the master. An editor
  lays them on their own track; baking them in would be an irreversible
  decision the tool has no business making.
* **Anchors are targets, not commands.** A `[@ MM:SS]` marker records where the
  writer wanted a section to begin. TTS duration cannot be dialled to a mark,
  so the timeline reports the signed drift and leaves the response — trim,
  re-roll, or accept — to a person.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from narrate.db.models import Chunk, Cut, Effect, EffectSlot, Project, Script, Take
from narrate.script_parse import format_time

# What a planned (ungenerated) effect is drawn as when its slot names no
# duration. Only ever used for display — nothing is billed from it.
ASSUMED_EFFECT_SECONDS = 3.0

NARRATION = "narration"
EFFECT = "effect"
PLANNED = "planned"


@dataclass(frozen=True)
class TimelineEntry:
    index: int
    kind: str
    start_s: float
    end_s: float
    label: str
    chunk_ordinal: int | None = None
    take_id: int | None = None
    slot_id: int | None = None
    effect_id: int | None = None
    source_path: Path | None = None
    target_s: float | None = None
    generated: bool = True

    # The chapter this entry begins, if it begins one. Carried here so a chapter
    # list is a read of the timeline rather than a second query — the timestamp
    # a chapter needs is `start_s`, which only exists here.
    chapter_title: str | None = None

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)

    @property
    def drift_s(self) -> float | None:
        """Signed difference from an `[@ MM:SS]` target. Positive means late."""
        if self.target_s is None:
            return None
        return round(self.start_s - self.target_s, 3)

    @property
    def start_stamp(self) -> str:
        return format_time(self.start_s)

    @property
    def end_stamp(self) -> str:
        return format_time(self.end_s)


@dataclass(frozen=True)
class Timeline:
    script_id: int
    script_title: str
    project_name: str
    gap_seconds: float
    entries: list[TimelineEntry]

    @property
    def runtime_s(self) -> float:
        """Length of the narration track. Overlaid effects cannot extend it."""
        narration = [e for e in self.entries if e.kind == NARRATION]
        return max((e.end_s for e in narration), default=0.0)

    @property
    def narration(self) -> list[TimelineEntry]:
        return [e for e in self.entries if e.kind == NARRATION]

    @property
    def effects(self) -> list[TimelineEntry]:
        return [e for e in self.entries if e.kind == EFFECT]

    @property
    def planned(self) -> list[TimelineEntry]:
        return [e for e in self.entries if e.kind == PLANNED]

    @property
    def chapter_starts(self) -> list[TimelineEntry]:
        """Narration entries that begin a named chapter, in order.

        Narration only, deliberately. An effect slot at chunk 1 also starts at
        0.0, so iterating every entry would offer two things at `0:00` and a
        chapter list with a repeated timestamp is discarded whole.
        """
        return [e for e in self.narration if e.chapter_title]

    @property
    def drifts(self) -> list[TimelineEntry]:
        return [e for e in self.entries if e.drift_s is not None]

    @property
    def is_complete(self) -> bool:
        """Every chunk has audio — the precondition for a meaningful export."""
        return bool(self.narration) and all(e.generated for e in self.narration)


def takes_by_voice(session: Session, script_id: int, voice_id: str) -> dict[int, Take]:
    """The newest succeeded take per chunk **in one voice**.

    This is what makes two performances of one episode possible without storing
    anything new: a take already records the voice that produced it, so
    generating a script twice in two voices leaves both sets side by side and a
    variant is just a different way of choosing between them.

    Newest wins, so re-rolling a line in that voice supersedes the earlier
    attempt — the same rule the cut follows when it auto-selects.
    """
    rows = session.execute(
        select(Chunk.id, Take)
        .join(Take, Take.chunk_id == Chunk.id)
        .where(
            Chunk.script_id == script_id,
            Take.voice_id == voice_id,
            Take.status == "succeeded",
        )
        .order_by(Chunk.ordinal, Take.ordinal)
    ).all()
    # Later rows overwrite earlier ones, leaving the highest ordinal per chunk.
    return {chunk_id: take for chunk_id, take in rows}


def voices_used(session: Session, script_id: int) -> list[tuple[str, int]]:
    """`(voice_id, chunks covered)` for every voice this script has takes in.

    Ordered by coverage, so the voice that could actually produce a complete
    export comes first. A voice covering fewer chunks than the script has is
    a partial performance, and saying so is the difference between "here are
    your variants" and "here is a list of voice ids".
    """
    rows = session.execute(
        select(Take.voice_id, func.count(func.distinct(Chunk.id)))
        .join(Chunk, Chunk.id == Take.chunk_id)
        .where(Chunk.script_id == script_id, Take.status == "succeeded")
        .group_by(Take.voice_id)
    ).all()
    return sorted(((v, n) for v, n in rows), key=lambda pair: (-pair[1], pair[0]))


def build_timeline(
    session: Session,
    script_id: int,
    gap_seconds: float = 0.0,
    voice_id: str | None = None,
) -> Timeline:
    """Assemble the running order from what has actually been generated.

    `voice_id` builds a **variant**: the running order as that one voice
    performed it, rather than as the cut selects it. Everything downstream —
    durations, positions, the editing plan, the export — follows from this one
    choice, so a variant needs no separate machinery.
    """
    script = session.get(Script, script_id)
    if script is None:
        raise ValueError(f"No script with id {script_id}.")
    project = session.get(Project, script.project_id)

    chunks = list(
        session.scalars(
            select(Chunk).where(Chunk.script_id == script_id).order_by(Chunk.ordinal)
        ).all()
    )
    if voice_id is not None:
        selected = takes_by_voice(session, script_id, voice_id)
    else:
        selected = {
            chunk_id: take
            for chunk_id, take in session.execute(
                select(Cut.chunk_id, Take)
                .join(Take, Take.id == Cut.take_id)
                .where(Cut.script_id == script_id)
            ).all()
        }

    slots_by_chunk: dict[int, list[EffectSlot]] = {}
    for slot in session.scalars(
        select(EffectSlot)
        .where(EffectSlot.script_id == script_id)
        .order_by(EffectSlot.at_chunk_ordinal, EffectSlot.ordinal)
    ).all():
        slots_by_chunk.setdefault(slot.at_chunk_ordinal, []).append(slot)

    entries: list[TimelineEntry] = []
    cursor = 0.0
    index = 1

    for chunk in chunks:
        # Effects sit at the chunk's start — a boundary whose time is exact.
        for slot in slots_by_chunk.get(chunk.ordinal, []):
            if not slot.accepted:
                continue
            effect = session.get(Effect, slot.effect_id) if slot.effect_id else None
            generated = effect is not None and effect.asset_path is not None
            length = (
                (effect.actual_duration_s or effect.duration_s or ASSUMED_EFFECT_SECONDS)
                if effect is not None
                else (slot.duration_s or ASSUMED_EFFECT_SECONDS)
            )
            entries.append(
                TimelineEntry(
                    index=index,
                    kind=EFFECT if generated else PLANNED,
                    start_s=cursor,
                    end_s=cursor + length,
                    label=slot.description,
                    chunk_ordinal=chunk.ordinal,
                    slot_id=slot.id,
                    effect_id=effect.id if effect else None,
                    source_path=(
                        Path(effect.asset_path)
                        if effect is not None and effect.asset_path
                        else None
                    ),
                    generated=generated,
                )
            )
            index += 1

        take = selected.get(chunk.id)
        # `duration_s` is only absent on takes generated before it was
        # recorded, or when ffmpeg was missing at generation time.
        length = float(take.duration_s) if take and take.duration_s else 0.0
        entries.append(
            TimelineEntry(
                index=index,
                kind=NARRATION,
                start_s=cursor,
                end_s=cursor + length,
                label=_excerpt(chunk.text),
                chunk_ordinal=chunk.ordinal,
                take_id=take.id if take else None,
                source_path=Path(take.asset_path) if take and take.asset_path else None,
                target_s=chunk.target_start_s,
                generated=bool(take and take.asset_path and take.duration_s),
                chapter_title=chunk.chapter_title,
            )
        )
        index += 1
        cursor += length
        if length:
            cursor += gap_seconds

    return Timeline(
        script_id=script_id,
        script_title=script.title,
        project_name=project.name if project else "project",
        gap_seconds=gap_seconds,
        entries=entries,
    )


def _excerpt(text: str, width: int = 72) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def export_basename(project_name: str, start_s: float, end_s: float) -> str:
    """`Project_HH-MM-SS-mmm_HH-MM-SS-mmm` — the delivery naming convention.

    Applied at export rather than at generation: a re-roll changes downstream
    timings, and files already delivered must not silently acquire new names.
    """
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in project_name).strip("_")
    return f"{safe or 'project'}_{_stamp(start_s)}_{_stamp(end_s)}"


def _stamp(seconds: float) -> str:
    """`HH-MM-SS-mmm` — filename-safe, and sorts chronologically."""
    return format_time(seconds).replace(":", "-").replace(".", "-")
