"""Assembling the deliverable (PRD F7, extended with the timeline).

An export produces four things:

* a **narration master** (WAV) and its mp3 — the selected takes stitched in
  order with the configured gap between them;
* **per-piece files** named by their timeline position, which is the naming
  convention the editing workflow asks for;
* **`plan.md`** — the editing document;
* **`timeline.json`** — the same information for tooling and the UI.

Every master comes in two versions, per format asked for: the narration alone
(`Episode-1.mp3`) and the finished episode **with its effects mixed in**
(`Episode-1_fx.mp3`). In the mix, a one-off sound plays once at its timeline
position, clearly under the voice; a looped ambience runs as a bed under its
whole chunk, faded in and out. Levels are set from measured loudness, relative
to the narration, because generated sounds arrive at whatever level they
arrive at.

Every effect also keeps its own timeline-named file, so an editor can still
lay them on their own track. `mix_effects` off (`narrate export --no-mix`)
writes the narration masters only.

The master is WAV deliberately: it is the correct input for the two-pass
`loudnorm` that F9 will add, so normalisation later will not mean re-encoding
from a lossy intermediate.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass, field
from glob import escape as glob_escape
from pathlib import Path

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from narrate.audio import (
    AudioFormat,
    FFmpegFailed,
    Overlay,
    concat,
    duration_seconds,
    encode,
    loudness_lufs,
    make_silence,
    mix,
    parse_formats,
)
from narrate.db.models import Chunk, Cut, Export, Take
from narrate.db.session import session_scope
from narrate.plan import PlanContext, gather_context, render_plan, render_timeline_json
from narrate.publish import gather_publish, render_publish_json, render_publish_pack
from narrate.settings import Settings
from narrate.timeline import (
    EFFECT,
    NARRATION,
    Timeline,
    TimelineEntry,
    build_timeline,
    export_basename,
    takes_by_voice,
)


class NothingToExport(RuntimeError):
    pass


@dataclass(frozen=True)
class ExportResult:
    master: Path
    masters: dict[str, Path]
    mp3: Path | None
    plan: Path
    timeline_json: Path
    publish_pack: Path
    out_dir: Path
    duration_s: float
    chunks: int
    effects: int
    planned: int
    gap_seconds: float
    fingerprint: str
    names: dict[int, str] = field(default_factory=dict)
    # The same masters with the effects mixed in, per format: `<title>_fx.*`.
    fx_masters: dict[str, Path] = field(default_factory=dict)
    effects_mixed: int = 0


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


def export_is_current(session: Session, script_id: int) -> bool:
    """Whether the newest export was made from the cut as it is now.

    A regeneration moves the cut; an export made before it plays the old takes.
    """
    latest = session.scalars(
        select(Export).where(Export.script_id == script_id).order_by(Export.id.desc()).limit(1)
    ).first()
    if latest is None:
        return False
    current = cut_fingerprint([take.id for _, take in selected_takes(session, script_id)])
    return latest.cut_fingerprint == current


# Loop ambiences fade rather than start and stop dead.
BED_FADE_IN_S = 1.0
BED_FADE_OUT_S = 2.0
# Without a usable loudness reading (silence, a failed read), fixed levels.
FALLBACK_EFFECT_DB = -6.0
FALLBACK_BED_DB = -18.0
# However far the measurement says to move a sound, not further than this.
GAIN_LIMITS_DB = (-40.0, 12.0)


def _overlays(
    timeline: Timeline, placed: list[TimelineEntry], narration: Path, settings: Settings
) -> list[Overlay]:
    """Each effect's place, length and level in the mix.

    A looped effect runs under its chunk's whole narration; a one-off plays
    once. Levels follow measured loudness: the effect is moved to sit a set
    distance below the narration, whatever level it was generated at.
    """
    voice = loudness_lufs(narration)
    measured: dict[Path, float | None] = {}
    ends = {e.chunk_ordinal: e.end_s for e in timeline.narration}
    out: list[Overlay] = []
    playable: dict[Path, bool] = {}
    for entry in placed:
        assert entry.source_path is not None
        source = entry.source_path
        if source not in playable:
            # A file with nothing to decode would fail the mix — or, looped,
            # hang it — so it is left out; its own piece still ships.
            try:
                playable[source] = duration_seconds(source) > 0
            except FFmpegFailed:
                playable[source] = False
        if not playable[source]:
            continue
        if source not in measured:
            measured[source] = loudness_lufs(source)
        own = measured[source]
        until = ends.get(entry.chunk_ordinal, entry.end_s)
        # Under its whole chunk, however long the sound itself is — a longer
        # ambience is cut and faded at the chunk's end, not left running on.
        bed = entry.loop and until > entry.start_s
        target = settings.bed_level_lu if entry.loop else settings.effect_level_lu
        if voice is not None and own is not None:
            low, high = GAIN_LIMITS_DB
            gain = min(high, max(low, voice + target - own))
        else:
            gain = FALLBACK_BED_DB if entry.loop else FALLBACK_EFFECT_DB
        out.append(
            Overlay(
                path=source,
                start_s=entry.start_s,
                gain_db=gain,
                length_s=(until - entry.start_s) if bed else None,
                fade_in_s=BED_FADE_IN_S if bed else 0.0,
                fade_out_s=BED_FADE_OUT_S if bed else 0.0,
            )
        )
    return out


def _backfill_durations(session: Session, script_id: int, voice_id: str | None) -> None:
    """Measure any take being exported whose length was never recorded.

    Positions on the timeline are sums of take lengths; a take generated while
    ffmpeg was missing has none, so everything after it — pieces, chapters and
    the effects in the mix — would be placed early.
    """
    takes = (
        list(takes_by_voice(session, script_id, voice_id).values())
        if voice_id is not None
        else [take for _, take in selected_takes(session, script_id)]
    )
    for take in takes:
        if take.duration_s is None and take.asset_path and Path(take.asset_path).exists():
            try:
                take.duration_s = duration_seconds(Path(take.asset_path))
            except FFmpegFailed:
                continue
    session.flush()


def _remove_stale_pieces(target: Path, project_name: str, wanted: set[str]) -> None:
    """Delete this project's timeline-named pieces that this export does not write.

    Only files named exactly by the piece convention, for this project, in this
    folder — nothing else is touched.
    """
    prefix = export_basename(project_name, 0.0, 0.0).rsplit("_", 2)[0]
    pattern = re.compile(
        rf"^{re.escape(prefix)}_\d{{2}}-\d{{2}}-\d{{2}}-\d{{3}}_\d{{2}}-\d{{2}}-\d{{2}}-\d{{3}}$"
    )
    for path in target.iterdir():
        if path.is_file() and pattern.match(path.stem) and path.stem not in wanted:
            path.unlink()


def _variant_slug(label: str) -> str:
    """A filename-safe tag for a variant. Short, because it is a suffix."""
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in label).strip("-")
    return safe[:24].lower() or "variant"


def _check_ready(
    session: Session, timeline: Timeline, script_id: int, voice_id: str | None = None
) -> None:
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
    if voice_id is None:
        selected = {
            c.chunk_id for c in session.scalars(select(Cut).where(Cut.script_id == script_id)).all()
        }
        unselected = [c.ordinal for c in chunks if c.id not in selected]
        if unselected:
            raise NothingToExport(
                f"Chunks {unselected} have no selected take. Generate them, or pick a take, "
                "before exporting — a gap in the middle of an episode is worse than no export."
            )
    else:
        # A variant is only exportable where that voice covers every chunk.
        # A partial variant would ship an episode with a silent hole in it, and
        # the useful thing to say is which lines are missing in *that voice*.
        covered = set(takes_by_voice(session, script_id, voice_id))
        missing = [c.ordinal for c in chunks if c.id not in covered]
        if missing:
            raise NothingToExport(
                f"Voice {voice_id} has no take for chunks {missing}. Generate them in "
                f"that voice first:  narrate generate {script_id} --only "
                f"{','.join(str(o) for o in missing)} --go"
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
    voice_id: str | None = None,
    variant_label: str | None = None,
    mix_effects: bool | None = None,
) -> ExportResult:
    """Stitch the cut and write it out in each requested delivery format.

    `formats` are the masters. `piece_format` is what the timeline-named files
    are encoded as, since those are the ones dropped onto an editing track.

    `voice_id` exports a **variant**: the episode as that one voice performed
    it, taken from the takes already on record rather than from the cut. Two
    voices of the same script therefore produce two masters from one generation
    history — which is the point of keeping every take.

    A variant writes to its own directory and carries the voice in its filename,
    because the alternative is one export quietly overwriting the other.
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

    slug = _variant_slug(variant_label or voice_id) if voice_id else ""

    with session_scope(engine) as session:
        _backfill_durations(session, script_id, voice_id)
        timeline = build_timeline(session, script_id, gap_seconds=gap, voice_id=voice_id)
        _check_ready(session, timeline, script_id, voice_id=voice_id)
        context = gather_context(session, script_id)
        take_ids = [e.take_id for e in timeline.narration if e.take_id]
        project_name = timeline.project_name
        safe_title = _safe_title(timeline.script_title, script_id)
        if slug:
            safe_title = f"{safe_title}__{slug}"

    default_dir = f"script-{script_id}" + (f"-{slug}" if slug else "")
    target = out_dir or (settings.assets_dir / "exports" / default_dir)
    target.mkdir(parents=True, exist_ok=True)

    # Timeline-named deliverables. Applied here rather than at generation, so a
    # re-roll never silently renames a file already handed over.
    piece_fmt = _piece_format(piece)
    names: dict[int, str] = {}
    wanted = {
        export_basename(project_name, e.start_s, e.end_s)
        for e in timeline.entries
        if e.kind in (NARRATION, EFFECT) and e.source_path is not None
    }
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

    # The finished episode: the same master with the effects laid in.
    mixing = settings.mix_effects if mix_effects is None else mix_effects
    placed = [e for e in timeline.effects if e.source_path is not None]
    fx_master: Path | None = None
    mixed = 0
    overlays = _overlays(timeline, placed, master, settings) if mixing and placed else []
    if overlays:
        # A WAV only when WAV was asked for: otherwise the mix is scratch, and
        # must not overwrite a `_fx.wav` of the user's in a folder they chose.
        wants_wav = any(f.key == "wav" for f in requested)
        try:
            fx_master = mix(
                master,
                overlays,
                target / (f"{safe_title}_fx.wav" if wants_wav else "_mix.wav"),
                sample_rate=settings.sample_rate,
                channels=1,
            )
            mixed = len(overlays)
        except FFmpegFailed:
            # The narration masters still ship; the report says nothing was mixed.
            fx_master = None

    masters: dict[str, Path] = {}
    fx_masters: dict[str, Path] = {}
    for fmt in requested:
        if fmt.key == "wav":
            masters["wav"] = master
            if fx_master is not None:
                fx_masters["wav"] = fx_master
            continue
        masters[fmt.key] = encode(
            master, target / f"{safe_title}{fmt.suffix}", fmt, settings.mp3_bitrate
        )
        if fx_master is not None:
            fx_masters[fmt.key] = encode(
                fx_master, target / f"{safe_title}_fx{fmt.suffix}", fmt, settings.mp3_bitrate
            )
    if fx_master is not None and "wav" not in fx_masters:
        # The PCM mix was only the source for the formats asked for.
        fx_master.unlink(missing_ok=True)
    if out_dir is None:
        # An `_fx` master this export did not write carries an old cut — from a
        # format no longer asked for, or from before mixing was turned off.
        keep = set(fx_masters.values())
        for stale in target.glob(f"{glob_escape(safe_title)}_fx.*"):
            if stale not in keep:
                stale.unlink()
    mp3_path = masters.get("mp3")

    plan_path = target / "plan.md"
    json_path = target / "timeline.json"
    pack_path = target / "publish.md"
    pack_json_path = target / "publish.json"
    with session_scope(engine) as session:
        fx_names = [path.name for path in fx_masters.values()]
        plan_path.write_text(
            render_plan(timeline, session, names, context, fx_names=fx_names), encoding="utf-8"
        )
        # Written here rather than on demand because the chapter timestamps are
        # only correct once the audio they describe exists — which is now.
        pack = gather_publish(session, script_id)
    json_path.write_text(render_timeline_json(timeline, names, context), encoding="utf-8")
    pack_path.write_text(render_publish_pack(timeline, pack), encoding="utf-8")
    pack_json_path.write_text(render_publish_json(timeline, pack), encoding="utf-8")

    if out_dir is None:
        # Only now the new export is complete: a regeneration changes timings,
        # so the pieces an earlier export wrote carry times that are wrong, and
        # two files for one chunk is how an editor lays the wrong one.
        _remove_stale_pieces(target, project_name, wanted)

    fingerprint = cut_fingerprint([t for t in take_ids])

    with session_scope(engine) as session:
        both = [*masters.items(), *((f"{key}_fx", path) for key, path in fx_masters.items())]
        for fmt_key, deliverable in both:
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
        publish_pack=pack_path,
        out_dir=target,
        duration_s=result.duration_s,
        chunks=len(sources),
        effects=len(timeline.effects),
        planned=len(timeline.planned),
        gap_seconds=gap,
        fingerprint=fingerprint,
        fx_masters=fx_masters,
        effects_mixed=mixed,
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


def write_publish_pack_only(
    engine: Engine, script_id: int, settings: Settings, out: Path | None = None
) -> Path:
    """Write `publish.md` without exporting audio.

    Mirrors `write_plan_only`, including the caveat it does not have: the chapter
    timestamps come from measured take durations, so a pack written before
    everything is generated says so in the document rather than being refused.
    Somebody planning an upload wants to see the shape of it early.
    """
    with session_scope(engine) as session:
        timeline = build_timeline(session, script_id, gap_seconds=settings.gap_seconds)
        context = gather_publish(session, script_id)
        body = render_publish_pack(timeline, context)
        data = render_publish_json(timeline, context)

    path = out or (settings.assets_dir / "exports" / f"script-{script_id}" / "publish.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.with_suffix(".json").write_text(data, encoding="utf-8")
    return path


def _piece_format(key: str) -> AudioFormat | None:
    """The per-piece format, or `None` to copy the take files as they are."""
    if key in ("", "source", "same"):
        return None
    return parse_formats([key])[0]


def _safe_title(title: str, script_id: int) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_ " else "_" for c in title).strip()
    return cleaned.replace(" ", "-")[:60] or f"script-{script_id}"
