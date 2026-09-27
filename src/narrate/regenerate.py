"""Generating a chunk again, when its take is not good enough.

Two reasons to want this, and the tool treats them differently:

* **Verification flagged it** — a word missing, an extra word, a garble. The
  new take is checked the same way, and the cut moves to it only if it is
  *strictly* better. A re-roll is a fresh sample and can introduce a new defect
  as easily as fix the old one; swapping a known problem for an unknown one is
  not a repair — which is also why a new take whose words nothing could hear
  (no check at all, or the waveform check alone) never counts as better.
* **You did not like it** — a delivery, an emphasis, a pronunciation no check
  can judge. The new take is kept beside the old and the cut stays where it was
  unless the check says the new one is better. Listening is the only way to
  choose between two clean takes, so the choice stays with the listener.

Nothing is ever deleted: every take stays on disk and in the ledger, so a
regeneration that turned out worse costs only its own price, never the take it
was meant to replace.

Spending is guarded the way the rest of the tool guards it:

* the price is shown before anything is sent, and it is computed the way the
  runner computes it — each chunk's own model, voice, tags and settings — so
  the figure confirmed is the figure the run is held to: unless a lower limit is
  given, the quoted maximum *is* the spending limit;
* the monthly cap is never overridden from here;
* a chunk with a request whose outcome is `unknown` — sent, but never answered —
  is refused unless explicitly accepted. That request may already have been
  billed, and regenerating on top of it could pay twice for one line.

Rewording a chunk before regenerating it is allowed only when no other chunk's
request carries this chunk's text. On a model with text conditioning a request
includes its neighbours' words; with request stitching it includes a fingerprint
of the chunks before it. Change one chunk there and its neighbours' requests
change too, so the next ordinary run would generate — and bill — them again.
Dialogue chunks are refused as well: their words live in their speaker turns,
which rewording one block of text cannot update.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from narrate import audio
from narrate import produce as produce_mod
from narrate.db.models import Chunk, Cut, Project, Script, Take
from narrate.db.session import session_scope
from narrate.provider.base import SFXProvider, TTSProvider
from narrate.registry import ModelSpec, Registry, UnknownModel
from narrate.runner import build_jobs, resolve_chunk_config
from narrate.script_parse import parse_script
from narrate.settings import Settings, get_settings
from narrate.verify import service as verify_service
from narrate.verify.compare import FAIL, REVIEW, Finding
from narrate.verify.transcribe import ModelBroken, ModelMissing, SttUnavailable, Transcriber

# A manual regeneration is one attempt; an automatic repair may try again, but
# never more than this, however it is asked. ElevenLabs' own long-form product
# retries "up to twice".
MAX_ATTEMPTS = 3

# How the cut should respond to a new take.
MOVE_BETTER = "better"  # only when the check says the new take is strictly better
MOVE_NEW = "new"  # to the new take unless it is worse
MOVE_NEVER = "never"

# Verdicts that mean a take was actually checked.
_CHECKED = frozenset({"clear", "review", "suspect"})


class RegenerateRefused(RuntimeError):
    """This chunk cannot be regenerated right now, and the message says why."""


class ScriptNotFound(RegenerateRefused):
    """No such script — distinct, so an API can answer 404 rather than 422."""


@dataclass(frozen=True)
class ChunkPlan:
    """What regenerating one chunk would involve, before anything is spent."""

    ordinal: int
    chunk_id: int
    chars: int
    price_micros: int
    model_id: str
    cut_take: int | None
    cut_status: str
    blocker: str | None = None


@dataclass
class ChunkResult:
    ordinal: int
    new_takes: list[int] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    spent_micros: int = 0
    cut_before: int | None = None
    cut_after: int | None = None
    stopped: str | None = None
    # Set when the words were changed first, and a take was made with them.
    reworded: bool = False
    # Set when the words were changed but no take was made with them, so they
    # were put back: the chunk's text always matches something that exists.
    words_restored: bool = False
    # Requests sent but never answered. Each may have been billed, and blocks
    # the next regeneration of the chunk until it is reconciled.
    unknown_takes: list[int] = field(default_factory=list)
    # Why the cut ended where it did, when that was not this module's choice.
    cut_note: str | None = None

    @property
    def moved(self) -> bool:
        return self.cut_after != self.cut_before


def _spec_for(
    chunk: Chunk, script: Script, project: Project | None, registry: Registry
) -> ModelSpec:
    model_id = chunk.model_id or script.model_id or (project.model_id if project else "")
    try:
        return registry.get(model_id)
    except UnknownModel as exc:
        raise RegenerateRefused(f"Chunk {chunk.ordinal}: {exc}") from exc


def plan(
    engine: Engine,
    script_id: int,
    registry: Registry,
    *,
    chunks: list[int] | None = None,
    suspect_only: bool = False,
    texts: dict[int, str] | None = None,
    settings: Settings | None = None,
) -> list[ChunkPlan]:
    """Price and check each chunk to be regenerated. Spends nothing.

    `chunks` of `None` means "every chunk" (or every flagged one, with
    `suspect_only`); an empty list is refused rather than read as "all", because
    an empty selection reaching this far is a caller's mistake, and the mistake
    would be expensive.

    `texts` holds replacement words for some chunks. They are validated here,
    not only when applied, so a refusal arrives with the price rather than after
    the confirmation — and the price is for the words that will actually be sent.
    """
    if chunks is not None and not chunks:
        raise RegenerateRefused("Name at least one chunk to regenerate.")
    config = settings or get_settings()
    with session_scope(engine) as session:
        script = session.get(Script, script_id)
        if script is None:
            raise ScriptNotFound(f"No script {script_id}.")
        project = session.get(Project, script.project_id)
        if project is None:
            raise ScriptNotFound(f"Script {script_id} has no project.")
        cut = {
            c.chunk_id: c.take_id
            for c in session.scalars(select(Cut).where(Cut.script_id == script_id)).all()
        }
        rows = list(
            session.scalars(
                select(Chunk).where(Chunk.script_id == script_id).order_by(Chunk.ordinal)
            ).all()
        )
        known = {c.ordinal for c in rows}
        missing = sorted(set(chunks or []) - known)
        if missing:
            raise RegenerateRefused(
                f"Script {script_id} has no chunk {', '.join(map(str, missing))}."
            )

        out: list[ChunkPlan] = []
        for chunk in rows:
            if chunks and chunk.ordinal not in chunks:
                continue
            cut_take = session.get(Take, cut[chunk.id]) if chunk.id in cut else None
            status = cut_take.verify_status if cut_take else "none"
            if suspect_only and not chunks and status != "suspect":
                continue
            spec = _spec_for(chunk, script, project, registry)
            if texts and chunk.ordinal in texts:
                words = _validated_words(
                    session, script, project, chunk, texts[chunk.ordinal], registry
                )
                try:
                    _, _, _, prefix = resolve_chunk_config(chunk, script, project)
                except ValueError as exc:
                    raise RegenerateRefused(
                        f"Chunk {chunk.ordinal} cannot be generated: {exc}"
                    ) from exc
                chars = len(f"{prefix} {words}".strip() if prefix else words)
                price = spec.cost_micros(chars)
            else:
                chars, price = _priced(session, script, project, chunk, registry, config)
            out.append(
                ChunkPlan(
                    ordinal=chunk.ordinal,
                    chunk_id=chunk.id,
                    chars=chars,
                    price_micros=price,
                    model_id=spec.model_id,
                    cut_take=cut_take.ordinal if cut_take else None,
                    cut_status=status,
                    blocker=_unknown_blocker(chunk, cut_take),
                )
            )
        return out


def _priced(
    session: Session,
    script: Script,
    project: Project,
    chunk: Chunk,
    registry: Registry,
    settings: Settings,
) -> tuple[int, int]:
    """Characters and price of this chunk's request, as the runner will build it.

    Its own model, voice and tags — a chunk overridden to a dearer model or a
    longer tag string is priced as such — and the same expression the runner
    checks against the spending limit, so the two can never disagree.
    """
    try:
        [job] = build_jobs(session, script, project, registry, settings, only=[chunk.ordinal])
    except ValueError as exc:
        raise RegenerateRefused(f"Chunk {chunk.ordinal} cannot be generated: {exc}") from exc
    return job.request.char_count, job.spec.cost_micros(job.request.char_count)


def _unknown_blocker(chunk: Chunk, cut_take: Take | None) -> str | None:
    """A request sent but never answered, newer than the take in the cut."""
    newest_known = cut_take.id if cut_take else 0
    for take in chunk.takes:
        if take.status == "unknown" and take.id > newest_known:
            return (
                f"Chunk {chunk.ordinal} has a request whose outcome was never received "
                f"(take {take.ordinal}). It may already have been billed, so regenerating "
                "now could pay twice for one line. Compare against the provider with "
                "`narrate cost reconcile`; if it was not billed, or you accept the risk, "
                "regenerate with --accept-unknown."
            )
    return None


def _dependants(
    session: Session, script: Script, project: Project, chunk: Chunk, registry: Registry
) -> list[int]:
    """Other chunks whose request would change if this chunk's words did.

    With text conditioning a request carries the words of the chunk on either
    side; with request stitching it carries a fingerprint of the three before it.
    Each chunk's own model decides, because a chunk can be overridden.
    """
    out: list[int] = []
    for other in session.scalars(
        select(Chunk).where(Chunk.script_id == script.id, Chunk.id != chunk.id)
    ).all():
        mode = _spec_for(other, script, project, registry).continuity_mode
        distance = other.ordinal - chunk.ordinal
        if (mode == "text" and abs(distance) == 1) or (
            mode == "request_ids" and 1 <= distance <= 3
        ):
            out.append(other.ordinal)
    return sorted(out)


def _validated_words(
    session: Session,
    script: Script,
    project: Project,
    chunk: Chunk,
    text: str,
    registry: Registry,
) -> str:
    """The words a chunk would be sent, or the reason they cannot be."""
    ordinal = chunk.ordinal
    if chunk.turns_json:
        raise RegenerateRefused(
            f"Chunk {ordinal} is a conversation: its words live in its speaker turns, and "
            "rewording it as one block would send the old lines again. Change the lines "
            "in the script and ingest it as a new script."
        )
    spec = _spec_for(chunk, script, project, registry)
    dependants = _dependants(session, script, project, chunk, registry)
    if spec.continuity_mode != "none" or dependants:
        affected = dependants or [o for o in (ordinal - 1, ordinal + 1) if o > 0]
        listed = ", ".join(str(o) for o in affected)
        raise RegenerateRefused(
            f"Rewording chunk {ordinal} would change what chunk(s) {listed} are asked to "
            "say — their model carries neighbouring text into each request — so the next "
            "ordinary run would generate, and bill, them again. Regenerate the text as it "
            "is, or change it in the script and ingest it as a new script."
        )
    parsed = parse_script(text)
    if parsed.slots or parsed.anchors or parsed.turns or parsed.chapters:
        raise RegenerateRefused(
            "The new text contains a marker (an effect cue, a timing anchor, a speaker or "
            "a chapter). Those change the episode's structure and have to be added to the "
            "script itself, not to one chunk."
        )
    words = parsed.text.strip()
    if not words:
        raise RegenerateRefused("The new text is empty once formatting is removed.")
    if len(words) > spec.max_chars:
        raise RegenerateRefused(
            f"The new text is {len(words):,} characters; {spec.label} accepts at most "
            f"{spec.max_chars:,} in one request."
        )
    return words


def edit_text(engine: Engine, script_id: int, ordinal: int, text: str, registry: Registry) -> str:
    """Replace a chunk's words before regenerating it. Returns the old text.

    The new text goes through the same parser as a script does, so Markdown is
    stripped and audio tags are kept exactly as they would be on ingest.
    """
    with session_scope(engine) as session:
        script = session.get(Script, script_id)
        if script is None:
            raise ScriptNotFound(f"No script {script_id}.")
        project = session.get(Project, script.project_id)
        if project is None:
            raise ScriptNotFound(f"Script {script_id} has no project.")
        chunk = session.scalar(
            select(Chunk).where(Chunk.script_id == script_id, Chunk.ordinal == ordinal)
        )
        if chunk is None:
            raise RegenerateRefused(f"Script {script_id} has no chunk {ordinal}.")
        words = _validated_words(session, script, project, chunk, text, registry)
        old = chunk.text
        chunk.text = words
        return old


def _succeeded_since(engine: Engine, chunk_id: int, newest_before: int) -> list[int]:
    with session_scope(engine) as session:
        return list(
            session.scalars(
                select(Take.id)
                .where(
                    Take.chunk_id == chunk_id,
                    Take.id > newest_before,
                    Take.status == "succeeded",
                )
                .order_by(Take.id)
            ).all()
        )


def _put_back(engine: Engine, chunk_id: int, text: str) -> None:
    """Restore a chunk's words after a rewording that produced no take."""
    with session_scope(engine) as session:
        chunk = session.get(Chunk, chunk_id)
        if chunk is not None:
            chunk.text = text


