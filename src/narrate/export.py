"""Assembling the deliverable (PRD F7, extended with the timeline).

An export produces four things:

* a **narration master** (WAV) and its mp3 — the selected takes stitched in
  order with the configured gap between them;
* **per-piece files** named by their timeline position, which is the naming
  convention the editing workflow asks for;
* **`plan.md`** — the editing document;
* **`timeline.json`** — the same information for tooling and the UI.

Effects are copied in as separate files, not mixed into the master. They are
overlays with timeline positions; an editor lays them on their own track.
Baking them in would be irreversible, and the point of the plan is to leave
that decision to a person.

The master is WAV deliberately: it is the correct input for the two-pass
`loudnorm` that F9 will add, so normalisation later will not mean re-encoding
from a lossy intermediate.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from narrate.audio import AudioFormat, concat, encode, make_silence, parse_formats
from narrate.db.models import Chunk, Cut, Export, Take
from narrate.db.session import session_scope
from narrate.plan import PlanContext, gather_context, render_plan, render_timeline_json
from narrate.settings import Settings
from narrate.timeline import EFFECT, NARRATION, Timeline, build_timeline, export_basename


class NothingToExport(RuntimeError):
    pass


@dataclass(frozen=True)
class ExportResult:
    master: Path
    masters: dict[str, Path]
    mp3: Path | None
    plan: Path
    timeline_json: Path
    out_dir: Path
    duration_s: float
    chunks: int
    effects: int
    planned: int
    gap_seconds: float
    fingerprint: str
    names: dict[int, str] = field(default_factory=dict)


def cut_fingerprint(take_ids: list[int]) -> str:
    """Identifies a specific cut, so a stale export can be spotted."""
    return hashlib.sha256(",".join(str(t) for t in take_ids).encode()).hexdigest()[:16]


def selected_takes(session: Session, script_id: int) -> list[tuple[int, Take]]:
    """`(ordinal, take)` for the current cut, in playback order."""
    rows = session.execute(
        select(Chunk.ordinal, Take)
        .join(Cut, Cut.chunk_id == Chunk.id)
        .join(Take, Take.id == Cut.take_id)
        .where(Chunk.script_id == script_id)
        .order_by(Chunk.ordinal)
    ).all()
    return [(ordinal, take) for ordinal, take in rows]


def _check_ready(session: Session, timeline: Timeline, script_id: int) -> None:
    """Refuse an export that would ship a hole in the middle of an episode."""
    if not timeline.narration:
        raise NothingToExport(
            "This script has no chunks. Run `narrate chunk review` to see its state."
        )

    chunks = list(
        session.scalars(
            select(Chunk).where(Chunk.script_id == script_id).order_by(Chunk.ordinal)
        ).all()
    )
    selected = {
        c.chunk_id for c in session.scalars(select(Cut).where(Cut.script_id == script_id)).all()
    }
    unselected = [c.ordinal for c in chunks if c.id not in selected]
    if unselected:
        raise NothingToExport(
            f"Chunks {unselected} have no selected take. Generate them, or pick a take, "
            "before exporting — a gap in the middle of an episode is worse than no export."
        )

    for entry in timeline.narration:
        path = entry.source_path
        if path is not None and path.exists():
            continue

        # A sibling take may still be on disk, in which case moving the cut is
        # instant and free while regenerating would spend money for nothing.
        chunk = next(c for c in chunks if c.ordinal == entry.chunk_ordinal)
        alternatives = [
            t.ordinal
            for t in session.scalars(
                select(Take)
                .where(Take.chunk_id == chunk.id, Take.status == "succeeded")
                .order_by(Take.ordinal)
            ).all()
            if t.asset_path and Path(t.asset_path).exists()
        ]
        if alternatives:
            raise NothingToExport(
                f"Chunk {entry.chunk_ordinal}: the selected take has no audio on disk, "
                f"but take(s) {', '.join(str(a) for a in alternatives)} do.\n"
                f"Switch to one of them:  narrate cut set {script_id} "
                f"{entry.chunk_ordinal} --take {alternatives[-1]}"
            )
        raise NothingToExport(
            f"Chunk {entry.chunk_ordinal}: no take has audio on disk any more.\n"
            f"Regenerate it:  narrate generate {script_id} "
            f"--only {entry.chunk_ordinal} --go --force"
        )


def export_script(
    engine: Engine,
    script_id: int,
    settings: Settings,
    *,
    gap_seconds: float | None = None,
    want_mp3: bool = True,
    out_dir: Path | None = None,
    formats: list[str] | str | None = None,
    piece_format: str | None = None,
) -> ExportResult:
    """Stitch the cut and write it out in each requested delivery format.

    `formats` are the masters. `piece_format` is what the timeline-named files
    are encoded as, since those are the ones dropped onto an editing track.
    """
    gap = settings.gap_seconds if gap_seconds is None else gap_seconds

    requested = parse_formats(formats if formats is not None else settings.export_formats)
    if not want_mp3:
        # `--no-mp3` predates the format list; honour it as a filter so the old
        # flag keeps meaning what it said.
        requested = [f for f in requested if f.key != "mp3"] or parse_formats(["wav"])
    if not any(f.lossless for f in requested):
        # The stitch happens in PCM anyway, and every lossy encode is made from
        # that master rather than from another lossy file.
        requested = [*parse_formats(["wav"]), *requested]
    piece = (piece_format or settings.piece_format).lower().lstrip(".")

    with session_scope(engine) as session:
        timeline = build_timeline(session, script_id, gap_seconds=gap)
        _check_ready(session, timeline, script_id)
        context = gather_context(session, script_id)
        take_ids = [e.take_id for e in timeline.narration if e.take_id]
        project_name = timeline.project_name
        safe_title = _safe_title(timeline.script_title, script_id)

    target = out_dir or (settings.assets_dir / "exports" / f"script-{script_id}")
    target.mkdir(parents=True, exist_ok=True)

    # Timeline-named deliverables. Applied here rather than at generation, so a
    # re-roll never silently renames a file already handed over.
    piece_fmt = _piece_format(piece)
    names: dict[int, str] = {}
    for entry in timeline.entries:
        if entry.kind not in (NARRATION, EFFECT) or entry.source_path is None:
            continue
        source_suffix = entry.source_path.suffix.lower()
        suffix = piece_fmt.suffix if piece_fmt else (source_suffix or ".mp3")
        name = export_basename(project_name, entry.start_s, entry.end_s) + suffix
        names[entry.index] = name
        # A reused effect is written once per placement, so every timeline
        # position has a file the editor can drop straight onto the track.
        if piece_fmt is None or source_suffix == piece_fmt.suffix:
            shutil.copy2(entry.source_path, target / name)
        else:
            # A different delivery format means a transcode per piece — N extra
            # ffmpeg passes on a long episode, which is the price of handing an
            # editor files their NLE opens without converting first.
            encode(entry.source_path, target / name, piece_fmt, settings.mp3_bitrate)

    sources = [e.source_path for e in timeline.narration if e.source_path]
    inputs: list[Path] = []
    silence: Path | None = None
    if gap > 0 and len(sources) > 1:
        silence = make_silence(target / "_gap.wav", gap, settings.sample_rate)
        for i, source in enumerate(sources):
            if i:
                inputs.append(silence)
            inputs.append(source)
    else:
        inputs = list(sources)

    # The stitch is always PCM: one decode, one lossless master, and every
    # delivery format encoded from it rather than from another lossy file.
    master = target / f"{safe_title}.wav"
    try:
        result = concat(inputs, master, sample_rate=settings.sample_rate, channels=1)
    finally:
        if silence is not None:
            silence.unlink(missing_ok=True)

    masters: dict[str, Path] = {}
    for fmt in requested:
        if fmt.key == "wav":
            masters["wav"] = master
            continue
        masters[fmt.key] = encode(
            master, target / f"{safe_title}{fmt.suffix}", fmt, settings.mp3_bitrate
        )
    mp3_path = masters.get("mp3")

    plan_path = target / "plan.md"
    json_path = target / "timeline.json"
    with session_scope(engine) as session:
        plan_path.write_text(render_plan(timeline, session, names, context), encoding="utf-8")
    json_path.write_text(render_timeline_json(timeline, names, context), encoding="utf-8")

    fingerprint = cut_fingerprint([t for t in take_ids])

    with session_scope(engine) as session:
        for fmt_key, deliverable in masters.items():
            session.add(
                Export(
                    script_id=script_id,
                    path=str(deliverable),
                    fmt=fmt_key,
                    duration_s=result.duration_s,
                    cut_fingerprint=fingerprint,
                    gap_seconds=gap,
                )
            )

    return ExportResult(
        master=master,
        masters=masters,
        mp3=mp3_path,
        plan=plan_path,
        timeline_json=json_path,
        out_dir=target,
        duration_s=result.duration_s,
        chunks=len(sources),
        effects=len(timeline.effects),
        planned=len(timeline.planned),
        gap_seconds=gap,
        fingerprint=fingerprint,
        names=names,
    )


def write_plan_only(
    engine: Engine, script_id: int, settings: Settings, out: Path | None = None
) -> Path:
    """Write `plan.md` without exporting audio.

    Useful before anything has been generated: the plan is then a shot list —
    what to record, in what order, with the effect slots already marked.
    """
    with session_scope(engine) as session:
        timeline = build_timeline(session, script_id, gap_seconds=settings.gap_seconds)
        context = gather_context(session, script_id) if timeline.narration else PlanContext()
        body = render_plan(timeline, session, None, context)

    path = out or (settings.assets_dir / "exports" / f"script-{script_id}" / "plan.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _piece_format(key: str) -> AudioFormat | None:
    """The per-piece format, or `None` to copy the take files as they are."""
    if key in ("", "source", "same"):
        return None
    return parse_formats([key])[0]


def _safe_title(title: str, script_id: int) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_ " else "_" for c in title).strip()
    return cleaned.replace(" ", "-")[:60] or f"script-{script_id}"
