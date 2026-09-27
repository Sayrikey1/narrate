"""HTTP wrapper for the pipeline.

Deliberately thin. Every endpoint calls the same functions the CLI does —
`ingest_script`, `runner.generate`, `effects.generate_slots`, `export_script`,
`ledger.*` — and holds no logic of its own. Two entry points that disagree
about what a re-roll costs would be worse than having only one.

Generation runs as a background task with progress delivered over SSE, because
PRD §9 requires the UI never to block and progress to be streamed per chunk.
The runner already emits `on_event` callbacks, so this is a queue and a
generator rather than a new mechanism.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import tempfile
import threading
import zipfile
from collections.abc import AsyncIterator
from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import Engine, func, select

from narrate import audio, ledger, previews, templates
from narrate import effects as effects_mod
from narrate import media as media_mod
from narrate import produce as produce_mod
from narrate import regenerate as regen
from narrate import voices as voices_mod
from narrate.db.models import (
    CastMember,
    Chunk,
    Cut,
    Effect,
    EffectSlot,
    Project,
    Script,
    Take,
    ThumbnailBrief,
    TitleCandidate,
)
from narrate.db.session import open_db, session_scope
from narrate.export import ExportResult, NothingToExport, export_is_current, export_script
from narrate.ingest import ScriptHasTakes, UnsupportedScript, decode_script, ingest_script
from narrate.money import fmt_usd
from narrate.plan import gather_context, render_plan, render_timeline_json
from narrate.provider.base import SFXProvider, TTSProvider
from narrate.provider.elevenlabs import ElevenLabsProvider
from narrate.provider.mock import MockProvider, MockSFXProvider
from narrate.publish import (
    choose_brief,
    choose_title,
    gather_publish,
    render_publish_json,
    render_publish_pack,
)
from narrate.registry import Registry, UnknownModel
from narrate.settings import Settings, get_settings, resolve_provider
from narrate.timeline import build_timeline, voices_used
from narrate.verify import service as verify_service
from narrate.verify import transcribe as stt
from narrate.verify.compare import INFO, Word
from narrate.verify.transcribe import Transcriber

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


# ---------------------------------------------------------------------------
# Run tracking — one in-flight generation at a time, streamed
# ---------------------------------------------------------------------------


class RunTracker:
    """Progress for background generations, consumed over SSE.

    Deliberately in-memory and process-local: this is a single-operator tool,
    and persisting run chatter would mean inventing a retention policy for
    something nobody reads twice. The durable record is the ledger.
    """

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[str | None]] = {}
        self._results: dict[str, dict[str, Any]] = {}

    def start(self, run_key: str) -> asyncio.Queue[str | None]:
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._queues[run_key] = queue
        # A new run's result must not be mistaken for the last one's.
        self._results.pop(run_key, None)
        return queue

    def busy(self, run_key: str) -> bool:
        """A run has started under this key and not yet finished."""
        return run_key in self._queues and run_key not in self._results

    def emit(self, run_key: str, message: str) -> None:
        queue = self._queues.get(run_key)
        if queue is not None:
            queue.put_nowait(message)

    def finish(self, run_key: str, result: dict[str, Any]) -> None:
        self._results[run_key] = result
        queue = self._queues.get(run_key)
        if queue is not None:
            queue.put_nowait(None)

    def result(self, run_key: str) -> dict[str, Any] | None:
        return self._results.get(run_key)

    def queue(self, run_key: str) -> asyncio.Queue[str | None] | None:
        return self._queues.get(run_key)


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class ProjectIn(BaseModel):
    name: str
    voice_id: str | None = None
    model_id: str | None = None
    prefix_tags: str = ""
    monthly_cap_usd: float | None = None


class ProjectUpdate(BaseModel):
    voice_id: str | None = None
    model_id: str | None = None
    prefix_tags: str | None = None
    settings: dict[str, float | bool] | None = None
    monthly_cap_usd: float | None = None


class ScriptIn(BaseModel):
    project_id: int
    title: str
    text: str
    # Group each run of speaker turns into one conversation. Ignored, with a
    # warning, on a model that has no dialogue endpoint.
    dialogue: bool = False


class RegisterVoiceIn(BaseModel):
    voice_id: str
    name: str | None = None
    note: str = ""
    replace: bool = False
    # Skip confirming the voice exists. For a voice added to the account since
    # the last list, or when there is no key.
    offline: bool = False


class CastIn(BaseModel):
    name: str
    voice_id: str
    note: str = ""


class VerifyIn(BaseModel):
    chunks: list[int] | None = None
    recheck: bool = False
    all_takes: bool = False


class RegenerateIn(BaseModel):
    chunks: list[int]
    # New words for the chunk first — one chunk, v3-family models only.
    text: str | None = None
    # Tries per chunk while the new take is still flagged; stops at the first
    # clean one. The quote is for all of them.
    attempts: int = regen.MAX_ATTEMPTS
    # "better" (default): the cut moves only to a take that checks strictly
    # better. "new": to the new take unless it checks worse. "never".
    move: str = "better"
    max_spend_usd: float | None = None
    accept_unknown: bool = False
    # Rebuild the episode afterwards if the cut changed (or the last export is
    # stale), so the audio people download is the fixed one — in the formats
    # asked for, or every stale master would keep the old take.
    export: bool = False
    export_formats: list[str] | None = None
    # False returns the price and sends nothing. The button asks first.
    confirm: bool = False


class GenerateIn(BaseModel):
    only: list[int] | None = None
    force: bool = False
    dry_run: bool = True
    max_spend_usd: float | None = None
    override_cap: bool = False
    # Effects are included by default: one action produces a finished episode,
    # and the cap is checked against both phases together.
    with_effects: bool = True


class CutIn(BaseModel):
    chunk_ordinal: int
    take_ordinal: int


class SlotIn(BaseModel):
    at_chunk_ordinal: int
    description: str
    duration_s: float | None = None
    loop: bool = False


class ExportIn(BaseModel):
    formats: list[str] | None = None
    piece_format: str | None = None
    # Export one voice's performance instead of the cut. The masters go to their
    # own folder, so two variants of an episode never overwrite each other.
    voice_id: str | None = None
    variant_label: str | None = None


class EffectsGenerateIn(BaseModel):
    slot_ids: list[int] | None = None
    dry_run: bool = True


class _Locked:
    """A transcriber whose every call holds a lock."""

    def __init__(self, inner: Transcriber, lock: threading.Lock) -> None:
        self._inner = inner
        self._lock = lock

    @property
    def identity(self) -> str:
        return self._inner.identity

    def transcribe(self, path: Path, window: tuple[float, float] | None = None) -> list[Word]:
        with self._lock:
            return self._inner.transcribe(path, window)


def _check_payload(result: verify_service.TakeCheck) -> dict[str, Any]:
    return {
        "take_id": result.take_id,
        "chunk_ordinal": result.chunk_ordinal,
        "take_ordinal": result.take_ordinal,
        "status": result.status,
        "in_cut": result.in_cut,
        "seconds": result.seconds,
        "error": result.error,
        "findings": [
            {**f.as_dict(), "summary": f.summary} for f in result.findings if f.severity != INFO
        ],
    }


def _export_payload(result: ExportResult) -> dict[str, Any]:
    return {
        "out_dir": str(result.out_dir),
        "master": result.master.name,
        "masters": {k: v.name for k, v in result.masters.items()},
        "mp3": result.mp3.name if result.mp3 else None,
        "plan": result.plan.name,
        "duration_s": result.duration_s,
        "chunks": result.chunks,
        "effects": result.effects,
        "planned": result.planned,
        "names": result.names,
    }


def create_app(
    engine: Engine | None = None,
    settings: Settings | None = None,
    transcriber: Transcriber | None = None,
) -> FastAPI:
    app = FastAPI(title="narrate", docs_url="/api/docs", openapi_url="/api/openapi.json")
    db = engine or open_db()
    config = settings or get_settings()
    registry = Registry.load()
    tracker = RunTracker()

    # One speech model per process, loaded on first use: it is half a gigabyte
    # in memory and takes seconds to load. The lock keeps two checks from
    # transcribing at once — they would only compete for the same CPU.
    stt_lock = threading.Lock()
    loaded: dict[str, Transcriber] = {}

    def speech_to_text() -> Transcriber | None:
        if transcriber is not None:
            return transcriber
        if not (stt.stt_available() and stt.model_ready()):
            return None
        if "local" not in loaded:
            loaded["local"] = stt.LocalWhisper()
        return loaded["local"]

    def locked(inner: Transcriber | None) -> Transcriber | None:
        """The shared model, taken one transcription at a time.

        Regeneration checks its takes from a worker thread while `verify` may be
        running on another; both go through this lock rather than competing for
        the same model and the same CPU.
        """
        return None if inner is None else _Locked(inner, stt_lock)

    def verify_blocking(script_id: int, **options: Any) -> list[verify_service.TakeCheck]:
        with stt_lock:
            return verify_service.verify_script(
                db, script_id, speech_to_text(), registry, **options
            )

    # -- reference data ----------------------------------------------------

    @app.get("/api/models")
    def list_models() -> list[dict[str, Any]]:
        default = registry.recommended().model_id
        return [
            {
                "model_id": spec.model_id,
                # What a new project gets when none is chosen. Sent rather than
                # re-derived in the browser, so the form, the CLI and the API
                # cannot disagree about it.
                "default": spec.model_id == default,
                "label": spec.label,
                "usd_per_1k": spec.usd_per_1k,
                "max_chars": spec.effective_max_chars,
                "chunk_target": list(spec.chunk_target),
                "request_stitching": spec.request_stitching,
                "text_conditioning": spec.text_conditioning,
                "continuity_mode": spec.continuity_mode,
                "audio_tags": spec.audio_tags,
                # Which sliders to show. On v3 `speed`, `similarity_boost` and
                # `use_speaker_boost` do nothing, and offering a control that
                # silently has no effect is worse than not offering it.
                "settings_honoured": sorted(spec.settings_honoured),
                # Whether this model can render several speakers in one
                # request. Only eleven_v3 can, and its dialogue ceiling is
                # tighter than its speech one, so both are reported.
                "dialogue": spec.dialogue,
                "dialogue_max_chars": spec.dialogue_max_chars,
                "long_form": spec.long_form,
                "note": spec.note,
            }
            for spec in registry.all()
        ]

    @app.get("/api/voices")
    async def list_voices(search: str | None = None) -> list[dict[str, Any]]:
        """Voices the account can use, with a preview to audition before spending.

        Costs zero characters. The provider's own payload carries a great deal
        the picker does not need — samples, sharing, verified languages — so it
        is trimmed here rather than shipped whole to the browser.
        """
        if resolve_provider() == "mock" or not config.has_api_key:
            return _stub_voices()

        provider = ElevenLabsProvider(config)
        try:
            voices = await provider.list_voices(search=search)
        finally:
            await provider.aclose()

        return [
            {
                "voice_id": v.get("voice_id"),
                "name": v.get("name"),
                "category": v.get("category"),
                "labels": v.get("labels") or {},
                "description": v.get("description"),
                # Whether an audition is possible at all. The picker plays it
                # from `/api/voices/{id}/preview` rather than from the URL — the
                # provider's link is nullable, expiring and cross-origin, and
                # all three failures look like a dead button. See previews.py.
                "preview": previews.preview_url_of(v) is not None,
                "settings": v.get("settings"),
                "mock": False,
            }
            for v in voices
        ]

    # -- projects and scripts ---------------------------------------------

    @app.get("/api/projects")
    def list_projects() -> list[dict[str, Any]]:
        with session_scope(db) as session:
            out = []
            for project in session.scalars(select(Project).order_by(Project.id)).all():
                rollup = ledger.project_rollup(session, project.id)
                budget = ledger.project_budget(session, project.id)
                out.append(
                    {
                        "id": project.id,
                        "name": project.name,
                        "voice_id": project.voice_id,
                        "model_id": project.model_id,
                        "prefix_tags": project.prefix_tags,
                        "spend_micros": rollup.cost_micros,
                        "waste_pct": rollup.waste_pct,
                        "cap_micros": budget.cap_micros,
                        "cap_used_pct": budget.used_pct,
                    }
                )
            return out

    @app.post("/api/projects")
    def create_project(body: ProjectIn) -> dict[str, Any]:
        model_id = body.model_id or registry.recommended().model_id
        if model_id not in registry:
            raise HTTPException(400, f"Unknown model {model_id!r}")
        spec = registry.get(model_id)
        if body.prefix_tags and not spec.audio_tags:
            # The same refusal the CLI and PATCH make: the tags would be read
            # aloud, and billed, on every chunk.
            raise HTTPException(
                422,
                f"{spec.label} does not support audio tags. Drop the prefix tags or "
                "choose a model that does.",
            )
        with session_scope(db) as session:
            if session.scalar(select(Project).where(Project.name == body.name)):
                raise HTTPException(409, f"A project named {body.name!r} already exists")
            project = Project(
                name=body.name,
                voice_id=body.voice_id,
                model_id=model_id,
                prefix_tags=body.prefix_tags,
                monthly_cap_micros=(
                    int(body.monthly_cap_usd * 1_000_000) if body.monthly_cap_usd else None
                ),
            )
            session.add(project)
            session.flush()
            return {"id": project.id, "name": project.name}

    @app.patch("/api/projects/{project_id}")
    def update_project(project_id: int, body: ProjectUpdate) -> dict[str, Any]:
        """Change a project's voice, model or delivery settings.

        Settings the chosen model does not honour are dropped rather than
        stored, with the dropped names returned so the UI can say so — silently
        keeping a `speed` that v3 ignores would misrepresent the delivery.
        """
        with session_scope(db) as session:
            project = session.get(Project, project_id)
            if project is None:
                raise HTTPException(404, "No such project")

            if body.model_id is not None:
                if body.model_id not in registry:
                    raise HTTPException(400, f"Unknown model {body.model_id!r}")
                project.model_id = body.model_id
            if body.voice_id is not None:
                project.voice_id = body.voice_id
            if body.monthly_cap_usd is not None:
                project.monthly_cap_micros = int(body.monthly_cap_usd * 1_000_000)

            spec = registry.get(project.model_id)

            if body.prefix_tags is not None:
                if body.prefix_tags and not spec.audio_tags:
                    raise HTTPException(400, f"{spec.label} does not support audio tags")
                project.prefix_tags = body.prefix_tags
            elif project.prefix_tags and not spec.audio_tags:
                # A model change that would leave the project's tags on a model
                # that reads them aloud. Refused here (the whole change is rolled
                # back), not discovered when a generation fails.
                raise HTTPException(
                    422,
                    f"{spec.label} does not support audio tags, and this project has "
                    f"prefix tags {project.prefix_tags!r}. Clear them in the same change, "
                    "or keep a model that honours them.",
                )

            rejected: list[str] = []
            if body.settings is not None:
                merged = {**json.loads(project.settings_json or "{}"), **body.settings}
                rejected = spec.rejected_settings(merged)
                project.settings_json = json.dumps(spec.filter_settings(merged), sort_keys=True)

            return {
                "id": project.id,
                "voice_id": project.voice_id,
                "model_id": project.model_id,
                "prefix_tags": project.prefix_tags,
                "settings": json.loads(project.settings_json or "{}"),
                "rejected_settings": rejected,
            }

    @app.get("/api/scripts")
    def list_scripts() -> list[dict[str, Any]]:
        with session_scope(db) as session:
            out = []
            for script in session.scalars(select(Script).order_by(Script.id)).all():
                chunks = list(
                    session.scalars(select(Chunk).where(Chunk.script_id == script.id)).all()
                )
                project = session.get(Project, script.project_id)
                out.append(
                    {
                        "id": script.id,
                        "project_id": script.project_id,
                        "title": script.title,
                        "chunks": len(chunks),
                        "chars": sum(len(c.text) for c in chunks),
                        # The script's own model where it has one: a script can
                        # be added on another model than its project's, and the
                        # page's continuity notice must describe this script.
                        "model_id": script.model_id or (project.model_id if project else None),
                    }
                )
            return out

    @app.post("/api/scripts")
    def create_script(body: ScriptIn) -> dict[str, Any]:
        return _create_script(body.project_id, body.title, body.text, body.dialogue)

    def _create_script(
        project_id: int, title: str, text: str, dialogue: bool = False
    ) -> dict[str, Any]:
        with session_scope(db) as session:
            project = session.get(Project, project_id)
            if project is None:
                raise HTTPException(404, "No such project")
            spec = registry.get(project.model_id)
            script = Script(
                project_id=project.id,
                title=title,
                source_text=text,
                source_sha256=hashlib.sha256(text.encode()).hexdigest(),
            )
            session.add(script)
            session.flush()
            try:
                result = ingest_script(
                    session,
                    script,
                    spec,
                    project.prefix_tags,
                    cast=_cast_map(session, project.id),
                    dialogue=dialogue,
                )
            except ScriptHasTakes as exc:
                raise HTTPException(409, str(exc)) from exc
            return {
                "id": script.id,
                "chunks": len(result.chunks),
                "slots": result.slots_created,
                "warnings": result.warnings,
                "speakers": result.speakers,
                "turns_assigned": result.turns_assigned,
                "dialogue": result.dialogue,
            }

    def _cast_map(session: Any, project_id: int) -> dict[str, str]:
        rows = session.scalars(select(CastMember).where(CastMember.project_id == project_id)).all()
        return {row.name: row.voice_id for row in rows}

    # -- cast --------------------------------------------------------------

    @app.get("/api/projects/{project_id}/cast")
    def list_cast(project_id: int) -> list[dict[str, Any]]:
        """Who speaks in this project, and in which voice.

        The cast is what makes a `Morag:` prefix a speaker change rather than
        prose — an uncast name is left as narration, which is why this can run
        over ordinary writing without editing it.
        """
        with session_scope(db) as session:
            if session.get(Project, project_id) is None:
                raise HTTPException(404, "No such project")
            rows = session.scalars(
                select(CastMember)
                .where(CastMember.project_id == project_id)
                .order_by(CastMember.name)
            ).all()
            return [
                {
                    "id": row.id,
                    "name": row.name,
                    "voice_id": row.voice_id,
                    "note": row.note,
                }
                for row in rows
            ]

    @app.post("/api/projects/{project_id}/cast")
    def upsert_cast(project_id: int, body: CastIn) -> dict[str, Any]:
        name = body.name.strip()
        if not name:
            raise HTTPException(422, "A cast member needs a name.")
        if ":" in name:
            raise HTTPException(
                422, "A speaker name cannot contain a colon — that is what separates it."
            )
        with session_scope(db) as session:
            if session.get(Project, project_id) is None:
                raise HTTPException(404, "No such project")
            member = session.scalars(
                select(CastMember).where(
                    CastMember.project_id == project_id, CastMember.name == name
                )
            ).first()
            if member is None:
                member = CastMember(project_id=project_id, name=name, voice_id=body.voice_id)
                session.add(member)
            else:
                member.voice_id = body.voice_id
            member.note = body.note
            session.flush()
            return {"id": member.id, "name": member.name, "voice_id": member.voice_id}

    @app.delete("/api/projects/{project_id}/cast/{member_id}")
    def delete_cast(project_id: int, member_id: int) -> dict[str, bool]:
        with session_scope(db) as session:
            member = session.get(CastMember, member_id)
            if member is None or member.project_id != project_id:
                raise HTTPException(404, "No such cast member")
            session.delete(member)
            return {"deleted": True}

    @app.post("/api/scripts/upload")
    async def upload_script(
        project_id: int = Form(...),
        title: str | None = Form(None),
        dialogue: bool = Form(False),
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        """Ingest a script from a file.

        Goes through the same `ingest_script` the paste path uses, so an
        uploaded script is parsed, chunked and scanned for markers identically.
        Two ingestion paths that diverged would mean markers stripped in one and
        spoken aloud — and billed — in the other.
        """
        raw = await file.read()
        try:
            text = decode_script(file.filename or "upload", raw)
        except UnsupportedScript as exc:
            raise HTTPException(415, str(exc)) from exc

        stem = Path(file.filename or "script").stem
        return _create_script(project_id, title or stem, text, dialogue)

    @app.get("/api/scripts/{script_id}/download")
    def download_script(script_id: int, format: str = "md") -> Response:
        """The original script text back out, as an attachment."""
        if format not in ("md", "txt"):
            raise HTTPException(400, "format must be 'md' or 'txt'")
        with session_scope(db) as session:
            script = _script(session, script_id)
            text, title = script.source_text, script.title
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in title).strip("-")
        return Response(
            content=text,
            media_type="text/markdown" if format == "md" else "text/plain",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{safe or f"script-{script_id}"}.{format}"'
                )
            },
        )

    @app.get("/api/templates")
    def list_templates() -> list[dict[str, str]]:
        """Starter scripts. Costs nothing."""
        return [
            {"slug": t.slug, "title": t.title, "summary": t.summary, "filename": t.filename}
            for t in templates.all_templates()
        ]

    @app.get("/api/templates/{slug}")
    def get_template(slug: str, download: bool = False) -> Response:
        """One starter script, as text or as a download.

        Served as `text/markdown` so the browser shows it inline by default;
        `?download=1` makes it save instead. Both come from the same file the
        CLI writes, so the guidance cannot drift between the two.
        """
        try:
            template = templates.get(slug)
        except KeyError as exc:
            raise HTTPException(404, str(exc).strip('"')) from exc

        headers = (
            {"Content-Disposition": f'attachment; filename="{template.filename}"'}
            if download
            else {}
        )
        return Response(
            content=template.read(),
            media_type="text/markdown; charset=utf-8",
            headers=headers,
        )

    @app.get("/api/formats")
    def list_formats() -> list[dict[str, Any]]:
        """Delivery formats export can write, for the picker."""
        return [
            {
                "key": fmt.key,
                "label": fmt.label,
                "suffix": fmt.suffix,
                "codec": fmt.codec,
                "lossless": fmt.lossless,
                "note": fmt.note,
            }
            for fmt in audio.FORMATS.values()
        ]

    @app.get("/api/scripts/{script_id}/chunks")
    def script_chunks(script_id: int) -> list[dict[str, Any]]:
        with session_scope(db) as session:
            _script(session, script_id)
            rows = session.scalars(
                select(Chunk).where(Chunk.script_id == script_id).order_by(Chunk.ordinal)
            ).all()
            selected = {
                c.chunk_id: c.take_id
                for c in session.scalars(select(Cut).where(Cut.script_id == script_id)).all()
            }
            out = []
            for row in rows:
                takes = session.scalars(
                    select(Take).where(Take.chunk_id == row.id).order_by(Take.ordinal)
                ).all()
                out.append(
                    {
                        "ordinal": row.ordinal,
                        "text": row.text,
                        "chars": len(row.text),
                        "source": row.source,
                        "target_start_s": row.target_start_s,
                        "takes": [
                            {
                                "id": take.id,
                                "ordinal": take.ordinal,
                                "status": take.status,
                                "billed_chars": take.billed_chars,
                                "cost_micros": take.cost_micros,
                                "duration_s": take.duration_s,
                                "model_id": take.model_id,
                                "in_cut": selected.get(row.id) == take.id,
                                "verify_status": take.verify_status,
                                "findings": [
                                    {**f.as_dict(), "summary": f.summary}
                                    for f in verify_service.findings_of(take)
                                ],
                            }
                            for take in takes
                        ],
                    }
                )
            return out

    @app.get("/api/scripts/{script_id}/timeline")
    def script_timeline(script_id: int) -> dict[str, Any]:
        with session_scope(db) as session:
            _script(session, script_id)
            tl = build_timeline(session, script_id, gap_seconds=config.gap_seconds)
            return {
                "script_id": tl.script_id,
                "title": tl.script_title,
                "project": tl.project_name,
                "runtime_s": tl.runtime_s,
                "complete": tl.is_complete,
                "entries": [
                    {
                        **{k: v for k, v in asdict(e).items() if k != "source_path"},
                        "start": e.start_stamp,
                        "end": e.end_stamp,
                        "duration_s": e.duration_s,
                        "drift_s": e.drift_s,
                    }
                    for e in tl.entries
                ],
            }

    @app.get("/api/scripts/{script_id}/plan")
    def script_plan(script_id: int) -> dict[str, Any]:
        """The editing plan, as the document *and* as data.

        `render_timeline_json` was already written and served nowhere. Shipping
        both means the page can render the document a person reads and also
        answer questions about it — drift outliers, cues still missing, runtime
        by lane — without parsing the markdown back apart, which would be
        deriving the same facts a second way.
        """
        with session_scope(db) as session:
            _script(session, script_id)
            tl = build_timeline(session, script_id, gap_seconds=config.gap_seconds)
            context = gather_context(session, script_id)
            return {
                "markdown": render_plan(tl, session, None, context),
                "data": json.loads(render_timeline_json(tl, None, context)),
            }

    @app.get("/api/scripts/{script_id}/publish")
    def script_publish(script_id: int) -> dict[str, Any]:
        """The publish pack, as the document *and* as data.

        Same pairing as the plan, and for the same reason: the page renders the
        markdown somebody reads, and answers questions about it — which chapter
        rules are broken, how long a viewer has to stay — from the JSON twin
        rather than by parsing the prose back apart.
        """
        with session_scope(db) as session:
            _script(session, script_id)
            tl = build_timeline(session, script_id, gap_seconds=config.gap_seconds)
            context = gather_publish(session, script_id)
            return {
                "markdown": render_publish_pack(tl, context),
                "data": json.loads(render_publish_json(tl, context)),
            }

    @app.post("/api/scripts/{script_id}/titles/{candidate_id}/accept")
    def accept_title(script_id: int, candidate_id: int) -> dict[str, Any]:
        """Choose a title. Writes through to the script, unchooses the rest."""
        with session_scope(db) as session:
            _script(session, script_id)
            row = session.get(TitleCandidate, candidate_id)
            if row is None or row.script_id != script_id:
                raise HTTPException(status_code=404, detail=f"No title candidate {candidate_id}.")
            choose_title(session, row)
            return {"id": row.id, "text": row.text, "accepted": True}

    @app.post("/api/scripts/{script_id}/briefs/{brief_id}/choose")
    def choose_thumbnail(script_id: int, brief_id: int) -> dict[str, Any]:
        """Choose which thumbnail brief this episode ships."""
        with session_scope(db) as session:
            _script(session, script_id)
            row = session.get(ThumbnailBrief, brief_id)
            if row is None or row.script_id != script_id:
                raise HTTPException(status_code=404, detail=f"No thumbnail brief {brief_id}.")
            choose_brief(session, row)
            return {"id": row.id, "archetype": row.archetype, "accepted": True}

    @app.get("/api/scripts/{script_id}/variants")
    def script_variants(script_id: int) -> list[dict[str, Any]]:
        """Every voice this script has takes in, and whether each is complete.

        A variant needs nothing stored: a take already records the voice that
        produced it, so generating a script twice in two voices leaves both sets
        side by side and this is just a different way of counting them.
        """
        with session_scope(db) as session:
            _script(session, script_id)
            total = (
                session.scalar(select(func.count(Chunk.id)).where(Chunk.script_id == script_id))
                or 0
            )
            return [
                {
                    "voice_id": voice_id,
                    "chunks": covered,
                    "chunks_total": total,
                    # Only a voice covering every chunk can be exported; a
                    # partial one would ship an episode with a hole in it.
                    "complete": covered >= total > 0,
                }
                for voice_id, covered in voices_used(session, script_id)
            ]

    @app.get("/api/scripts/{script_id}/cost")
    def script_cost(script_id: int) -> dict[str, Any]:
        with session_scope(db) as session:
            script = _script(session, script_id)
            rollup = ledger.script_rollup(session, script_id)
            budget = ledger.project_budget(session, script.project_id)
            todo = ledger.outstanding(session, script_id, registry)
            return {
                **_rollup_payload(rollup),
                "cost_per_minute_micros": ledger.cost_per_minute_micros(session, script_id),
                "cap_micros": budget.cap_micros,
                "cap_used_pct": budget.used_pct,
                "outstanding": {
                    "chunks": todo.chunks,
                    "chars": todo.chars,
                    "effects": todo.effects,
                    "effect_seconds": todo.effect_seconds,
                    "cost_micros": todo.cost_micros,
                },
                "projected_micros": rollup.cost_micros + todo.cost_micros,
            }

    # -- generation --------------------------------------------------------

    @app.post("/api/scripts/{script_id}/generate")
    async def generate(
        script_id: int, body: GenerateIn, background: BackgroundTasks
    ) -> dict[str, Any]:
        """Produce an episode: narration, then its accepted effect cues.

        Runs in the background with both phases streamed over the same SSE
        channel, so the UI never blocks on a run.
        """
        run_key = f"script-{script_id}"
        # Priced before the run is marked started: a projection that cannot be
        # made (a chunk naming a model the rate card no longer has) must answer
        # with the reason, not leave the script marked busy until a restart.
        try:
            projection = produce_mod.project(
                db,
                script_id,
                registry,
                with_effects=body.with_effects,
                only=body.only,
                force=body.force,
                settings=config,
            )
        except UnknownModel as exc:
            raise HTTPException(422, str(exc.args[0])) from exc

        # One run per script: a second would share the run's event stream and,
        # worse, generate the same chunks as a regeneration already in flight.
        # Nothing awaits between the check and the start, so two requests cannot
        # both pass it.
        if tracker.busy(run_key):
            raise HTTPException(409, "A run is already in progress for this script.")
        tracker.start(run_key)

        async def work() -> None:
            mocked = resolve_provider() == "mock" or body.dry_run
            tts: TTSProvider | None = None
            sfx: SFXProvider | None = None
            try:
                # Inside the try: a provider that cannot be built (no key, say)
                # must still finish the run, or the script stays "busy" for good.
                tts = MockProvider() if mocked else ElevenLabsProvider(config)
                sfx = MockSFXProvider() if mocked else ElevenLabsProvider(config)
                report = await produce_mod.produce(
                    db,
                    script_id,
                    tts,
                    sfx,
                    registry,
                    config,
                    with_effects=body.with_effects,
                    only=body.only,
                    dry_run=body.dry_run,
                    force=body.force,
                    max_spend_micros=(
                        round(body.max_spend_usd * 1_000_000)
                        if body.max_spend_usd is not None
                        else None
                    ),
                    override_cap=body.override_cap,
                    on_event=lambda m: tracker.emit(run_key, m),
                )
                speech = report.speech
                tracker.finish(
                    run_key,
                    {
                        "dry_run": report.dry_run,
                        "blocked": report.blocked,
                        "spent_micros": report.spent_micros,
                        "succeeded": len(speech.succeeded) if speech else 0,
                        "skipped": len(speech.skipped) if speech else 0,
                        "failed": len(speech.failed) if speech else 0,
                        "effects_generated": report.effects_generated,
                        "effects_reused": report.effects_reused,
                        "effects_failed": len(report.effects_failed),
                        "stopped_reason": report.stopped_reason,
                        "outcomes": [asdict(o) for o in (speech.outcomes if speech else [])],
                        "effects": [asdict(o) for o in report.effects],
                    },
                )
            except Exception as exc:
                tracker.finish(run_key, {"error": str(exc)})
            finally:
                if tts is not None:
                    await tts.aclose()
                if sfx is not None:
                    await sfx.aclose()

        background.add_task(work)
        return {
            "run_key": run_key,
            "streaming": True,
            "projection": {
                "speech_micros": projection.speech_micros,
                "effect_micros": projection.effect_micros,
                "total_micros": projection.total_micros,
                "chunks": projection.chunks,
                "chars": projection.chars,
                "effects": projection.effects,
                "effect_seconds": projection.effect_seconds,
            },
        }

    @app.get("/api/runs/{run_key}/events")
    async def run_events(run_key: str, request: Request) -> StreamingResponse:
        queue = tracker.queue(run_key)
        if queue is None:
            raise HTTPException(404, "No such run")

        async def stream() -> AsyncIterator[str]:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield ": keepalive\n\n"  # keeps proxies from closing an idle stream
                    continue
                if message is None:
                    payload = tracker.result(run_key) or {}
                    yield f"event: done\ndata: {json.dumps(payload)}\n\n"
                    return
                yield f"data: {json.dumps({'message': message})}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    # -- verify and regenerate ------------------------------------------------

    @app.get("/api/verify")
    def verify_capability() -> dict[str, Any]:
        """Whether takes can be checked, and what to do if not."""
        return {
            "available": transcriber is not None or stt.stt_available(),
            "model_ready": transcriber is not None or stt.model_ready(),
            "model": stt.DEFAULT_MODEL,
            "model_mb": stt.MODEL_SIZE_MB.get(stt.DEFAULT_MODEL, 0),
            "install_hint": stt.INSTALL_HINT,
            "download_hint": "narrate verify --download-model",
        }

    @app.post("/api/scripts/{script_id}/verify")
    async def verify_takes(
        script_id: int, body: VerifyIn, background: BackgroundTasks
    ) -> dict[str, Any]:
        """Check takes against the script. Costs nothing; streamed like a run."""
        with session_scope(db) as session:
            _script(session, script_id)
        run_key = f"verify-{script_id}"
        # Before the run is marked busy: finding the speech model touches the
        # disk and can raise, and an exception after start() would leave the
        # button answering 409 until a restart.
        mode = "speech-to-text" if speech_to_text() is not None else "waveform only"
        if tracker.busy(run_key):
            raise HTTPException(409, "A check is already running for this script.")
        tracker.start(run_key)

        async def work() -> None:
            def progress(result: verify_service.TakeCheck) -> None:
                tracker.emit(
                    run_key,
                    f"chunk {result.chunk_ordinal} take {result.take_ordinal}: {result.status}",
                )

            try:
                results = await asyncio.to_thread(
                    verify_blocking,
                    script_id,
                    chunks=body.chunks,
                    scope="all" if body.all_takes else "cut",
                    recheck=body.recheck,
                    on_result=progress,
                )
                tracker.finish(
                    run_key,
                    {
                        "mode": mode,
                        "checked": len(results),
                        "results": [_check_payload(r) for r in results],
                    },
                )
            except Exception as exc:
                tracker.finish(run_key, {"error": str(exc)})

        background.add_task(work)
        return {"run_key": run_key, "mode": mode}

    @app.post("/api/scripts/{script_id}/regenerate")
    async def regenerate_chunks(
        script_id: int, body: RegenerateIn, background: BackgroundTasks
    ) -> dict[str, Any]:
        """Price a regeneration, or — with `confirm` — run it in the background."""
        if body.move not in (regen.MOVE_BETTER, regen.MOVE_NEW, regen.MOVE_NEVER):
            raise HTTPException(422, "move must be 'better', 'new' or 'never'.")
        if not body.chunks:
            raise HTTPException(422, "Name at least one chunk to regenerate.")
        if body.text is not None and len(body.chunks) != 1:
            raise HTTPException(422, "New text replaces one chunk's words; name one chunk.")
        texts = {body.chunks[0]: body.text} if body.text is not None else None
        limit = round(body.max_spend_usd * 1_000_000) if body.max_spend_usd is not None else None
        try:
            plans = regen.plan(
                db, script_id, registry, chunks=body.chunks, texts=texts, settings=config
            )
        except regen.ScriptNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except regen.RegenerateRefused as exc:
            raise HTTPException(422, str(exc)) from exc

        tries = max(1, min(body.attempts, regen.MAX_ATTEMPTS))
        runnable = [p for p in plans if body.accept_unknown or not p.blocker]
        # Once the words change, the old take says something the script no
        # longer does, so the new take wins unless it checks worse.
        move = (
            regen.MOVE_NEW
            if body.text is not None and body.move == regen.MOVE_BETTER
            else body.move
        )
        worst = sum(p.price_micros for p in runnable) * tries
        if limit is not None:
            worst = min(worst, limit)
        quote = {
            "plans": [asdict(p) for p in plans],
            "attempts": tries,
            "worst_micros": worst,
            "worst_usd": fmt_usd(worst, 4),
            "checked_with": "speech-to-text" if speech_to_text() is not None else "waveform only",
            "mock": resolve_provider() == "mock",
        }
        if not body.confirm:
            return {"dry_run": True, **quote}
        if not runnable:
            raise HTTPException(409, "Every chunk named has an unknown outcome to reconcile.")
        cheapest = min(p.price_micros for p in runnable)
        if limit is not None and limit < cheapest:
            raise HTTPException(
                422,
                f"A limit of {fmt_usd(limit, 4)} is below the price of one try "
                f"({fmt_usd(cheapest, 4)}), so nothing would be sent.",
            )

        run_key = f"script-{script_id}"
        if tracker.busy(run_key):
            raise HTTPException(409, "A run is already in progress for this script.")
        tracker.start(run_key)

        async def work() -> None:
            mocked = resolve_provider() == "mock"
            tts: TTSProvider | None = None
            try:
                # Inside the try: a provider that cannot be built (no key, say)
                # must still finish the run, or the script stays "busy" for good.
                tts = MockProvider() if mocked else ElevenLabsProvider(config)
                results = await regen.regenerate(
                    db,
                    script_id,
                    [p.ordinal for p in runnable],
                    tts,
                    MockSFXProvider(),
                    registry,
                    config,
                    transcriber=locked(speech_to_text()),
                    attempts=tries,
                    max_spend_micros=limit,
                    move_cut=move,
                    accept_unknown=body.accept_unknown,
                    texts=texts,
                    on_event=lambda m: tracker.emit(run_key, m),
                )
                payload: dict[str, Any] = {
                    "regenerated": [{**asdict(r), "moved": r.moved} for r in results],
                    "spent_micros": sum(r.spent_micros for r in results),
                    "mock": mocked,
                }
                if body.export:
                    with session_scope(db) as session:
                        stale = not export_is_current(session, script_id)
                    if stale or any(r.moved for r in results):
                        try:
                            # Off the event loop: stitching is seconds of ffmpeg.
                            rebuilt = await asyncio.to_thread(
                                partial(
                                    export_script,
                                    db,
                                    script_id,
                                    config,
                                    formats=body.export_formats,
                                )
                            )
                            payload["exported"] = _export_payload(rebuilt)
                        except Exception as exc:
                            # The regeneration is done and paid for; report it
                            # whatever became of the rebuild.
                            payload["export_error"] = str(exc)
                tracker.finish(run_key, payload)
            except Exception as exc:
                tracker.finish(run_key, {"error": str(exc)})
            finally:
                if tts is not None:
                    await tts.aclose()

        background.add_task(work)
        return {"dry_run": False, "run_key": run_key, **quote}

    @app.post("/api/scripts/{script_id}/cut")
    def set_cut(script_id: int, body: CutIn) -> dict[str, Any]:
        with session_scope(db) as session:
            chunk = session.scalar(
                select(Chunk).where(
                    Chunk.script_id == script_id, Chunk.ordinal == body.chunk_ordinal
                )
            )
            if chunk is None:
                raise HTTPException(404, "No such chunk")
            take = session.scalar(
                select(Take).where(Take.chunk_id == chunk.id, Take.ordinal == body.take_ordinal)
            )
            if take is None:
                raise HTTPException(404, "No such take")
            if take.status != "succeeded":
                raise HTTPException(400, f"Take is {take.status!r} and cannot be selected")
            cut = session.get(Cut, chunk.id)
            if cut:
                cut.take_id = take.id
            else:
                session.add(Cut(chunk_id=chunk.id, script_id=script_id, take_id=take.id))
            return {"chunk_ordinal": body.chunk_ordinal, "take_ordinal": body.take_ordinal}

    # -- effects -----------------------------------------------------------

    @app.get("/api/scripts/{script_id}/effects")
    def list_slots(script_id: int) -> list[dict[str, Any]]:
        with session_scope(db) as session:
            _script(session, script_id)
            out = []
            for slot in session.scalars(
                select(EffectSlot)
                .where(EffectSlot.script_id == script_id)
                .order_by(EffectSlot.at_chunk_ordinal, EffectSlot.ordinal)
            ).all():
                effect = session.get(Effect, slot.effect_id) if slot.effect_id else None
                out.append(
                    {
                        "id": slot.id,
                        "at_chunk_ordinal": slot.at_chunk_ordinal,
                        "description": slot.description,
                        "duration_s": slot.duration_s,
                        "loop": slot.loop,
                        "source": slot.source,
                        "accepted": slot.accepted,
                        "note": slot.note,
                        "effect_id": slot.effect_id,
                        "slug": effect.slug if effect else None,
                        "cost_micros": effect.cost_micros if effect else 0,
                    }
                )
            return out

    @app.post("/api/scripts/{script_id}/effects")
    def create_slot(script_id: int, body: SlotIn) -> dict[str, Any]:
        with session_scope(db) as session:
            _script(session, script_id)
            slot = effects_mod.add_slot(
                session,
                script_id,
                body.at_chunk_ordinal,
                body.description,
                body.duration_s,
                body.loop,
                source="manual",
            )
            return {"id": slot.id}

    @app.post("/api/effects/slots/{slot_id}/accept")
    def accept_slot(slot_id: int, accepted: bool = True) -> dict[str, Any]:
        with session_scope(db) as session:
            slot = session.get(EffectSlot, slot_id)
            if slot is None:
                raise HTTPException(404, "No such slot")
            slot.accepted = accepted
            return {"id": slot_id, "accepted": accepted}

    @app.delete("/api/effects/slots/{slot_id}")
    def delete_slot(slot_id: int) -> dict[str, Any]:
        with session_scope(db) as session:
            slot = session.get(EffectSlot, slot_id)
            if slot is not None:
                session.delete(slot)
            return {"deleted": slot_id}

    @app.post("/api/scripts/{script_id}/effects/generate")
    async def generate_effects(script_id: int, body: EffectsGenerateIn) -> dict[str, Any]:
        provider = (
            MockSFXProvider()
            if resolve_provider() == "mock" or body.dry_run
            else ElevenLabsProvider(config)
        )
        try:
            outcomes = await effects_mod.generate_slots(
                db,
                script_id,
                provider,
                config,
                slot_ids=body.slot_ids,
                dry_run=body.dry_run,
            )
        finally:
            await provider.aclose()
        return {
            "outcomes": [asdict(o) for o in outcomes],
            "cost_micros": sum(o.cost_micros for o in outcomes),
        }

    # -- export ------------------------------------------------------------

    @app.post("/api/scripts/{script_id}/export")
    def export(script_id: int, body: ExportIn | None = None) -> dict[str, Any]:
        options = body or ExportIn()
        try:
            result = export_script(
                db,
                script_id,
                config,
                formats=options.formats,
                piece_format=options.piece_format,
                voice_id=options.voice_id,
                variant_label=options.variant_label,
            )
        except NothingToExport as exc:
            raise HTTPException(409, str(exc)) from exc
        except audio.UnknownFormat as exc:
            raise HTTPException(400, str(exc)) from exc
        return _export_payload(result)

    # -- projects in detail, and their media --------------------------------

    @app.get("/api/projects/{project_id}")
    def project_detail(project_id: int) -> dict[str, Any]:
        with session_scope(db) as session:
            project = session.get(Project, project_id)
            if project is None:
                raise HTTPException(404, "No such project")
            scripts = session.scalars(
                select(Script).where(Script.project_id == project_id).order_by(Script.id)
            ).all()
            rollup = ledger.project_totals(session, project_id)
            budget = ledger.project_budget(session, project_id)
            return {
                "id": project.id,
                "name": project.name,
                "voice_id": project.voice_id,
                "model_id": project.model_id,
                "prefix_tags": project.prefix_tags,
                "scripts": [{"id": s.id, "title": s.title} for s in scripts],
                "cost": _rollup_payload(rollup),
                "cap_micros": budget.cap_micros,
                "cap_used_pct": budget.used_pct,
            }

    @app.get("/api/projects/{project_id}/media")
    def project_media_listing(project_id: int) -> dict[str, Any]:
        with session_scope(db) as session:
            try:
                media = media_mod.project_media(session, project_id, config)
            except ValueError as exc:
                raise HTTPException(404, str(exc)) from exc
        return {
            "project_id": media.project_id,
            "project": media.project_name,
            "files": media.file_count,
            "bytes": media.total_bytes,
            "groups": [
                {
                    "title": group.title,
                    "kind": kind,
                    "bytes": group.bytes,
                    "files": [asdict(f) for f in group.files],
                }
                for group, kind in (
                    *[(g, "export") for g in media.exports],
                    (media.effects, "effect"),
                    (media.takes, "take"),
                )
                if group.files
            ],
        }

    @app.get("/api/media/file")
    def media_file(path: str, download: bool = False) -> FileResponse:
        """Stream one artifact, inline for playback or as an attachment.

        `path` is caller-supplied, which is why it goes through
        `resolve_media_path` — without that check this endpoint would serve
        `../../.env` and hand over the API key.
        """
        try:
            resolved = media_mod.resolve_media_path(path, config)
        except media_mod.OutsideMediaRoot as exc:
            raise HTTPException(403, str(exc)) from exc
        if not resolved.is_file():
            raise HTTPException(404, "No such file")
        return FileResponse(
            resolved,
            media_type=media_mod.media_type(resolved),
            # FileResponse handles Range requests either way, which scrubbing
            # an audio element depends on.
            filename=resolved.name if download else None,
        )

    @app.get("/api/exports/{script_id}/archive")
    def export_archive(script_id: int, background: BackgroundTasks) -> FileResponse:
        """Zip one export — audio plus `plan.md` — for downloading in one go."""
        with session_scope(db) as session:
            _script(session, script_id)
            directory = media_mod.export_dir(session, script_id)
            title = _script(session, script_id).title
        if directory is None or not directory.is_dir():
            raise HTTPException(409, "This script has not been exported yet")

        # Built to a temp file and cleaned up afterwards rather than held in
        # memory: a 30-minute episode's export folder is tens of megabytes.
        handle = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        handle.close()
        archive = Path(handle.name)
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for item in sorted(directory.iterdir()):
                if item.is_file() and not item.name.startswith("_"):
                    bundle.write(item, item.name)
        background.add_task(archive.unlink, missing_ok=True)

        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in title).strip("-")
        return FileResponse(
            archive,
            media_type="application/zip",
            filename=f"{safe or f'script-{script_id}'}.zip",
            background=background,
        )

    # -- audio -------------------------------------------------------------

    @app.get("/api/audio/take/{take_id}")
    def take_audio(take_id: int) -> FileResponse:
        with session_scope(db) as session:
            take = session.get(Take, take_id)
            path = Path(take.asset_path) if take and take.asset_path else None
        return _audio_response(path)

    @app.get("/api/audio/effect/{effect_id}")
    def effect_audio(effect_id: int) -> FileResponse:
        with session_scope(db) as session:
            effect = session.get(Effect, effect_id)
            path = Path(effect.asset_path) if effect and effect.asset_path else None
        return _audio_response(path)

    @app.get("/api/voices/registered")
    def list_registered_voices() -> list[dict[str, Any]]:
        """Voices given a local name. Costs nothing and needs no key."""
        with session_scope(db) as session:
            return [
                {
                    "slug": v.slug,
                    "label": v.label,
                    "voice_id": v.voice_id,
                    "category": v.category,
                    "note": v.note,
                    "verified": v.verified,
                }
                for v in voices_mod.all_voices(session)
            ]

    @app.post("/api/voices/registered")
    async def register_voice(body: RegisterVoiceIn) -> dict[str, Any]:
        """Name a voice so it can be used without its id.

        Registering is not creating: a voice cloned in ElevenLabs' own interface
        can be named here, and the tool never has to have made a voice to give
        it a handle. The voice is confirmed against the account first, so a
        mistyped id is caught now rather than at a billed generation.
        """
        label: str | None = None
        category = ""

        if not body.offline and resolve_provider() != "mock" and config.has_api_key:
            provider = ElevenLabsProvider(config)
            try:
                found = await provider.get_voice(body.voice_id)
            finally:
                await provider.aclose()
            if found is None:
                raise HTTPException(
                    404,
                    f"No voice {body.voice_id!r} on this account. Pass offline=true to "
                    "register it anyway.",
                )
            label = found.get("name")
            category = str(found.get("category") or "")

        with session_scope(db) as session:
            try:
                row = voices_mod.register(
                    session,
                    body.voice_id,
                    name=body.name,
                    label=label,
                    category=category,
                    note=body.note,
                    verified=not body.offline,
                    replace=body.replace,
                )
            except voices_mod.BadVoiceName as exc:
                raise HTTPException(400, str(exc)) from exc
            except (voices_mod.VoiceNameTaken, voices_mod.VoiceAlreadyRegistered) as exc:
                raise HTTPException(409, str(exc)) from exc
            return {
                "slug": row.slug,
                "label": row.label,
                "voice_id": row.voice_id,
                "category": row.category,
            }

    @app.delete("/api/voices/registered/{name}")
    def forget_voice(name: str) -> dict[str, str]:
        """Drop a local name. The voice stays on the account."""
        with session_scope(db) as session:
            try:
                row = voices_mod.forget(session, name)
            except voices_mod.UnknownVoice as exc:
                raise HTTPException(404, f"No registered voice called {name!r}.") from exc
            return {"slug": name, "voice_id": row.voice_id}

    @app.get("/api/voices/{voice_id}/preview")
    async def voice_preview(voice_id: str) -> FileResponse:
        """Audition a voice. Costs zero characters — it is a static sample.

        Served from here rather than linked to the provider's CDN because the
        direct link has four separate ways to fail that all look identical in a
        browser: no `preview_url` on the voice, an expired storage URL, a
        cross-origin refusal, and an offline stand-in with no provider at all.
        See [previews.py](previews.py).
        """
        if not previews.is_safe_voice_id(voice_id):
            raise HTTPException(400, "That is not a valid voice id.")

        cached = previews.preview_path(config.assets_dir, voice_id)
        if cached.exists() and cached.stat().st_size > 0:
            return _audio_response(cached)

        if resolve_provider() == "mock" or not config.has_api_key:
            # An offline stand-in auditions as a short tone. Silence would be
            # the obvious choice and is the wrong one: a preview that plays
            # nothing is indistinguishable from one that is broken.
            return _audio_response(previews.synthesize_stub(config.assets_dir, voice_id))

        provider = ElevenLabsProvider(config)
        try:
            voice = await provider.get_voice(voice_id)
        finally:
            await provider.aclose()

        if voice is None:
            raise HTTPException(404, f"No voice {voice_id!r} on this account.")
        url = previews.preview_url_of(voice)
        if not url:
            raise HTTPException(
                404,
                f"{voice.get('name') or voice_id} has no sample on the provider. "
                "Generate a chunk to hear it.",
            )
        try:
            return _audio_response(await previews.fetch_preview(config.assets_dir, voice_id, url))
        except previews.PreviewUnavailable as exc:
            raise HTTPException(502, str(exc)) from exc

    # The built frontend, when there is one.
    if FRONTEND_DIST.is_dir():
        # Static assets first, then a catch-all returning the shell. Without
        # the catch-all, a hard refresh on `/script/1/media` 404s — the browser
        # asks the server for a path only the client router knows about, which
        # is the first thing anyone tries after bookmarking a page.
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str) -> Response:
            # Registered after every `/api/...` route, so those still win. A
            # missing API path must 404 rather than quietly return HTML, or a
            # typo in a fetch looks like a parse error instead of a 404.
            if full_path.startswith("api/"):
                raise HTTPException(404, "No such endpoint")
            direct = (FRONTEND_DIST / full_path).resolve()
            if full_path and direct.is_file() and FRONTEND_DIST.resolve() in direct.parents:
                return FileResponse(direct)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


def _stub_voices() -> list[dict[str, Any]]:
    """Stand-ins so the picker is usable offline and in the demo.

    Marked `mock` so the UI can say plainly that these are not real voices and
    will not produce audio from the provider. They still audition, as a short
    tone — the control has to demonstrably work offline, or there is no way to
    tell a stand-in apart from a bug.
    """
    return [
        {
            "voice_id": f"mock-voice-{n}",
            "name": name,
            "category": "mock",
            "labels": {"accent": accent, "use_case": "narration"},
            "description": "Offline stand-in — no audio will be generated.",
            "preview": True,
            "settings": None,
            "mock": True,
        }
        for n, (name, accent) in enumerate(
            [("Mock Narrator", "neutral"), ("Mock Reader", "british")], start=1
        )
    ]


def _rollup_payload(rollup: ledger.Rollup) -> dict[str, Any]:
    return {
        "operations": rollup.operations,
        "takes": rollup.takes,
        "billed_chars": rollup.billed_chars,
        "cost_micros": rollup.cost_micros,
        "credits": rollup.credits,
        "selected_micros": rollup.selected_micros,
        "wasted_micros": rollup.wasted_micros,
        "waste_pct": rollup.waste_pct,
        "display": fmt_usd(rollup.cost_micros, 4),
        "by_kind": [
            {
                "kind": k.kind,
                "provider": k.provider,
                "unit_kind": k.unit_kind,
                "operations": k.operations,
                "units": k.units,
                "units_display": k.units_display,
                "cost_micros": k.cost_micros,
                "credits": k.credits,
            }
            for k in rollup.by_kind
        ],
    }


def _script(session: Any, script_id: int) -> Script:
    script = session.get(Script, script_id)
    if script is None:
        raise HTTPException(404, f"No script with id {script_id}")
    return script  # type: ignore[no-any-return]


def _audio_response(path: Path | None) -> FileResponse:
    if path is None or not path.exists():
        raise HTTPException(404, "No audio on disk for that item")
    # FileResponse handles Range requests, which <audio> scrubbing needs.
    return FileResponse(path, media_type="audio/mpeg")


def serve(host: str = "127.0.0.1", port: int = 8420, reload: bool = False) -> None:
    import uvicorn

    with contextlib.suppress(KeyboardInterrupt):
        uvicorn.run(create_app(), host=host, port=port, reload=reload, log_level="warning")
