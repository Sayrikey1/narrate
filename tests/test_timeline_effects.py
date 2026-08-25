"""Timeline maths, effect reuse, and the editing plan."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import Engine, select

from narrate.db.models import EffectSlot, Project, Script
from narrate.db.session import session_scope
from narrate.effects import EffectRates, add_slot, generate_slots
from narrate.export import export_script
from narrate.ingest import ingest_script
from narrate.plan import render_plan, render_timeline_json
from narrate.provider.mock import MockProvider, MockSFXProvider
from narrate.registry import Registry
from narrate.runner import generate
from narrate.settings import Settings
from narrate.timeline import build_timeline, export_basename

from .conftest import needs_ffmpeg

SCRIPT = """[@ 00:00]

First paragraph of the episode, which runs for a sentence or two.

[SFX: wind howling, 4s]

Second paragraph picks the story up again after the wind.

[@ 00:20]

Third paragraph closes it out with something final.

[SFX: wind howling, 4s]

Fourth paragraph is a short coda.
"""


def _ingest(engine: Engine, registry: Registry, text: str = SCRIPT) -> int:
    """Project + script + chunks + slots, by the same path the CLI takes."""
    spec = registry.get("eleven_multilingual_v2")
    with session_scope(engine) as session:
        project = Project(name="Ep14", voice_id="voice-1", model_id=spec.model_id)
        session.add(project)
        session.flush()
        script = Script(project_id=project.id, title="Episode", source_text=text, source_sha256="x")
        session.add(script)
        session.flush()
        ingest_script(session, script, spec)
        return script.id


# -- the money-protecting property, end to end ------------------------------


@needs_ffmpeg
async def test_no_marker_ever_reaches_the_provider(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """The whole point of parsing before chunking.

    A marker left inline would be read aloud and billed, and on v3 misread as
    an audio tag. This asserts against what was actually sent.
    """
    script_id = _ingest(engine, registry)
    provider = MockProvider()
    await generate(engine, script_id, provider, registry, settings, dry_run=False)

    assert provider.calls
    for call in provider.calls:
        assert "[SFX" not in call.text
        assert "[@" not in call.text
        assert not re.search(r"\[\s*(SFX|@)", call.text, re.IGNORECASE)


# -- timeline maths ---------------------------------------------------------


@needs_ffmpeg
async def test_timeline_starts_are_the_sum_of_what_came_before(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = _ingest(engine, registry)
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)

    gap = 0.4
    with session_scope(engine) as session:
        tl = build_timeline(session, script_id, gap_seconds=gap)

    cursor = 0.0
    for entry in tl.narration:
        assert entry.start_s == pytest.approx(cursor, abs=1e-6)
        cursor = entry.end_s + gap


@needs_ffmpeg
async def test_an_effect_sits_exactly_on_a_chunk_boundary(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """Effects land on known times rather than interpolated ones."""
    script_id = _ingest(engine, registry)
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)

    with session_scope(engine) as session:
        tl = build_timeline(session, script_id, gap_seconds=0.4)

    starts = {e.start_s for e in tl.narration}
    assert tl.planned or tl.effects
    for entry in tl.planned + tl.effects:
        assert entry.start_s in starts


@needs_ffmpeg
async def test_effects_do_not_shift_the_narration(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """They are overlays. A track length that changed would be a bug."""
    script_id = _ingest(engine, registry)
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)

    with session_scope(engine) as session:
        before = build_timeline(session, script_id, gap_seconds=0.4).runtime_s

    await generate_slots(engine, script_id, MockSFXProvider(), settings, dry_run=False)

    with session_scope(engine) as session:
        after = build_timeline(session, script_id, gap_seconds=0.4)

    assert after.runtime_s == pytest.approx(before)
    assert after.effects


@needs_ffmpeg
async def test_anchor_drift_is_signed_and_reported(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = _ingest(engine, registry)
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)

    with session_scope(engine) as session:
        tl = build_timeline(session, script_id, gap_seconds=0.4)

    drifted = [e for e in tl.drifts if e.target_s]
    assert drifted, "the [@ 00:20] anchor should attach to a chunk"
    entry = drifted[0]
    assert entry.drift_s == pytest.approx(entry.start_s - (entry.target_s or 0), abs=1e-6)


def test_an_ungenerated_script_still_produces_a_timeline(
    engine: Engine, registry: Registry
) -> None:
    """Before anything is generated the plan is a shot list."""
    script_id = _ingest(engine, registry)
    with session_scope(engine) as session:
        tl = build_timeline(session, script_id, gap_seconds=0.4)
    assert tl.narration
    assert tl.is_complete is False
    assert all(e.duration_s == 0 for e in tl.narration)


# -- effect reuse -----------------------------------------------------------


@needs_ffmpeg
async def test_one_cue_used_twice_generates_once(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """The reason the library and the slots are separate tables."""
    script_id = _ingest(engine, registry)
    provider = MockSFXProvider()

    outcomes = await generate_slots(engine, script_id, provider, settings, dry_run=False)

    generated = [o for o in outcomes if o.status == "generated"]
    reused = [o for o in outcomes if o.status == "reused"]
    assert len(provider.calls) == 1, "the same cue must not be generated twice"
    assert len(generated) == 1
    assert len(reused) == 1
    # Both slots point at the one effect.
    assert {o.effect_id for o in generated + reused} == {generated[0].effect_id}


@needs_ffmpeg
async def test_reuse_is_charged_once(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    from narrate.db.models import LedgerEntry

    script_id = _ingest(engine, registry)
    outcomes = await generate_slots(engine, script_id, MockSFXProvider(), settings, dry_run=False)

    rates = EffectRates.load()
    with session_scope(engine) as session:
        entries = [e for e in session.scalars(select(LedgerEntry)).all() if e.kind == "effect"]
    assert len(entries) == 1
    assert entries[0].cost_micros == rates.cost_micros(4.0)
    assert sum(o.cost_micros for o in outcomes) == entries[0].cost_micros


@needs_ffmpeg
async def test_a_second_run_reuses_rather_than_regenerating(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = _ingest(engine, registry)
    await generate_slots(engine, script_id, MockSFXProvider(), settings, dry_run=False)

    second = MockSFXProvider()
    outcomes = await generate_slots(engine, script_id, second, settings, dry_run=False)

    assert second.calls == []
    assert all(o.status == "reused" for o in outcomes)


@needs_ffmpeg
async def test_effect_cost_comes_from_duration_not_the_header(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """Sound effects are billed per second; the header's unit is undocumented."""
    from narrate.db.models import Effect

    script_id = _ingest(engine, registry)
    await generate_slots(engine, script_id, MockSFXProvider(), settings, dry_run=False)

    rates = EffectRates.load()
    with session_scope(engine) as session:
        effect = session.scalars(select(Effect)).first()
    assert effect is not None
    assert effect.cost_source == "duration"
    assert effect.cost_micros == rates.cost_micros(effect.actual_duration_s or 0)
    # The header is kept for reconciliation, not used to charge.
    assert effect.observed_cost is not None


