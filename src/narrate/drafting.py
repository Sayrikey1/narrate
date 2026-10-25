"""Drafting with a language model: titles, thumbnail briefs, effect suggestions.

Each is priced before anything is sent, charged to the ledger before its
results are looked at — spent is spent, whether or not the draft is any good —
and stored unchosen, so a proposal never becomes what ships until a person
picks it. The command line and the web page both call these, so the two cannot
price, charge or store a draft differently.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from narrate import copywrite, ledger, llm, publish
from narrate.db.models import Chunk, Script, ThumbnailBrief
from narrate.db.session import session_scope
from narrate.effects import add_slot
from narrate.llm import TokenUsage
from narrate.settings import Settings
from narrate.suggest import SYSTEM_PROMPT, Suggestion, suggest_for_chunks

# Rough output per request, so a quote covers the answer as well as the
# question. Estimates only: the charge is always what the provider reports.
OUTPUT_TOKENS = {"titles": 700, "package": 500, "briefs": 700, "effects": 220}


class NoGroqKey(RuntimeError):
    pass


class NothingToDraftFrom(RuntimeError):
    pass


@dataclass(frozen=True)
class Quote:
    kind: str
    requests: int
    input_tokens: int
    output_tokens: int
    model: str

    @property
    def micros(self) -> int:
        return llm.cost_micros(self.model, self.input_tokens, self.output_tokens)


@dataclass
class Drafted:
    """What a draft produced and what it cost."""

    kind: str
    stored: int = 0
    usage: TokenUsage = field(default_factory=TokenUsage)
    errors: list[str] = field(default_factory=list)
    description_written: bool = False
    suggestions: list[Suggestion] = field(default_factory=list)

    @property
    def cost_micros(self) -> int:
        return self.usage.cost_micros


def episode_brief(session: Session, script_id: int) -> str:
    """What the model is told about the episode.

    Deliberately not the whole script. The free tier allows 8,000 tokens per
    minute and a 20,000-character episode exceeds that on its own — but a title
    does not need every word, it needs the shape. The chapter names *are* that
    shape when they exist, which is why they go first.
    """
    script = session.get(Script, script_id)
    if script is None:
        raise ValueError(f"No script {script_id}.")
    chunks = list(
        session.scalars(
            select(Chunk).where(Chunk.script_id == script_id).order_by(Chunk.ordinal)
        ).all()
    )
    chapters = [c.chapter_title for c in chunks if c.chapter_title]
    parts = [f"Working title: {script.title}"]
    if chapters:
        parts.append("Sections:\n" + "\n".join(f"- {c}" for c in chapters))
    if chunks:
        parts.append("How it opens:\n" + chunks[0].text[:1600])
    if len(chunks) > 1:
        parts.append("How it ends:\n" + chunks[-1].text[-600:])
    return "\n\n".join(parts)


def _chunks(session: Session, script_id: int) -> list[tuple[int, str]]:
    return [
        (c.ordinal, c.text)
        for c in session.scalars(
            select(Chunk).where(Chunk.script_id == script_id).order_by(Chunk.ordinal)
        ).all()
    ]


def quote(
    session: Session,
    script_id: int,
    settings: Settings,
    kind: str,
    *,
    model: str | None = None,
    package: bool = True,
) -> Quote:
    """What a draft would send, and roughly what it would cost. Sends nothing."""
    chosen = model or settings.groq_model
    if kind == "effects":
        chunks = _chunks(session, script_id)
        return Quote(
            kind,
            len(chunks),
            llm.estimate_tokens([text for _, text in chunks], SYSTEM_PROMPT),
            OUTPUT_TOKENS["effects"] * len(chunks),
            chosen,
        )
    brief = episode_brief(session, script_id)
    if kind == "titles":
        requests = 2 if package else 1
        return Quote(
            kind,
            requests,
            llm.estimate_tokens([brief] * requests, copywrite.TITLE_PROMPT, per_text=False),
            OUTPUT_TOKENS["titles"] + (OUTPUT_TOKENS["package"] if package else 0),
            chosen,
        )
    if kind == "briefs":
        return Quote(
            kind,
            1,
            llm.estimate_tokens([brief], copywrite.THUMBNAIL_PROMPT),
            OUTPUT_TOKENS["briefs"],
            chosen,
        )
    raise ValueError(f"Nothing called {kind!r} is drafted.")


def _gate(settings: Settings, model: str | None) -> None:
    if not settings.has_groq_key:
        raise NoGroqKey("No Groq key is set — add GROQ_API_KEY to .env to draft with a model.")
    llm.resolve_model(model, settings)


def record_spend(engine: Engine, script_id: int, kind: str, usage: TokenUsage, note: str) -> None:
    """Charge the ledger for tokens spent, before the results are looked at.

    Writing the row first means a run whose output is then discarded is still
    accounted for, which is the only way the monthly figure stays true.
    """
    if not usage.spent:
        return
    with session_scope(engine) as session:
        script = session.get(Script, script_id)
        ledger.record_operation(
            session,
            kind=kind,
            provider=ledger.GROQ,
            units=float(usage.total_tokens),
            unit_kind=ledger.TOKENS,
            cost_micros=usage.cost_micros,
            model_id=usage.model,
            project_id=script.project_id if script else None,
            script_id=script_id,
            cost_source="usage",
            note=f"{note} ({usage.prompt_tokens} in, {usage.completion_tokens} out)",
        )


async def draft_titles(
    engine: Engine,
    script_id: int,
    settings: Settings,
    *,
    count: int = 6,
    package: bool = True,
    model: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> Drafted:
    """Propose titles — unchosen — and, with `package`, a description and tags,
    which are written straight in: there is one of each and nothing acts on them."""
    _gate(settings, model)
    with session_scope(engine) as session:
        brief = episode_brief(session, script_id)
    run = await copywrite.propose_copy(
        brief, settings, model=model, titles=count, want_package=package, client=client
    )
    record_spend(engine, script_id, "copy", run.usage, f"{len(run.titles)} title(s)")
    done = Drafted("titles", usage=run.usage, errors=list(run.errors))
    with session_scope(engine) as session:
        for idea in run.titles:
            try:
                publish.add_title(
                    session,
                    script_id,
                    idea.text,
                    formula=idea.formula,
                    rationale=idea.reason,
                    source="proposed",
                    accepted=False,
                    model_id=run.model,
                )
            except publish.DuplicateTitle:
                continue
            done.stored += 1
        script = session.get(Script, script_id)
        if script is not None and run.description:
            script.description = run.description
            done.description_written = True
        if script is not None and run.tags:
            script.tags = ", ".join(run.tags)
    return done


async def draft_briefs(
    engine: Engine,
    script_id: int,
    settings: Settings,
    *,
    count: int = 3,
    model: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> Drafted:
    """Propose thumbnail briefs, unchosen. No image is generated: the brief is
    the deliverable."""
    _gate(settings, model)
    with session_scope(engine) as session:
        brief = episode_brief(session, script_id)
    run = await copywrite.propose_thumbnails(
        brief, settings, model=model, count=count, client=client
    )
    record_spend(engine, script_id, "copy", run.usage, f"{len(run.briefs)} thumbnail brief(s)")
    done = Drafted("briefs", usage=run.usage, errors=list(run.errors))
    with session_scope(engine) as session:
        highest = session.scalar(
            select(func.max(ThumbnailBrief.ordinal)).where(ThumbnailBrief.script_id == script_id)
        )
        for offset, idea in enumerate(run.briefs, start=(highest or 0) + 1):
            session.add(
                ThumbnailBrief(
                    script_id=script_id,
                    ordinal=offset,
                    archetype=idea.archetype,
                    overlay_text=idea.overlay_text,
                    subject=idea.subject,
                    contrast=idea.contrast,
                    rationale=idea.reason,
                    principles=idea.principles,
                    source="proposed",
                    accepted=False,
                    model_id=run.model,
                )
            )
            done.stored += 1
    return done


async def suggest_effects(
    engine: Engine,
    script_id: int,
    settings: Settings,
    *,
    max_per_chunk: int = 2,
    accept: bool = False,
    model: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> Drafted:
    """Ask where effects might belong. Every proposal is stored unaccepted unless
    `accept`, so nothing is generated until a person says so."""
    _gate(settings, model)
    with session_scope(engine) as session:
        chunks = _chunks(session, script_id)
    if not chunks:
        raise NothingToDraftFrom("This episode has no chunks yet.")
    run = await suggest_for_chunks(
        chunks, settings, model=model, max_per_chunk=max_per_chunk, client=client
    )
    usage = TokenUsage(run.prompt_tokens, run.completion_tokens, run.model)
    record_spend(
        engine,
        script_id,
        "suggestion",
        usage,
        f"{len(run.suggestions)} proposed from {len(chunks)} chunk(s)",
    )
    done = Drafted("effects", usage=usage, errors=list(run.errors))
    with session_scope(engine) as session:
        for item in run.suggestions:
            add_slot(
                session,
                script_id,
                item.chunk_ordinal,
                item.description,
                item.duration_s,
                item.loop,
                source="suggested",
                accepted=accept,
                note=item.reason,
            )
            done.stored += 1
    done.suggestions = list(run.suggestions)
    return done
