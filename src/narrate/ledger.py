"""The cost ledger (PRD §6).

Two rules the rest of the tool relies on:

**Cost comes from the provider, not from arithmetic on the text.** Every
response carries a `character-cost` header stating what was billed. Recording
that instead of `len(text)` makes the tool correct regardless of whether audio
tags, prefix tags or context text are billable — a question the docs never
answer. When the header is absent the estimate is used instead and the entry is
marked `cost_source='estimated'`, so reconciliation can see exactly which
figures were inferred.

**Entries are never modified.** The database enforces it with triggers. A
correction is a new compensating entry, which is what lets reconciliation
replay history rather than trust a mutable running total.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from narrate.db.models import Chunk, Cut, Export, LedgerEntry, Project, Script, Take
from narrate.money import pct
from narrate.registry import ModelSpec

# Unit vocabulary. Speech is billed per character, effects per second of
# audio, and an LLM per token — one ledger has to hold all three.
CHARACTERS = "characters"
SECONDS = "seconds"
TOKENS = "tokens"

ELEVENLABS = "elevenlabs"
GROQ = "groq"


def record_operation(
    session: Session,
    *,
    kind: str,
    cost_micros: int,
    units: float = 0.0,
    unit_kind: str = CHARACTERS,
    provider: str = ELEVENLABS,
    credits: float = 0.0,
    model_id: str = "",
    project_id: int | None = None,
    script_id: int | None = None,
    chunk_id: int | None = None,
    take_id: int | None = None,
    run_id: int | None = None,
    billed_chars: int = 0,
    rate_usd_per_1k: float = 0.0,
    rate_card_version: str = "",
    cost_source: str = "header",
    request_id: str | None = None,
    note: str = "",
) -> LedgerEntry:
    """Append one charge. The single way anything reaches the ledger.

    `project_id` may be `None` for an account-level charge — a diagnostic
    probe belongs to no project, and billing it to one would corrupt the
    per-project total this table exists to produce.

    `billed_chars` is set only for character-billed work, because
    `ledger_total_micros` sums it against the provider's character usage; a
    four-second effect is not four characters.
    """
    entry = LedgerEntry(
        project_id=project_id,
        script_id=script_id,
        chunk_id=chunk_id,
        take_id=take_id,
        run_id=run_id,
        kind=kind,
        provider=provider,
        model_id=model_id,
        units=units,
        unit_kind=unit_kind,
        billed_chars=billed_chars,
        rate_usd_per_1k=rate_usd_per_1k,
        rate_card_version=rate_card_version,
        cost_micros=cost_micros,
        credits=credits,
        cost_source=cost_source,
        request_id=request_id,
        note=note,
    )
    session.add(entry)
    return entry


def record_generation(
    session: Session,
    take: Take,
    *,
    project_id: int,
    script_id: int,
    run_id: int | None = None,
    note: str = "",
) -> LedgerEntry:
    """Append the cost of one take. Called once per billable generation.

    Kept as its own function because a take carries a dozen fields worth
    copying across in exactly one place.
    """
    return record_operation(
        session,
        kind="generation",
        provider=ELEVENLABS,
        units=float(take.billed_chars),
        unit_kind=CHARACTERS,
        billed_chars=take.billed_chars,
        cost_micros=take.cost_micros,
        credits=take.credits,
        model_id=take.model_id,
        project_id=project_id,
        script_id=script_id,
        chunk_id=take.chunk_id,
        take_id=take.id,
        run_id=run_id,
        rate_usd_per_1k=take.rate_usd_per_1k,
        rate_card_version=take.rate_card_version,
        cost_source=take.cost_source,
        request_id=take.request_id,
        note=note,
    )


def record_generation_row(
    session: Session,
    *,
    project_id: int,
    kind: str,
    cost_micros: int,
    model_id: str = "",
    script_id: int | None = None,
    billed_chars: int = 0,
    credits: float = 0.0,
    rate_usd_per_1k: float = 0.0,
    rate_card_version: str = "",
    cost_source: str = "header",
    request_id: str | None = None,
    note: str = "",
    units: float = 0.0,
    unit_kind: str = CHARACTERS,
    provider: str = ELEVENLABS,
) -> LedgerEntry:
    """Append a charge that has no `Take` behind it — a sound effect, say."""
    return record_operation(
        session,
        kind=kind,
        provider=provider,
        units=units,
        unit_kind=unit_kind,
        billed_chars=billed_chars,
        cost_micros=cost_micros,
        credits=credits,
        model_id=model_id,
        project_id=project_id,
        script_id=script_id,
        rate_usd_per_1k=rate_usd_per_1k,
        rate_card_version=rate_card_version,
        cost_source=cost_source,
        request_id=request_id,
        note=note,
    )


def record_correction(
    session: Session,
    *,
    project_id: int,
    cost_micros: int,
    note: str,
    script_id: int | None = None,
    take_id: int | None = None,
    model_id: str = "",
) -> LedgerEntry:
    """Append a compensating entry. The only way to change a total."""
    entry = LedgerEntry(
        project_id=project_id,
        script_id=script_id,
        take_id=take_id,
        kind="correction",
        model_id=model_id,
        cost_micros=cost_micros,
        note=note,
    )
    session.add(entry)
    return entry


# ---------------------------------------------------------------------------
# Rollups (C3, C4, C7)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KindTotal:
    """What one class of operation consumed and cost."""

    kind: str
    provider: str
    unit_kind: str
    operations: int
    units: float
    cost_micros: int
    credits: float

    @property
    def units_display(self) -> str:
        if self.unit_kind == SECONDS:
            return f"{self.units:,.1f} s"
        if self.unit_kind == TOKENS:
            return f"{self.units:,.0f} tok"
        return f"{self.units:,.0f} chars"


@dataclass(frozen=True)
class Rollup:
    """Spend for one scope, split by whether it ended up in the cut."""

    label: str
    takes: int
    billed_chars: int
    cost_micros: int
    selected_micros: int
    wasted_micros: int
    estimated_entries: int = 0
    credits: float = 0.0
    by_kind: tuple[KindTotal, ...] = ()

    @property
    def waste_pct(self) -> float:
        """Share of spend on takes that were never selected (C4).

        The single number that says whether the direction process is working.
        High waste means the script or the tags need fixing, not the voice.

        Measured against speech spend only: an effect has no takes to choose
        between, so counting it would dilute the ratio and make a wasteful
        session look thrifty.
        """
        speech = sum(k.cost_micros for k in self.by_kind if k.kind == "generation")
        return pct(self.wasted_micros, speech or self.cost_micros)

    @property
    def operations(self) -> int:
        return sum(k.operations for k in self.by_kind)


def _kind_totals(entries: list[LedgerEntry]) -> tuple[KindTotal, ...]:
    """Group entries by what kind of operation they paid for."""
    buckets: dict[tuple[str, str, str], list[LedgerEntry]] = {}
    for entry in entries:
        buckets.setdefault((entry.kind, entry.provider, entry.unit_kind), []).append(entry)

    return tuple(
        sorted(
            (
                KindTotal(
                    kind=kind,
                    provider=provider,
                    unit_kind=unit_kind,
                    operations=len(rows),
                    units=round(sum(r.units for r in rows), 3),
                    cost_micros=sum(r.cost_micros for r in rows),
                    credits=round(sum(r.credits for r in rows), 2),
                )
                for (kind, provider, unit_kind), rows in buckets.items()
            ),
            key=lambda k: -k.cost_micros,
        )
    )


def _selected_take_ids(session: Session, script_id: int | None = None) -> set[int]:
    stmt = select(Cut.take_id)
    if script_id is not None:
        stmt = stmt.where(Cut.script_id == script_id)
    return set(session.scalars(stmt).all())


def script_rollup(session: Session, script_id: int) -> Rollup:
    """Everything billed to one episode — speech, effects and suggestions alike.

    Deliberately not filtered to speech: "what did episode 14 cost" means the
    whole episode, and an effect generated for it is part of that answer.
    `by_kind` keeps the composition visible, and `waste_pct` stays
    speech-specific because only takes have alternatives to waste.
    """
    script = session.get(Script, script_id)
    label = script.title if script else f"script {script_id}"

    entries = list(
        session.scalars(select(LedgerEntry).where(LedgerEntry.script_id == script_id)).all()
    )
    selected = _selected_take_ids(session, script_id)
    return _summarise(label, entries, selected)


def project_rollup(
    session: Session,
    project_id: int,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Rollup:
    stmt = select(LedgerEntry).where(LedgerEntry.project_id == project_id)
    if since is not None:
        stmt = stmt.where(LedgerEntry.ts >= since)
    if until is not None:
        stmt = stmt.where(LedgerEntry.ts <= until)

    entries = list(session.scalars(stmt).all())
    selected = _selected_take_ids(session)
    return _summarise(f"project {project_id}", entries, selected)


def _summarise(label: str, entries: list[LedgerEntry], selected: set[int]) -> Rollup:
    generations = [e for e in entries if e.kind == "generation"]

    # Everything billable counts toward the total — speech, effects and the
    # LLM alike. Waste and the character count stay speech-specific, because
    # that is what they mean.
    total = sum(e.cost_micros for e in entries)
    picked = sum(e.cost_micros for e in generations if e.take_id in selected)
    wasted = sum(e.cost_micros for e in generations if e.take_id not in selected)

    return Rollup(
        label=label,
        takes=len(generations),
        billed_chars=sum(e.billed_chars for e in generations),
        cost_micros=total,
        selected_micros=picked,
        wasted_micros=wasted,
        estimated_entries=sum(1 for e in generations if e.cost_source != "header"),
        credits=round(sum(e.credits for e in entries), 2),
        by_kind=_kind_totals([e for e in entries if e.kind != "correction"]),
    )


def chunk_costs(session: Session, script_id: int) -> list[tuple[int, int, int, int]]:
    """Per-chunk `(ordinal, takes, total_micros, selected_micros)`."""
    rows: list[tuple[int, int, int, int]] = []
    chunks = session.scalars(
        select(Chunk).where(Chunk.script_id == script_id).order_by(Chunk.ordinal)
    ).all()
    selected = _selected_take_ids(session, script_id)

    for chunk in chunks:
        entries = session.scalars(
            select(LedgerEntry).where(
                LedgerEntry.chunk_id == chunk.id, LedgerEntry.kind == "generation"
            )
        ).all()
        total = sum(e.cost_micros for e in entries)
        picked = sum(e.cost_micros for e in entries if e.take_id in selected)
        rows.append((chunk.ordinal, len(entries), total, picked))
    return rows


def exported_seconds(session: Session, script_id: int) -> float:
    """Runtime of the most recent export, for cost per finished minute."""
    duration = session.scalar(
        select(Export.duration_s)
        .where(Export.script_id == script_id)
        .order_by(Export.created_at.desc())
        .limit(1)
    )
    return float(duration or 0.0)


def cut_seconds(session: Session, script_id: int) -> float:
    """Runtime of the selected takes, from their measured durations.

    Lets cost-per-minute be answered before anything has been exported — the
    takes are the audio, the export is only a container for them.
    """
    rows = session.execute(
        select(Take.duration_s).join(Cut, Cut.take_id == Take.id).where(Cut.script_id == script_id)
    ).all()
    return sum(float(r[0]) for r in rows if r[0])


def cost_per_minute_micros(session: Session, script_id: int) -> int | None:
    """Spend ÷ finished runtime (C7).

    Prefers the exported runtime, since that is the actual deliverable and
    includes inter-chunk gaps. Falls back to the summed duration of the
    selected takes so the metric is available as soon as a cut exists.
    """
    seconds = exported_seconds(session, script_id) or cut_seconds(session, script_id)
    if seconds <= 0:
        return None
    return int(script_rollup(session, script_id).cost_micros / (seconds / 60))


def unknown_takes(session: Session, script_id: int | None = None) -> list[Take]:
    """Takes whose billing outcome was never observed.

    These are the ones a human has to resolve — the request was sent, the
    response was never read, and it may or may not have been charged.
    """
    stmt = select(Take).where(Take.status == "unknown")
    if script_id is not None:
        stmt = stmt.join(Chunk, Take.chunk_id == Chunk.id).where(Chunk.script_id == script_id)
    return list(session.scalars(stmt).all())


def ledger_total_micros(session: Session, since: datetime, until: datetime) -> tuple[int, int]:
    """`(billed_chars, cost_micros)` in a window — the reconciliation input.

    Restricted to **ElevenLabs character-billed** work on purpose. The provider
    figure this is compared against counts characters, so effect seconds and
    Groq tokens must not enter the sum — including them would manufacture drift
    out of spend that was never denominated in characters to begin with.
    """
    row = session.execute(
        select(
            func.coalesce(func.sum(LedgerEntry.billed_chars), 0),
            func.coalesce(func.sum(LedgerEntry.cost_micros), 0),
        ).where(
            LedgerEntry.ts >= since,
            LedgerEntry.ts <= until,
            LedgerEntry.provider == ELEVENLABS,
            LedgerEntry.unit_kind == CHARACTERS,
            LedgerEntry.kind.in_(("generation", "probe")),
        )
    ).one()
    return int(row[0]), int(row[1])


def month_spend_micros(session: Session, project_id: int, since: datetime) -> int:
    """Spend since a date — backs the budget guardrail."""
    return int(
        session.scalar(
            select(func.coalesce(func.sum(LedgerEntry.cost_micros), 0)).where(
                LedgerEntry.project_id == project_id, LedgerEntry.ts >= since
            )
        )
        or 0
    )


def month_start(now: datetime | None = None) -> datetime:
    """First instant of the current calendar month, UTC."""
    moment = now or datetime.now(tz=UTC)
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


WARN_AT = 0.80


@dataclass(frozen=True)
class Budget:
    """A project's monthly cap measured against what it has already spent (F10)."""

    cap_micros: int | None
    spent_micros: int
    projected_micros: int

    @property
    def remaining_micros(self) -> int | None:
        """Headroom left under the cap, or `None` when no cap is set."""
        if self.cap_micros is None:
            return None
        return max(0, self.cap_micros - self.spent_micros)

    @property
    def used_pct(self) -> float:
        if not self.cap_micros:
            return 0.0
        return round(self.spent_micros / self.cap_micros * 100, 1)

    @property
    def projected_pct(self) -> float:
        if not self.cap_micros:
            return 0.0
        return round((self.spent_micros + self.projected_micros) / self.cap_micros * 100, 1)

    @property
    def blocked(self) -> bool:
        """The run would take the month past 100% of the cap."""
        if self.cap_micros is None:
            return False
        return self.spent_micros + self.projected_micros > self.cap_micros

    @property
    def warn(self) -> bool:
        """Already past the warning threshold, but the run still fits."""
        if self.cap_micros is None or self.blocked:
            return False
        return self.projected_pct >= WARN_AT * 100