async def test_an_unaccepted_slot_is_never_generated(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """Suggestions must not be able to spend money on their own."""
    script_id = _ingest(engine, registry)
    with session_scope(engine) as session:
        for slot in session.scalars(select(EffectSlot)).all():
            slot.accepted = False
        add_slot(session, script_id, 2, "proposed rain", 5.0, source="suggested", accepted=False)

    provider = MockSFXProvider()
    outcomes = await generate_slots(engine, script_id, provider, settings, dry_run=False)

    assert provider.calls == []
    assert all(o.status == "skipped_unaccepted" for o in outcomes)


# -- naming and the plan ----------------------------------------------------


def test_export_basename_matches_the_convention() -> None:
    assert export_basename("Ep14", 0.0, 6.96) == "Ep14_00-00-00-000_00-00-06-960"
    assert export_basename("Ep14", 7.36, 11.36) == "Ep14_00-00-07-360_00-00-11-360"


def test_export_basename_sanitises_the_project_name() -> None:
    assert export_basename("My Channel / 2026!", 0.0, 1.0).startswith("My_Channel___2026_")
    assert export_basename("", 0.0, 1.0).startswith("project_")


def test_export_basenames_sort_chronologically() -> None:
    names = [export_basename("Ep", s, s + 1) for s in (0.0, 7.36, 14.24, 121.0)]
    assert names == sorted(names)


@needs_ffmpeg
async def test_plan_marks_a_planned_slot_as_outstanding(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """ "If no effect was selected, indicate where one should be"."""
    script_id = _ingest(engine, registry)
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)

    with session_scope(engine) as session:
        tl = build_timeline(session, script_id, gap_seconds=0.4)
        body = render_plan(tl, session)

    assert "Outstanding" in body
    assert "wind howling" in body
    assert "planned" in body


@needs_ffmpeg
async def test_plan_states_that_a_reused_effect_is_one_file(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = _ingest(engine, registry)
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)
    await generate_slots(engine, script_id, MockSFXProvider(), settings, dry_run=False)

    with session_scope(engine) as session:
        tl = build_timeline(session, script_id, gap_seconds=0.4)
        body = render_plan(tl, session)

    assert "generated once, placed at" in body


@needs_ffmpeg
async def test_exported_filenames_describe_their_own_audio(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """The single property the whole phase is judged on."""
    from narrate.audio import duration_seconds

    script_id = _ingest(engine, registry)
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)
    await generate_slots(engine, script_id, MockSFXProvider(), settings, dry_run=False)

    result = export_script(engine, script_id, settings, gap_seconds=0.4)

    stamp = re.compile(r"^.+_(\d+)-(\d+)-(\d+)-(\d+)_(\d+)-(\d+)-(\d+)-(\d+)\.\w+$")
    checked = 0
    for name in result.names.values():
        match = stamp.match(name)
        assert match, name
        h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(g) for g in match.groups())
        claimed = (h2 * 3600 + m2 * 60 + s2 + ms2 / 1000) - (h1 * 3600 + m1 * 60 + s1 + ms1 / 1000)
        actual = duration_seconds(result.out_dir / name)
        assert actual == pytest.approx(claimed, abs=0.06), name
        checked += 1
    assert checked == len(result.names) > 0


