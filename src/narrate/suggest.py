"""Proposing where sound effects belong, via Groq.

Entirely optional. Markers you write are always honoured; this only proposes
*additional* slots, and every proposal is stored unaccepted so **nothing it
suggests can cause a generation on its own**. That keeps the tool's
spend-is-opt-in posture intact even when a model is doing the thinking.

Two constraints shape the implementation:

* **Strict `json_schema` output.** Groq supports constrained decoding on
  exactly `openai/gpt-oss-120b` and `openai/gpt-oss-20b`, which is why those
  are the only models offered. The response cannot come back malformed, so
  there is no defensive parsing here.
* **Per-chunk requests.** The free tier allows 8,000 tokens per minute — below
  a full episode plus its response. Asking chunk by chunk keeps each request
  small and lets a partial failure cost only one chunk.

Raw `httpx`, matching the decision made for ElevenLabs: one endpoint does not
justify a second SDK, and the OpenAI-compatible surface is a single POST.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

from narrate.money import MICROS_PER_USD
from narrate.settings import Settings

# Published Groq rates, per million tokens.
MODEL_RATES: dict[str, tuple[float, float]] = {
    "openai/gpt-oss-120b": (0.15, 0.60),
    "openai/gpt-oss-20b": (0.075, 0.30),
}

MAX_PER_CHUNK = 3

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["effects"],
    "properties": {
        "effects": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["description", "duration_seconds", "loop", "reason"],
                "properties": {
                    "description": {
                        "type": "string",
                        "description": (
                            "A concrete sound to generate, in the style of a foley "
                            "brief: 'heavy wooden door creaking open', not 'tension'."
                        ),
                    },
                    "duration_seconds": {"type": "number", "minimum": 0.5, "maximum": 30},
                    "loop": {
                        "type": "boolean",
                        "description": "True for ambience beds, false for one-shot hits.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "One short sentence on what in the text calls for it.",
                    },
                },
            },
        }
    },
}

SYSTEM_PROMPT = """You are a sound designer marking up a narration script.

For the passage given, propose at most {max_effects} sound effects that would
sit at its START. Only propose a sound when the text plainly calls for one —
an event, a place, or a shift the listener should hear. Propose nothing if
nothing is called for; an empty list is a good answer.

Write each description as a foley brief a generator can act on: name the
object, the action and the surface. "Boots on wet gravel, close" is usable.
"Something ominous" is not. Prefer one-shot hits; use loop only for ambience
that should sit under the whole passage.
"""


@dataclass(frozen=True)
class Suggestion:
    chunk_ordinal: int
    description: str
    duration_s: float
    loop: bool
    reason: str


@dataclass
class SuggestionRun:
    suggestions: list[Suggestion] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def cost_micros(self) -> int:
        """What the suggestions cost to produce. Small, but not nothing."""
        rate_in, rate_out = MODEL_RATES.get(self.model, (0.0, 0.0))
        total = (
            Decimal(self.prompt_tokens) * Decimal(str(rate_in))
            + Decimal(self.completion_tokens) * Decimal(str(rate_out))
        ) / Decimal(1_000_000)
        return int(total * MICROS_PER_USD)


class GroqError(RuntimeError):
    pass


async def suggest_for_chunks(
    chunks: list[tuple[int, str]],
    settings: Settings,
    *,
    model: str | None = None,
    max_per_chunk: int = MAX_PER_CHUNK,
    client: httpx.AsyncClient | None = None,
) -> SuggestionRun:
    """Ask for effect proposals at the start of each given chunk.

    `chunks` is `(ordinal, text)`. Requests are sequential rather than
    concurrent: the free tier's limits are per minute, and a burst of parallel
    calls is the fastest way to hit a 429 for no benefit.
    """
    chosen = model or settings.groq_model
    if chosen not in MODEL_RATES:
        raise GroqError(
            f"{chosen!r} is not a Groq model with strict structured output. "
            f"Use one of: {', '.join(sorted(MODEL_RATES))}."
        )

    run = SuggestionRun(model=chosen)
    owns_client = client is None
    http = client or httpx.AsyncClient(
        base_url=settings.groq_base_url,
        headers={"Authorization": f"Bearer {settings.require_groq_key()}"},
        timeout=httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0),
    )

    try:
        for ordinal, text in chunks:
            try:
                payload = await _ask(http, chosen, text, max_per_chunk)
            except httpx.HTTPError as exc:
                run.errors.append(f"chunk {ordinal}: {exc}")
                continue
            except GroqError as exc:
                run.errors.append(f"chunk {ordinal}: {exc}")
                continue

            usage = payload.get("usage", {})
            run.prompt_tokens += int(usage.get("prompt_tokens", 0))
            run.completion_tokens += int(usage.get("completion_tokens", 0))

            content = payload["choices"][0]["message"]["content"]
            for item in json.loads(content).get("effects", [])[:max_per_chunk]:
                run.suggestions.append(
                    Suggestion(
                        chunk_ordinal=ordinal,
                        description=str(item["description"]).strip(),
                        duration_s=float(item["duration_seconds"]),
                        loop=bool(item["loop"]),
                        reason=str(item["reason"]).strip(),
                    )
                )
    finally:
        if owns_client:
            await http.aclose()

    return run


async def _ask(
    client: httpx.AsyncClient, model: str, text: str, max_effects: int
) -> dict[str, Any]:
    response = await client.post(
        "/chat/completions",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT.format(max_effects=max_effects)},
                {"role": "user", "content": text},
            ],
            # Constrained decoding: "never errors or produces invalid JSON".
            # Note Groq does not allow this together with `tools`, and it
            # cannot be streamed.
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "effect_slots", "strict": True, "schema": SCHEMA},
            },
            "temperature": 0.4,
        },
    )
    if response.status_code == 429:
        retry = response.headers.get("retry-after", "?")
        raise GroqError(
            f"rate limited (retry after {retry}s). The free tier allows 8,000 tokens "
            "per minute, which a long script exceeds."
        )
    if response.status_code >= 400:
        raise GroqError(f"HTTP {response.status_code}: {response.text[:200]}")
    result: dict[str, Any] = response.json()
    return result


def estimate_tokens(chunks: list[tuple[int, str]]) -> int:
    """Rough input size, for showing a cost before the call.

    Four characters per token is the usual approximation for English prose;
    it only needs to be good enough to say "fractions of a cent" convincingly.
    """
    prose = sum(len(text) for _, text in chunks)
    overhead = len(SYSTEM_PROMPT) * len(chunks)
    return (prose + overhead) // 4
