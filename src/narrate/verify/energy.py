"""What the waveform says, independently of any transcriber.

Two measurements, both from one decode of the take:

* **bursts** — a short, loud island of sound walled in by digital silence on
  both sides. That is the exact signature the dropped "Them." left: the model
  kept the dramatic pauses around the word and rendered the word itself as
  about 200ms of noise. Across 184 pauses in a 21-minute episode it occurred
  once, at that spot.
* **voiced time** in a span — how much of a stretch is actually sound, used to
  tell a transcriber stretching one word over a gap (little voice) from one word
  covering a repeated phrase (a lot of voice).

A burst on its own is only ever a `review`. It is calibrated on eleven_v3,
which renders pauses as true digital silence; models that leave room tone in
their pauses never produce the silences it looks for, and the scan says so by
finding nothing rather than by guessing. Faster speech also narrows the gap
between a clipped word and a genuinely short sentence ("Do it." is ~480ms), so
the transcript has to agree before anything is called a fail.

Standard library plus ffmpeg: this runs in the core install, without the
`verify` extra.
"""

from __future__ import annotations

import math
import subprocess
from array import array
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from narrate import audio

SAMPLE_RATE = 16_000
FRAME_S = 0.01
_FRAME = int(SAMPLE_RATE * FRAME_S)

# A frame this quiet is digital silence — v3's pauses sit at -85 to -90 dB.
SILENCE_DB = -75.0
# How long a silence must last to wall in a burst.
SILENCE_MIN_S = 0.2
# The island between two walls: the clipped "Them" was 338ms; the shortest
# real one-word sentences in the episode were 477 and 496ms.
BURST_MAX_S = 0.40
# ...and loud enough to be speech rather than a breath or a click.
BURST_PEAK_DB = -30.0
# What counts as audible when measuring voiced time.
VOICED_DB = -40.0


@dataclass(frozen=True)
class Burst:
    start_s: float
    end_s: float
    peak_db: float

    @property
    def duration(self) -> float:
        return self.end_s - self.start_s


class Envelope:
    """10ms loudness frames for one take."""

    def __init__(self, frames_db: list[float]) -> None:
        self.frames_db = frames_db

    @classmethod
    def of(cls, path: Path) -> Envelope:
        """Decode `path` to 16kHz mono and measure every 10ms frame."""
        command = [
            audio.require_ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "s16le",
            "-",
        ]
        result = subprocess.run(command, capture_output=True, check=False)
        if result.returncode != 0:
            raise audio.FFmpegFailed(command, result.stderr.decode(errors="replace"))
        samples = array("h")
        samples.frombytes(result.stdout[: len(result.stdout) // 2 * 2])
        return cls(_frames_db(samples))

    @property
    def duration(self) -> float:
        return len(self.frames_db) * FRAME_S

    def has_digital_silence(self) -> bool:
        """Whether this take pauses in true silence — the precondition for bursts."""
        return any(db < SILENCE_DB for db in self.frames_db)

    def voiced(self, start: float, end: float) -> float:
        """Seconds of audible signal between two times."""
        lo = max(0, int(start / FRAME_S))
        hi = min(len(self.frames_db), math.ceil(end / FRAME_S))
        return sum(1 for db in self.frames_db[lo:hi] if db >= VOICED_DB) * FRAME_S

    def silences(self) -> list[tuple[float, float]]:
        """Runs of digital silence at least `SILENCE_MIN_S` long."""
        out: list[tuple[float, float]] = []
        run_start: int | None = None
        for index, db in enumerate([*self.frames_db, 0.0]):
            if db < SILENCE_DB:
                if run_start is None:
                    run_start = index
            elif run_start is not None:
                if (index - run_start) * FRAME_S >= SILENCE_MIN_S:
                    out.append((run_start * FRAME_S, index * FRAME_S))
                run_start = None
        return out

    def bursts(self) -> list[Burst]:
        """Short loud islands walled in by silence on both sides."""
        found: list[Burst] = []
        walls = self.silences()
        for (_, left_end), (right_start, _) in pairwise(walls):
            if right_start - left_end > BURST_MAX_S:
                continue
            lo, hi = int(left_end / FRAME_S), int(right_start / FRAME_S)
            island = self.frames_db[lo:hi]
            if island and max(island) > BURST_PEAK_DB:
                found.append(Burst(left_end, right_start, round(max(island), 1)))
        return found


def _frames_db(samples: array[int]) -> list[float]:
    frames: list[float] = []
    for offset in range(0, len(samples) - _FRAME + 1, _FRAME):
        frame = samples[offset : offset + _FRAME]
        mean_square = sum(s * s for s in frame) / _FRAME
        # Full scale for 16-bit audio is 32768; silence floors at -120 dB.
        frames.append(10 * math.log10(mean_square / 32768.0**2) if mean_square else -120.0)
    return frames