@needs_ffmpeg
async def test_timeline_json_matches_the_plan(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    import json

    script_id = _ingest(engine, registry)
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)
    result = export_script(engine, script_id, settings, gap_seconds=0.4)

    payload = json.loads(Path(result.timeline_json).read_text())
    assert payload["title"] == "Episode"
    assert payload["runtime_s"] == pytest.approx(result.duration_s, abs=0.5)
    assert len(payload["entries"]) >= len(result.names)

    with session_scope(engine) as session:
        tl = build_timeline(session, script_id, gap_seconds=0.4)
        assert json.loads(render_timeline_json(tl))["entries"][0]["start"] == "00:00:00.000"


# -- what the provider actually bills for effects ---------------------------


def test_fit_rate_recovers_the_observed_effect_billing() -> None:
    """Real numbers from `narrate probe --live --effects` on 2026-08-23.

    0.5s billed 3, 1.0s billed 5, 1.5s billed 8 — which is 5 per second with
    partial seconds rounded up, not the 40 credits/second the subscription
    docs quote. Pinned so a future probe run can be compared against it.
    """
    from narrate.probe import fit_rate

    assert fit_rate([(0.5, 3), (1.0, 5), (1.5, 8)]) == (5, True)


def test_fit_rate_distinguishes_exact_from_rounded() -> None:
    from narrate.probe import fit_rate

    assert fit_rate([(1.0, 10), (2.0, 20), (0.5, 5)]) == (10, False)
    assert fit_rate([(0.5, 1), (1.0, 7), (2.0, 3)]) is None


def test_the_plans_cost_per_minute_divides_into_the_runtime_it_reports(
    engine: Engine,
) -> None:
    """A delivered document must not disagree with itself.

    The plan is written *during* the export, so the ledger's own figure has no
    export row to use yet and falls back to the gapless sum of the takes —
    giving a rate that did not divide into the runtime on the line above it.
    """
    from narrate.plan import PlanContext, render_plan
    from narrate.timeline import Timeline, TimelineEntry

    timeline = Timeline(
        script_id=1,
        script_title="Ep",
        project_name="P",
        gap_seconds=0.4,
        entries=[
            TimelineEntry(
                index=1,
                kind="narration",
                start_s=0.0,
                end_s=60.0,
                label="One.",
                chunk_ordinal=1,
            ),
            TimelineEntry(
                index=2,
                kind="narration",
                start_s=60.4,
                end_s=120.4,
                label="Two.",
                chunk_ordinal=2,
            ),
        ],
    )
    with session_scope(engine) as session:
        text = render_plan(timeline, session, context=PlanContext(cost_micros=1_000_000))

    # $1.00 over the 120.4s this plan reports as its runtime.
    assert "Runtime **00:02:00.400**" in text
    assert "$0.4983/min" in text
