"""Runner behaviour: dedupe, resume, retry policy, and the budget stop."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select

from narrate import ledger
from narrate.db.models import Chunk, Cut, LedgerEntry, Project, Take
from narrate.db.session import session_scope
from narrate.provider.mock import MockProvider
from narrate.registry import Registry
from narrate.runner import RunReport, generate
from narrate.settings import Settings

from .conftest import chunks_of, needs_ffmpeg, require

pytestmark = needs_ffmpeg


async def _run(
    engine: Engine,
    script_id: int,
    settings: Settings,
    registry: Registry,
    provider: MockProvider | None = None,
    **kwargs: Any,
) -> RunReport:
    return await generate(
        engine, script_id, provider or MockProvider(), registry, settings, **kwargs
    )


async def test_dry_run_sends_nothing(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    provider = MockProvider()
    report = await _run(engine, script_id, settings, registry, provider, dry_run=True)

    assert provider.calls == []
    assert report.dry_run is True
    assert all(o.status == "would_generate" for o in report.outcomes)
    with session_scope(engine) as session:
        assert session.scalars(select(Take)).all() == []


async def test_generates_every_chunk_and_writes_the_ledger(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    report = await _run(engine, script_id, settings, registry, dry_run=False)

    assert report.failed == []
    assert len(report.succeeded) == len(report.outcomes)

    with session_scope(engine) as session:
        takes = session.scalars(select(Take)).all()
        entries = session.scalars(select(LedgerEntry)).all()
        chunks = session.scalars(select(Chunk).where(Chunk.script_id == script_id)).all()

    assert len(takes) == len(chunks)
    assert len(entries) == len(chunks)
    assert all(t.status == "succeeded" for t in takes)
    # Cost came from the provider's header, not from len(text).
    assert all(t.cost_source == "header" for t in takes)
    assert all(t.asset_path for t in takes)


async def test_billed_chars_come_from_the_header(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    await _run(engine, script_id, settings, registry, dry_run=False)
    with session_scope(engine) as session:
        for take in session.scalars(select(Take)).all():
            assert take.billed_chars == take.submitted_chars
            assert take.cost_micros > 0
            assert take.rate_usd_per_1k > 0
            assert take.rate_card_version


async def test_editing_an_early_chunk_invalidates_the_ones_after_it(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """The other half of the continuity-fingerprint contract.

    On a stitching model, chunk N is conditioned on the audio of the chunks
    before it. Change chunk 1's text and chunk 2 is genuinely a different
    generation, so it must not be treated as already done.
    """
    _, script_id = project_and_script
    await _run(engine, script_id, settings, registry, dry_run=False)

    with session_scope(engine) as session:
        chunks_of(session, script_id)[0].text = "A completely different opening line."

    second = MockProvider()
    report = await _run(engine, script_id, settings, registry, second, dry_run=False)

    # Chunk 1 changed, and every downstream chunk was conditioned on it.
    assert len(report.succeeded) == 4
    assert report.skipped == []


async def test_rerunning_regenerates_nothing(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """The resume path — and the reason a duplicate charge cannot happen."""
    _, script_id = project_and_script
    await _run(engine, script_id, settings, registry, dry_run=False)

    second = MockProvider()
    report = await _run(engine, script_id, settings, registry, second, dry_run=False)

    assert second.calls == []
    assert len(report.skipped) == len(report.outcomes)
    assert report.spent_micros == 0


async def test_force_creates_a_second_take_without_disturbing_the_cut(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """F6: re-rolling chunk 1 must leave the rest, and the existing cut, alone."""
    _, script_id = project_and_script
    await _run(engine, script_id, settings, registry, dry_run=False)

    with session_scope(engine) as session:
        first_chunk = chunks_of(session, script_id)[0]
        original_cut = require(session.get(Cut, first_chunk.id)).take_id
        other_takes = {
            t.id for t in session.scalars(select(Take).where(Take.chunk_id != first_chunk.id)).all()
        }

    await _run(engine, script_id, settings, registry, dry_run=False, only=[1], force=True)

    with session_scope(engine) as session:
        takes = session.scalars(select(Take).where(Take.chunk_id == first_chunk.id)).all()
        still_there = {
            t.id for t in session.scalars(select(Take).where(Take.chunk_id != first_chunk.id)).all()
        }
        # A new take exists, but the deliberate selection is not overwritten.
        assert len(takes) == 2
        assert require(session.get(Cut, first_chunk.id)).take_id == original_cut
        assert still_there == other_takes


async def test_partial_failure_keeps_completed_takes(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """PRD §9: partial run failure never loses completed takes."""
    _, script_id = project_and_script
    with session_scope(engine) as session:
        chunks = chunks_of(session, script_id)
        doomed = chunks[-1].text[:20]

    provider = MockProvider(fail_containing={doomed}, latency_s=0)
    report = await _run(engine, script_id, settings, registry, provider, dry_run=False)

    assert len(report.failed) == 1
    assert len(report.succeeded) == len(chunks) - 1
    with session_scope(engine) as session:
        assert (
            len(session.scalars(select(Take).where(Take.status == "succeeded")).all())
            == len(chunks) - 1
        )


async def test_a_run_resumes_from_where_it_stopped(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    with session_scope(engine) as session:
        chunks = chunks_of(session, script_id)
        doomed = chunks[-1].text[:20]

    await _run(
        engine, script_id, settings, registry, MockProvider(fail_containing={doomed}), dry_run=False
    )

    # Second run: the provider now works, and only the missing chunk is sent.
    healthy = MockProvider()
    report = await _run(engine, script_id, settings, registry, healthy, dry_run=False)

    assert len(healthy.calls) == 1
    assert len(report.succeeded) == 1


async def test_retryable_failure_is_retried_then_succeeds(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    with session_scope(engine) as session:
        marker = chunks_of(session, script_id)[0].text[:20]

    provider = MockProvider(fail_once_containing={marker}, latency_s=0)
    report = await _run(engine, script_id, settings, registry, provider, dry_run=False)

    assert report.failed == []
    retried = [o for o in report.succeeded if o.attempts > 1]
    assert len(retried) == 1


async def test_unknown_outcome_is_never_retried(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """A read timeout may already have been billed. Retrying it risks double-charging."""
    _, script_id = project_and_script
    with session_scope(engine) as session:
        marker = chunks_of(session, script_id)[0].text[:20]

    provider = MockProvider(fail_containing={marker}, failure_mode="unknown", latency_s=0)
    report = await _run(engine, script_id, settings, registry, provider, dry_run=False)

    sent_for_that_chunk = [c for c in provider.calls if marker in c.text]
    assert len(sent_for_that_chunk) == 1, "an unknown outcome must not be retried"

    assert any(o.status == "unknown" for o in report.outcomes)
    with session_scope(engine) as session:
        unknown = session.scalars(select(Take).where(Take.status == "unknown")).all()
        assert len(unknown) == 1
        # It is written to the ledger at zero cost with a note, not omitted —
        # reconciliation has to be able to see that something happened here.
        entry = session.scalars(
            select(LedgerEntry).where(LedgerEntry.take_id == unknown[0].id)
        ).first()
        assert entry is not None
        assert entry.cost_micros == 0
        assert "unknown" in entry.note


async def test_budget_cap_stops_the_run(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """F10 / the budget stop flow: blocked rather than silently overspending."""
    _, script_id = project_and_script
    report = await _run(
        engine, script_id, settings, registry, dry_run=False, max_spend_micros=1, concurrency=1
    )
    assert report.stopped_reason == "budget"
    assert report.succeeded == []


async def test_first_take_becomes_the_cut_automatically(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    await _run(engine, script_id, settings, registry, dry_run=False)
    with session_scope(engine) as session:
        chunks = session.scalars(select(Chunk).where(Chunk.script_id == script_id)).all()
        cuts = session.scalars(select(Cut).where(Cut.script_id == script_id)).all()
    assert len(cuts) == len(chunks)


async def test_stitching_models_generate_sequentially(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """Chunk N conditions on the request id returned by N-1, so order matters."""
    _, script_id = project_and_script
    provider = MockProvider()
    report = await _run(engine, script_id, settings, registry, provider, dry_run=False)

    assert report.sequential is True
    # Every request after the first carries the prior request's id as context.
    assert provider.calls[0].previous_request_ids == ()
    if len(provider.calls) > 1:
        assert provider.calls[1].previous_request_ids != ()


async def test_v3_is_sent_no_continuity_context_at_all(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """v3 rejects both continuity mechanisms with a 400.

    Sending either would fail every chunk after the first, so the runner must
    send neither. This is the regression guard for that.
    """
    from narrate.db.models import Script

    _, script_id = project_and_script
    with session_scope(engine) as session:
        require(session.get(Script, script_id)).model_id = "eleven_v3"

    provider = MockProvider()
    report = await _run(engine, script_id, settings, registry, provider, dry_run=False)

    assert report.sequential is False
    assert len(provider.calls) == 4
    assert all(c.previous_request_ids == () for c in provider.calls)
    assert all(c.next_request_ids == () for c in provider.calls)
    assert all(c.previous_text is None for c in provider.calls)
    assert all(c.next_text is None for c in provider.calls)


async def test_prefix_tags_on_a_model_without_them_is_rejected(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    from narrate.db.models import Project

    project_id, script_id = project_and_script
    with session_scope(engine) as session:
        require(session.get(Project, project_id)).prefix_tags = "[urgent]"

    with pytest.raises(ValueError, match="does not support audio tags"):
        await _run(engine, script_id, settings, registry, dry_run=True)


@needs_ffmpeg
async def test_a_take_whose_audio_is_gone_is_regenerated(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """Pruned or deleted assets must not make a chunk look finished.

    Otherwise the run skips it, export finds nothing to stitch, and the only
    remedy is `--force` across the whole script.
    """
    _, script_id = project_and_script
    await _run(engine, script_id, settings, registry, dry_run=False)

    with session_scope(engine) as session:
        takes = list(session.scalars(select(Take)).all())
        assert takes[0].asset_path is not None
        Path(takes[0].asset_path).unlink()

    second = MockProvider()
    report = await _run(engine, script_id, settings, registry, second, dry_run=False)

    assert len(second.calls) == 1
    assert len(report.succeeded) == 1
    assert len(report.skipped) == 3

    # The original take stays in the ledger — it really was billed.
    with session_scope(engine) as session:
        assert len(list(session.scalars(select(LedgerEntry)).all())) == 5


@needs_ffmpeg
async def test_a_cut_pointing_at_missing_audio_is_repaired(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """Regenerating after a deleted asset must also fix the cut.

    Otherwise the new take exists but the cut still points at the take whose
    audio is gone, and export stays broken.
    """
    _, script_id = project_and_script
    await _run(engine, script_id, settings, registry, dry_run=False)

    with session_scope(engine) as session:
        first = chunks_of(session, script_id)[0]
        stale_take_id = require(session.get(Cut, first.id)).take_id
        stale = require(session.get(Take, stale_take_id))
        assert stale.asset_path is not None
        Path(stale.asset_path).unlink()

    await _run(engine, script_id, settings, registry, dry_run=False)

    with session_scope(engine) as session:
        cut = require(session.get(Cut, first.id))
        assert cut.take_id != stale_take_id
        fresh = require(session.get(Take, cut.take_id))
        assert fresh.asset_path is not None and Path(fresh.asset_path).exists()


@needs_ffmpeg
async def test_a_deliberate_cut_survives_a_re_roll(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """The guard above must not trample a valid selection."""
    _, script_id = project_and_script
    await _run(engine, script_id, settings, registry, dry_run=False)

    with session_scope(engine) as session:
        first = chunks_of(session, script_id)[0]
        chosen = require(session.get(Cut, first.id)).take_id

    await _run(engine, script_id, settings, registry, dry_run=False, only=[1], force=True)

    with session_scope(engine) as session:
        assert require(session.get(Cut, first.id)).take_id == chosen


# -- monthly budget cap (F10) ----------------------------------------------


@needs_ffmpeg
async def test_monthly_cap_blocks_before_anything_is_sent(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """Block at 100% of the cap — and block *before* spending, not partway."""
    project_id, script_id = project_and_script
    with session_scope(engine) as session:
        require(session.get(Project, project_id)).monthly_cap_micros = 1

    provider = MockProvider()
    report = await _run(engine, script_id, settings, registry, provider, dry_run=False)

    assert report.stopped_reason == "monthly_cap"
    assert provider.calls == []
    assert report.succeeded == []
    assert report.budget is not None and report.budget.blocked


@needs_ffmpeg
async def test_monthly_cap_can_be_overridden(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """PRD F10: block at 100% *pending override*, not permanently."""
    project_id, script_id = project_and_script
    with session_scope(engine) as session:
        require(session.get(Project, project_id)).monthly_cap_micros = 1

    report = await _run(engine, script_id, settings, registry, dry_run=False, override_cap=True)
    assert report.stopped_reason is None
    assert len(report.succeeded) == 4


@needs_ffmpeg
async def test_spend_already_on_the_ledger_counts_against_the_cap(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """A cap is about the month, not about one run."""
    project_id, script_id = project_and_script
    await _run(engine, script_id, settings, registry, dry_run=False)

    with session_scope(engine) as session:
        spent = ledger.month_spend_micros(session, project_id, ledger.month_start())
        assert spent > 0
        # Cap set just below what has already been spent.
        require(session.get(Project, project_id)).monthly_cap_micros = spent - 1

    provider = MockProvider()
    report = await _run(engine, script_id, settings, registry, provider, dry_run=False, force=True)
    assert report.stopped_reason == "monthly_cap"
    assert provider.calls == []


@needs_ffmpeg
async def test_no_cap_means_no_block(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    report = await _run(engine, script_id, settings, registry, dry_run=False)
    assert report.stopped_reason is None
    assert report.budget is not None
    assert report.budget.cap_micros is None
    assert report.budget.blocked is False


@needs_ffmpeg
async def test_takes_record_their_duration(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """Per-take runtime is what cost-per-minute divides by (C7)."""
    _, script_id = project_and_script
    await _run(engine, script_id, settings, registry, dry_run=False)

    with session_scope(engine) as session:
        takes = list(session.scalars(select(Take)).all())
        assert takes
        assert all(t.duration_s and t.duration_s > 0 for t in takes)
        # Available before any export exists.
        assert ledger.cut_seconds(session, script_id) > 0
        assert ledger.cost_per_minute_micros(session, script_id) is not None


# -- per-chunk overrides (F3, F4) ------------------------------------------


@needs_ffmpeg
async def test_a_chunk_override_beats_the_project_profile(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """F4: "some sections want different direction"."""
    _, script_id = project_and_script
    with session_scope(engine) as session:
        second = chunks_of(session, script_id)[1]
        second.voice_id = "other-voice"
        second.model_id = "eleven_v3"
        second.prefix_tags = "[urgent]"
        second.settings_json = '{"stability": 0.9}'
        marker = second.text[:20]

    provider = MockProvider()
    await _run(engine, script_id, settings, registry, provider, dry_run=False)

    overridden = next(c for c in provider.calls if marker in c.text)
    others = [c for c in provider.calls if marker not in c.text]

    assert overridden.voice_id == "other-voice"
    assert overridden.model_id == "eleven_v3"
    assert overridden.text.startswith("[urgent] ")
    assert overridden.settings == {"stability": 0.9}
    assert all(c.voice_id == "voice-1" for c in others)
    assert all(c.model_id == "eleven_multilingual_v2" for c in others)


@needs_ffmpeg
async def test_chunk_override_settings_are_filtered_to_the_chunks_model(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """An override must not smuggle a field the overriding model rejects."""
    _, script_id = project_and_script
    with session_scope(engine) as session:
        second = chunks_of(session, script_id)[1]
        second.model_id = "eleven_v3"
        second.settings_json = '{"stability": 0.4, "speed": 0.8, "similarity_boost": 0.7}'
        marker = second.text[:20]

    provider = MockProvider()
    await _run(engine, script_id, settings, registry, provider, dry_run=False)

    overridden = next(c for c in provider.calls if marker in c.text)
    assert overridden.settings == {"stability": 0.4}
