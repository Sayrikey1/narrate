"""Deleting a project or an episode, and replacing an episode's script.

**A delete archives.** The row stays, with `archived_at` set: it disappears from
every list and refuses any new spend, but nothing it cost goes anywhere. The
reasons are the migration's — an append-only ledger with no foreign keys, ids
SQLite would hand to the next row, waste that is "spend not in a cut" — and
they mean a delete that removed rows would quietly move money between
episodes. Spend on a deleted episode stays attributed to it, under its name, in
every cost report. The audio files are kept too unless their removal is asked
for, and then only files no surviving row still points at.

**A replacement keeps what did not change.** The new script is planned in full
before anything is touched. Each new chunk whose words match an old one keeps
that old row — its takes, its cut, its verification, its cost history — so an
edited script pays only for what was edited. Old chunks that no longer appear
are not deleted: those with takes move to an archived copy of the episode
("… — replaced <date>"), keeping every paid take and its ledger link. The price
of what is new is worked out by applying the replacement and rolling it back,
so the quote is the runner's own figure, not an estimate of one.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from narrate import ledger
from narrate.archive import Archived, ensure_live
from narrate.db.models import (
    CastMember,
    Chunk,
    Cut,
    Effect,
    EffectSlot,
    Export,
    LedgerEntry,
    Project,
    Run,
    Script,
    ScriptBeat,
    Take,
    utcnow,
)
from narrate.db.session import make_session_factory, session_scope
from narrate.effects import _nearest_chunk, _next_slot_ordinal
from narrate.ingest import _attach_anchors, _attach_chapters, locate_chunks, plan_chunks
from narrate.registry import Registry, UnknownModel
from narrate.runner import build_jobs, existing_take
from narrate.script_parse import parse_script, spoken_text
from narrate.settings import Settings
from narrate.verify.compare import normalise


class EpisodeRefused(RuntimeError):
    """This cannot be done right now, and the message says why."""


class EpisodeNotFound(EpisodeRefused):
    """No such project or episode — distinct, so an API can answer 404."""


class StillRunning(EpisodeRefused):
    """A generation is recorded as running. `force` goes past it — for a run
    that crashed and so will never say it finished."""


# -- deleting -----------------------------------------------------------------


@dataclass
class DeletePlan:
    """What a delete would do — and, once `done`, what it did."""

    kind: str  # "episode" | "project"
    target_id: int
    name: str
    episodes: list[int]
    takes: int
    # What these cost, which stays on the ledger and in every report.
    spend_micros: int
    # Audio and exports that would be removed if asked, and their size.
    files: list[Path]
    file_bytes: int
    # Episodes with a generation in progress; a delete waits for them.
    running: list[int]
    done: bool = False
    files_deleted: int = 0


def plan_delete_episode(session: Session, script_id: int, settings: Settings) -> DeletePlan:
    script = _script(session, script_id)
    if script.archived_at is not None:
        raise EpisodeRefused(f"Episode {script_id} is already deleted.")
    return _plan(session, "episode", script.id, script.title, [script.id], settings)


def plan_delete_project(session: Session, project_id: int, settings: Settings) -> DeletePlan:
    project = session.get(Project, project_id)
    if project is None:
        raise EpisodeNotFound(f"No project {project_id}.")
    if project.archived_at is not None:
        raise EpisodeRefused(f"Project {project.name!r} is already deleted.")
    ids = list(
        session.scalars(
            select(Script.id).where(Script.project_id == project_id, Script.archived_at.is_(None))
        ).all()
    )
    return _plan(session, "project", project.id, project.name, ids, settings)


def delete_episode(
    engine: Engine,
    script_id: int,
    settings: Settings,
    *,
    confirm: bool = False,
    delete_files: bool = False,
    force: bool = False,
) -> DeletePlan:
    """Archive an episode; with `confirm` off, only say what that would do."""
    with session_scope(engine) as session:
        plan = plan_delete_episode(session, script_id, settings)
        if not confirm:
            return plan
        _refuse_if_running(plan, force)
        _abandon(session, plan.running)
        _script(session, script_id).archived_at = utcnow()
        plan.done = True
    if delete_files:
        plan.files_deleted = _remove(plan.files, settings)
    return plan


def delete_project(
    engine: Engine,
    project_id: int,
    settings: Settings,
    *,
    confirm: bool = False,
    delete_files: bool = False,
    force: bool = False,
) -> DeletePlan:
    """Archive a project and its episodes; with `confirm` off, only say what.

    The project's name is freed — renamed "<name> (deleted #<id>)" — so a new
    project can take it; restoring puts it back if it is still free.
    """
    with session_scope(engine) as session:
        plan = plan_delete_project(session, project_id, settings)
        if not confirm:
            return plan
        _refuse_if_running(plan, force)
        _abandon(session, plan.running)
        project = session.get(Project, project_id)
        assert project is not None
        now = utcnow()
        # One timestamp for the project and the episodes it takes with it, so
        # a restore brings back exactly those — not ones deleted on their own.
        for script in session.scalars(
            select(Script).where(Script.id.in_(plan.episodes), Script.archived_at.is_(None))
        ).all():
            script.archived_at = now
        project.archived_at = now
        project.name = _deleted_name(project.name, project.id)
        plan.done = True
    if delete_files:
        plan.files_deleted = _remove(plan.files, settings)
    return plan


def restore_episode(engine: Engine, script_id: int) -> Script:
    with session_scope(engine) as session:
        script = _script(session, script_id)
        if script.archived_at is None:
            raise EpisodeRefused(f"Episode {script_id} is not deleted.")
        project = session.get(Project, script.project_id)
        if project is not None and project.archived_at is not None:
            raise EpisodeRefused(
                f"Its project, {project.name!r}, is deleted. Restore the project with "
                f"`narrate project restore {project.id}`."
            )
        script.archived_at = None
        session.flush()
        session.expunge(script)
        return script


def restore_project(engine: Engine, project_id: int) -> Project:
    with session_scope(engine) as session:
        project = session.get(Project, project_id)
        if project is None:
            raise EpisodeNotFound(f"No project {project_id}.")
        if project.archived_at is None:
            raise EpisodeRefused(f"Project {project.name!r} is not deleted.")
        when = project.archived_at
        for script in session.scalars(
            select(Script).where(Script.project_id == project_id, Script.archived_at == when)
        ).all():
            script.archived_at = None
        project.archived_at = None
        original = _original_name(project.name, project.id)
        taken = session.scalar(
            select(Project.id).where(Project.name == original, Project.id != project.id)
        )
        if original != project.name and taken is None:
            project.name = original
        session.flush()
        session.expunge(project)
        return project


def _plan(
    session: Session,
    kind: str,
    target_id: int,
    name: str,
    episodes: list[int],
    settings: Settings,
) -> DeletePlan:
    # The files a project delete may remove belong to all its episodes — the
    # ones deleted earlier and the archived copies a replace made too — or
    # their audio could never be reclaimed.
    owned = (
        list(session.scalars(select(Script.id).where(Script.project_id == target_id)).all())
        if kind == "project"
        else episodes
    )
    takes = list(
        session.scalars(
            select(Take).join(Chunk, Chunk.id == Take.chunk_id).where(Chunk.script_id.in_(owned))
        ).all()
    )
    if kind == "project":
        spend = session.scalar(
            select(func.coalesce(func.sum(LedgerEntry.cost_micros), 0)).where(
                LedgerEntry.project_id == target_id
            )
        )
    else:
        spend = session.scalar(
            select(func.coalesce(func.sum(LedgerEntry.cost_micros), 0)).where(
                LedgerEntry.script_id.in_(episodes)
            )
        )
    running = list(
        session.scalars(
            select(Run.script_id)
            .where(Run.script_id.in_(episodes), Run.status == "running")
            .distinct()
        ).all()
    )
    files = _files_to_remove(session, kind, target_id, owned, takes, settings)
    return DeletePlan(
        kind=kind,
        target_id=target_id,
        name=name,
        episodes=episodes,
        takes=len(takes),
        spend_micros=int(spend or 0),
        files=files,
        file_bytes=sum(p.stat().st_size for p in files if p.is_file()),
        running=running,
    )


def _files_to_remove(
    session: Session,
    kind: str,
    target_id: int,
    episodes: list[int],
    takes: list[Take],
    settings: Settings,
) -> list[Path]:
    """Files only these rows point at, inside the media folder.

    A path another surviving row still uses is left alone — two projects whose
    names sanitise alike share an effects folder — and so is anything outside
    `assets_dir`, which this tool does not own.
    """
    root = settings.assets_dir.resolve()
    ours: set[Path] = {Path(t.asset_path).resolve() for t in takes if t.asset_path}
    for export in session.scalars(select(Export).where(Export.script_id.in_(episodes))).all():
        ours.add(Path(export.path).resolve())
    exports = settings.assets_dir / "exports"
    for script_id in episodes:
        for folder in (exports / f"script-{script_id}", *exports.glob(f"script-{script_id}-*")):
            if folder.is_dir():
                ours.update(p.resolve() for p in folder.rglob("*") if p.is_file())
    if kind == "project":
        ours.update(
            Path(e.asset_path).resolve()
            for e in session.scalars(select(Effect).where(Effect.project_id == target_id)).all()
            if e.asset_path
        )

    others: set[Path] = set()
    for path in session.scalars(
        select(Take.asset_path)
        .join(Chunk, Chunk.id == Take.chunk_id)
        .where(Chunk.script_id.not_in(episodes), Take.asset_path.is_not(None))
    ).all():
        if path:
            others.add(Path(path).resolve())
    for path in session.scalars(
        select(Effect.asset_path).where(
            Effect.project_id != target_id if kind == "project" else Effect.id.is_not(None),
            Effect.asset_path.is_not(None),
        )
    ).all():
        if path:
            others.add(Path(path).resolve())
    for path in session.scalars(select(Export.path).where(Export.script_id.not_in(episodes))).all():
        others.add(Path(path).resolve())

    return sorted(p for p in ours if p.is_file() and p not in others and _inside(p, root))


def _remove(files: Iterable[Path], settings: Settings) -> int:
    """Delete the listed files, then any folder under the media root left empty."""
    root = settings.assets_dir.resolve()
    removed = 0
    parents: set[Path] = set()
    for path in files:
        if path.is_file() and _inside(path, root):
            path.unlink()
            removed += 1
            parents.add(path.parent)
    for folder in sorted(parents, key=lambda p: len(p.parts), reverse=True):
        current = folder
        while current != root and _inside(current, root) and current.is_dir():
            if any(current.iterdir()):
                break
            shutil.rmtree(current)
            current = current.parent
    return removed


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False
    return True


def _refuse_if_running(plan: DeletePlan, force: bool) -> None:
    if plan.running and not force:
        raise StillRunning(
            f"A generation is still running for episode(s) "
            f"{', '.join(map(str, plan.running))}. Wait for it to finish — or, if it "
            "crashed and will never finish, force it."
        )


def _abandon(session: Session, script_ids: Iterable[int]) -> None:
    """Close the runs a forced delete or replace went past.

    Forcing says those runs crashed; left open, each would go on refusing every
    later delete and replace of the episode, and so need forcing every time.
    """
    ids = list(script_ids)
    if not ids:
        return
    for run in session.scalars(
        select(Run).where(Run.script_id.in_(ids), Run.status == "running")
    ).all():
        run.status = "failed"
        run.finished_at = utcnow()


def _deleted_name(name: str, project_id: int) -> str:
    # Never truncated: a restore strips the suffix, and has to get back the
    # whole name. SQLite does not enforce the column's length.
    return f"{name} (deleted #{project_id})"


def _original_name(name: str, project_id: int) -> str:
    suffix = f" (deleted #{project_id})"
    return name[: -len(suffix)] if name.endswith(suffix) else name


def _script(session: Session, script_id: int) -> Script:
    script = session.get(Script, script_id)
    if script is None:
        raise EpisodeNotFound(f"No episode {script_id}.")
    return script


# -- replacing ------------------------------------------------------------------


@dataclass
class ReplacePlan:
    """What replacing an episode's script would do — and, once `done`, did."""

    script_id: int
    title: str
    unchanged: bool = False
    # Chunks whose takes carry over, and how many takes that is.
    kept: int = 0
    kept_takes: int = 0
    # Of those, chunks whose request changed around them (a neighbour on a
    # model that carries context) and so are generated again.
    kept_regenerated: int = 0
    new: int = 0
    # Old chunks no longer in the script; those with takes move to `retired_to`.
    retired: int = 0
    retired_takes: int = 0
    retired_to: int | None = None
    beats_detached: int = 0
    # Hand-placed or suggested effect slots whose passage the new script dropped.
    slots_dropped: int = 0
    # The price of generating what the replacement leaves undone.
    chunks_to_generate: int = 0
    chars: int = 0
    effects_to_generate: int = 0
    quote_micros: int = 0
    warnings: list[str] = field(default_factory=list)
    done: bool = False