def _rank(status: str, findings: list[Finding]) -> tuple[int, int, int]:
    """Lower is better: fails, then reviews, then whether it was checked at all."""
    fails = sum(1 for f in findings if f.severity == FAIL)
    reviews = sum(1 for f in findings if f.severity == REVIEW)
    unchecked = 0 if status in _CHECKED else 1
    return (fails, reviews, unchecked)


def _take_rank(take: Take) -> tuple[int, int, int]:
    return _rank(take.verify_status, verify_service.findings_of(take))


async def regenerate(
    engine: Engine,
    script_id: int,
    chunks: list[int],
    tts: TTSProvider,
    sfx: SFXProvider,
    registry: Registry,
    settings: Settings,
    *,
    transcriber: Transcriber | None = None,
    attempts: int = 1,
    max_spend_micros: int | None = None,
    move_cut: str = MOVE_BETTER,
    accept_unknown: bool = False,
    texts: dict[int, str] | None = None,
    on_event: Callable[[str], None] | None = None,
) -> list[ChunkResult]:
    """Generate each chunk again, check the new take, and move the cut if earned.

    `attempts` above one means: if the new take is still suspect, try again —
    each try is a new, separately billed request, stopping as soon as a take
    checks clean. Unless a lower `max_spend_micros` is given, the quoted maximum
    (every chunk's price, times the attempts) is the limit, so nothing can cost
    more than the figure that was confirmed.

    `texts` rewords chunks first. The new words are kept only if a take is made
    with them. If none is — the limit, the monthly cap, a failed or unanswered
    request, an interruption — the old words are put back and
    `ChunkResult.words_restored` says so. Leaving them would make the chunk's
    text disagree with every take it has: the next ordinary run would price and
    send the new words, and after an unanswered request that is a second charge
    for the same line.
    """
    attempts = max(1, min(attempts, MAX_ATTEMPTS))

    def note(message: str) -> None:
        if on_event:
            on_event(message)

    plans = plan(engine, script_id, registry, chunks=chunks, texts=texts, settings=settings)
    runnable = [p for p in plans if accept_unknown or not p.blocker]
    quoted = sum(p.price_micros for p in runnable) * attempts
    remaining = quoted if max_spend_micros is None else min(max_spend_micros, quoted)
    results: list[ChunkResult] = []

    for chunk_plan in plans:
        result = ChunkResult(ordinal=chunk_plan.ordinal)
        results.append(result)
        if chunk_plan.blocker and not accept_unknown:
            result.stopped = chunk_plan.blocker
            note(chunk_plan.blocker)
            continue

        with session_scope(engine) as session:
            cut = session.get(Cut, chunk_plan.chunk_id)
            result.cut_before = cut.take_id if cut else None
            newest_before = (
                session.scalar(
                    select(func.max(Take.id)).where(Take.chunk_id == chunk_plan.chunk_id)
                )
                or 0
            )

        original: str | None = None
        if texts and chunk_plan.ordinal in texts and remaining >= chunk_plan.price_micros:
            original = edit_text(
                engine, script_id, chunk_plan.ordinal, texts[chunk_plan.ordinal], registry
            )
        try:
            await _attempt(
                engine,
                script_id,
                chunk_plan,
                result,
                tts,
                sfx,
                registry,
                settings,
                transcriber,
                attempts,
                remaining,
                on_event,
            )
        finally:
            remaining -= result.spent_micros
            if original is not None:
                # An interruption can land after the runner committed a paid take
                # but before it was reported here. The database is the record:
                # a take with the new words exists, so the words stay.
                result.new_takes += [
                    t
                    for t in _succeeded_since(engine, chunk_plan.chunk_id, newest_before)
                    if t not in result.new_takes
                ]
                if result.new_takes:
                    result.reworded = True
                else:
                    _put_back(engine, chunk_plan.chunk_id, original)
                    result.words_restored = True

        _settle_cut(engine, chunk_plan.chunk_id, result, move_cut)
        if result.moved:
            note(f"chunk {chunk_plan.ordinal}: the cut now uses the new take")
    return results


