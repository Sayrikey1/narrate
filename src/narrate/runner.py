"""Batch generation (PRD F5) — the correctness core.

Three properties this module exists to guarantee:

**No duplicate charges.** Every request carries a locally-derived idempotency
key. A key that already has a succeeded take is skipped rather than resent. The
API has no idempotency header of its own, so this is the only line of defence,
and PRD §11 rates a duplicate charge as direct financial loss.

**Partial failure loses nothing.** Completed takes are committed as they land,
not at the end of the run. Re-running resumes from what is missing.

**Retries are classified by billing implication, not by status code.** A
request that was rejected before generating is safe to retry; one whose
response was never read is not, because it may already have been billed. The
latter is recorded as `unknown` and left for a human.

On concurrency: models where request stitching works generate *sequentially*,
because chunk N conditions on the request id returned by chunk N-1. That is a
real cost — a 4-chunk v2 run is serial where a 13-chunk v3 run is parallel —
but prosodic continuity across boundaries is the reason to use stitching at
all, and there is no way to have both.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from narrate.archive import ensure_live
from narrate.audio import FFmpegFailed, FFmpegMissing, duration_seconds
from narrate.db.models import Chunk, Cut, Project, Run, Script, Take, utcnow
from narrate.db.session import session_scope
from narrate.ledger import Budget, project_budget, record_generation
from narrate.provider.base import (
    DialogueRequest,
    DialogueTurn,
    FatalError,
    ProviderError,
    RetryableError,
    TTSProvider,
    TTSRequest,
    UnknownOutcomeError,
    dialogue_idempotency_key,
    idempotency_key,
)
from narrate.registry import ModelSpec, Registry
from narrate.settings import Settings

MAX_ATTEMPTS = 4
BASE_BACKOFF_S = 1.0
MAX_BACKOFF_S = 30.0

# How much surrounding text to pass as context on models without stitching.
# Enough to establish cadence, short enough that it cannot dominate the request.
CONTEXT_CHARS = 400


@dataclass
class ChunkOutcome:
    ordinal: int
    chunk_id: int
    status: str
    take_id: int | None = None
    cost_micros: int = 0
    billed_chars: int = 0
    attempts: int = 1
    error: str | None = None


@dataclass
class RunReport:
    run_id: int | None
    dry_run: bool
    model_id: str
    sequential: bool
    outcomes: list[ChunkOutcome] = field(default_factory=list)
    stopped_reason: str | None = None
    budget: Budget | None = None

    @property
    def spent_micros(self) -> int:
        return sum(o.cost_micros for o in self.outcomes)

    @property
    def succeeded(self) -> list[ChunkOutcome]:
        return [o for o in self.outcomes if o.status == "succeeded"]

    @property
    def failed(self) -> list[ChunkOutcome]:
        return [o for o in self.outcomes if o.status in ("failed", "unknown")]

    @property
    def skipped(self) -> list[ChunkOutcome]:
        return [o for o in self.outcomes if o.status == "skipped_duplicate"]


@dataclass
class _Job:
    chunk_id: int
    ordinal: int
    request: TTSRequest | DialogueRequest
    key: str
    spec: ModelSpec

    @property
    def is_dialogue(self) -> bool:
        return isinstance(self.request, DialogueRequest)

    def cost_micros(self, chars: int) -> int:
        """What this job costs for `chars` characters.

        Dialogue may be priced differently and the rate card says so —
        undocumented, so the multiplier defaults to 1.0 and is labelled
        unverified rather than guessed at.
        """
        return (
            self.spec.dialogue_cost_micros(chars)
            if self.is_dialogue
            else self.spec.cost_micros(chars)
        )


def resolve_chunk_config(
    chunk: Chunk, script: Script, project: Project
) -> tuple[str, str, dict[str, Any], str]:
    """Most specific wins: chunk override, then script, then project profile."""
    model_id = chunk.model_id or script.model_id or project.model_id
    voice_id = chunk.voice_id or script.voice_id or project.voice_id
    if not voice_id:
        raise ValueError(
            f"No voice set for chunk {chunk.ordinal}. "
            "Set one on the project with `narrate project set --voice <id>`."
        )
    raw = chunk.settings_json or project.settings_json or "{}"
    settings: dict[str, Any] = json.loads(raw)
    prefix = chunk.prefix_tags if chunk.prefix_tags is not None else project.prefix_tags
    return model_id, voice_id, settings, prefix or ""


def build_jobs(
    session: Any,
    script: Script,
    project: Project,
    registry: Registry,
    settings: Settings,
    only: list[int] | None = None,
    voice_overrides: dict[int, dict[str, Any]] | None = None,
) -> list[_Job]:
    """Turn chunks into fully-formed requests, continuity included.

    `voice_overrides` maps a chunk ordinal to voice settings laid over its own
    for this run only — a regeneration trying a steadier delivery. Nothing is
    stored on the chunk: the take records the request it was made from, and the
    next ordinary run still finds the chunk's own take by its own request.
    """
    chunks = list(
        session.scalars(
            select(Chunk).where(Chunk.script_id == script.id).order_by(Chunk.ordinal)
        ).all()
    )
    if not chunks:
        raise ValueError("Script has no chunks. Run `narrate chunk <script>` first.")

    texts = {c.ordinal: c.text for c in chunks}
    jobs: list[_Job] = []

    for chunk in chunks:
        if only and chunk.ordinal not in only:
            continue

        model_id, voice_id, raw_settings, prefix = resolve_chunk_config(chunk, script, project)
        if voice_overrides and chunk.ordinal in voice_overrides:
            raw_settings = {**raw_settings, **voice_overrides[chunk.ordinal]}
        spec = registry.get(model_id)

        if prefix and not spec.audio_tags:
            raise ValueError(
                f"{spec.label} does not support audio tags, but chunk {chunk.ordinal} "
                f"has prefix tags {prefix!r}. Clear the tags or pick a model that "
                "supports them (`narrate models`)."
            )

        text = f"{prefix} {chunk.text}".strip() if prefix else chunk.text

        # Text context is safe to compute up front. Request-id context cannot
        # be — it only exists once the prior chunk has generated — so the
        # sequential pass fills in the ids while the *fingerprint* computed
        # here keeps the idempotency key stable across runs.
        previous_text = next_text = None
        fingerprint = ""
        mode = spec.continuity_mode
        if mode == "text":
            prior = texts.get(chunk.ordinal - 1)
            following = texts.get(chunk.ordinal + 1)
            previous_text = prior[-CONTEXT_CHARS:] if prior else None
            next_text = following[:CONTEXT_CHARS] if following else None
        elif mode == "request_ids":
            fingerprint = _continuity_fingerprint(texts, chunk.ordinal)
        # mode == "none": send no context. v3 rejects both mechanisms outright.

        # A chunk that carries speaker turns is one request in several voices,
        # so it goes to the dialogue endpoint instead. Ingestion only writes
        # `turns_json` for a model that can do it, and only when asked.
        dialogue_request = _dialogue_request_for(chunk, spec, raw_settings, settings)
        if dialogue_request is not None:
            jobs.append(
                _Job(
                    chunk_id=chunk.id,
                    ordinal=chunk.ordinal,
                    request=dialogue_request,
                    key=dialogue_idempotency_key(dialogue_request),
                    spec=spec,
                )
            )
            continue

        request = TTSRequest(
            text=text,
            voice_id=voice_id,
            model_id=model_id,
            settings=spec.filter_settings(raw_settings),
            output_format=settings.output_format,
            previous_text=previous_text,
            next_text=next_text,
            continuity_fingerprint=fingerprint,
        )
        jobs.append(
            _Job(
                chunk_id=chunk.id,
                ordinal=chunk.ordinal,
                request=request,
                key=idempotency_key(request),
                spec=spec,
            )
        )
    return jobs


def _dialogue_request_for(
    chunk: Chunk, spec: ModelSpec, raw_settings: dict[str, Any], settings: Settings
) -> DialogueRequest | None:
    """A `DialogueRequest` for a chunk that holds several speakers, else None.

    Returns None — rather than raising — when the model cannot do dialogue, so
    a project switched from v3 to another model still generates. The chunk's
    concatenated text is then read in the lead voice, which is a downgrade in
    performance and never a failure or a surprise charge.
    """
    if not chunk.turns_json or not spec.dialogue:
        return None
    try:
        raw = json.loads(chunk.turns_json)
    except json.JSONDecodeError:
        return None
    turns = tuple(
        DialogueTurn(
            text=str(item.get("text", "")),
            voice_id=str(item.get("voice_id", "")),
            speaker=str(item.get("speaker", "")),
        )
        for item in raw
        if item.get("text") and item.get("voice_id")
    )
    if len(turns) < 2:
        return None
    return DialogueRequest(
        turns=turns,
        model_id=spec.model_id,
        settings=spec.filter_settings(raw_settings),
        output_format=settings.output_format,
    )


def _continuity_fingerprint(texts: dict[int, str], ordinal: int) -> str:
    """Hash the chunks this one will be conditioned on.

    Request stitching passes at most three preceding request ids, so the
    audio this generation depends on is exactly the three chunks before it.
    Hashing their text gives a value that is identical across runs — so a
    resume matches — but changes the moment an earlier chunk is edited.
    """
    preceding = [texts[o] for o in range(max(1, ordinal - 3), ordinal) if o in texts]
    if not preceding:
        return ""
    return hashlib.sha256("\x00".join(preceding).encode("utf-8")).hexdigest()[:16]


def existing_take(session: Session, chunk_id: int, key: str) -> Take | None:
    """A prior succeeded take for this exact request — the resume primitive.

    A take only counts if its audio is still on disk. Pruned or deleted assets
    would otherwise leave the run believing the chunk is done while export has
    nothing to stitch, and the only way out would be `--force` on the whole
    script. Skipping the record instead lets the missing chunk regenerate on
    its own — and the original take stays in the ledger, because it was
    genuinely billed.
    """
    candidates = session.scalars(
        select(Take)
        .where(Take.chunk_id == chunk_id, Take.idempotency_key == key, Take.status == "succeeded")
        .order_by(Take.ordinal.desc())
    ).all()
    for take in candidates:
        if take.asset_path and Path(take.asset_path).exists():
            return take
    return None


async def _attempt(
    provider: TTSProvider, job: _Job, on_note: Callable[[str], None] | None = None
) -> tuple[Any, int]:
    """Call the provider, retrying only what is provably safe to retry."""
    attempt = 0
    while True:
        attempt += 1
        try:
            if isinstance(job.request, DialogueRequest):
                generate_dialogue = getattr(provider, "generate_dialogue", None)
                if generate_dialogue is None:
                    raise FatalError(
                        f"{getattr(provider, 'name', 'provider')} cannot generate dialogue. "
                        "Re-ingest the script without --dialogue."
                    )
                return await generate_dialogue(job.request), attempt
            return await provider.synthesize(job.request), attempt
        except UnknownOutcomeError:
            # Never retried. It may already have been billed.
            raise
        except RetryableError as exc:
            if attempt >= MAX_ATTEMPTS:
                raise
            delay = exc.retry_after_s
            if delay is None:
                # Exponential backoff with full jitter — the provider frames
                # its limit as concurrency, so a thundering retry is worse
                # than a slow one.
                delay = random.uniform(0, min(MAX_BACKOFF_S, BASE_BACKOFF_S * 2**attempt))
            if on_note:
                on_note(f"chunk {job.ordinal}: retry {attempt}/{MAX_ATTEMPTS} in {delay:.1f}s")
            await asyncio.sleep(delay)
        except FatalError:
            raise


def _asset_path(
    settings: Settings, project_name: str, script_id: int, ordinal: int, take_no: int
) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in project_name)[:40]
    return (
        settings.assets_dir
        / safe
        / f"script-{script_id}"
        / f"chunk-{ordinal:03d}-take-{take_no:02d}.mp3"
    )


def _free_path(path: Path) -> Path:
    """`path`, or the first free variant of it — never a file that exists.

    A take's name is its chunk's position and its number, and positions move:
    an episode replaced by a new upload renumbers its chunks, and a chunk moved
    aside keeps its files. Writing over an existing name would replace paid
    audio another take still points at.
    """
    if not path.exists():
        return path
    for n in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"No free name for {path}")


async def generate(
    engine: Engine,
    script_id: int,
    provider: TTSProvider,
    registry: Registry,
    settings: Settings,
    *,
    only: list[int] | None = None,
    dry_run: bool = True,
    force: bool = False,
    max_spend_micros: int | None = None,
    concurrency: int | None = None,
    override_cap: bool = False,
    extra_projected_micros: int = 0,
    on_event: Callable[[str], None] | None = None,
    voice_overrides: dict[int, dict[str, Any]] | None = None,
) -> RunReport:
    """Generate every chunk of a script that does not already have a take.

    `extra_projected_micros` is spend this run will incur outside the speech
    phase — the effects a combined production is about to generate. It is
    added to the monthly-cap projection so the gate sees the whole run
    rather than half of it: a run that fits under the cap on narration alone
    can still take the month past it once the cues are counted.
    """

    def note(message: str) -> None:
        if on_event:
            on_event(message)

    with session_scope(engine) as session:
        script = session.get(Script, script_id)
        if script is None:
            raise ValueError(f"No script with id {script_id}.")
        project = session.get(Project, script.project_id)
        if project is None:
            raise ValueError(f"Script {script_id} has no project.")
        if not dry_run:
            ensure_live(session, script)
        jobs = build_jobs(session, script, project, registry, settings, only, voice_overrides)

        # Resume: anything already generated with an identical request is done.
        pending: list[_Job] = []
        report = RunReport(
            run_id=None,
            dry_run=dry_run,
            model_id=jobs[0].spec.model_id if jobs else project.model_id,
            sequential=bool(jobs) and jobs[0].spec.continuity_mode == "request_ids",
        )
        for job in jobs:
            prior = None if force else existing_take(session, job.chunk_id, job.key)
            if prior is not None:
                report.outcomes.append(
                    ChunkOutcome(
                        ordinal=job.ordinal,
                        chunk_id=job.chunk_id,
                        status="skipped_duplicate",
                        take_id=prior.id,
                    )
                )
                continue
            pending.append(job)

        project_id = project.id
        project_name = project.name

    if not pending:
        note("Every chunk already has a take for these exact settings. Nothing to generate.")
        return report

    projected = (
        sum(j.spec.cost_micros(j.request.char_count) for j in pending) + extra_projected_micros
    )
    with session_scope(engine) as session:
        budget = project_budget(session, project_id, projected)
    report.budget = budget

    # F10: warn at 80% of the monthly cap, block at 100% pending an override.
    # Checked before the run starts so nothing is spent on a run that cannot
    # legitimately finish.
    if budget.blocked and not override_cap:
        # `report.budget` carries the numbers; the caller decides how to present
        # them, so no prose here — the CLI would otherwise print it twice.
        report.stopped_reason = "monthly_cap"
        return report
    if budget.warn:
        note(
            f"Warning: this run takes {project_name} to {budget.projected_pct}% of its monthly cap."
        )

    if dry_run:
        note(f"Dry run: {len(pending)} chunk(s) would be generated. Nothing was sent.")
        for job in pending:
            report.outcomes.append(
                ChunkOutcome(
                    ordinal=job.ordinal,
                    chunk_id=job.chunk_id,
                    status="would_generate",
                    cost_micros=job.spec.cost_micros(job.request.char_count),
                    billed_chars=job.request.char_count,
                )
            )
        return report

    # -- from here on, characters get spent -------------------------------

    with session_scope(engine) as session:
        run = Run(
            script_id=script_id,
            model_id=report.model_id,
            status="running",
            estimate_micros=sum(j.spec.cost_micros(j.request.char_count) for j in pending),
            concurrency=1 if report.sequential else (concurrency or settings.concurrency),
            max_spend_micros=max_spend_micros,
            dry_run=False,
        )
        session.add(run)
        session.flush()
        run_id = run.id
    report.run_id = run_id

    spent = 0
    db_lock = asyncio.Lock()
    stop = asyncio.Event()

    async def persist(job: _Job, result: Any, attempts: int) -> ChunkOutcome:
        nonlocal spent
        async with db_lock:
            with session_scope(engine) as session:
                take_no = (
                    session.scalar(
                        select(Take.ordinal)
                        .where(Take.chunk_id == job.chunk_id)
                        .order_by(Take.ordinal.desc())
                        .limit(1)
                    )
                    or 0
                ) + 1

                # The provider's own figure wins; the estimate is the fallback.
                billed = result.billed_chars
                source = "header"
                if billed is None:
                    billed = job.request.char_count
                    source = "estimated"

                path = _free_path(
                    _asset_path(settings, project_name, script_id, job.ordinal, take_no)
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(result.audio)

                # Measured here rather than at export so runtime is known per
                # take, which is what cost-per-minute divides by. ffmpeg being
                # absent must not fail a generation that already succeeded and
                # was already billed.
                try:
                    duration = duration_seconds(path)
                except (FFmpegMissing, FFmpegFailed):
                    duration = None

                take = Take(
                    chunk_id=job.chunk_id,
                    run_id=run_id,
                    ordinal=take_no,
                    idempotency_key=job.key,
                    request_id=result.request_id,
                    model_id=job.spec.model_id,
                    voice_id=job.request.voice_id,
                    settings_json=json.dumps(job.request.settings, sort_keys=True),
                    output_format=job.request.output_format,
                    submitted_text=job.request.text,
                    submitted_chars=job.request.char_count,
                    billed_chars=billed,
                    cost_source=source,
                    rate_usd_per_1k=job.spec.usd_per_1k,
                    rate_card_version=registry.rate_card_version,
                    voices_json=(
                        json.dumps(list(job.request.voice_ids))
                        if isinstance(job.request, DialogueRequest)
                        else None
                    ),
                    cost_micros=job.cost_micros(billed),
                    credits=job.spec.credits(billed),
                    asset_path=str(path),
                    duration_s=duration,
                    status="succeeded",
                    attempts=attempts,
                )
                session.add(take)
                session.flush()

                record_generation(
                    session, take, project_id=project_id, script_id=script_id, run_id=run_id
                )

                # First take for a chunk becomes the cut automatically; later
                # ones never displace a deliberate choice — unless that choice
                # points at audio that no longer exists, which is a dangling
                # reference rather than a decision worth preserving.
                cut = session.get(Cut, job.chunk_id)
                if cut is None:
                    session.add(Cut(chunk_id=job.chunk_id, script_id=script_id, take_id=take.id))
                else:
                    selected = session.get(Take, cut.take_id)
                    missing = (
                        selected is None
                        or not selected.asset_path
                        or not Path(selected.asset_path).exists()
                    )
                    if missing:
                        cut.take_id = take.id

                spent += take.cost_micros
                outcome = ChunkOutcome(
                    ordinal=job.ordinal,
                    chunk_id=job.chunk_id,
                    status="succeeded",
                    take_id=take.id,
                    cost_micros=take.cost_micros,
                    billed_chars=billed,
                    attempts=attempts,
                )
        return outcome

    async def persist_failure(
        job: _Job, exc: ProviderError, status: str, attempts: int
    ) -> ChunkOutcome:
        async with db_lock:
            with session_scope(engine) as session:
                take_no = (
                    session.scalar(
                        select(Take.ordinal)
                        .where(Take.chunk_id == job.chunk_id)
                        .order_by(Take.ordinal.desc())
                        .limit(1)
                    )
                    or 0
                ) + 1
                take = Take(
                    chunk_id=job.chunk_id,
                    run_id=run_id,
                    ordinal=take_no,
                    idempotency_key=job.key,
                    request_id=exc.request_id,
                    model_id=job.spec.model_id,
                    voice_id=job.request.voice_id,
                    settings_json=json.dumps(job.request.settings, sort_keys=True),
                    output_format=job.request.output_format,
                    submitted_text=job.request.text,
                    submitted_chars=job.request.char_count,
                    billed_chars=0,
                    cost_source="none",
                    rate_usd_per_1k=job.spec.usd_per_1k,
                    rate_card_version=registry.rate_card_version,
                    cost_micros=0,
                    status=status,
                    error_json=json.dumps(exc.as_dict()),
                    attempts=attempts,
                )
                session.add(take)
                session.flush()
                # An `unknown` take may have been billed, so it is written to
                # the ledger at zero cost with a note rather than omitted —
                # reconciliation needs to see that something happened here.
                if status == "unknown":
                    record_generation(
                        session,
                        take,
                        project_id=project_id,
                        script_id=script_id,
                        run_id=run_id,
                        note="outcome unknown — may have been billed; "
                        "verify against provider usage",
                    )
        return ChunkOutcome(
            ordinal=job.ordinal,
            chunk_id=job.chunk_id,
            status=status,
            take_id=take.id,
            attempts=attempts,
            error=exc.message,
        )

    # The monthly cap constrains the run as well as gating its start: a
    # long run can cross the cap partway through, and stopping then is the
    # whole point of a cap.
    monthly_headroom = None if override_cap else budget.remaining_micros
    limits = [x for x in (max_spend_micros, monthly_headroom) if x is not None]
    run_ceiling: int | None = min(limits) if limits else None
    ceiling_reason = (
        "budget" if run_ceiling is None or run_ceiling == max_spend_micros else "monthly_cap"
    )

    def would_exceed_budget(job: _Job) -> bool:
        if run_ceiling is None:
            return False
        return spent + job.spec.cost_micros(job.request.char_count) > run_ceiling

    async def run_one(job: _Job) -> ChunkOutcome | None:
        if stop.is_set():
            return None
        if would_exceed_budget(job):
            stop.set()
            report.stopped_reason = ceiling_reason
            which = "monthly cap" if ceiling_reason == "monthly_cap" else "run's spending cap"
            note(f"Stopped before chunk {job.ordinal}: the {which} would be exceeded.")
            return None
        try:
            result, attempts = await _attempt(provider, job, note)
        except UnknownOutcomeError as exc:
            note(f"chunk {job.ordinal}: OUTCOME UNKNOWN — {exc.message}")
            return await persist_failure(job, exc, "unknown", MAX_ATTEMPTS)
        except ProviderError as exc:
            note(f"chunk {job.ordinal}: failed — {exc.message}")
            if isinstance(exc, FatalError) and exc.status == 402:
                stop.set()
                report.stopped_reason = "credits"
            return await persist_failure(job, exc, "failed", 1)
        outcome = await persist(job, result, attempts)
        note(f"chunk {job.ordinal}: ok ({outcome.billed_chars} chars billed)")
        return outcome

    if report.sequential:
        # Stitching: each request conditions on the ids of the ones before it.
        recent_ids: list[str] = []
        for job in pending:
            # Dialogue never takes this path in practice — stitching needs
            # `request_stitching`, and the only model that can do dialogue is
            # v3, which has neither stitching nor text conditioning. Stated
            # rather than assumed, because the two capabilities are read from a
            # config file and a future model could have both.
            if recent_ids and isinstance(job.request, TTSRequest):
                job.request = replace(job.request, previous_request_ids=tuple(recent_ids[-3:]))
                job.key = idempotency_key(job.request)
            outcome = await run_one(job)
            if outcome is None:
                break
            report.outcomes.append(outcome)
            if outcome.status == "succeeded" and outcome.take_id is not None:
                with session_scope(engine) as session:
                    take = session.get(Take, outcome.take_id)
                    if take and take.request_id:
                        recent_ids.append(take.request_id)
    else:
        semaphore = asyncio.Semaphore(concurrency or settings.concurrency)

        async def guarded(job: _Job) -> ChunkOutcome | None:
            async with semaphore:
                return await run_one(job)

        results = await asyncio.gather(*(guarded(j) for j in pending))
        report.outcomes.extend(o for o in results if o is not None)

    with session_scope(engine) as session:
        finished = session.get(Run, run_id)
        if finished is not None:
            finished.spent_micros = spent
            finished.finished_at = utcnow()
            if report.stopped_reason in ("budget", "monthly_cap"):
                finished.status = "stopped_by_budget"
            elif report.failed:
                finished.status = "failed"
            else:
                finished.status = "completed"

    return report