def project_budget(
    session: Session, project_id: int, projected_micros: int = 0, now: datetime | None = None
) -> Budget:
    """Where a project stands against its monthly cap, if it has one."""
    project = session.get(Project, project_id)
    cap = project.monthly_cap_micros if project else None
    spent = month_spend_micros(session, project_id, month_start(now))
    return Budget(cap_micros=cap, spent_micros=spent, projected_micros=projected_micros)


# ---------------------------------------------------------------------------
# Pre-flight estimate (C1)
# ---------------------------------------------------------------------------


def billing_ratio(
    session: Session, model_id: str, min_samples: int = 3
) -> tuple[float, int] | None:
    """Observed billed-per-submitted-character ratio for a model.

    The provider does not bill one character per character. On this account a
    12-character v3 request came back with `character-cost: 3` — API
    generations are discounted, and the discount is not published as a formula.

    A raw `len(text)` estimate would therefore read roughly four times the real
    cost, which is useless for a pre-flight figure and misses the PRD's ±2%
    accuracy target by a mile. So the ratio is *learned* from what this account
    has actually been billed, rather than hardcoded — a repricing corrects
    itself after a few takes instead of silently skewing every estimate.

    Only takes whose cost came from the `character-cost` header count; an
    estimated fallback would make the calibration circular. Returns `None`
    until there is enough history to be worth trusting.
    """
    row = session.execute(
        select(
            func.coalesce(func.sum(Take.billed_chars), 0),
            func.coalesce(func.sum(Take.submitted_chars), 0),
            func.count(Take.id),
        ).where(
            Take.model_id == model_id,
            Take.status == "succeeded",
            Take.cost_source == "header",
        )
    ).one()
    billed, submitted, count = int(row[0]), int(row[1]), int(row[2])
    if count < min_samples or submitted <= 0:
        return None
    return billed / submitted, count


