"""Sound effects: a reusable library, and the slots that place them.

The split between an `Effect` and an `EffectSlot` is the point of the feature.
An effect is the generated audio, keyed by everything that determines it. A
slot is a position on a timeline. Many slots may point at one effect, so a cue
used four times in an episode is **one generation and one charge**.

A slot with no effect is *planned*: the editing plan marks where something
belongs without anything having been generated. That is what makes the plan
useful before any money has been spent.

**Costing is not the narration model.** Effects are billed per second, so USD
comes from duration at the published $0.12/minute. The `character-cost` header
is stored in `Effect.observed_cost` for reconciliation only — nothing
documents its unit for a per-second product, and a guess there would quietly
corrupt every total.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from narrate.db.models import Effect, EffectSlot, Project, Script
from narrate.db.session import session_scope
from narrate.ledger import SECONDS, record_generation_row
from narrate.money import micros_from_seconds
from narrate.provider.base import (
    ProviderError,
    SFXProvider,
    SFXRequest,
    effect_idempotency_key,
)
from narrate.registry import CONFIG_PATH
from narrate.script_parse import ParsedScript
from narrate.settings import Settings


@dataclass(frozen=True)
class EffectRates:
    """The `[effects]` block of the rate card (C6: rates in config, not code)."""

    model_id: str
    usd_per_minute: float
    credits_per_second: float
    min_seconds: float
    max_seconds: float
    default_prompt_influence: float

    @classmethod
    def load(cls, config_path: Path | None = None) -> EffectRates:
        raw = tomllib.loads((config_path or CONFIG_PATH).read_text(encoding="utf-8"))
        block = raw.get("effects", {})
        return cls(
            model_id=block.get("model_id", "eleven_text_to_sound_v2"),
            usd_per_minute=float(block.get("usd_per_minute", 0.12)),
            credits_per_second=float(block.get("credits_per_second", 40)),
            min_seconds=float(block.get("min_seconds", 0.5)),
            max_seconds=float(block.get("max_seconds", 30.0)),
            default_prompt_influence=float(block.get("default_prompt_influence", 0.3)),
        )

    def cost_micros(self, seconds: float) -> int:
        return micros_from_seconds(seconds, self.usd_per_minute)

    def credits(self, seconds: float) -> float:
        return round(seconds * self.credits_per_second, 2)


# ---------------------------------------------------------------------------
# Slots
# ---------------------------------------------------------------------------


def sync_slots_from_script(
    session: Session, script_id: int, parsed: ParsedScript, chunk_offsets: dict[int, int]
) -> list[EffectSlot]:
    """Create slots for the `[SFX: ...]` markers found in a script.

    `chunk_offsets` maps a chunk ordinal to the source offset it begins at.
    Effect markers forced those boundaries, so every slot has a chunk start
    close to it — but not necessarily an identical offset, because a boundary
    snaps to the nearest sentence start. Matching on nearest rather than equal
    is what makes the mapping survive that snap. A marker before any narration
    lands at chunk 1, i.e. 00:00.
    """
    existing = {
        (s.at_chunk_ordinal, s.description, s.duration_s, s.loop)
        for s in session.scalars(
            select(EffectSlot).where(
                EffectSlot.script_id == script_id, EffectSlot.source == "marker"
            )
        ).all()
    }

    created: list[EffectSlot] = []
    next_ordinal = _next_slot_ordinal(session, script_id)

    for slot in parsed.slots:
        ordinal = _nearest_chunk(chunk_offsets, slot.offset)
        key = (ordinal, slot.description, slot.duration_s, slot.loop)
        if key in existing:
            continue
        row = EffectSlot(
            script_id=script_id,
            ordinal=next_ordinal,
            at_chunk_ordinal=ordinal,
            description=slot.description,
            duration_s=slot.duration_s,
            loop=slot.loop,
            source="marker",
            accepted=True,
        )
        session.add(row)
        created.append(row)
        existing.add(key)
        next_ordinal += 1

    session.flush()
    return created


def _nearest_chunk(chunk_offsets: dict[int, int], offset: int) -> int:
    """The chunk whose start is closest to a marker's position."""
    if not chunk_offsets:
        return 1
    return min(chunk_offsets.items(), key=lambda pair: (abs(pair[1] - offset), pair[0]))[0]


