"""Waveforms: peaks from the audio itself, worked out once per file."""

from __future__ import annotations

from pathlib import Path

import pytest

from narrate import audio, waveform
from narrate.settings import Settings

from .conftest import needs_ffmpeg

pytestmark = needs_ffmpeg


def test_a_tone_draws_as_bars_that_follow_its_fades(tmp_path: Path) -> None:
    """The test tone fades in and out: the bars at its ends are lower."""
    tone = audio.make_tone(tmp_path / "tone.wav", 1.0)
    peaks = audio.peaks(tone, 16)

    assert len(peaks) == 16
    assert max(peaks) == 1.0
    assert min(peaks[4:12]) > 0.9
    assert peaks[0] < peaks[8] and peaks[-1] < peaks[8]


def test_silence_draws_flat(tmp_path: Path) -> None:
    quiet = audio.make_silence(tmp_path / "quiet.wav", 0.5)
    assert audio.peaks(quiet, 8) == [0.0] * 8


def test_peaks_are_worked_out_once_per_file(
    tmp_path: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    tone = audio.make_tone(tmp_path / "tone.wav", 0.5)
    calls: list[Path] = []
    real = audio.peaks

    def counting(path: Path, buckets: int = 64, sample_rate: int = 2000) -> list[float]:
        calls.append(path)
        return real(path, buckets, sample_rate)

    monkeypatch.setattr(audio, "peaks", counting)
    first = waveform.peaks_for(tone, 12, settings)
    again = waveform.peaks_for(tone, 12, settings)

    assert first == again
    assert len(calls) == 1
    # Kept in narrate's own folder, never beside the file.
    assert not any(p.suffix == ".json" for p in tmp_path.iterdir())
    assert list(waveform.cache_dir(settings).glob("*.json"))