async def _attempt(
    engine: Engine,
    script_id: int,
    chunk_plan: ChunkPlan,
    result: ChunkResult,
    tts: TTSProvider,
    sfx: SFXProvider,
    registry: Registry,
    settings: Settings,
    transcriber: Transcriber | None,
    attempts: int,
    remaining: int,
    on_event: Callable[[str], None] | None,
) -> None:
    """Generate and check one chunk, trying again while the new take is suspect.

    Everything it learns goes on `result`, so the caller can still settle the
    words and the cut if this is interrupted part-way.
    """

    def note(message: str) -> None:
        if on_event:
            on_event(message)

    for attempt in range(1, attempts + 1):
        if remaining < chunk_plan.price_micros:
            result.stopped = "the spending limit for this regeneration was reached"
            return
        note(f"chunk {chunk_plan.ordinal}: generating (attempt {attempt} of {attempts})")
        report = await produce_mod.produce(
            engine,
            script_id,
            tts,
            sfx,
            registry,
            settings,
            with_effects=False,
            only=[chunk_plan.ordinal],
            dry_run=False,
            force=True,
            max_spend_micros=remaining,
            override_cap=False,
            on_event=on_event,
        )
        if report.blocked:
            result.stopped = (
                "the project's monthly cap blocked it — raise it with "
                "`narrate project set <project> --monthly-cap`"
            )
            break
        spent = report.spent_micros
        result.spent_micros += spent
        remaining -= spent
        made = [o for o in (report.speech.outcomes if report.speech else []) if o.take_id]
        result.unknown_takes += [o.take_id for o in made if o.status == "unknown" and o.take_id]
        new = [o for o in made if o.status == "succeeded"]
        if not new:
            failure = next((o for o in made if o.error), None)
            result.stopped = (
                failure.error if failure and failure.error else "the request did not succeed"
            )
            break

        take_id = new[-1].take_id
        assert take_id is not None  # filtered above
        result.new_takes.append(take_id)
        # Off the event loop: transcription is seconds of CPU, and in the web
        # server it would otherwise freeze every other request meanwhile.
        status = await asyncio.to_thread(_check, engine, script_id, take_id, transcriber, registry)
        result.statuses.append(status)
        note(f"chunk {chunk_plan.ordinal}: new take checked — {status}")
        if status != "suspect":
            break


