"""One operation producing a finished episode — speech and effects together."""

from __future__ import annotations

from sqlalchemy import Engine, select

from narrate import ledger
from narrate.db.models import EffectSlot, LedgerEntry, Project, Take
from narrate.db.session import session_scope
from narrate.effects import EffectRates
from narrate.produce import produce, project
from narrate.provider.mock import MockProvider, MockSFXProvider
from narrate.registry import Registry
from narrate.settings import Settings

from .conftest import needs_ffmpeg, require
from .test_timeline_effects import _ingest

pytestmark = needs_ffmpeg


# -- the money test ---------------------------------------------------------


async def test_the_cap_sees_both_phases_not_just_the_speech(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """A cap the narration alone fits under, but the whole run does not.

    The speech runner used to project its cap check from the pending chunks
    alone. In a combined run that sees half the spend, so an episode could pass
    the gate on narration and then take the month past the cap on cues. This is
    the regression that guards that.
    """
    script_id = _ingest(engine, registry)
    projection = project(engine, script_id, registry)

    assert projection.speech_micros > 0
    assert projection.effect_micros > 0

    # Enough for the narration, not enough for narration plus cues.
    cap = projection.speech_micros + projection.effect_micros // 2
    with session_scope(engine) as session:
        require(session.get(Project, 1)).monthly_cap_micros = cap

    tts, sfx = MockProvider(), MockSFXProvider()
    report = await produce(engine, script_id, tts, sfx, registry, settings, dry_run=False)

    assert report.blocked is True
    assert report.stopped_reason == "monthly_cap"
    # Nothing was sent to either provider.
    assert tts.calls == []
    assert sfx.calls == []
    with session_scope(engine) as session:
        assert session.scalars(select(Take)).all() == []
        assert session.scalars(select(LedgerEntry)).all() == []


async def test_a_cap_covering_the_whole_run_lets_it_through(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = _ingest(engine, registry)
    projection = project(engine, script_id, registry)

    with session_scope(engine) as session:
        require(session.get(Project, 1)).monthly_cap_micros = projection.total_micros * 2

    report = await produce(
        engine,
        script_id,
        MockProvider(),
        MockSFXProvider(),
        registry,
        settings,
        dry_run=False,
    )
    assert report.blocked is False
    assert report.speech is not None and report.speech.succeeded
    assert report.effects_generated > 0


# -- the estimate -----------------------------------------------------------


def test_the_projection_is_the_sum_and_agrees_with_the_ledger(
    engine: Engine, registry: Registry
) -> None:
    """One number, computed one way."""
    script_id = _ingest(engine, registry)
    projection = project(engine, script_id, registry)

    with session_scope(engine) as session:
        todo = ledger.outstanding(session, script_id, registry)

    assert projection.total_micros == todo.cost_micros
    assert projection.chunks == todo.chunks
    assert projection.effects == todo.effects
    assert projection.speech_micros + projection.effect_micros == projection.total_micros


def test_without_effects_the_projection_is_speech_only(engine: Engine, registry: Registry) -> None:
    script_id = _ingest(engine, registry)
    full = project(engine, script_id, registry)
    speech_only = project(engine, script_id, registry, with_effects=False)

    assert speech_only.effect_micros == 0
    assert speech_only.effects == 0
    assert speech_only.speech_micros == full.speech_micros
    assert speech_only.total_micros < full.total_micros


# -- one operation, both phases --------------------------------------------


async def test_one_call_produces_speech_and_effects(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = _ingest(engine, registry)
    tts, sfx = MockProvider(), MockSFXProvider()

    report = await produce(engine, script_id, tts, sfx, registry, settings, dry_run=False)

    assert report.speech is not None
    assert len(report.speech.succeeded) == 3
    assert tts.calls and sfx.calls
    assert report.spent_micros > 0

    with session_scope(engine) as session:
        kinds = {e.kind for e in session.scalars(select(LedgerEntry)).all()}
    assert kinds == {"generation", "effect"}


async def test_no_effects_behaves_exactly_as_before(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """`--no-effects` must be indistinguishable from the old speech-only run."""
    script_id = _ingest(engine, registry)
    sfx = MockSFXProvider()

    report = await produce(
        engine,
        script_id,
        MockProvider(),
        sfx,
        registry,
        settings,
        dry_run=False,
        with_effects=False,
    )

    assert sfx.calls == []
    assert report.effects == []
    with session_scope(engine) as session:
        kinds = {e.kind for e in session.scalars(select(LedgerEntry)).all()}
        takes = len(list(session.scalars(select(Take)).all()))
    assert kinds == {"generation"}
    assert takes == 3


async def test_reuse_still_charges_once_inside_a_combined_run(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """The fixture names the same cue twice; that is one generation."""
    script_id = _ingest(engine, registry)
    sfx = MockSFXProvider()

    report = await produce(
        engine, script_id, MockProvider(), sfx, registry, settings, dry_run=False
    )

    with session_scope(engine) as session:
        slots = len(list(session.scalars(select(EffectSlot)).all()))
        charges = [e for e in session.scalars(select(LedgerEntry)).all() if e.kind == "effect"]

    assert slots == 2
    assert len(sfx.calls) == 1
    assert len(charges) == 1
    assert report.effects_generated == 1
    assert report.effects_reused == 1


async def test_a_dry_run_sends_nothing_to_either_provider(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = _ingest(engine, registry)
    tts, sfx = MockProvider(), MockSFXProvider()

    report = await produce(engine, script_id, tts, sfx, registry, settings, dry_run=True)

    assert tts.calls == []
    assert sfx.calls == []
    assert report.dry_run is True
    assert report.projection.total_micros > 0
    # The dry run still reports what it would have done.
    assert any(o.status == "would_generate" for o in report.effects)


async def test_failed_speech_does_not_stop_the_cues(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """A cue does not depend on the narration around it."""
    from .conftest import chunks_of

    script_id = _ingest(engine, registry)
    with session_scope(engine) as session:
        doomed = chunks_of(session, script_id)[-1].text[:20]

    tts = MockProvider(fail_containing={doomed}, latency_s=0)
    sfx = MockSFXProvider()
    report = await produce(engine, script_id, tts, sfx, registry, settings, dry_run=False)

    assert report.speech is not None and report.speech.failed
    assert sfx.calls, "the effects phase should still have run"
    assert report.effects_generated > 0


async def test_the_report_sums_both_phases(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = _ingest(engine, registry)
    report = await produce(
        engine,
        script_id,
        MockProvider(),
        MockSFXProvider(),
        registry,
        settings,
        dry_run=False,
    )

    with session_scope(engine) as session:
        recorded = sum(e.cost_micros for e in session.scalars(select(LedgerEntry)).all())

    assert report.spent_micros == recorded
    rates = EffectRates.load()
    assert report.spent_micros > rates.cost_micros(1.0)


def test_describe_shows_the_split(engine: Engine, registry: Registry) -> None:
    from narrate.produce import describe

    script_id = _ingest(engine, registry)
    lines = describe(project(engine, script_id, registry))
    assert any("narration" in line for line in lines)
    assert any("effects" in line for line in lines)
