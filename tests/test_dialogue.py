"""Multiple people speaking in one script.

Two modes, and they are deliberately different trades:

* **Per-turn voices** work on every model. Each turn is its own chunk with its
  own voice, separately re-rollable and separately costed.
* **Dialogue** is `eleven_v3` only. Consecutive turns go to
  `POST /v1/text-to-dialogue` in one request, so the model hears the whole
  exchange — at the cost of coarser resume granularity.

The load-bearing test in here is the last one: a dialogue charge has to stay
inside reconciliation. Effects were once filtered out of episode totals by a
`kind == "generation"` check, and this is the same shape of mistake waiting to
be made again.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, select

from narrate import ledger
from narrate.db.models import Chunk, LedgerEntry, Project, Script, Take
from narrate.db.session import session_scope
from narrate.ingest import ingest_script
from narrate.provider.base import DialogueRequest, DialogueTurn, dialogue_idempotency_key
from narrate.provider.mock import MockProvider
from narrate.registry import Registry
from narrate.runner import build_jobs, generate
from narrate.settings import Settings

from .conftest import needs_ffmpeg

SCRIPT = """[CAST] Morag = voice_morag · Keeper = voice_keeper

Morag: You knew it would end. Everyone knew, and nobody said it aloud.

Keeper: I hoped not. Hoping is a different thing from knowing.

Morag: The light went out on a Tuesday, in the middle of the afternoon.

