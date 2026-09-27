"""Proposing where sound effects belong, via Groq.

Entirely optional. Markers you write are always honoured; this only proposes
*additional* slots, and every proposal is stored unaccepted so **nothing it
suggests can cause a generation on its own**. That keeps the tool's
spend-is-opt-in posture intact even when a model is doing the thinking.

The request machinery — the client, the rate card, strict schema decoding, the
429 handling — lives in `narrate.llm`, shared with the other features that ask a
model a question. What stays here is the part specific to sound: the schema, the
brief given to the model, and the decision to ask **per chunk** rather than per
episode, which keeps each request inside the free tier's per-minute budget and
lets a partial failure cost only one chunk.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from narrate import llm
from narrate.llm import (
    MODEL_RATES,
    GroqError,
    TokenUsage,
    chat_json,
    client_for,
    cost_micros,
    resolve_model,
)
from narrate.settings import Settings

# Re-exported: `MODEL_RATES` and `GroqError` were part of this module's surface
# before the shared client existed, and the CLI imports them from here.
__all__ = [
    "MAX_PER_CHUNK",
    "MODEL_RATES",
    "SCHEMA",
    "SYSTEM_PROMPT",
    "GroqError",
    "Suggestion",
    "SuggestionRun",
    "estimate_tokens",
    "suggest_for_chunks",
]

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
        return cost_micros(self.model, self.prompt_tokens, self.completion_tokens)

    def add(self, usage: TokenUsage) -> None:
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens


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

    A failing chunk is recorded and the loop continues, so a partial run still
    returns what it got — and is still charged for what it spent.
    """
    chosen = resolve_model(model, settings)
    run = SuggestionRun(model=chosen)

    owns_client = client is None
    http = client or client_for(settings)

    try:
        for ordinal, text in chunks:
            try:
                content, usage = await chat_json(
                    http,
                    chosen,
                    system=SYSTEM_PROMPT.format(max_effects=max_per_chunk),
                    user=text,
                    schema=SCHEMA,
                    name="effect_slots",
                )
            except (httpx.HTTPError, GroqError) as exc:
                run.errors.append(f"chunk {ordinal}: {exc}")
                continue

            run.add(usage)
            for item in content.get("effects", [])[:max_per_chunk]:
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


def estimate_tokens(chunks: list[tuple[int, str]]) -> int:
    """Rough input size for a suggestion run, for showing a cost before the call."""
    return llm.estimate_tokens([text for _, text in chunks], SYSTEM_PROMPT)
