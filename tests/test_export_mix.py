"""The finished episode: every master also written with its effects mixed in.

Mock narration and mock effects are silence, so each test swaps the effects'
audio for a tone and listens for where it lands: nowhere in the plain master,
at the timeline's times in the `_fx` one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine, select

from narrate import audio
from narrate.db.models import Effect, EffectSlot, Project, Script
from narrate.db.session import session_scope
from narrate.effects import generate_slots
from narrate.export import export_script
from narrate.ingest import ingest_script
from narrate.provider.mock import MockProvider, MockSFXProvider
from narrate.registry import Registry
from narrate.runner import generate
from narrate.settings import Settings
from narrate.timeline import build_timeline
from narrate.verify.energy import Envelope

from .conftest import needs_ffmpeg

pytestmark = needs_ffmpeg

SCRIPT = """First paragraph of the episode, which runs for a sentence or two.

[SFX: wind howling, 1s]

Second paragraph picks the story up again after the wind, and carries on for
a good while longer so that there is room under it for a looping bed.

[SFX: door slam, 1s]

Third paragraph closes it out with something final.
"""


async def _episode(engine: Engine, registry: Registry, settings: Settings) -> int:
    """An episode whose effects are audible tones over silent narration."""
    spec = registry.get("eleven_multilingual_v2")
    with session_scope(engine) as session:
        project = Project(name="Ep", voice_id="voice-1", model_id=spec.model_id)
        session.add(project)
        session.flush()
        script = Script(
            project_id=project.id, title="Episode", source_text=SCRIPT, source_sha256="x"
        )
        session.add(script)
        session.flush()
        ingest_script(session, script, spec)
        script_id = script.id
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)
    await generate_slots(engine, script_id, MockSFXProvider(), settings, dry_run=False)
    with session_scope(engine) as session:
        for effect in session.scalars(select(Effect)).all():
            assert effect.asset_path
            wav = Path(effect.asset_path).with_suffix(".tone.wav")
            audio.make_tone(wav, 1.0, 440)
            audio.to_mp3(wav, Path(effect.asset_path))
    return script_id


def _heard(path: Path, start: float, end: float) -> float:
    return Envelope.of(path).voiced(start, end)


def _loudest(path: Path, start: float, end: float) -> float:
    """The loudest 10ms in a window, in dBFS: -inf-ish for digital silence.

    A bed is meant to sit well under the voice, so "is it there" is a question
    for the peak, not for the speech-level threshold `voiced` uses.
    """
    frames = Envelope.of(path).frames_db
    lo, hi = int(start / 0.01), int(end / 0.01)
    return max(frames[lo:hi])


# -- the mixer ----------------------------------------------------------------


def test_a_sound_lands_where_it_is_placed_and_the_base_sets_the_length(
    tmp_path: Path,
) -> None:
    base = audio.make_silence(tmp_path / "base.wav", 4.0)
    tone = audio.make_tone(tmp_path / "tone.wav", 0.5, 440)

    out = audio.mix(base, [audio.Overlay(tone, start_s=2.0)], tmp_path / "out.wav")

    assert audio.duration_seconds(out) == pytest.approx(4.0, abs=0.05)
    assert _heard(out, 0.0, 1.9) == 0.0
    assert _heard(out, 2.0, 2.5) > 0.3


def test_a_looped_sound_fills_the_length_asked_for(tmp_path: Path) -> None:
    base = audio.make_silence(tmp_path / "base.wav", 6.0)
    tone = audio.make_tone(tmp_path / "tone.wav", 0.5, 440)

    out = audio.mix(base, [audio.Overlay(tone, start_s=1.0, length_s=4.0)], tmp_path / "out.wav")

    # Heard in every second of the four, and not after.
    for second in range(1, 5):
        assert _heard(out, float(second), second + 1.0) > 0.3, second
    assert _heard(out, 5.1, 6.0) == 0.0


def test_the_base_is_not_turned_down_by_the_mix(tmp_path: Path) -> None:
    """`amix` divides by its input count unless told not to — the narration
    would get quieter for every effect added."""
    base = audio.make_tone(tmp_path / "base.wav", 3.0, 220)
    tone = audio.make_tone(tmp_path / "tone.wav", 0.2, 880)

    out = audio.mix(base, [audio.Overlay(tone, start_s=2.5, gain_db=-30.0)], tmp_path / "out.wav")

    before = audio.loudness_lufs(base)
    after = audio.loudness_lufs(out)
    assert before is not None and after is not None
    assert after == pytest.approx(before, abs=0.5)


def test_silence_has_no_loudness_to_measure(tmp_path: Path) -> None:
    assert audio.loudness_lufs(audio.make_silence(tmp_path / "s.wav", 2.0)) is None
    assert audio.loudness_lufs(audio.make_tone(tmp_path / "t.wav", 2.0, 440)) is not None


# -- the export -----------------------------------------------------------------


async def test_every_format_is_written_with_and_without_its_effects(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = await _episode(engine, registry, settings)

    result = export_script(engine, script_id, settings, formats=["wav", "mp3"])

    assert set(result.masters) == set(result.fx_masters) == {"wav", "mp3"}
    assert result.fx_masters["mp3"].name == "Episode_fx.mp3"
    assert result.masters["mp3"].name == "Episode.mp3"
    assert result.effects_mixed == 2
    with session_scope(engine) as session:
        effects = build_timeline(session, script_id, gap_seconds=settings.gap_seconds).effects
    plain, mixed = result.masters["wav"], result.fx_masters["wav"]
    assert audio.duration_seconds(mixed) == pytest.approx(audio.duration_seconds(plain), abs=0.05)
    for effect in effects:
        window = (effect.start_s + 0.1, effect.start_s + 0.6)
        assert _loudest(plain, *window) < -70
        assert _loudest(mixed, *window) > -40


async def test_a_looped_effect_runs_under_its_whole_chunk(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = await _episode(engine, registry, settings)
    with session_scope(engine) as session:
        first = session.scalars(select(EffectSlot).order_by(EffectSlot.id)).first()
        assert first is not None
        first.loop = True
        timeline = build_timeline(session, script_id, gap_seconds=settings.gap_seconds)
    bed = timeline.effects[0]
    chunk_end = next(e.end_s for e in timeline.narration if e.chunk_ordinal == bed.chunk_ordinal)
    assert chunk_end - bed.start_s > 3.0  # the test needs room for a bed

    result = export_script(engine, script_id, settings, formats=["wav"])

    mixed = result.fx_masters["wav"]
    # Well past the one-second sound, still under the chunk: the loop — and,
    # being a bed, quieter than the same sound played once.
    middle = (bed.start_s + chunk_end) / 2
    assert _loudest(mixed, middle - 0.5, middle + 0.5) > -60
    once = timeline.effects[1]
    assert _loudest(mixed, middle - 0.5, middle + 0.5) < _loudest(
        mixed, once.start_s + 0.1, once.start_s + 0.6
    )


async def test_no_mix_writes_the_plain_masters_and_clears_an_old_mix(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = await _episode(engine, registry, settings)
    first = export_script(engine, script_id, settings, formats=["mp3"])
    assert first.fx_masters

    again = export_script(engine, script_id, settings, formats=["mp3"], mix_effects=False)

    assert not again.fx_masters and again.effects_mixed == 0
    assert not list(again.out_dir.glob("Episode_fx.*"))
    assert (again.out_dir / "Episode.mp3").exists()


async def test_pieces_from_an_earlier_export_are_cleared_and_nothing_else(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """A regeneration moves the timings; the old pieces' names would lie."""
    script_id = await _episode(engine, registry, settings)
    first = export_script(engine, script_id, settings, formats=["wav"])
    stale = first.out_dir / "Ep_00-09-00-000_00-09-30-000.mp3"
    stale.write_bytes(b"")
    kept = first.out_dir / "my notes.txt"
    kept.write_text("mine")

    again = export_script(engine, script_id, settings, formats=["wav"])

    assert not stale.exists()
    assert kept.exists()
    assert all((again.out_dir / name).exists() for name in again.names.values())


