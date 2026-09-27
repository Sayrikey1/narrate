"""A transcriber for tests that hears each take's own words.

It looks the take up by its audio path and returns what that take was asked to
say, one word every 0.3s — perfectly, unless told to drop a word from a given
take file or to invent one. That makes it possible to test the whole loop
(generate, check, regenerate, move the cut) against real takes on disk from the
mock provider, whose audio is silence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import Engine, select

from narrate.db.models import Take
from narrate.db.session import session_scope
from narrate.verify.compare import Word

STEP_S = 0.3
HOLE_S = 0.9


@dataclass
class HearsTheScript:
    engine: Engine
    # file name -> a word this take "dropped", leaving a hole where it was.
    drops: dict[str, str] = field(default_factory=dict)
    # file name -> (word, probability) spoken but never written, after word 2.
    extras: dict[str, tuple[str, float]] = field(default_factory=dict)
    # file names whose extra word vanishes on a second listen (a phantom).
    phantoms: set[str] = field(default_factory=set)
    calls: list[tuple[str, tuple[float, float] | None]] = field(default_factory=list)
    identity: str = "hears-the-script"

    def transcribe(self, path: Path, window: tuple[float, float] | None = None) -> list[Word]:
        self.calls.append((path.name, window))
        with session_scope(self.engine) as session:
            take = session.scalar(select(Take).where(Take.asset_path == str(path)))
            text = take.submitted_text if take else ""

        words: list[Word] = []
        cursor = 0.0
        for index, word in enumerate(text.split()):
            if self.drops.get(path.name) == word.strip(".,").lower():
                cursor += HOLE_S
                continue
            words.append(Word(f" {word}", cursor, cursor + STEP_S * 0.9, 0.99))
            cursor += STEP_S
            if index == 1 and path.name in self.extras:
                extra, probability = self.extras[path.name]
                if window is None or path.name not in self.phantoms:
                    words.append(Word(f" {extra}", cursor, cursor + STEP_S * 0.9, probability))
                cursor += STEP_S

        if window is None:
            return words
        lo, hi = window
        return [w for w in words if w.end > lo and w.start < hi]
