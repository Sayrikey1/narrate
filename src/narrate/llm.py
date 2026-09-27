"""One Groq client, shared by everything that asks a model a question.

This began as private helpers inside `suggest.py`, which for a while was the
only thing here that talked to an LLM. Once titles, thumbnail briefs and script
outlines arrived, the choice was to copy a forty-line `_ask` four times or to
name it once — and the part that actually varies between those callers is small:
a JSON schema, a system prompt, and a label for the schema.

What does not vary is everything that matters for correctness:

* **Strict `json_schema` output.** Groq supports constrained decoding on exactly
  `openai/gpt-oss-120b` and `openai/gpt-oss-20b`, which is why `MODEL_RATES`
  doubles as the allowlist. The response cannot come back malformed, so callers
  do not parse defensively — but they do have to declare every property as
  `required` with `additionalProperties: False`, because strict mode has no
  notion of an optional field.
* **Sequential requests.** The free tier allows 8,000 tokens per minute, below a
  full episode plus its response. Callers loop and call once per unit of work; a
  burst of parallel calls is the fastest way to hit a 429 for no benefit.
* **Costs computed from reported usage, in `Decimal`.** Groq publishes rates per
  *million* tokens, so the arithmetic is small enough that binary float drift is
  visible at the micro-USD the ledger stores.

Raw `httpx`, matching the decision made for ElevenLabs: one endpoint does not
justify a second SDK, and the OpenAI-compatible surface is a single POST.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

from narrate.money import MICROS_PER_USD
from narrate.settings import Settings

# Published Groq rates, per million tokens, as (input, output).
#
# Also the allowlist: a model absent from here is refused before any network
# call, because these two are the only Groq production text models with strict
# structured output, and that guarantee is what lets callers skip defensive
# parsing. Adding a model without that support would silently move the failure
# from "refused up front" to "malformed JSON halfway through a paid run".
MODEL_RATES: dict[str, tuple[float, float]] = {
    "openai/gpt-oss-120b": (0.15, 0.60),
    "openai/gpt-oss-20b": (0.075, 0.30),
}

# Four characters per token is the usual approximation for English prose. It
# only needs to be good enough to say "fractions of a cent" convincingly before
# a call, which is all any estimate here is used for.
CHARS_PER_TOKEN = 4


class GroqError(RuntimeError):
    """The provider refused, rate-limited, or could not be reached."""


def cost_micros(model: str, prompt_tokens: int, completion_tokens: int) -> int:
    """What a number of tokens cost, in micro-USD.

    An unknown model costs nothing rather than raising: this is called while
    *reporting* a run that has already happened, and a rate card that has moved
    on must not turn a completed, already-charged operation into a crash. The
    allowlist in `resolve_model` is where an unknown model is actually refused,
    and it runs before anything is sent.
    """
    rate_in, rate_out = MODEL_RATES.get(model, (0.0, 0.0))
    total = (
        Decimal(prompt_tokens) * Decimal(str(rate_in))
        + Decimal(completion_tokens) * Decimal(str(rate_out))
    ) / Decimal(1_000_000)
    return int(total * MICROS_PER_USD)


@dataclass(frozen=True)
class TokenUsage:
    """What one request consumed, and what that cost.

    Addable so a caller looping over chunks can accumulate without tracking two
    running integers by hand. Adding usages from different models keeps the
    left-hand model, since a mixed-model run has no single rate to report — in
    practice one run uses one model, which `resolve_model` settles up front.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def cost_micros(self) -> int:
        return cost_micros(self.model, self.prompt_tokens, self.completion_tokens)

    @property
    def spent(self) -> bool:
        """Whether anything was consumed — the test for charging the ledger."""
        return bool(self.prompt_tokens or self.completion_tokens)

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            model=self.model or other.model,
        )


def resolve_model(model: str | None, settings: Settings) -> str:
    """The model to use, refused up front if it cannot be trusted to be strict."""
    chosen = model or settings.groq_model
    if chosen not in MODEL_RATES:
        raise GroqError(
            f"{chosen!r} is not a Groq model with strict structured output. "
            f"Use one of: {', '.join(sorted(MODEL_RATES))}."
        )
    return chosen


def client_for(settings: Settings) -> httpx.AsyncClient:
    """A client pointed at Groq, with its key attached.

    Timeouts are set here rather than read from `settings.connect_timeout_s` and
    `read_timeout_s`, which are ElevenLabs': a long generation and a short chat
    completion do not want the same patience.
    """
    return httpx.AsyncClient(
        base_url=settings.groq_base_url,
        headers={"Authorization": f"Bearer {settings.require_groq_key()}"},
        timeout=httpx.Timeout(connect=10.0, read=60.0, write=60.0, pool=10.0),
    )


async def chat_json(
    client: httpx.AsyncClient,
    model: str,
    *,
    system: str,
    user: str,
    schema: dict[str, Any],
    name: str,
    temperature: float = 0.4,
) -> tuple[dict[str, Any], TokenUsage]:
    """One constrained-decoding request, returning the parsed object and its cost.

    `name` labels the schema for the provider. `schema` must declare every
    property in `required` with `additionalProperties: False`, which strict mode
    demands — a schema that does not is rejected by Groq, not here.

    The usage is returned alongside the content rather than folded into it so a
    caller can charge for a request whose *content* it then decides to discard.
    Tokens are spent either way.
    """
    response = await client.post(
        "/chat/completions",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # Constrained decoding: "never errors or produces invalid JSON".
            # Note Groq does not allow this together with `tools`, and it
            # cannot be streamed.
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            },
            "temperature": temperature,
        },
    )
    if response.status_code == 429:
        retry = response.headers.get("retry-after", "?")
        raise GroqError(
            f"rate limited (retry after {retry}s). The free tier allows 8,000 tokens "
            "per minute, which a long script exceeds."
        )
    if response.status_code >= 400:
        # Truncated: a provider error page would otherwise flood the console.
        raise GroqError(f"HTTP {response.status_code}: {response.text[:200]}")

    payload: dict[str, Any] = response.json()
    reported = payload.get("usage", {})
    usage = TokenUsage(
        prompt_tokens=int(reported.get("prompt_tokens", 0)),
        completion_tokens=int(reported.get("completion_tokens", 0)),
        model=model,
    )
    content: dict[str, Any] = json.loads(payload["choices"][0]["message"]["content"])
    return content, usage


def estimate_tokens(texts: list[str], system: str, *, per_text: bool = True) -> int:
    """Rough input size, for showing a cost before spending anything.

    `per_text` reflects how the caller will actually send the work: one request
    per text pays for the system prompt every time, while a single request pays
    for it once. Getting that wrong understates a long script badly.
    """
    prose = sum(len(text) for text in texts)
    overhead = len(system) * (len(texts) if per_text else 1)
    return (prose + overhead) // CHARS_PER_TOKEN
