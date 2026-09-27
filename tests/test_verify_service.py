"""Checking real takes and recording the verdict on each.

Takes come from the mock provider, so they are real files with real ledger rows;
the transcriber is a stand-in that hears each take's own words unless told
otherwise (see `verify_support`). What is under test is the loop around the
detector: which takes get checked, what is stored, what is skipped next time,
and the second listen that separates a phantom from a real extra word.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import Engine, select

from narrate.db.models import Chunk, Take
from narrate.db.session import session_scope
from narrate.provider.mock import MockProvider
from narrate.registry import Registry
from narrate.runner import generate
from narrate.settings import Settings
from narrate.verify import service
from narrate.verify.compare import FAIL, INFO, REVIEW, RULES_VERSION, Word

from .conftest import needs_ffmpeg, require
from .verify_support import HearsTheScript

pytestmark = needs_ffmpeg


async def _generated(
    engine: Engine, script_id: int, settings: Settings, registry: Registry
) -> None:
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)


def _take_file(engine: Engine, script_id: int, ordinal: int) -> str:
    with session_scope(engine) as session:
        take = session.scalar(
            select(Take)
            .join(Chunk, Chunk.id == Take.chunk_id)
            .where(Chunk.script_id == script_id, Chunk.ordinal == ordinal)
        )
        return str(require(take).asset_path).rsplit("/", 1)[-1]


async def test_a_clean_episode_is_recorded_as_no_issues_found(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    await _generated(engine, script_id, settings, registry)

    results = service.verify_script(engine, script_id, HearsTheScript(engine), registry)

    assert [r.status for r in results] == ["clear"] * 4
    with session_scope(engine) as session:
        for take in session.scalars(select(Take)).all():
            assert take.verify_status == "clear"
            assert take.verified_at is not None
            assert take.verifier == f"hears-the-script|rules:{RULES_VERSION}"


async def test_a_dropped_word_marks_the_take_suspect_and_says_where(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    await _generated(engine, script_id, settings, registry)
    broken = _take_file(engine, script_id, 2)

    results = service.verify_script(
        engine, script_id, HearsTheScript(engine, drops={broken: "halvorsen"}), registry
    )

    by_chunk = {r.chunk_ordinal: r for r in results}
    assert by_chunk[2].status == "suspect"
    [problem] = by_chunk[2].problems
    assert (problem.severity, problem.kind, problem.expected) == (FAIL, "missing", "halvorsen")
    assert all(by_chunk[n].status == "clear" for n in (1, 3, 4))

    with session_scope(engine) as session:
        take = session.scalar(select(Take).where(Take.asset_path.like(f"%{broken}")))
        stored = json.loads(require(take).verify_findings_json or "[]")
        assert stored[0]["expected"] == "halvorsen"


async def test_a_phantom_extra_word_is_dropped_on_a_second_listen(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """The transcriber invents words too. One that is not there when the same
    few seconds are heard again was never spoken."""
    _, script_id = project_and_script
    await _generated(engine, script_id, settings, registry)
    target = _take_file(engine, script_id, 3)
    hears = HearsTheScript(engine, extras={target: ("however", 0.3)}, phantoms={target})

    results = service.verify_script(engine, script_id, hears, registry)

    chunk3 = next(r for r in results if r.chunk_ordinal == 3)
    assert chunk3.status == "clear"
    [extra] = [f for f in chunk3.findings if f.kind == "extra"]
    assert extra.severity == INFO
    assert "second listen" in extra.note
    # The second listen was a window, not the whole take again.
    assert any(name == target and window is not None for name, window in hears.calls)


async def test_a_real_extra_word_survives_the_second_listen(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    await _generated(engine, script_id, settings, registry)
    confident = _take_file(engine, script_id, 1)
    unsure = _take_file(engine, script_id, 4)
    hears = HearsTheScript(engine, extras={confident: ("god", 0.94), unsure: ("well", 0.3)})

    results = {
        r.chunk_ordinal: r for r in service.verify_script(engine, script_id, hears, registry)
    }

    assert [f.severity for f in results[1].findings if f.kind == "extra"] == [FAIL]
    assert [f.severity for f in results[4].findings if f.kind == "extra"] == [REVIEW]
    assert results[1].status == "suspect"
    assert results[4].status == "review"


async def test_a_take_checked_already_is_not_checked_again_unless_asked(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """Re-running after a regeneration should cost only the new take's time."""
    _, script_id = project_and_script
    await _generated(engine, script_id, settings, registry)
    hears = HearsTheScript(engine)

    service.verify_script(engine, script_id, hears, registry)
    first = len(hears.calls)
    assert service.verify_script(engine, script_id, hears, registry) == []
    assert len(hears.calls) == first

    again = service.verify_script(engine, script_id, hears, registry, recheck=True)
    assert len(again) == 4