@dataclass(frozen=True)
class Estimate:
    """What a run will cost, shown before anything is spent."""

    model: ModelSpec
    chunks: int
    text_chars: int
    tag_chars: int
    cost_micros: int
    credits: float
    # Learned from this account's own history; see `billing_ratio`.
    ratio: float | None = None
    ratio_samples: int = 0

    @property
    def total_chars(self) -> int:
        return self.text_chars + self.tag_chars

    @property
    def calibrated_micros(self) -> int:
        """Best available estimate: calibrated when there is history to use."""
        if self.ratio is None:
            return self.cost_micros
        return self.model.cost_micros(round(self.total_chars * self.ratio))

    @property
    def is_calibrated(self) -> bool:
        return self.ratio is not None


def estimate_run(
    spec: ModelSpec,
    chunk_char_counts: list[int],
    tag_chars: int = 0,
    ratio: tuple[float, int] | None = None,
) -> Estimate:
    """Estimate a run from already-measured chunk lengths.

    The counts passed in must be of the *submitted* text — prefix tags
    included. PRD F4 is explicit that tags are billable characters and must be
    in the estimate rather than stripped from it.
    """
    total = sum(chunk_char_counts)
    return Estimate(
        model=spec,
        chunks=len(chunk_char_counts),
        text_chars=total - tag_chars,
        tag_chars=tag_chars,
        cost_micros=spec.cost_micros(total),
        credits=spec.credits(total),
        ratio=ratio[0] if ratio else None,
        ratio_samples=ratio[1] if ratio else 0,
    )


