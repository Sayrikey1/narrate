"""Offline provider. Produces real, playable audio and spends nothing.

This is what makes the whole pipeline — chunking, the runner's concurrency and
resume logic, the ledger, and export — testable end to end at zero cost. It
generates genuine mp3 silence of a plausible length for the text and reports a
`character-cost` the way the real provider does, so the ledger follows an
identical code path in tests and in production.
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from pathlib import Path

from narrate.audio import make_silence, to_mp3
from narrate.provider.base import (
    DialogueRequest,
    RetryableError,
    SFXRequest,
    SFXResult,
    TTSRequest,
    TTSResult,
    UnknownOutcomeError,
)

# Roughly the docs' own figure: v3's 5,000-character limit is described as
# about five minutes of audio.
SECONDS_PER_CHAR = 0.06


class MockProvider:
    """A drop-in `TTSProvider` that never makes a network call.

    Failure injection matches on text content rather than call order, so it
    stays deterministic when the runner has several requests in flight.
    """

    name = "mock"

    def __init__(
        self,
        latency_s: float = 0.02,
        fail_containing: set[str] | None = None,
        fail_once_containing: set[str] | None = None,
        failure_mode: str = "retryable",
        cache_dir: Path | None = None,
    ) -> None:
        self.latency_s = latency_s
        self.fail_containing = fail_containing or set()
        self.fail_once_containing = fail_once_containing or set()
        self.failure_mode = failure_mode
        self.calls: list[TTSRequest] = []
        self.dialogue_calls: list[DialogueRequest] = []
        self._failed_once: set[str] = set()
        self._cache = cache_dir or Path(tempfile.gettempdir()) / "narrate-mock"
        self._cache.mkdir(parents=True, exist_ok=True)

    def _should_fail(self, text: str) -> bool:
        return any(marker in text for marker in self.fail_containing)

    def _should_fail_once(self, text: str) -> bool:
        for marker in self.fail_once_containing:
            if marker in text and marker not in self._failed_once:
                self._failed_once.add(marker)
                return True
        return False

    async def synthesize(self, request: TTSRequest) -> TTSResult:
        self.calls.append(request)
        await asyncio.sleep(self.latency_s)

        if self._should_fail(request.text) or self._should_fail_once(request.text):
            if self.failure_mode == "unknown":
                raise UnknownOutcomeError("Mock: simulated read timeout after send.")
            raise RetryableError("Mock: simulated 429.", status=429, retry_after_s=0.01)

        digest = hashlib.sha256(request.text.encode("utf-8")).hexdigest()[:16]
        path = self._cache / f"{digest}.mp3"
        if not path.exists():
            seconds = max(0.3, round(len(request.text) * SECONDS_PER_CHAR, 2))
            wav = self._cache / f"{digest}.wav"
            make_silence(wav, seconds)
            to_mp3(wav, path)
            wav.unlink(missing_ok=True)

        return TTSResult(
            audio=path.read_bytes(),
            request_id=f"mock-{digest}",
            billed_chars=len(request.text),
            headers={
                "character-cost": str(len(request.text)),
                "request-id": f"mock-{digest}",
                "maximum-concurrent-requests": "5",
                "current-concurrent-requests": "1",
            },
        )

    async def generate_dialogue(self, request: DialogueRequest) -> TTSResult:
        """Multi-speaker generation, offline.

        Records the calls so a test can assert what a dialogue run actually
        sent — which turns, in which voices, in which order. That is the whole
        thing worth checking about this path, and it cannot be checked from the
        audio.
        """
        self.dialogue_calls.append(request)
        await asyncio.sleep(self.latency_s)

        joined = "\n".join(f"{t.voice_id}:{t.text}" for t in request.turns)
        if self._should_fail(joined) or self._should_fail_once(joined):
            if self.failure_mode == "unknown":
                raise UnknownOutcomeError("Mock: simulated read timeout after send.")
            raise RetryableError("Mock: simulated 429.", status=429, retry_after_s=0.01)

        digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]
        path = self._cache / f"dlg-{digest}.mp3"
        if not path.exists():
            seconds = max(0.3, round(request.char_count * SECONDS_PER_CHAR, 2))
            wav = self._cache / f"dlg-{digest}.wav"
            make_silence(wav, seconds)
            to_mp3(wav, path)
            wav.unlink(missing_ok=True)

        return TTSResult(
            audio=path.read_bytes(),
            request_id=f"mock-dlg-{digest}",
            billed_chars=request.char_count,
            headers={
                "character-cost": str(request.char_count),
                "request-id": f"mock-dlg-{digest}",
                "maximum-concurrent-requests": "5",
                "current-concurrent-requests": "1",
            },
        )

    async def aclose(self) -> None:
        return None


class MockSFXProvider:
    """Offline sound-effect provider. Generates real, playable silence.

    Reports an `observed_cost` following the documented 40-credits-per-second
    rule, so the reconciliation path has something plausible to compare
    against without anyone having guessed at a live value.
    """

    name = "mock"

    CREDITS_PER_SECOND = 40

    def __init__(self, latency_s: float = 0.02, cache_dir: Path | None = None) -> None:
        self.latency_s = latency_s
        self.calls: list[SFXRequest] = []
        self._cache = cache_dir or Path(tempfile.gettempdir()) / "narrate-mock-sfx"
        self._cache.mkdir(parents=True, exist_ok=True)

    async def generate_effect(self, request: SFXRequest) -> SFXResult:
        self.calls.append(request)
        await asyncio.sleep(self.latency_s)

        # An omitted duration is the provider's choice; pick something stable
        # so tests stay deterministic.
        seconds = request.duration_s if request.duration_s is not None else 3.0

        digest = hashlib.sha256(f"{request.prompt}|{seconds}|{request.loop}".encode()).hexdigest()[
            :16
        ]
        path = self._cache / f"{digest}.mp3"
        if not path.exists():
            wav = self._cache / f"{digest}.wav"
            make_silence(wav, seconds)
            to_mp3(wav, path)
            wav.unlink(missing_ok=True)

        return SFXResult(
            audio=path.read_bytes(),
            request_id=f"mock-sfx-{digest}",
            observed_cost=int(seconds * self.CREDITS_PER_SECOND),
            headers={
                "character-cost": str(int(seconds * self.CREDITS_PER_SECOND)),
                "request-id": f"mock-sfx-{digest}",
                "maximum-concurrent-requests": "5",
            },
        )

    async def aclose(self) -> None:
        return None
