"""Provider protocol, request/result types, and the idempotency key.

The error taxonomy here is the thing that keeps the ledger honest. Failures are
sorted by *what they imply about billing*, not by HTTP status:

    RetryableError      the request was rejected before generating — safe to retry
    FatalError          it will never succeed as written — do not retry
    UnknownOutcomeError the request was sent and the outcome was never seen

`UnknownOutcomeError` is the one that matters. A read timeout after the body
went out may have generated and billed audio. Retrying it risks a double
charge, which PRD §11 rates as direct financial loss, so it is recorded and
surfaced for a human instead.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class TTSRequest:
    text: str
    voice_id: str
    model_id: str
    settings: dict[str, Any] = field(default_factory=dict)
    output_format: str = "mp3_44100_128"
    seed: int | None = None

    # Continuity. Which pair is populated depends on the model's capabilities:
    # request ids condition on generated audio, text only on the words.
    previous_text: str | None = None
    next_text: str | None = None
    previous_request_ids: tuple[str, ...] = ()
    next_request_ids: tuple[str, ...] = ()

    # Identifies *what this generation was conditioned on*, without depending on
    # the volatile ids that express it. Never transmitted — see `idempotency_key`.
    continuity_fingerprint: str = ""

    @property
    def char_count(self) -> int:
        return len(self.text)


@dataclass(frozen=True)
class TTSResult:
    audio: bytes
    request_id: str | None
    billed_chars: int | None
    headers: dict[str, str]

    @property
    def max_concurrent(self) -> int | None:
        """The account's real concurrency ceiling for this model family.

        Only reported here — it is absent from the subscription endpoint, so
        the response header is the single programmatic source.
        """
        raw = self.headers.get("maximum-concurrent-requests")
        return int(raw) if raw and raw.isdigit() else None

    @property
    def current_concurrent(self) -> int | None:
        raw = self.headers.get("current-concurrent-requests")
        return int(raw) if raw and raw.isdigit() else None


class ProviderError(Exception):
    """Base for anything that went wrong talking to the provider."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        code: str | None = None,
        request_id: str | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.request_id = request_id
        self.retry_after_s = retry_after_s

    def as_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "status": self.status,
            "code": self.code,
            "request_id": self.request_id,
        }


class RetryableError(ProviderError):
    """Rejected before any audio was generated. Retrying cannot double-charge."""


class FatalError(ProviderError):
    """Will never succeed as written — bad voice, bad body, no credits."""


class UnknownOutcomeError(ProviderError):
    """Sent, but the outcome was never observed. May or may not have been billed."""


class InsufficientCredits(FatalError):
    """HTTP 402. Distinguished so the CLI can say what actually happened."""


@dataclass(frozen=True)
class DialogueTurn:
    """One speaker's line inside a dialogue request."""

    text: str
    voice_id: str
    # Carried for reporting only; the endpoint takes text and voice alone.
    speaker: str = ""


@dataclass(frozen=True)
class DialogueRequest:
    """Several speakers in one request: `POST /v1/text-to-dialogue`.

    Distinct from `TTSRequest` because the endpoint is a different shape — a
    list of `{text, voice_id}` rather than one text against one voice in the
    path — and because its limits are different. Two in particular:

    * **eleven_v3 only.** No other model supports the endpoint.
    * **2,000 characters across all turns**, which is tighter than v3's own
      5,000-character request limit, so the chunker needs its own ceiling for
      dialogue rather than reusing the model's.

    There is no continuity field. That costs nothing here: v3 rejects
    `previous_text` outright — `narrate probe --live` recorded the 400 — so
    dialogue gives up a mechanism v3 never had. Within a request the model
    hears the whole exchange, which is the point.
    """

    turns: tuple[DialogueTurn, ...]
    model_id: str = "eleven_v3"
    settings: dict[str, Any] = field(default_factory=dict)
    output_format: str = "mp3_44100_128"
    seed: int | None = None
    language_code: str | None = None

    @property
    def char_count(self) -> int:
        """Billable length: the sum of the turns, which is what the ceiling
        applies to."""
        return sum(len(t.text) for t in self.turns)

    @property
    def voice_ids(self) -> tuple[str, ...]:
        """Distinct voices, in first-appearance order. The API reference caps a
        request at ten."""
        seen: dict[str, None] = {}
        for turn in self.turns:
            seen.setdefault(turn.voice_id, None)
        return tuple(seen)

    @property
    def voice_id(self) -> str:
        """The lead speaker's voice.

        `Take.voice_id` is a single non-null column that every existing query
        reads, so it keeps meaning "a voice this take used" and the full list
        goes to `Take.voices_json`. Naming the lead is honest; inventing a
        sentinel would not be.
        """
        return self.turns[0].voice_id if self.turns else ""

    @property
    def text(self) -> str:
        """What was submitted, as one readable block.

        Recorded on the take so `narrate takes` can show what was said. The
        speaker labels are added back *here*, for the record — they were
        stripped before the request, and this string is never sent.
        """
        return "\n".join(f"{t.speaker}: {t.text}" if t.speaker else t.text for t in self.turns)


