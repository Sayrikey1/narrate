"""Ledger behaviour: immutability, attribution, and the waste metric."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from narrate import ledger
from narrate.db.models import Cut, LedgerEntry, Take
from narrate.db.session import session_scope
from narrate.money import fmt_usd, micros_from_chars
from narrate.registry import ModelSpec, Registry
from narrate.settings import Settings

from .conftest import chunks_of, needs_ffmpeg


def _add_take(session: Session, chunk_id: int, ordinal: int, chars: int, spec: ModelSpec) -> Take:
    take = Take(
        chunk_id=chunk_id,
        ordinal=ordinal,
        idempotency_key=f"key-{chunk_id}-{ordinal}",
        model_id=spec.model_id,
        voice_id="voice-1",
        submitted_text="x" * chars,
        submitted_chars=chars,
        billed_chars=chars,
        rate_usd_per_1k=spec.usd_per_1k,
        cost_micros=spec.cost_micros(chars),
        credits=spec.credits(chars),
        status="succeeded",
    )
    session.add(take)
    session.flush()
    return take


# -- immutability -----------------------------------------------------------


def test_ledger_rejects_updates(engine: Engine) -> None:
    """PRD §7: the ledger is append-only, enforced by the database itself."""
    with session_scope(engine) as session:
        session.add(LedgerEntry(project_id=1, cost_micros=1000, billed_chars=10))

    with pytest.raises(Exception, match="append-only"):
        with engine.begin() as conn:
            conn.execute(text("UPDATE ledger_entry SET cost_micros = 0"))


def test_ledger_rejects_deletes(engine: Engine) -> None:
    with session_scope(engine) as session:
        session.add(LedgerEntry(project_id=1, cost_micros=1000, billed_chars=10))

    with pytest.raises(Exception, match="append-only"):
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM ledger_entry"))


def test_corrections_are_compensating_entries(engine: Engine) -> None:
    """The only way to change a total is to add to it."""
    with session_scope(engine) as session:
        session.add(LedgerEntry(project_id=1, kind="generation", cost_micros=10_000))
    with session_scope(engine) as session:
        ledger.record_correction(
            session, project_id=1, cost_micros=-4_000, note="double-charged on retry"
        )
    with session_scope(engine) as session:
        roll = ledger.project_rollup(session, 1)
    assert roll.cost_micros == 6_000


# -- attribution and rollups ------------------------------------------------


def test_waste_is_the_cost_of_unselected_takes(
    engine: Engine, project_and_script: tuple[int, int], registry: Registry
) -> None:
    """C4 — the number that says whether the direction process is working."""
    project_id, script_id = project_and_script
    spec = registry.get("eleven_multilingual_v2")

    with session_scope(engine) as session:
        chunk_id = chunks_of(session, script_id)[0].id

        keeper = _add_take(session, chunk_id, 1, 1000, spec)
        reroll = _add_take(session, chunk_id, 2, 1000, spec)
        for take in (keeper, reroll):
            ledger.record_generation(session, take, project_id=project_id, script_id=script_id)
        # The second take is the one that made it into the cut.
        session.add(Cut(chunk_id=chunk_id, script_id=script_id, take_id=reroll.id))

    with session_scope(engine) as session:
        roll = ledger.script_rollup(session, script_id)

    assert roll.takes == 2
    assert roll.cost_micros == spec.cost_micros(2000)
    assert roll.selected_micros == spec.cost_micros(1000)
    assert roll.wasted_micros == spec.cost_micros(1000)
    assert roll.waste_pct == 50.0


def test_waste_is_zero_when_every_take_is_used(
    engine: Engine, project_and_script: tuple[int, int], registry: Registry
) -> None:
    project_id, script_id = project_and_script
    spec = registry.get("eleven_multilingual_v2")

    with session_scope(engine) as session:
        chunks = chunks_of(session, script_id)
        for chunk in chunks:
            take = _add_take(session, chunk.id, 1, 500, spec)
            ledger.record_generation(session, take, project_id=project_id, script_id=script_id)
            session.add(Cut(chunk_id=chunk.id, script_id=script_id, take_id=take.id))

    with session_scope(engine) as session:
        roll = ledger.script_rollup(session, script_id)
    assert roll.waste_pct == 0.0


def test_estimated_entries_are_counted_separately(
    engine: Engine, project_and_script: tuple[int, int], registry: Registry
) -> None:
    """A missing character-cost header must be visible, not silently averaged in."""
    project_id, script_id = project_and_script
    spec = registry.get("eleven_multilingual_v2")

    with session_scope(engine) as session:
        chunk = chunks_of(session, script_id)[0]
        take = _add_take(session, chunk.id, 1, 100, spec)
        take.cost_source = "estimated"
        ledger.record_generation(session, take, project_id=project_id, script_id=script_id)

    with session_scope(engine) as session:
        roll = ledger.script_rollup(session, script_id)
    assert roll.estimated_entries == 1


def test_unknown_takes_are_surfaced(
    engine: Engine, project_and_script: tuple[int, int], registry: Registry
) -> None:
    _, script_id = project_and_script
    spec = registry.get("eleven_multilingual_v2")
    with session_scope(engine) as session:
        chunk = chunks_of(session, script_id)[0]
        take = _add_take(session, chunk.id, 1, 100, spec)
        take.status = "unknown"

    with session_scope(engine) as session:
        assert len(ledger.unknown_takes(session, script_id)) == 1


# -- money ------------------------------------------------------------------


def test_cost_is_exact_at_the_published_rate() -> None:
    """1,000 characters at $0.10/1k is exactly ten cents, with no float drift."""
    assert micros_from_chars(1000, 0.10) == 100_000
    assert micros_from_chars(150, 0.10) == 15_000
    assert micros_from_chars(1000, 0.05) == 50_000


def test_many_small_charges_sum_exactly() -> None:
    """The reason costs are integers: a ledger sums thousands of sub-cent amounts."""
    total = sum(micros_from_chars(1, 0.10) for _ in range(10_000))
    assert total == micros_from_chars(10_000, 0.10)


def test_sub_cent_costs_are_not_rounded_away_in_display() -> None:
    assert fmt_usd(15_000) == "$0.0150"
    assert fmt_usd(-4_000) == "-$0.0040"


def test_estimate_includes_prefix_tag_characters(registry: Registry) -> None:
    spec = registry.get("eleven_v3")
    est = ledger.estimate_run(spec, [1000, 1000], tag_chars=40)
    assert est.total_chars == 2000
    assert est.tag_chars == 40
    assert est.text_chars == 1960
    assert est.cost_micros == spec.cost_micros(2000)


# -- estimate calibration ---------------------------------------------------


def test_billing_ratio_needs_enough_history(
    engine: Engine, project_and_script: tuple[int, int], registry: Registry
) -> None:
    _, script_id = project_and_script
    spec = registry.get("eleven_v3")
    with session_scope(engine) as session:
        chunk_id = chunks_of(session, script_id)[0].id
        _add_take(session, chunk_id, 1, 100, spec)
        assert ledger.billing_ratio(session, spec.model_id) is None


def test_billing_ratio_is_learned_from_what_was_actually_billed(
    engine: Engine, project_and_script: tuple[int, int], registry: Registry
) -> None:
    """The provider bills fewer characters than are submitted.

    A live probe submitted 12 characters and was billed 3. A raw `len(text)`
    estimate would read four times the real cost, so the ratio is learned
    rather than hardcoded.
    """
    _, script_id = project_and_script
    spec = registry.get("eleven_v3")
    with session_scope(engine) as session:
        chunk_id = chunks_of(session, script_id)[0].id
        for i in range(4):
            take = _add_take(session, chunk_id, i + 1, 12, spec)
            take.billed_chars = 3

    with session_scope(engine) as session:
        result = ledger.billing_ratio(session, spec.model_id)
    assert result is not None
    ratio, samples = result
    assert ratio == pytest.approx(0.25)
    assert samples == 4


def test_estimated_takes_do_not_feed_the_calibration(
    engine: Engine, project_and_script: tuple[int, int], registry: Registry
) -> None:
    """Calibrating off estimates would be circular."""
    _, script_id = project_and_script
    spec = registry.get("eleven_v3")
    with session_scope(engine) as session:
        chunk_id = chunks_of(session, script_id)[0].id
        for i in range(4):
            take = _add_take(session, chunk_id, i + 1, 12, spec)
            take.cost_source = "estimated"

    with session_scope(engine) as session:
        assert ledger.billing_ratio(session, spec.model_id) is None


def test_calibrated_estimate_uses_the_observed_ratio(registry: Registry) -> None:
    spec = registry.get("eleven_v3")
    raw = ledger.estimate_run(spec, [1000])
    assert raw.calibrated_micros == raw.cost_micros
    assert raw.is_calibrated is False

    calibrated = ledger.estimate_run(spec, [1000], ratio=(0.25, 8))
    assert calibrated.cost_micros == spec.cost_micros(1000)
    assert calibrated.calibrated_micros == spec.cost_micros(250)
    assert calibrated.is_calibrated is True


# -- every operation is recorded, in its own unit ---------------------------


@needs_ffmpeg
async def test_every_billable_call_writes_exactly_one_entry(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """The property the whole feature rests on: no spend without a record."""
    from narrate.effects import generate_slots
    from narrate.provider.mock import MockProvider, MockSFXProvider
    from narrate.runner import generate

    from .test_timeline_effects import _ingest

    script_id = _ingest(engine, registry)
    speech = MockProvider()
    sfx = MockSFXProvider()
    await generate(engine, script_id, speech, registry, settings, dry_run=False)
    await generate_slots(engine, script_id, sfx, settings, dry_run=False)

    with session_scope(engine) as session:
        entries = list(session.scalars(select(LedgerEntry)).all())

    # One entry per call the providers actually received.
    assert len(entries) == len(speech.calls) + len(sfx.calls)
    assert sum(1 for e in entries if e.kind == "generation") == len(speech.calls)
    assert sum(1 for e in entries if e.kind == "effect") == len(sfx.calls)
    assert all(e.cost_micros > 0 for e in entries)


@needs_ffmpeg
async def test_each_kind_records_its_own_unit(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """A four-second effect is not four characters."""
    from narrate.effects import generate_slots
    from narrate.provider.mock import MockProvider, MockSFXProvider
    from narrate.runner import generate

    from .test_timeline_effects import _ingest

    script_id = _ingest(engine, registry)
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)
    await generate_slots(engine, script_id, MockSFXProvider(), settings, dry_run=False)

    with session_scope(engine) as session:
        speech = [e for e in session.scalars(select(LedgerEntry)).all() if e.kind == "generation"]
        effects = [e for e in session.scalars(select(LedgerEntry)).all() if e.kind == "effect"]

    assert all(e.unit_kind == ledger.CHARACTERS and e.units == e.billed_chars for e in speech)
    assert all(e.unit_kind == ledger.SECONDS and e.units > 0 for e in effects)
    # Effects must not pollute the character count reconciliation compares.
    assert all(e.billed_chars == 0 for e in effects)


def test_a_suggestion_records_tokens_against_groq(
    engine: Engine, project_and_script: tuple[int, int]
) -> None:
    project_id, script_id = project_and_script
    with session_scope(engine) as session:
        ledger.record_operation(
            session,
            kind="suggestion",
            provider=ledger.GROQ,
            units=2877,
            unit_kind=ledger.TOKENS,
            cost_micros=900,
            model_id="openai/gpt-oss-120b",
            project_id=project_id,
            script_id=script_id,
        )

    with session_scope(engine) as session:
        roll = ledger.script_rollup(session, script_id)
    suggestion = next(k for k in roll.by_kind if k.kind == "suggestion")
    assert suggestion.provider == "groq"
    assert suggestion.units_display == "2,877 tok"
    assert roll.cost_micros == 900


# -- attribution ------------------------------------------------------------


def test_unattributed_spend_is_in_the_account_and_no_project(
    engine: Engine, project_and_script: tuple[int, int]
) -> None:
    """A probe is real money that belongs to no episode."""
    project_id, _ = project_and_script
    with session_scope(engine) as session:
        ledger.record_operation(
            session,
            kind="generation",
            cost_micros=10_000,
            units=100,
            project_id=project_id,
            billed_chars=100,
        )
        ledger.record_operation(
            session,
            kind="probe",
            cost_micros=5_900,
            units=59,
            project_id=None,
            billed_chars=59,
        )

    with session_scope(engine) as session:
        assert ledger.project_totals(session, project_id).cost_micros == 10_000
        assert ledger.unattributed(session).cost_micros == 5_900
        assert ledger.account_totals(session).cost_micros == 15_900


def test_the_cost_log_filters_and_totals_match_the_report(
    engine: Engine, project_and_script: tuple[int, int]
) -> None:
    project_id, script_id = project_and_script
    with session_scope(engine) as session:
        for micros in (1_000, 2_000, 3_000):
            ledger.record_operation(
                session,
                kind="generation",
                cost_micros=micros,
                units=micros / 10,
                project_id=project_id,
                script_id=script_id,
                billed_chars=int(micros / 10),
            )
        ledger.record_operation(
            session,
            kind="effect",
            cost_micros=500,
            units=2.5,
            unit_kind=ledger.SECONDS,
            project_id=project_id,
            script_id=script_id,
        )
        ledger.record_operation(session, kind="probe", cost_micros=99, project_id=None)

    with session_scope(engine) as session:
        rows = ledger.cost_log(session, project_id=project_id)
        roll = ledger.project_totals(session, project_id)
        effects_only = ledger.cost_log(session, project_id=project_id, kind="effect")
        loose = ledger.cost_log(session, unattributed_only=True)

    assert sum(r.cost_micros for r in rows) == roll.cost_micros == 6_500
    assert len(effects_only) == 1
    assert len(loose) == 1 and loose[0].kind == "probe"


# -- both units -------------------------------------------------------------


@needs_ffmpeg
async def test_credits_are_totalled_alongside_dollars(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """PRD §6.1: both figures, recorded and available, for every operation."""
    from narrate.effects import EffectRates, generate_slots
    from narrate.provider.mock import MockProvider, MockSFXProvider
    from narrate.runner import generate

    from .test_timeline_effects import _ingest

    script_id = _ingest(engine, registry)
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)
    await generate_slots(engine, script_id, MockSFXProvider(), settings, dry_run=False)

    with session_scope(engine) as session:
        roll = ledger.script_rollup(session, script_id)

    speech = next(k for k in roll.by_kind if k.kind == "generation")
    effects = next(k for k in roll.by_kind if k.kind == "effect")

    # v2 bills one credit per character; effects 40 per second.
    assert speech.credits == pytest.approx(speech.units)
    assert effects.credits == pytest.approx(effects.units * EffectRates.load().credits_per_second)
    assert roll.credits == pytest.approx(speech.credits + effects.credits)


# -- reconciliation stays character-only ------------------------------------


def test_reconciliation_ignores_seconds_and_tokens(
    engine: Engine, project_and_script: tuple[int, int]
) -> None:
    """Counting effect seconds or Groq tokens would manufacture drift."""
    from datetime import UTC, datetime, timedelta

    project_id, script_id = project_and_script
    with session_scope(engine) as session:
        ledger.record_operation(
            session,
            kind="generation",
            cost_micros=100_000,
            units=1000,
            billed_chars=1000,
            project_id=project_id,
            script_id=script_id,
        )
        ledger.record_operation(
            session,
            kind="effect",
            cost_micros=8_000,
            units=4.0,
            unit_kind=ledger.SECONDS,
            project_id=project_id,
            script_id=script_id,
        )
        ledger.record_operation(
            session,
            kind="suggestion",
            provider=ledger.GROQ,
            cost_micros=900,
            units=2877,
            unit_kind=ledger.TOKENS,
            project_id=project_id,
        )

    now = datetime.now(tz=UTC)
    with session_scope(engine) as session:
        chars, micros = ledger.ledger_total_micros(
            session, now - timedelta(days=1), now + timedelta(days=1)
        )

    assert chars == 1000
    assert micros == 100_000  # the effect and the suggestion are excluded
