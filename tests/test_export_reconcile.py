"""Export stitching and ledger reconciliation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, select

from narrate import audio, ledger
from narrate.db.models import Cut, Export, LedgerEntry
from narrate.db.session import session_scope
from narrate.export import NothingToExport, cut_fingerprint, export_script
from narrate.provider.mock import MockProvider
from narrate.reconcile import (
    UnreadableUsageResponse,
    extract_usage_total,
    reconcile,
)
from narrate.registry import Registry
from narrate.runner import generate
from narrate.settings import Settings

from .conftest import needs_ffmpeg

# -- export -----------------------------------------------------------------


@needs_ffmpeg
async def test_export_stitches_the_cut_into_one_track(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)

    result = export_script(engine, script_id, settings, gap_seconds=0.2)

    assert result.master.exists()
    assert result.mp3 is not None and result.mp3.exists()
    assert result.duration_s > 0

    # Every narration piece is delivered under its timeline name.
    assert len(result.names) == result.chunks
    for name in result.names.values():
        assert (result.out_dir / name).exists()

    # The plan and its machine-readable twin ship with it.
    assert result.plan.exists()
    assert result.timeline_json.exists()


@needs_ffmpeg
async def test_export_duration_includes_the_gaps(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)

    tight = export_script(engine, script_id, settings, gap_seconds=0.0, want_mp3=False)
    loose = export_script(engine, script_id, settings, gap_seconds=1.0, want_mp3=False)

    gaps = tight.chunks - 1
    assert loose.duration_s == pytest.approx(tight.duration_s + gaps, abs=0.25)


@needs_ffmpeg
async def test_export_records_runtime_for_cost_per_minute(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """C7 — the metric that answers 'can I afford twice-weekly?'"""
    _, script_id = project_and_script
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)
    export_script(engine, script_id, settings, want_mp3=False)

    with session_scope(engine) as session:
        assert session.scalars(select(Export)).all()
        assert ledger.exported_seconds(session, script_id) > 0
        assert ledger.cost_per_minute_micros(session, script_id) is not None


def test_export_refuses_when_a_chunk_has_no_take(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings
) -> None:
    """A gap in the middle of an episode is worse than no export."""
    _, script_id = project_and_script
    with pytest.raises(NothingToExport, match="no selected take"):
        export_script(engine, script_id, settings)


@needs_ffmpeg
async def test_export_refuses_a_partial_cut(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)
    with session_scope(engine) as session:
        first = session.scalars(select(Cut).where(Cut.script_id == script_id)).first()
        session.delete(first)

    with pytest.raises(NothingToExport, match="no selected take"):
        export_script(engine, script_id, settings)


def test_cut_fingerprint_identifies_a_specific_selection() -> None:
    assert cut_fingerprint([1, 2, 3]) == cut_fingerprint([1, 2, 3])
    assert cut_fingerprint([1, 2, 3]) != cut_fingerprint([1, 2, 4])
    # Order is part of the identity — the same takes in a different order is a
    # different track.
    assert cut_fingerprint([1, 2, 3]) != cut_fingerprint([3, 2, 1])


@needs_ffmpeg
def test_concat_reports_a_readable_error_for_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Missing audio"):
        audio.concat([tmp_path / "nope.mp3"], tmp_path / "out.wav")


# -- reconciliation ---------------------------------------------------------


def test_extract_usage_finds_the_credits_column() -> None:
    payload = {
        "columns": ["timestamp", "model", "credits"],
        "column_types": ["DateTime", "String", "Int"],
        "rows": [["2026-08-01", "eleven_v3", 1200], ["2026-08-02", "eleven_v3", 800]],
    }
    assert extract_usage_total(payload) == 2000


def test_extract_usage_falls_back_to_the_last_numeric_column() -> None:
    payload = {
        "columns": ["timestamp", "model", "total"],
        "column_types": ["DateTime", "String", "Float"],
        "rows": [["2026-08-01", "eleven_v3", 1500.0]],
    }
    assert extract_usage_total(payload) == 1500


def test_extract_usage_rejects_an_unrecognisable_response() -> None:
    """A contract change must fail loudly, not silently report zero usage."""
    with pytest.raises(UnreadableUsageResponse):
        extract_usage_total({"unexpected": "shape"})


def test_reconciliation_passes_when_the_ledger_agrees(engine: Engine) -> None:
    now = datetime.now(tz=UTC)
    with session_scope(engine) as session:
        session.add(
            LedgerEntry(project_id=1, kind="generation", billed_chars=1000, cost_micros=100_000)
        )

    payload = {
        "columns": ["ts", "credits"],
        "column_types": ["DateTime", "Int"],
        "rows": [["x", 1000]],
    }
    result = reconcile(engine, payload, now - timedelta(days=1), now + timedelta(days=1))

    assert result.local_chars == 1000
    assert result.provider_chars == 1000
    assert result.drift_pct == 0.0
    assert result.within_threshold is True


def test_reconciliation_flags_drift_beyond_the_threshold(engine: Engine) -> None:
    """PRD §10 targets estimate accuracy within 2%."""
    now = datetime.now(tz=UTC)
    with session_scope(engine) as session:
        session.add(
            LedgerEntry(project_id=1, kind="generation", billed_chars=1000, cost_micros=100_000)
        )

    payload = {
        "columns": ["ts", "credits"],
        "column_types": ["DateTime", "Int"],
        "rows": [["x", 1500]],
    }
    result = reconcile(engine, payload, now - timedelta(days=1), now + timedelta(days=1))

    assert result.within_threshold is False
    assert result.drift_pct < 0  # the ledger under-counted relative to the provider
    assert result.unaccounted_chars == 500
    assert "unaccounted for" in result.verdict


def test_reconciliation_explains_usage_the_ledger_never_saw(engine: Engine) -> None:
    """The common case on a real account: the provider counts everything.

    Usage from the web UI, another client, or `narrate probe` is not an error
    in the ledger — it is outside its scope, and the report should say so
    rather than implying the figures are wrong.
    """
    now = datetime.now(tz=UTC)
    payload = {
        "columns": ["ts", "credits"],
        "column_types": ["DateTime", "Int"],
        "rows": [["x", 900]],
    }
    result = reconcile(engine, payload, now - timedelta(days=1), now + timedelta(days=1))
    assert result.unaccounted_chars == 900
    assert result.coverage_pct == 0.0
    assert "came from somewhere else" in result.note


def test_reconciliation_flags_the_ledger_over_counting(engine: Engine) -> None:
    """The dangerous direction: more recorded than billed means double-counting."""
    now = datetime.now(tz=UTC)
    with session_scope(engine) as session:
        session.add(
            LedgerEntry(project_id=1, kind="generation", billed_chars=2000, cost_micros=200_000)
        )
    payload = {
        "columns": ["ts", "credits"],
        "column_types": ["DateTime", "Int"],
        "rows": [["x", 1000]],
    }
    result = reconcile(engine, payload, now - timedelta(days=1), now + timedelta(days=1))
    assert result.within_threshold is False
    assert "double-counting" in result.verdict


def test_reconciliation_is_persisted(engine: Engine) -> None:
    from narrate.db.models import Reconciliation

    now = datetime.now(tz=UTC)
    payload = {
        "columns": ["ts", "credits"],
        "column_types": ["DateTime", "Int"],
        "rows": [["x", 0]],
    }
    reconcile(engine, payload, now - timedelta(days=1), now)
    with session_scope(engine) as session:
        assert len(session.scalars(select(Reconciliation)).all()) == 1


@needs_ffmpeg
async def test_export_points_at_the_surviving_take_when_the_cut_is_dangling(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """When the selected take's audio is gone but a sibling survives, say so.

    Telling the operator to regenerate would spend money for nothing; moving
    the cut is instant and free.
    """
    from narrate.db.models import Cut, Take

    from .conftest import chunks_of, require

    _, script_id = project_and_script
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)
    await generate(
        engine, script_id, MockProvider(), registry, settings, dry_run=False, only=[1], force=True
    )

    with session_scope(engine) as session:
        first = chunks_of(session, script_id)[0]
        selected = require(session.get(Take, require(session.get(Cut, first.id)).take_id))
        assert selected.asset_path is not None
        Path(selected.asset_path).unlink()

    with pytest.raises(NothingToExport, match="narrate cut set"):
        export_script(engine, script_id, settings)
