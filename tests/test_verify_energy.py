"""The waveform check: a short loud burst walled in by digital silence.

That is the exact signature the dropped "Them." left — the model kept the
pauses around the word and rendered the word as ~200ms of noise. These tests
build that shape from tones and silence, so the thresholds are pinned without
committing anybody's narration to the repository.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from narrate import audio
from narrate.verify.energy import BURST_MAX_S, Envelope

from .conftest import needs_ffmpeg

pytestmark = needs_ffmpeg


def _voice(path: Path, seconds: float) -> Path:
    """A tone at the level narration sits at.

    Not `audio.make_tone`, which is a gentle preview beep at about -33 dB —
    ffmpeg's sine source starts at an eighth of full scale. Speech in a real
    take peaks around -11 dB, and a burst is defined by being that loud.
    """
    subprocess.run(
        [
            audio.require_ffmpeg(),
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=220:sample_rate=44100:duration={seconds:.3f}",
            "-af",
            "volume=4",
            str(path),
        ],
        check=True,
    )
    return path


def _build(tmp_path: Path, *parts: tuple[str, float]) -> Path:
    """Concatenate ("tone"|"silence", seconds) pieces into one wav."""
    pieces = []
    for index, (kind, seconds) in enumerate(parts):
        if kind == "tone":
            pieces.append(_voice(tmp_path / f"{index:02d}-tone.wav", seconds))
        else:
            # Digital silence, written as PCM — the same kind v3 renders pauses as.
            pieces.append(audio.make_silence(tmp_path / f"{index:02d}-silence.wav", seconds))
    return audio.concat(pieces, tmp_path / "take.wav").path


def test_a_clipped_word_between_two_pauses_is_one_burst(tmp_path: Path) -> None:
    take = _build(
        tmp_path,
        ("tone", 1.0),
        ("silence", 0.45),
        ("tone", 0.2),
        ("silence", 0.45),
        ("tone", 1.0),
    )
    bursts = Envelope.of(take).bursts()
    assert len(bursts) == 1
    assert bursts[0].duration <= BURST_MAX_S
    assert 1.0 < bursts[0].start_s < 1.8


def test_a_short_real_sentence_is_not_a_burst(tmp_path: Path) -> None:
    """ "Do it." and "Good." ran 477 to 496ms in the episode; half a second of sound
    between two pauses is a sentence, not a garble."""
    take = _build(
        tmp_path,
        ("tone", 1.0),
        ("silence", 0.45),
        ("tone", 0.6),
        ("silence", 0.45),
        ("tone", 1.0),
    )
    assert Envelope.of(take).bursts() == []


def test_a_short_sound_without_silence_either_side_is_not_a_burst(tmp_path: Path) -> None:
    take = _build(tmp_path, ("tone", 1.0), ("tone", 0.2), ("tone", 1.0))
    assert Envelope.of(take).bursts() == []


def test_voiced_time_counts_sound_and_not_silence(tmp_path: Path) -> None:
    take = _build(tmp_path, ("silence", 1.0), ("tone", 1.0), ("silence", 1.0))
    envelope = Envelope.of(take)
    assert envelope.voiced(0.0, 1.0) == pytest.approx(0.0, abs=0.1)
    assert envelope.voiced(1.0, 2.0) == pytest.approx(1.0, abs=0.15)


def test_a_take_with_no_digital_silence_is_out_of_scope(tmp_path: Path) -> None:
    """Models that leave room tone in their pauses never produce the silences
    this looks for — the scan finds nothing rather than guessing."""
    take = _build(tmp_path, ("tone", 2.0))
    envelope = Envelope.of(take)
    assert not envelope.has_digital_silence()
    assert envelope.bursts() == []