def _check(
    engine: Engine,
    script_id: int,
    take_id: int,
    transcriber: Transcriber | None,
    registry: Registry,
) -> str:
    """Verify one freshly made take. Never raises: the take is already paid for.

    A speech model that will not load, or audio ffmpeg cannot decode, must not
    turn a successful, billed generation into a crash that hides what was spent.
    The take is recorded as not checked, and the cut rules treat it that way.
    """
    try:
        checked = verify_service.verify_script(
            engine, script_id, transcriber, registry, take_ids=[take_id], recheck=True
        )
    except (SttUnavailable, ModelMissing, ModelBroken, audio.FFmpegFailed, RuntimeError, OSError):
        return "error"
    return checked[0].status if checked else "unverified"


def _settle_cut(engine: Engine, chunk_id: int, result: ChunkResult, move_cut: str) -> None:
    """Point the cut at the best take by the rule asked for, and record the truth.

    The runner itself selects a new take in two situations — the chunk had no cut
    at all, or the cut's audio file was missing — because there is nothing else
    to play. That choice is kept (putting back a reference to missing audio would
    be worse), but it is reported as the move it is, even under `never`.
    """
    with session_scope(engine) as session:
        cut = session.get(Cut, chunk_id)
        current = session.get(Take, cut.take_id) if cut else None
        if (
            current is not None
            and current.id in result.new_takes
            and current.id != result.cut_before
        ):
            result.cut_note = "the chunk had no playable take in the cut, so the new one was used"

        candidates = [t for t in (session.get(Take, i) for i in result.new_takes) if t]
        # Only a take whose words were heard can be shown to be better. One
        # nothing could check is not evidence of anything, and neither is one
        # that only had the waveform check: that check cannot hear a missing or
        # an extra word, and the burst it flags is itself the shape of a garble.
        if move_cut == MOVE_BETTER:
            candidates = [
                t
                for t in candidates
                if t.verify_status in _CHECKED and verify_service.heard_by_transcript(t.verifier)
            ]

        if move_cut != MOVE_NEVER and candidates:
            best = min(candidates, key=lambda t: (_take_rank(t), -t.id))
            if current is None:
                chosen: Take = best
            elif move_cut == MOVE_NEW:
                chosen = best if _take_rank(best) <= _take_rank(current) else current
            else:
                chosen = best if _take_rank(best) < _take_rank(current) else current
            if chosen is not current:
                if cut is None:
                    chunk = session.get(Chunk, chunk_id)
                    assert chunk is not None
                    session.add(
                        Cut(chunk_id=chunk_id, script_id=chunk.script_id, take_id=chosen.id)
                    )
                else:
                    cut.take_id = chosen.id
                current = chosen

        result.cut_after = current.id if current else None