# ---------------------------------------------------------------------------
# Per-project view: what it has cost, and what it will cost finished
# ---------------------------------------------------------------------------


def project_totals(session: Session, project_id: int, since: datetime | None = None) -> Rollup:
    """Everything billed to one project, broken down by operation."""
    project = session.get(Project, project_id)
    stmt = select(LedgerEntry).where(LedgerEntry.project_id == project_id)
    if since is not None:
        stmt = stmt.where(LedgerEntry.ts >= since)
    entries = list(session.scalars(stmt).all())
    selected = _selected_take_ids(session)
    return _summarise(project.name if project else f"project {project_id}", entries, selected)


def unattributed(session: Session, since: datetime | None = None) -> Rollup:
    """Account-level spend that belongs to no project — diagnostics, mostly.

    Visible in the account total and deliberately absent from every project,
    because attributing a probe to an episode would misstate what that episode
    cost.
    """
    stmt = select(LedgerEntry).where(LedgerEntry.project_id.is_(None))
    if since is not None:
        stmt = stmt.where(LedgerEntry.ts >= since)
    return _summarise("unattributed", list(session.scalars(stmt).all()), set())


def account_totals(session: Session, since: datetime | None = None) -> Rollup:
    """Every charge on the ledger, attributed or not."""
    stmt = select(LedgerEntry)
    if since is not None:
        stmt = stmt.where(LedgerEntry.ts >= since)
    return _summarise("account", list(session.scalars(stmt).all()), _selected_take_ids(session))


