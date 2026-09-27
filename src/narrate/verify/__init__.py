"""Did every word in the script actually get spoken?

A text-to-speech model occasionally renders a take that is not what it was
sent: a word dropped or reduced to a noise, a word that was never in the
script, a word swapped for another. It happens inside the provider, on correct
input — the episode that motivated this had five such defects in 21 minutes,
one of them an unscripted "God" — and nothing in the API reports it. The
provider's own long-form product handles it the same way this does: by
checking each render for missing or additional words and regenerating.

* `compare`   — script against transcript: normalise, align, and decide what
                matters. Pure.
* `energy`    — the waveform's own evidence, from ffmpeg alone.
* `transcribe`— speech to text behind a seam: local faster-whisper (the
                optional `verify` extra), or a mock for tests.
* `service`   — check takes and record the verdict on each.

Nothing here spends money, and nothing downloads without being asked.
"""

from narrate.verify.compare import FAIL, INFO, REVIEW, Finding, Word
from narrate.verify.transcribe import (
    LocalWhisper,
    MockTranscriber,
    ModelBroken,
    ModelMissing,
    SttUnavailable,
    Transcriber,
    model_ready,
    stt_available,
)

__all__ = [
    "FAIL",
    "INFO",
    "REVIEW",
    "Finding",
    "LocalWhisper",
    "MockTranscriber",
    "ModelBroken",
    "ModelMissing",
    "SttUnavailable",
    "Transcriber",
    "Word",
    "model_ready",
    "stt_available",
]