@dataclass(frozen=True)
class SFXRequest:
    """One sound-effect generation.

    `duration_s` is optional: omitting it lets the provider choose, which the
    docs recommend over describing a length in the prompt. When given it must
    sit inside 0.5-30s, which `script_parse` already enforces.
    """

    prompt: str
    duration_s: float | None = None
    loop: bool = False
    prompt_influence: float = 0.3
    model_id: str = "eleven_text_to_sound_v2"
    output_format: str = "mp3_44100_128"


@dataclass(frozen=True)
class SFXResult:
    audio: bytes
    request_id: str | None
    # The `character-cost` header. Declared by the endpoint, but nothing
    # documents what it means for a product billed per second — recorded for
    # reconciliation, never used to compute a charge. See `effects.py`.
    observed_cost: int | None
    headers: dict[str, str]


class TTSProvider(Protocol):
    """What the runner needs. `mock` and `elevenlabs` both satisfy this."""

    name: str

    async def synthesize(self, request: TTSRequest) -> TTSResult: ...

    async def aclose(self) -> None: ...


class DialogueProvider(Protocol):
    """Multi-speaker generation. Separate from `TTSProvider` because not every
    provider — and not every *model* — can do it, and the runner has to be able
    to ask before it commits to the path."""

    name: str

    async def generate_dialogue(self, request: DialogueRequest) -> TTSResult: ...

    async def aclose(self) -> None: ...


class SFXProvider(Protocol):
    """Sound-effect generation. `mock` and `elevenlabs` both satisfy this."""

    name: str

    async def generate_effect(self, request: SFXRequest) -> SFXResult: ...

    async def aclose(self) -> None: ...


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def _canonical(value: Any) -> Any:
    """Normalise a value so equivalent requests hash identically.

    Floats are the trap: `0.5` and `0.50` are the same number but round-trip
    through JSON differently depending on where they came from, and
    `0.1 + 0.2` is not `0.3`. Formatting every float to fixed precision makes
    the hash depend on the value rather than on its provenance.
    """
    if isinstance(value, float):
        return f"{value:.6f}"
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value


def idempotency_key(request: TTSRequest) -> str:
    """A stable hash of everything that determines what gets generated and billed.

    The API has no idempotency header of its own — the whole OpenAPI spec has
    no such field — so dedupe is entirely ours to do.

    **`previous_request_ids` / `next_request_ids` are deliberately excluded.**
    They are ephemeral — the provider expires them after two hours — and their
    values differ on every run, so a key that included them could never match
    on a second run. Resume would break, and every chunk after the first would
    be silently regenerated and re-charged.

    What actually matters is not *which ids* conditioned the generation but
    *what audio* they pointed at, and that is captured by
    `continuity_fingerprint`, which the runner derives deterministically from
    the preceding chunks' text. Edit an earlier chunk and the fingerprint
    changes, so the dependent chunks correctly become new generations.
    """
    payload = _canonical(
        {
            "text": request.text,
            "voice_id": request.voice_id,
            "model_id": request.model_id,
            "settings": request.settings,
            "output_format": request.output_format,
            "seed": request.seed,
            "previous_text": request.previous_text,
            "next_text": request.next_text,
            "continuity_fingerprint": request.continuity_fingerprint,
        }
    )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def dialogue_idempotency_key(request: DialogueRequest) -> str:
    """Identifies a dialogue by everything that determines its audio.

    The ordered turns *are* the identity: the same lines in the same voices in
    a different order is a different performance, so the sequence is hashed
    rather than a set. `speaker` is excluded — it is a label for reporting, and
    renaming a character must not re-charge an exchange that would generate
    byte-for-byte the same audio.
    """
    payload = _canonical(
        {
            "turns": [[t.text, t.voice_id] for t in request.turns],
            "model_id": request.model_id,
            "settings": request.settings,
            "output_format": request.output_format,
            "seed": request.seed,
            "language_code": request.language_code,
        }
    )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def effect_idempotency_key(request: SFXRequest) -> str:
    """Identifies a sound effect by everything that determines the audio.

    This is what makes an effect a *library* item: the same cue asked for at
    four points in an episode hashes once, so it is generated once and billed
    once, then placed four times.
    """
    payload = _canonical(
        {
            "prompt": request.prompt.strip().lower(),
            "duration_s": request.duration_s,
            "loop": request.loop,
            "prompt_influence": request.prompt_influence,
            "model_id": request.model_id,
            "output_format": request.output_format,
        }
    )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