def replace_episode(
    engine: Engine,
    script_id: int,
    text: str,
    registry: Registry,
    settings: Settings,
    *,
    filename: str | None = None,
    title: str | None = None,
    confirm: bool = False,
    force: bool = False,
) -> ReplacePlan:
    """Replace an episode's script, keeping every take whose words did not change.

    Without `confirm` the whole replacement is applied and rolled back — so the
    quote is the one the next generation will actually be held to.
    """
    session = make_session_factory(engine)()
    try:
        plan = _replace(session, script_id, text, registry, settings, filename, title, force)
        if confirm and not plan.unchanged:
            session.commit()
            plan.done = True
        else:
            session.rollback()
        return plan
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def _norm(text: str) -> str:
    """Text as compared for matching: line endings, a BOM and trailing spaces
    are an editor's doing, not the writer's, and must not cost a take."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def _replace(
    session: Session,
    script_id: int,
    text: str,
    registry: Registry,
    settings: Settings,
    filename: str | None,
    title: str | None,
    force: bool,
) -> ReplacePlan:
    script = _script(session, script_id)
    try:
        ensure_live(session, script)
    except Archived as exc:
        raise EpisodeRefused(str(exc)) from exc
    project = session.get(Project, script.project_id)
    if project is None:
        raise EpisodeNotFound(f"Episode {script_id} has no project.")
    running = session.scalar(
        select(Run.id).where(Run.script_id == script_id, Run.status == "running").limit(1)
    )
    if running is not None and not force:
        raise StillRunning(
            "A generation is still running for this episode. Wait for it to finish — or, "
            "if it crashed and will never finish, force it."
        )
    if running is not None:
        _abandon(session, [script_id])

    source = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    digest = hashlib.sha256(source.encode()).hexdigest()
    plan = ReplacePlan(script_id=script_id, title=title or script.title)
    if digest == script.source_sha256 and title in (None, script.title):
        plan.unchanged = True
        return plan

    try:
        spec = registry.get(script.model_id or project.model_id)
    except UnknownModel as exc:
        raise EpisodeRefused(str(exc.args[0])) from exc
    old = list(
        session.scalars(
            select(Chunk).where(Chunk.script_id == script_id).order_by(Chunk.ordinal)
        ).all()
    )
    cast = {
        m.name: m.voice_id
        for m in session.scalars(
            select(CastMember).where(CastMember.project_id == project.id)
        ).all()
    }
    if digest == script.source_sha256:
        # The same words under a new title: nothing to re-plan, so nothing is
        # re-chunked — not even where a chunk's edges would plan differently now.
        script.title = title or script.title
        if filename:
            script.source_path = filename
        plan.kept = len(old)
        plan.kept_takes = sum(len(row.takes) for row in old)
        session.flush()
        _quote(session, plan, registry, settings)
        return plan

    dialogue = any(c.turns_json for c in old)
    planned_all = plan_chunks(
        source, spec, project.prefix_tags, cast, dialogue, keep=[row.text for row in old]
    )
    parsed, planned = planned_all.parsed, planned_all.planned
    plan.warnings += planned_all.warnings

    # Who the old script gave each chunk to, read from the old script itself:
    # a row's stored voice cannot say whether the cast put it there or someone
    # set it by hand, and only the first must follow the script's speakers.
    before = parse_script(script.source_text, cast)
    old_voices = {
        row.id: before.voice_at(span[0])
        for row, span in zip(old, locate_chunks([r.text for r in old], before.text), strict=True)
        if span is not None
    }

    pairs, successor = _pair(old, planned, spec.audio_tags, old_voices)
    kept_ids = {row.id for row in pairs.values()}
    old_by_ordinal = {row.ordinal: row for row in old}

    # Phase one: kept rows step aside to negative ordinals, so the new order
    # can be written without tripping UNIQUE(script_id, ordinal) midway.
    for index, row in enumerate(pairs.values(), start=1):
        row.ordinal = -index
    retired = [row for row in old if row.id not in kept_ids]
    sibling: Script | None = None
    for row in retired:
        if row.takes:
            if sibling is None:
                sibling = _retired_copy(session, script)
            row.script_id = sibling.id
            for cut in session.scalars(select(Cut).where(Cut.chunk_id == row.id)).all():
                cut.script_id = sibling.id
            plan.retired_takes += len(row.takes)
        else:
            session.delete(row)
        plan.retired += 1
    plan.retired_to = sibling.id if sibling else None
    session.flush()

    # Phase two: every planned chunk takes its place — an old row where the
    # words match, a new row where they do not.
    offsets: dict[int, int] = {}
    new_row_by_ordinal: dict[int, Chunk] = {}
    for index, item in enumerate(planned):
        c = item.chunk
        kept = pairs.get(index)
        if kept is not None:
            row = kept
            row.ordinal = c.ordinal
            row.start_offset = c.start_offset
            row.target_start_s = None
            row.chapter_title = None
            plan.kept += 1
            plan.kept_takes += len(row.takes)
        else:
            row = Chunk(
                script_id=script_id,
                ordinal=c.ordinal,
                text=c.text,
                source=c.source,
                start_offset=c.start_offset,
            )
            if item.turns:
                row.turns_json = json.dumps(item.turns)
                row.source = "dialogue"
            if item.voice_id:
                row.voice_id = item.voice_id
                if not item.turns and c.source == "marker":
                    row.source = "speaker"
            session.add(row)
            plan.new += 1
        new_row_by_ordinal[c.ordinal] = row
        if c.start_offset is not None:
            offsets[c.ordinal] = c.start_offset
    session.flush()

    _attach_anchors(session, script_id, parsed, offsets)
    _attach_chapters(session, script_id, parsed, offsets, plan.warnings)
    # An edited paragraph's chunk is new, but a slot placed on it by hand was
    # placed on that passage: it follows to the chunk that took its place.
    followers = {old[i].id: planned[j].chunk.ordinal for i, j in successor.items()}
    plan.slots_dropped = _replace_slots(
        session,
        script_id,
        parsed,
        offsets,
        old_by_ordinal,
        new_row_by_ordinal,
        followers,
        old_markers=parse_script(script.source_text).slots,
    )

    # An outline would render the old script back over the new one on the next
    # `write sync`, so it is detached — moved to the archived copy, beside the
    # old words it was written for. Reaching here, the words did change.
    beats = session.scalars(select(ScriptBeat).where(ScriptBeat.script_id == script_id)).all()
    if beats:
        if sibling is None:
            sibling = _retired_copy(session, script)
            plan.retired_to = sibling.id
        for beat in beats:
            beat.script_id = sibling.id
    plan.beats_detached = len(beats)

    # Only now: the archived copy above is made from the old text.
    script.source_text = source
    script.source_sha256 = digest
    if filename:
        script.source_path = filename
    if title:
        script.title = title
    session.flush()

    # A kept chunk whose request changed — a neighbour's words, on a model that
    # carries context — has no take for its new request. Its cut would keep
    # playing audio made for different neighbours while the next run pays for a
    # new take that never plays; so the cut goes, and the new take takes it.
    try:
        for job in build_jobs(session, script, project, registry, settings):
            if job.chunk_id in kept_ids and existing_take(session, job.chunk_id, job.key) is None:
                stale = session.get(Cut, job.chunk_id)
                if stale is not None:
                    session.delete(stale)
                plan.kept_regenerated += 1
        session.flush()
    except ValueError as exc:
        plan.warnings.append(f"Could not price every chunk exactly: {exc}")

    _quote(session, plan, registry, settings)
    return plan


def _quote(session: Session, plan: ReplacePlan, registry: Registry, settings: Settings) -> None:
    """What generating the episode as it now stands would cost."""
    todo = ledger.outstanding(session, plan.script_id, registry, settings=settings)
    plan.chunks_to_generate = todo.chunks
    plan.chars = todo.chars
    plan.effects_to_generate = todo.effects
    plan.quote_micros = todo.cost_micros


def _pair(
    old: list[Chunk],
    planned: list,  # type: ignore[type-arg]
    audio_tags: bool,
    old_voices: dict[int, str | None] | None = None,
) -> tuple[dict[int, Chunk], dict[int, int]]:
    """Old rows for planned chunks, one-to-one and in script order.

    Returns the pairs (planned index → old row) and, for old rows that were
    edited rather than dropped, the planned chunk that took their place (old
    index → planned index) — which is where a slot placed on them follows.

    Matched on the words as written (after `_norm`) and the speaker turns.
    Where a stretch differs, a chunk whose spoken words and speakers are
    unchanged still matches: a break regeneration joined, or a tag, is the same
    line. A chunk also needs the same speaker in both scripts: `old_voices` is
    whom the old script gave each row to (by row id), and a line moved to
    another speaker, or whose label was removed, is a different request. A
    voice someone set on a chunk by hand is not the script's, and is kept.
    """
    voices = old_voices or {}

    def key(text: str, turns: str | None) -> tuple[str, str | None]:
        return _norm(text), turns

    def speakers(turns: str | list[dict[str, str]] | None) -> list[tuple[str, str]]:
        items: list[dict[str, str]] = json.loads(turns) if isinstance(turns, str) else turns or []
        return [(t.get("speaker", ""), t.get("voice_id", "")) for t in items]

    old_keys = [key(r.text, r.turns_json) for r in old]
    new_keys = [key(p.chunk.text, json.dumps(p.turns) if p.turns else None) for p in planned]
    pairs: dict[int, Chunk] = {}
    edited: list[tuple[range, range]] = []
    matcher = difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False)
    for op, a0, a1, b0, b1 in matcher.get_opcodes():
        if op == "equal":
            for i, j in zip(range(a0, a1), range(b0, b1), strict=True):
                pairs[j] = old[i]
        elif op == "replace":
            # In order, whatever the block sizes: an insertion beside a joined
            # break must not cost the joined chunk its takes.
            i = a0
            for j in range(b0, b1):
                for k in range(i, a1):
                    same_words = _spoken(old[k].text, audio_tags) == _spoken(
                        planned[j].chunk.text, audio_tags
                    )
                    if same_words and speakers(old[k].turns_json) == speakers(planned[j].turns):
                        pairs[j] = old[k]
                        i = k + 1
                        break
            edited.append((range(a0, a1), range(b0, b1)))
    # A paragraph moved elsewhere is still the same words. Where the model
    # carries nothing between chunks it is the same request, and its take
    # carries over; where it does, the runner's key check catches it.
    used = {row.id for row in pairs.values()}
    for j, wanted in enumerate(new_keys):
        if j in pairs:
            continue
        for i, have in enumerate(old_keys):
            if old[i].id not in used and have == wanted:
                pairs[j] = old[i]
                used.add(old[i].id)
                break

    def same_voice(j: int, row: Chunk) -> bool:
        if row.id in voices:
            return bool(voices[row.id] == planned[j].voice_id)
        # Not found in the old script — edited by hand since — so the stored
        # source is all there is to go on.
        if row.source in ("speaker", "dialogue") or planned[j].voice_id:
            return bool(planned[j].voice_id == row.voice_id)
        return True

    kept = {j: row for j, row in pairs.items() if same_voice(j, row)}
    index_of = {row.id: i for i, row in enumerate(old)}
    # Same words, new speaker: the same passage, so what was placed on it
    # follows it to the new chunk.
    successor = {index_of[row.id]: j for j, row in pairs.items() if j not in kept}
    # An edited passage is followed to the new chunk that holds most of its
    # words — by content, not position, or a dropped passage's slot would land
    # on whatever new line happened to sit where it did.
    paired = {row.id for row in kept.values()}
    for rows, candidates in edited:
        fresh = [j for j in candidates if j not in kept]
        for i in rows:
            if old[i].id in paired or i in successor:
                continue
            words = _spoken(old[i].text, audio_tags)
            best = max(
                fresh,
                key=lambda j: _share(words, _spoken(planned[j].chunk.text, audio_tags)),
                default=None,
            )
            if (
                best is not None
                and _share(words, _spoken(planned[best].chunk.text, audio_tags)) >= 0.5
            ):
                successor[i] = best
    return kept, successor


def _share(passage: list[str], other: list[str]) -> float:
    """How much of `passage` survives in `other`: the part of its words found there, in order."""
    if not passage:
        return 0.0
    blocks = difflib.SequenceMatcher(None, passage, other, autojunk=False).get_matching_blocks()
    return sum(block.size for block in blocks) / len(passage)


def _spoken(text: str, audio_tags: bool) -> list[str]:
    return normalise(spoken_text(text, audio_tags=audio_tags))


def _retired_copy(session: Session, script: Script) -> Script:
    """An archived copy of the episode to hold chunks the new script dropped."""
    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    copy = Script(
        project_id=script.project_id,
        title=f"{script.title} — replaced {when}"[:256],
        source_path=script.source_path,
        source_text=script.source_text,
        source_sha256=script.source_sha256,
        model_id=script.model_id,
        voice_id=script.voice_id,
        archived_at=utcnow(),
    )
    session.add(copy)
    session.flush()
    return copy


def _replace_slots(
    session: Session,
    script_id: int,
    parsed: object,
    offsets: dict[int, int],
    old_by_ordinal: dict[int, Chunk],
    new_row_by_ordinal: dict[int, Chunk],
    followers: dict[int, int],
    old_markers: list,  # type: ignore[type-arg]
) -> int:
    """Effect slots for the new script, keeping every generated sound that fits.

    A marker slot still in the new script keeps its row — and the audio already
    paid for — at the chunk it now sits at. A marker slot the new script dropped
    goes (its sound stays in the project's library), and a marker whose slot the
    user deleted stays deleted: a slot is created only for a marker the old
    script did not have. A hand-placed or suggested slot follows its chunk — to
    the chunk that replaced it, when its passage was edited — and goes only when
    its passage was dropped. Returns how many such slots went.
    """
    from collections import Counter

    new_ordinal = {row.id: ordinal for ordinal, row in new_row_by_ordinal.items()}
    slots = list(
        session.scalars(
            select(EffectSlot).where(EffectSlot.script_id == script_id).order_by(EffectSlot.ordinal)
        ).all()
    )
    markers = [s for s in slots if s.source == "marker"]
    before = Counter((m.description, m.duration_s, m.loop) for m in old_markers)
    used: set[int] = set()
    for wanted in getattr(parsed, "slots", []):
        ordinal = _nearest_chunk(offsets, wanted.offset)
        identity = (wanted.description, wanted.duration_s, wanted.loop)
        match = next(
            (
                s
                for s in markers
                if s.id not in used and (s.description, s.duration_s, s.loop) == identity
            ),
            None,
        )
        if match is not None:
            match.at_chunk_ordinal = ordinal
            used.add(match.id)
            before[identity] -= 1
            continue
        if before[identity] > 0:
            # The old script had this marker and its slot is gone: the user
            # removed it, and a replace must not bring it back to be billed.
            before[identity] -= 1
            continue
        session.add(
            EffectSlot(
                script_id=script_id,
                ordinal=_next_slot_ordinal(session, script_id),
                at_chunk_ordinal=ordinal,
                description=wanted.description,
                duration_s=wanted.duration_s,
                loop=wanted.loop,
                source="marker",
                accepted=True,
            )
        )
        session.flush()
    for slot in markers:
        if slot.id not in used:
            session.delete(slot)
    dropped = 0
    for slot in slots:
        if slot.source == "marker":
            continue
        owner = old_by_ordinal.get(slot.at_chunk_ordinal)
        if owner is not None and owner.id in new_ordinal:
            slot.at_chunk_ordinal = new_ordinal[owner.id]
        elif owner is not None and owner.id in followers:
            slot.at_chunk_ordinal = followers[owner.id]
        else:
            session.delete(slot)
            dropped += 1
    session.flush()
    return dropped