def add_slot(
    session: Session,
    script_id: int,
    at_chunk_ordinal: int,
    description: str,
    duration_s: float | None = None,
    loop: bool = False,
    source: str = "manual",
    accepted: bool = True,
    note: str = "",
) -> EffectSlot:
    row = EffectSlot(
        script_id=script_id,
        ordinal=_next_slot_ordinal(session, script_id),
        at_chunk_ordinal=at_chunk_ordinal,
        description=description,
        duration_s=duration_s,
        loop=loop,
        source=source,
        accepted=accepted,
        note=note,
    )
    session.add(row)
    session.flush()
    return row


def _next_slot_ordinal(session: Session, script_id: int) -> int:
    highest = session.scalar(
        select(EffectSlot.ordinal)
        .where(EffectSlot.script_id == script_id)
        .order_by(EffectSlot.ordinal.desc())
        .limit(1)
    )
    return (highest or 0) + 1


# ---------------------------------------------------------------------------
# The library
# ---------------------------------------------------------------------------


def request_for(slot: EffectSlot, rates: EffectRates, output_format: str) -> SFXRequest:
    return SFXRequest(
        prompt=slot.description,
        duration_s=slot.duration_s,
        loop=slot.loop,
        prompt_influence=rates.default_prompt_influence,
        model_id=rates.model_id,
        output_format=output_format,
    )


def find_existing(session: Session, project_id: int, key: str) -> Effect | None:
    """A previously generated effect for this exact request, if its file survives.

    Checking the file rather than only the row means a pruned asset does not
    leave a slot pointing at audio that is not there.
    """
    for effect in session.scalars(
        select(Effect)
        .where(
            Effect.project_id == project_id,
            Effect.idempotency_key == key,
            Effect.status == "succeeded",
        )
        .order_by(Effect.id.desc())
    ).all():
        if effect.asset_path and Path(effect.asset_path).exists():
            return effect
    return None


def effect_path(settings: Settings, project_name: str, slug: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in project_name)[:40]
    return settings.assets_dir / safe / "effects" / f"{slug}.mp3"


@dataclass
class EffectOutcome:
    slot_id: int
    description: str
    status: str  # generated | reused | failed | skipped_unaccepted
    effect_id: int | None = None
    cost_micros: int = 0
    duration_s: float | None = None
    error: str | None = None