@dataclass(frozen=True)
class Outstanding:
    """What a script has not paid for yet."""

    chunks: int
    chars: int
    effects: int
    effect_seconds: float
    cost_micros: int

    @property
    def is_empty(self) -> bool:
        return self.chunks == 0 and self.effects == 0


def outstanding(session: Session, script_id: int, registry: object | None = None) -> Outstanding:
    """Price the work still to do, so a project total can be projected.

    Counts chunks with no selected take and accepted effect slots with no
    audio. Both are priced from the rate card, the same way a pre-flight
    estimate is — this is the estimate, just scoped to what is left.
    """
    from narrate.db.models import EffectSlot
    from narrate.effects import EffectRates
    from narrate.registry import Registry

    script = session.get(Script, script_id)
    if script is None:
        return Outstanding(0, 0, 0, 0.0, 0)
    project = session.get(Project, script.project_id)

    cards = registry if isinstance(registry, Registry) else Registry.load()
    spec = cards.get(script.model_id or (project.model_id if project else ""))
    prefix = project.prefix_tags if project else ""
    prefix_len = len(prefix) + 1 if prefix else 0

    selected = {
        c.chunk_id for c in session.scalars(select(Cut).where(Cut.script_id == script_id)).all()
    }
    pending = [
        c
        for c in session.scalars(
            select(Chunk).where(Chunk.script_id == script_id).order_by(Chunk.ordinal)
        ).all()
        if c.id not in selected
    ]
    chars = sum(len(c.text) + prefix_len for c in pending)

    rates = EffectRates.load()
    slots = [
        s
        for s in session.scalars(select(EffectSlot).where(EffectSlot.script_id == script_id)).all()
        if s.accepted and s.effect_id is None
    ]
    # A slot with no stated duration gets the provider's own default guess.
    seconds = sum(s.duration_s or 3.0 for s in slots)

    return Outstanding(
        chunks=len(pending),
        chars=chars,
        effects=len(slots),
        effect_seconds=round(seconds, 2),
        cost_micros=spec.cost_micros(chars) + rates.cost_micros(seconds),
    )


def cost_log(
    session: Session,
    *,
    project_id: int | None = None,
    script_id: int | None = None,
    kind: str | None = None,
    limit: int = 50,
    unattributed_only: bool = False,
) -> list[LedgerEntry]:
    """Individual charges, newest first — the per-operation trail."""
    stmt = select(LedgerEntry).order_by(LedgerEntry.ts.desc(), LedgerEntry.id.desc())
    if unattributed_only:
        stmt = stmt.where(LedgerEntry.project_id.is_(None))
    elif project_id is not None:
        stmt = stmt.where(LedgerEntry.project_id == project_id)
    if script_id is not None:
        stmt = stmt.where(LedgerEntry.script_id == script_id)
    if kind:
        stmt = stmt.where(LedgerEntry.kind == kind)
    return list(session.scalars(stmt.limit(limit)).all())