# -- found in review ---------------------------------------------------------


def test_loudness_is_measured_as_the_mono_mix_hears_it(tmp_path: Path) -> None:
    """A stereo sound whose channels cancel when folded to mono is silent in the
    mix, however loud it reads as stored."""
    import subprocess

    cancelling = tmp_path / "cancel.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-af", "volume=4,pan=stereo|c0=c0|c1=-1*c0",
            str(cancelling),
        ],
        check=True,
    )  # fmt: skip
    assert audio.loudness_lufs(cancelling) is None


def test_a_sound_shorter_than_a_gating_block_still_has_a_loudness(tmp_path: Path) -> None:
    click = audio.make_tone(tmp_path / "click.wav", 0.3, 880)
    assert audio.loudness_lufs(click) is not None


async def test_an_unreadable_effect_is_left_out_and_the_export_still_ships(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = await _episode(engine, registry, settings)
    with session_scope(engine) as session:
        first = session.scalars(select(Effect).order_by(Effect.id)).first()
        assert first is not None and first.asset_path
        Path(first.asset_path).write_bytes(b"<html>busy</html>" * 50)

    result = export_script(engine, script_id, settings, formats=["mp3"])

    assert result.effects_mixed == 1
    assert (result.out_dir / "Episode.mp3").exists()
    assert (result.out_dir / "Episode_fx.mp3").exists()


async def test_a_loop_longer_than_its_chunk_stops_with_the_chunk(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = await _episode(engine, registry, settings)
    with session_scope(engine) as session:
        slot = session.scalars(select(EffectSlot).order_by(EffectSlot.id)).first()
        assert slot is not None
        slot.loop = True
        effect = session.get(Effect, slot.effect_id)
        assert effect is not None and effect.asset_path
        long_wav = Path(effect.asset_path).with_suffix(".long.wav")
        audio.make_tone(long_wav, 30.0, 440)
        audio.to_mp3(long_wav, Path(effect.asset_path))
        effect.actual_duration_s = audio.duration_seconds(Path(effect.asset_path))
        timeline = build_timeline(session, script_id, gap_seconds=settings.gap_seconds)
    bed = timeline.effects[0]
    chunk_end = next(e.end_s for e in timeline.narration if e.chunk_ordinal == bed.chunk_ordinal)
    next_effect = timeline.effects[1].start_s

    result = export_script(engine, script_id, settings, formats=["wav"])

    mixed = result.fx_masters["wav"]
    # In the gap after the chunk, before the next effect: nothing of the bed.
    assert _loudest(mixed, chunk_end + 0.05, min(chunk_end + 0.35, next_effect)) < -70


async def test_the_plan_says_effects_are_mixed_only_when_they_were(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = await _episode(engine, registry, settings)

    mixed = export_script(engine, script_id, settings, formats=["mp3"])
    assert "Episode_fx.mp3" in mixed.plan.read_text()

    plain = export_script(engine, script_id, settings, formats=["mp3"], mix_effects=False)
    text = plain.plan.read_text()
    assert "_fx" not in text and "overlays" in text


async def test_an_fx_master_in_a_format_no_longer_asked_for_is_cleared(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = await _episode(engine, registry, settings)
    export_script(engine, script_id, settings, formats=["mp3", "m4a"])

    again = export_script(engine, script_id, settings, formats=["mp3"])

    assert (again.out_dir / "Episode_fx.mp3").exists()
    assert not (again.out_dir / "Episode_fx.m4a").exists()


async def test_a_failed_export_leaves_the_previous_pieces_in_place(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """Old pieces go only once a new export has succeeded, so a folder never
    disagrees with its own plan."""
    script_id = await _episode(engine, registry, settings)
    first = export_script(engine, script_id, settings, formats=["mp3"])
    before = sorted(p.name for p in first.out_dir.iterdir())
    with session_scope(engine) as session:
        last = session.scalars(select(Effect).order_by(Effect.id.desc())).first()
        assert last is not None and last.asset_path
        Path(last.asset_path).unlink()

    with pytest.raises(FileNotFoundError):
        export_script(engine, script_id, settings, formats=["mp3"], gap_seconds=1.0)

    after = {p.name for p in first.out_dir.iterdir()}
    assert set(before) <= after


async def test_a_take_with_no_recorded_length_is_measured_before_placing_effects(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    from narrate.db.models import Cut, Take

    script_id = await _episode(engine, registry, settings)
    with session_scope(engine) as session:
        first_cut = session.scalars(select(Cut).order_by(Cut.chunk_id)).first()
        assert first_cut is not None
        take = session.get(Take, first_cut.take_id)
        assert take is not None
        real = take.duration_s
        take.duration_s = None

    export_script(engine, script_id, settings, formats=["wav"])

    with session_scope(engine) as session:
        take = session.get(Take, first_cut.take_id)
        assert take is not None and take.duration_s == pytest.approx(real, abs=0.05)