async def generate_slots(
    engine: Engine,
    script_id: int,
    provider: SFXProvider,
    settings: Settings,
    *,
    slot_ids: list[int] | None = None,
    dry_run: bool = True,
    rates: EffectRates | None = None,
    on_event: Callable[[str], None] | None = None,
) -> list[EffectOutcome]:
    """Generate the effects the accepted slots ask for, reusing where possible."""
    from narrate.audio import FFmpegFailed, FFmpegMissing, duration_seconds

    rate_card = rates or EffectRates.load()

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
        project_id, project_name = project.id, project.name

        query = select(EffectSlot).where(EffectSlot.script_id == script_id)
        if slot_ids:
            query = query.where(EffectSlot.id.in_(slot_ids))
        slots = list(session.scalars(query.order_by(EffectSlot.at_chunk_ordinal)).all())

        # Grouped by idempotency key, not listed per slot: two slots asking for
        # the same cue in a single run must produce one generation and one
        # charge, exactly as two slots across separate runs would.
        pending: dict[str, tuple[SFXRequest, list[tuple[int, str]]]] = {}
        outcomes: list[EffectOutcome] = []

        for slot in slots:
            if not slot.accepted:
                outcomes.append(EffectOutcome(slot.id, slot.description, "skipped_unaccepted"))
                continue
            request = request_for(slot, rate_card, settings.output_format)
            key = effect_idempotency_key(request)

            reused = find_existing(session, project_id, key)
            if reused is not None:
                # The reuse path — this is what makes one cue used four times
                # a single charge.
                if slot.effect_id != reused.id:
                    slot.effect_id = reused.id
                outcomes.append(
                    EffectOutcome(
                        slot.id,
                        slot.description,
                        "reused",
                        effect_id=reused.id,
                        duration_s=reused.actual_duration_s,
                    )
                )
                continue
            pending.setdefault(key, (request, []))[1].append((slot.id, slot.description))

    if dry_run:
        for request, members in pending.values():
            seconds = request.duration_s or 3.0
            first, *rest = members
            outcomes.append(
                EffectOutcome(
                    first[0],
                    first[1],
                    "would_generate",
                    cost_micros=rate_card.cost_micros(seconds),
                    duration_s=request.duration_s,
                )
            )
            # The other slots wanting this cue cost nothing.
            outcomes += [
                EffectOutcome(slot_id, description, "reused", duration_s=request.duration_s)
                for slot_id, description in rest
            ]
        return outcomes

    for key, (request, members) in pending.items():
        description = members[0][1]
        try:
            result = await provider.generate_effect(request)
        except ProviderError as exc:
            note(f"effect {description!r}: failed — {exc.message}")
            # One failed generation fails every slot that wanted that cue.
            outcomes += [
                EffectOutcome(member_id, name, "failed", error=exc.message)
                for member_id, name in members
            ]
            continue

        slug = _slug(description, request.duration_s, request.loop)
        path = effect_path(settings, project_name, slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(result.audio)

        try:
            actual = duration_seconds(path)
        except (FFmpegMissing, FFmpegFailed):
            actual = request.duration_s or 0.0

        # Billed on duration, at the documented per-minute rate. The header is
        # recorded beside it, never used to derive the charge.
        billed_seconds = actual or request.duration_s or 0.0
        cost = rate_card.cost_micros(billed_seconds)

        with session_scope(engine) as session:
            effect = Effect(
                project_id=project_id,
                slug=slug,
                prompt=request.prompt,
                duration_s=request.duration_s,
                loop=request.loop,
                prompt_influence=request.prompt_influence,
                model_id=request.model_id,
                output_format=request.output_format,
                idempotency_key=key,
                request_id=result.request_id,
                asset_path=str(path),
                actual_duration_s=actual,
                observed_cost=result.observed_cost,
                rate_usd_per_min=rate_card.usd_per_minute,
                rate_card_version=_rate_card_version(),
                cost_micros=cost,
                credits=rate_card.credits(billed_seconds),
                cost_source="duration",
                status="succeeded",
            )
            session.add(effect)
            session.flush()

            record_generation_row(
                session,
                project_id=project_id,
                script_id=script_id,
                kind="effect",
                model_id=request.model_id,
                # Billed per second of audio, so seconds are the quantity —
                # `billed_chars` stays zero because this is not character work.
                units=billed_seconds,
                unit_kind=SECONDS,
                cost_micros=cost,
                credits=rate_card.credits(billed_seconds),
                request_id=result.request_id,
                cost_source="duration",
                note=f"effect {slug} ({billed_seconds:.2f}s)",
                rate_card_version=_rate_card_version(),
            )

            for slot_id, _name in members:
                target = session.get(EffectSlot, slot_id)
                if target is not None:
                    target.effect_id = effect.id
            effect_id = effect.id

        placed = f" (placed {len(members)}x)" if len(members) > 1 else ""
        note(f"effect {description!r}: ok ({billed_seconds:.2f}s){placed}")

        first, *rest = members
        outcomes.append(
            EffectOutcome(
                first[0],
                first[1],
                "generated",
                effect_id=effect_id,
                cost_micros=cost,
                duration_s=actual,
            )
        )
        outcomes += [
            EffectOutcome(slot_id, name, "reused", effect_id=effect_id, duration_s=actual)
            for slot_id, name in rest
        ]

    return outcomes


def _slug(description: str, duration_s: float | None, loop: bool) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", description.lower()).strip("-")[:48] or "effect"
    parts = [base]
    if duration_s is not None:
        parts.append(f"{duration_s:g}s")
    if loop:
        parts.append("loop")
    return "-".join(parts)


def _rate_card_version() -> str:
    raw = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    version: str = raw.get("meta", {}).get("rate_card_version", "unknown")
    return version


def library(session: Session, project_id: int) -> list[Effect]:
    """Every effect generated for a project — the reusable set."""
    return list(
        session.scalars(
            select(Effect)
            .where(Effect.project_id == project_id, Effect.status == "succeeded")
            .order_by(Effect.slug)
        ).all()
    )


def placements(session: Session, effect_id: int) -> list[EffectSlot]:
    """Every slot using one effect — what makes reuse legible in the plan."""
    return list(
        session.scalars(
            select(EffectSlot).where(EffectSlot.effect_id == effect_id).order_by(EffectSlot.id)
        ).all()
    )


def slot_summary(session: Session, script_id: int) -> dict[str, int]:
    slots = list(session.scalars(select(EffectSlot).where(EffectSlot.script_id == script_id)).all())
    return {
        "total": len(slots),
        "generated": sum(1 for s in slots if s.effect_id is not None),
        "planned": sum(1 for s in slots if s.effect_id is None and s.accepted),
        "unaccepted": sum(1 for s in slots if not s.accepted),
    }