Keeper: And nobody came to watch. That is the part I mind.
"""


def _setup(
    engine: Engine,
    registry: Registry,
    *,
    model: str = "eleven_v3",
    dialogue: bool = False,
    text: str = SCRIPT,
) -> int:
    with session_scope(engine) as session:
        session.add(Project(name="Cast", model_id=model, voice_id="fallback"))
        session.flush()
        script = Script(project_id=1, title="Two speakers", source_text=text, source_sha256="x")
        session.add(script)
        session.flush()
        ingest_script(session, script, registry.get(model), dialogue=dialogue)
        return int(script.id)


# ---------------------------------------------------------------------------
# Mode A — one chunk per turn, on any model
# ---------------------------------------------------------------------------


def test_each_turn_becomes_its_own_chunk_with_its_own_voice(
    engine: Engine, registry: Registry
) -> None:
    _setup(engine, registry, model="eleven_multilingual_v2")
    with session_scope(engine) as session:
        rows = list(session.scalars(select(Chunk).order_by(Chunk.ordinal)).all())

    assert [c.voice_id for c in rows] == [
        "voice_morag",
        "voice_keeper",
        "voice_morag",
        "voice_keeper",
    ]
    # No prefix survives into anything billable.
    assert not any(c.text.startswith(("Morag:", "Keeper:")) for c in rows)


def test_a_turn_after_a_chunk_marker_keeps_its_own_voice(
    engine: Engine, registry: Registry
) -> None:
    """A turn's cut is exact. Snapped to the nearest sentence start it landed
    on the marker line, still inside Morag's turn, and Keeper's words went to
    her voice."""
    text = SCRIPT.replace("\n\nKeeper: I hoped", "\n\n## [CHUNK]\n\nKeeper: I hoped")
    _setup(engine, registry, model="eleven_multilingual_v2", text=text)
    with session_scope(engine) as session:
        rows = list(session.scalars(select(Chunk).order_by(Chunk.ordinal)).all())

    assert [c.voice_id for c in rows] == [
        "voice_morag",
        "voice_keeper",
        "voice_morag",
        "voice_keeper",
    ]


def test_a_turn_ending_in_a_dash_does_not_take_the_next_speakers_words(
    engine: Engine, registry: Registry
) -> None:
    text = SCRIPT.replace("nobody said it aloud.", "nobody said it —")
    _setup(engine, registry, model="eleven_multilingual_v2", text=text)
    with session_scope(engine) as session:
        rows = list(session.scalars(select(Chunk).order_by(Chunk.ordinal)).all())

    assert rows[0].text.endswith("nobody said it —")
    assert rows[1].text.startswith("I hoped not.") and rows[1].voice_id == "voice_keeper"


def test_every_piece_of_a_long_turn_is_in_its_speakers_voice(
    engine: Engine, registry: Registry
) -> None:
    """Only the first piece of a subdivided turn has an offset; the rest used
    to fall back to the narrator."""
    long_turn = "\n\n".join(
        f"Paragraph {i} of a long speech. " + "The light turned and turned, all night. " * 20
        for i in range(12)
    )
    text = (
        f"[CAST] Morag = voice_morag · Keeper = voice_keeper\n\nMorag: {long_turn}\n\nKeeper: Yes."
    )
    _setup(engine, registry, model="eleven_multilingual_v2", text=text)
    with session_scope(engine) as session:
        rows = list(session.scalars(select(Chunk).order_by(Chunk.ordinal)).all())

    assert len(rows) >= 3
    assert {c.voice_id for c in rows[:-1]} == {"voice_morag"}
    assert rows[-1].voice_id == "voice_keeper"


def test_a_chunk_voice_beats_the_script_and_the_project(engine: Engine, registry: Registry) -> None:
    """The reason this mode needed no new generation code: `chunk.voice_id` was
    already the highest-priority term in `resolve_chunk_config`."""
    script_id = _setup(engine, registry, model="eleven_multilingual_v2")
    with session_scope(engine) as session:
        script = session.get(Script, script_id)
        project = session.get(Project, 1)
        assert script is not None and project is not None
        jobs = build_jobs(session, script, project, registry, Settings(api_key=None))

    assert [j.request.voice_id for j in jobs] == [
        "voice_morag",
        "voice_keeper",
        "voice_morag",
        "voice_keeper",
    ]


@needs_ffmpeg
async def test_per_turn_records_one_generation_row_per_turn(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = _setup(engine, registry, model="eleven_multilingual_v2")
    provider = MockProvider()
    await generate(engine, script_id, provider, registry, settings, dry_run=False)

    assert len(provider.calls) == 4
    assert not provider.dialogue_calls
    with session_scope(engine) as session:
        rows = list(session.scalars(select(LedgerEntry)).all())
    assert len(rows) == 4
    assert {r.kind for r in rows} == {"generation"}


# ---------------------------------------------------------------------------
# Mode B — dialogue, v3 only
# ---------------------------------------------------------------------------


def test_dialogue_packs_the_turns_into_one_chunk(engine: Engine, registry: Registry) -> None:
    _setup(engine, registry, dialogue=True)
    with session_scope(engine) as session:
        rows = list(session.scalars(select(Chunk).order_by(Chunk.ordinal)).all())

    assert len(rows) == 1
    assert rows[0].source == "dialogue"
    turns = json.loads(rows[0].turns_json or "[]")
    assert [t["speaker"] for t in turns] == ["Morag", "Keeper", "Morag", "Keeper"]
    assert [t["voice_id"] for t in turns] == [
        "voice_morag",
        "voice_keeper",
        "voice_morag",
        "voice_keeper",
    ]


def test_a_turn_keeps_its_own_words(engine: Engine, registry: Registry) -> None:
    """Sliced by offset rather than searched for, because two speakers can say
    exactly the same thing and a search would give both lines to the first."""
    text = "[CAST] A = va · B = vb\n\nA: The same words.\n\nB: The same words.\n"
    _setup(engine, registry, dialogue=True, text=text)
    with session_scope(engine) as session:
        row = session.scalars(select(Chunk)).one()
    turns = json.loads(row.turns_json or "[]")
    assert [(t["speaker"], t["text"]) for t in turns] == [
        ("A", "The same words."),
        ("B", "The same words."),
    ]


def test_turns_are_packed_to_the_dialogue_ceiling(engine: Engine, registry: Registry) -> None:
    """2,000 characters across all inputs, which is *tighter* than v3's own
    5,000-character request limit — so it is its own packing problem."""
    ceiling = registry.get("eleven_v3").dialogue_max_chars
    line = "x" * 700
    text = "[CAST] A = va · B = vb\n\n" + "\n\n".join(
        f"{'A' if i % 2 == 0 else 'B'}: {line}" for i in range(6)
    )
    _setup(engine, registry, dialogue=True, text=text)

    with session_scope(engine) as session:
        rows = list(session.scalars(select(Chunk).order_by(Chunk.ordinal)).all())

    assert len(rows) > 1
    for row in rows:
        if row.turns_json:
            total = sum(len(t["text"]) for t in json.loads(row.turns_json))
            assert total <= ceiling


@needs_ffmpeg
async def test_dialogue_sends_one_request_in_several_voices(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = _setup(engine, registry, dialogue=True)
    provider = MockProvider()
    await generate(engine, script_id, provider, registry, settings, dry_run=False)

    assert not provider.calls, "dialogue must not fall back to the speech endpoint"
    assert len(provider.dialogue_calls) == 1
    sent = provider.dialogue_calls[0]
    assert [t.voice_id for t in sent.turns] == [
        "voice_morag",
        "voice_keeper",
        "voice_morag",
        "voice_keeper",
    ]
    # The prefixes were stripped at ingest and must not be reintroduced.
    assert not any(t.text.startswith(("Morag:", "Keeper:")) for t in sent.turns)


@needs_ffmpeg
async def test_a_dialogue_take_records_every_voice_it_used(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = _setup(engine, registry, dialogue=True)
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)

    with session_scope(engine) as session:
        take = session.scalars(select(Take)).one()

    # `voice_id` is non-null and every existing query reads it, so it names the
    # lead speaker rather than a sentinel.
    assert take.voice_id == "voice_morag"
    assert json.loads(take.voices_json or "[]") == ["voice_morag", "voice_keeper"]


@needs_ffmpeg
async def test_rerunning_a_dialogue_generates_nothing(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = _setup(engine, registry, dialogue=True)
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)

    provider = MockProvider()
    again = await generate(engine, script_id, provider, registry, settings, dry_run=False)

    assert not provider.dialogue_calls
    assert again.spent_micros == 0
    assert [o.status for o in again.outcomes] == ["skipped_duplicate"]


def test_renaming_a_speaker_is_not_a_new_generation() -> None:
    """The audio would be byte-for-byte identical, so re-charging for a rename
    would be charging for a label."""
    a = DialogueRequest(turns=(DialogueTurn("One.", "v1", "Ada"),))
    b = DialogueRequest(turns=(DialogueTurn("One.", "v1", "Renamed"),))
    assert dialogue_idempotency_key(a) == dialogue_idempotency_key(b)


def test_reordering_the_turns_is_a_new_generation() -> None:
    a = DialogueRequest(turns=(DialogueTurn("One.", "v1"), DialogueTurn("Two.", "v2")))
    b = DialogueRequest(turns=(DialogueTurn("Two.", "v2"), DialogueTurn("One.", "v1")))
    assert dialogue_idempotency_key(a) != dialogue_idempotency_key(b)


# ---------------------------------------------------------------------------
# Falling back rather than failing
# ---------------------------------------------------------------------------


def test_a_model_that_cannot_do_dialogue_falls_back_and_says_so(
    engine: Engine, registry: Registry
) -> None:
    with session_scope(engine) as session:
        session.add(Project(name="P", model_id="eleven_multilingual_v2", voice_id="v"))
        session.flush()
        script = Script(project_id=1, title="T", source_text=SCRIPT, source_sha256="x")
        session.add(script)
        session.flush()
        result = ingest_script(
            session,
            script,
            registry.get("eleven_multilingual_v2"),
            dialogue=True,
        )

    assert not result.dialogue
    assert result.turns_assigned == 4
    assert any("cannot generate dialogue" in w for w in result.warnings)


@needs_ffmpeg
async def test_a_lone_turn_takes_the_ordinary_speech_path(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """One speaker is not a dialogue. Sending a single-turn request to the
    dialogue endpoint would cost more for no benefit."""
    text = "[CAST] A = va\n\nA: Only one person speaks in this script.\n"
    script_id = _setup(engine, registry, dialogue=True, text=text)
    provider = MockProvider()
    await generate(engine, script_id, provider, registry, settings, dry_run=False)

    assert len(provider.calls) == 1
    assert not provider.dialogue_calls


# ---------------------------------------------------------------------------
# The money test
# ---------------------------------------------------------------------------


@needs_ffmpeg
async def test_a_dialogue_charge_stays_inside_reconciliation(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """Dialogue is character-billed, so it appears in the provider's own
    counter. Recording it under a new `kind` would put it outside
    `ledger_total_micros`, whose filter is `kind.in_(("generation", "probe"))`,
    and outside `waste_pct` — manufacturing drift against the provider from the
    first dialogue run onwards. Effects made exactly this mistake once.
    """
    script_id = _setup(engine, registry, dialogue=True)
    report = await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)
    assert report.spent_micros > 0

    now = datetime.now(UTC)
    with session_scope(engine) as session:
        entry = session.scalars(select(LedgerEntry)).one()
        assert entry.kind == "generation"
        assert entry.unit_kind == "characters"

        rollup = ledger.script_rollup(session, script_id)
        assert rollup.cost_micros == report.spent_micros

        chars, micros = ledger.ledger_total_micros(
            session, now - timedelta(days=1), now + timedelta(days=1)
        )

    assert micros == report.spent_micros
    assert chars == entry.billed_chars


@pytest.mark.parametrize("chars", [0, 1, 500, 2000])
def test_dialogue_pricing_is_declared_not_guessed(registry: Registry, chars: int) -> None:
    """No ElevenLabs page states the dialogue rate, and the only figures
    available are third-party. So the multiplier is 1.0 and flagged unverified
    until `narrate probe --live --dialogue` measures the header — the same way
    docs/probe-effects.md settled the per-second question for effects.
    """
    spec = registry.get("eleven_v3")
    assert not spec.dialogue_cost_verified
    assert spec.dialogue_cost_micros(chars) == spec.cost_micros(chars)


# ---------------------------------------------------------------------------
# The probe that settles what dialogue actually costs
# ---------------------------------------------------------------------------


async def test_the_dialogue_probe_reports_the_billed_ratio() -> None:
    """The rate card promised `narrate probe --live --dialogue` from the day
    dialogue was added. A declared-but-unmeasurable multiplier is a liability in
    a tool whose claim is that it can say what an episode cost."""
    from narrate import probe as probe_mod

    report = await probe_mod.run_dialogue_probe(
        MockProvider(), "eleven_v3", ["voice_a", "voice_b"], "mp3_44100_128"
    )

    assert report.error is None
    assert report.turns == 2
    assert report.voices == 2
    assert report.submitted_chars == sum(len(t) for t, _ in probe_mod.DIALOGUE_TURNS)
    # The mock bills exactly what was submitted, so the ratio is 1.0 offline —
    # the point of the live probe is that the real one may not be.
    assert report.billed_chars == report.submitted_chars
    assert report.multiplier == 1.0
    assert any("ratio" in finding for finding in report.findings)


async def test_the_dialogue_probe_writes_a_report(tmp_path: Path) -> None:
    from narrate import probe as probe_mod

    report = await probe_mod.run_dialogue_probe(
        MockProvider(), "eleven_v3", ["voice_a", "voice_b"], "mp3_44100_128"
    )
    written = probe_mod.write_dialogue_report(report, tmp_path / "probe-dialogue.md")
    text = written.read_text()

    assert "# Dialogue probe" in text
    assert "Billed / submitted" in text
    # The distinction that stops somebody setting the multiplier from this alone.
    assert "not the same thing as a" in text


async def test_a_refused_dialogue_probe_leaves_the_multiplier_unverified() -> None:
    """A 400 from the endpoint has to read as "still unknown", not as a
    measurement of zero."""
    from narrate import probe as probe_mod

    provider = MockProvider(fail_containing={"You knew"}, failure_mode="fatal")
    report = await probe_mod.run_dialogue_probe(
        provider, "eleven_v3", ["voice_a", "voice_b"], "mp3_44100_128"
    )

    assert report.multiplier is None
    assert any("unverified" in finding for finding in report.findings)