async def test_the_stored_state_is_reported_without_checking_anything(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """A take found suspect yesterday is still suspect today."""
    _, script_id = project_and_script
    await _generated(engine, script_id, settings, registry)
    broken = _take_file(engine, script_id, 2)
    service.verify_script(
        engine, script_id, HearsTheScript(engine, drops={broken: "halvorsen"}), registry
    )

    known = {r.chunk_ordinal: r.status for r in service.stored(engine, script_id)}
    assert known == {1: "clear", 2: "suspect", 3: "clear", 4: "clear"}


async def test_a_missing_audio_file_is_an_error_not_a_pass(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    await _generated(engine, script_id, settings, registry)
    with session_scope(engine) as session:
        take = session.scalar(select(Take).order_by(Take.id))
        assert take is not None and take.asset_path
        Path(take.asset_path).unlink()

    results = service.verify_script(engine, script_id, HearsTheScript(engine), registry)
    assert results[0].status == "error"
    assert "missing" in (results[0].error or "")


async def test_audio_tags_are_not_expected_to_be_spoken_on_v3(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """A v3 take is sent "[urgent] …" and never says "urgent"."""
    _, script_id = project_and_script
    await _generated(engine, script_id, settings, registry)
    with session_scope(engine) as session:
        take = require(session.scalar(select(Take).order_by(Take.id)))
        chunk = require(session.get(Chunk, take.chunk_id))
        take.model_id = "eleven_v3"
        take.submitted_text = f"[urgent][fast-paced] {chunk.text}"
        expected = service.expected_text(take, chunk, registry)
    assert "urgent" not in expected
    assert expected.startswith(chunk.text.split()[0])


async def test_a_dialogue_takes_speaker_labels_are_not_expected(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """A dialogue take records "Ada: line" for readability; the labels were
    never sent, so every speaker name would otherwise be a missing word."""
    _, script_id = project_and_script
    await _generated(engine, script_id, settings, registry)
    with session_scope(engine) as session:
        take = require(session.scalar(select(Take).order_by(Take.id)))
        chunk = require(session.get(Chunk, take.chunk_id))
        take.submitted_text = "Ada: Hello there.\nBo: Hi."
        take.voices_json = json.dumps(["v1", "v2"])
        chunk.turns_json = json.dumps(
            [
                {"speaker": "Ada", "voice_id": "v1", "text": "Hello there."},
                {"speaker": "Bo", "voice_id": "v2", "text": "Hi."},
            ]
        )
        assert service.expected_text(take, chunk, registry) == "Hello there. Hi."


async def test_without_a_transcriber_the_waveform_alone_never_clears_a_take(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """It cannot hear an extra word or a swapped one, so finding nothing is
    not a clean bill of health."""
    _, script_id = project_and_script
    await _generated(engine, script_id, settings, registry)
    results = service.verify_script(engine, script_id, None, registry)
    assert results
    assert all(r.status == "unverified" for r in results)


async def test_a_waveform_only_check_never_replaces_a_transcripts_verdict(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """The waveform cannot see an extra or swapped word. Letting it overwrite a
    transcript's "suspect" would quietly un-flag a take — even with recheck."""
    _, script_id = project_and_script
    await _generated(engine, script_id, settings, registry)
    broken = _take_file(engine, script_id, 2)
    service.verify_script(
        engine, script_id, HearsTheScript(engine, drops={broken: "halvorsen"}), registry
    )

    service.verify_script(engine, script_id, None, registry, recheck=True)

    known = {r.chunk_ordinal: r.status for r in service.stored(engine, script_id)}
    assert known[2] == "suspect"


class _SaysTheYearTwoWays:
    """First pass hears "1998"; the second listen hears it spelled out."""

    identity = "two-ways"

    def transcribe(self, path: Path, window: tuple[float, float] | None = None) -> list[Word]:
        first = [
            Word(" It", 0.0, 0.2, 0.99),
            Word(" was", 0.2, 0.4, 0.99),
            Word(" 1998", 0.4, 0.9, 0.3),
            Word(" then.", 0.9, 1.2, 0.99),
        ]
        again = [
            Word(" It", 0.0, 0.2, 0.99),
            Word(" was", 0.2, 0.4, 0.99),
            Word(" nineteen", 0.4, 0.6, 0.6),
            Word(" ninety-eight", 0.6, 0.9, 0.6),
            Word(" then.", 0.9, 1.2, 0.99),
        ]
        return again if window is not None else first


def test_the_second_listen_recognises_a_number_heard_again(tmp_path: Path) -> None:
    """Heard as "nineteen ninety-eight" the second time, it is the same "1998".
    Matched word by word it looked like a different word, and a real extra word
    was waved through as a phantom."""
    take = tmp_path / "take.mp3"
    take.write_bytes(b"")
    target = service.Target(
        take_id=1, chunk_ordinal=1, take_ordinal=1, path=take, expected="It was then.", in_cut=True
    )

    result = service.check(target, _SaysTheYearTwoWays(), use_energy=False)

    [extra] = [f for f in result.findings if f.kind == "extra"]
    assert extra.note == "heard again on a second listen"
    assert extra.severity == FAIL


def test_a_waveform_check_is_recognised_whatever_rules_made_it() -> None:
    """A stored waveform-only result from an older rules version must still be
    told apart from a transcript's, or a new version could never re-check it."""
    assert not service.heard_by_transcript(service.stamp(None))
    assert not service.heard_by_transcript("energy-only|rules:1")
    assert not service.heard_by_transcript(None)
    assert not service.heard_by_transcript("")
    assert service.heard_by_transcript("fw:small.en:int8:b5:ct2-4.8.2|rules:1")


def test_an_empty_model_directory_setting_means_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`NARRATE_STT_MODEL_DIR=` would otherwise be the current directory, and a
    480 MB download would land wherever the command happened to run."""
    monkeypatch.setenv("NARRATE_STT_MODEL_DIR", "")
    assert Settings().stt_model_dir is None


class _SplitsTheWordTheSecondTime:
    """First pass hears an unscripted "Cashflow"; the second, "cash flow"."""

    identity = "splits"

    def transcribe(self, path: Path, window: tuple[float, float] | None = None) -> list[Word]:
        if window is None:
            return [
                Word(" Read", 0.0, 0.2, 0.99),
                Word(" Cashflow", 0.2, 0.7, 0.4),
                Word(" today.", 0.7, 1.1, 0.99),
            ]
        return [
            Word(" Read", 0.0, 0.2, 0.99),
            Word(" cash", 0.2, 0.45, 0.6),
            Word(" flow", 0.45, 0.7, 0.6),
            Word(" today.", 0.7, 1.1, 0.99),
        ]


def test_the_second_listen_recognises_a_word_split_differently(tmp_path: Path) -> None:
    take = tmp_path / "take.mp3"
    take.write_bytes(b"")
    target = service.Target(
        take_id=1, chunk_ordinal=1, take_ordinal=1, path=take, expected="Read today.", in_cut=True
    )

    result = service.check(target, _SplitsTheWordTheSecondTime(), use_energy=False)

    [extra] = [f for f in result.findings if f.kind == "extra"]
    assert extra.note == "heard again on a second listen"
    assert extra.severity == FAIL


async def test_the_timeline_knows_which_takes_speech_to_text_heard(
    engine: Engine,
    project_and_script: tuple[int, int],
    settings: Settings,
    registry: Registry,
) -> None:
    """What `export --require-verified` reads: a take the waveform check marked
    for review was still never heard, and must not pass as checked."""
    from narrate.timeline import build_timeline

    _, script_id = project_and_script
    await _generated(engine, script_id, settings, registry)
    service.verify_script(engine, script_id, HearsTheScript(engine), registry)
    with session_scope(engine) as session:
        first = session.scalars(select(Take).order_by(Take.id)).first()
        assert first is not None
        first.verify_status = "review"
        first.verifier = service.stamp(None)

    with session_scope(engine) as session:
        narration = build_timeline(session, script_id).narration
    assert [e.heard for e in narration] == [False] + [True] * (len(narration) - 1)
