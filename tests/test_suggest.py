"""Asking a model where effects belong — the request, the cost, the failures.

This module had no direct coverage before the Groq client was factored out into
`narrate.llm`, which made the seam worth using: `suggest_for_chunks` accepts a
client, so `httpx.MockTransport` can stand in for the provider and every branch
becomes reachable without a key or a charge.

The tests worth having here are about **money and safety**, not about whether the
model is any good at sound design:

* an unrecognised model must be refused before anything is sent,
* tokens must be counted whether or not the model proposed anything,
* one chunk failing must not discard the chunks that succeeded, or their charge.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from narrate import llm, suggest
from narrate.settings import Settings


def _client(handler: Any) -> httpx.AsyncClient:
    """A stand-in provider.

    The `base_url` is not optional: `chat_json` posts the relative path
    `/chat/completions`, which only resolves against one. An injected client
    supplies its own headers too, so `require_groq_key` is never reached and no
    key is needed to exercise any of this.
    """
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://groq.test/openai/v1"
    )


def _reply(effects: list[dict[str, Any]], *, prompt: int = 100, completion: int = 20) -> str:
    """A Groq chat-completions body, shaped as the real one is."""
    return json.dumps(
        {
            "choices": [{"message": {"content": json.dumps({"effects": effects})}}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
        }
    )


def _effect(description: str = "rain on a tin roof", **over: Any) -> dict[str, Any]:
    return {
        "description": description,
        "duration_seconds": 4.0,
        "loop": False,
        "reason": "the text says it starts raining",
    } | over


# --------------------------------------------------------------------------
# The allowlist
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unknown_model_is_refused_before_anything_is_sent(settings: Settings) -> None:
    """The allowlist exists because strict decoding is what removes defensive
    parsing. A model without it must fail here, not halfway through a paid run."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, text=_reply([]))

    async with _client(handler) as client:
        with pytest.raises(llm.GroqError, match="strict structured output"):
            await suggest.suggest_for_chunks(
                [(1, "some prose")], settings, model="llama-3-70b", client=client
            )

    assert calls == []


@pytest.mark.parametrize("model", sorted(llm.MODEL_RATES))
def test_every_allowed_model_has_a_rate(model: str) -> None:
    rate_in, rate_out = llm.MODEL_RATES[model]
    assert rate_in > 0 and rate_out > rate_in


# --------------------------------------------------------------------------
# The request itself
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_request_asks_for_strict_constrained_decoding(settings: Settings) -> None:
    """Without `strict`, the reply can come back malformed and every caller
    would need defensive parsing that none of them has."""
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, text=_reply([]))

    async with _client(handler) as client:
        await suggest.suggest_for_chunks([(1, "prose")], settings, client=client)

    fmt = sent[0]["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["name"] == "effect_slots"
    assert sent[0]["model"] == settings.groq_model


def test_the_schema_is_strict_mode_legal() -> None:
    """Strict mode has no optional fields: every property must be `required`,
    and `additionalProperties` must be False at every level."""

    def check(node: dict[str, Any]) -> None:
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                check(child)
        if node.get("type") == "array":
            check(node["items"])

    check(suggest.SCHEMA)


@pytest.mark.asyncio
async def test_one_request_is_made_per_chunk(settings: Settings) -> None:
    """Per-chunk, sequentially: the free tier's budget is per minute, and a
    partial failure should cost one chunk rather than the episode."""
    bodies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content)["messages"][1]["content"])
        return httpx.Response(200, text=_reply([]))

    async with _client(handler) as client:
        await suggest.suggest_for_chunks(
            [(1, "first"), (2, "second"), (3, "third")], settings, client=client
        )

    assert bodies == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_an_injected_client_is_not_closed_by_the_callee(settings: Settings) -> None:
    """`owns_client` exists so a caller can reuse one client across calls."""
    client = _client(lambda _: httpx.Response(200, text=_reply([])))
    await suggest.suggest_for_chunks([(1, "prose")], settings, client=client)
    assert not client.is_closed
    await client.aclose()


# --------------------------------------------------------------------------
# What comes back
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proposals_are_parsed_and_attributed_to_their_chunk(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        text = json.loads(request.content)["messages"][1]["content"]
        return httpx.Response(200, text=_reply([_effect(f"sound for {text}")]))

    async with _client(handler) as client:
        run = await suggest.suggest_for_chunks([(1, "a"), (7, "b")], settings, client=client)

    assert [(s.chunk_ordinal, s.description) for s in run.suggestions] == [
        (1, "sound for a"),
        (7, "sound for b"),
    ]
    assert run.suggestions[0].duration_s == 4.0
    assert run.suggestions[0].loop is False
    assert not run.errors


@pytest.mark.asyncio
async def test_more_proposals_than_asked_for_are_discarded(settings: Settings) -> None:
    """`maxItems` cannot be expressed under strict mode, so the cap is applied
    here rather than trusted to the schema."""
    many = [_effect(f"sound {n}") for n in range(9)]
    async with _client(lambda _: httpx.Response(200, text=_reply(many))) as client:
        run = await suggest.suggest_for_chunks(
            [(1, "prose")], settings, max_per_chunk=2, client=client
        )

    assert len(run.suggestions) == 2


@pytest.mark.asyncio
async def test_an_empty_answer_still_costs_tokens(settings: Settings) -> None:
    """ "Propose nothing" is a good answer, and it is not a free one."""
    async with _client(lambda _: httpx.Response(200, text=_reply([]))) as client:
        run = await suggest.suggest_for_chunks([(1, "prose")], settings, client=client)

    assert run.suggestions == []
    assert run.prompt_tokens == 100
    assert run.completion_tokens == 20
    assert run.cost_micros > 0


# --------------------------------------------------------------------------
# Failure
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_rate_limited_chunk_is_recorded_and_the_rest_continue(
    settings: Settings,
) -> None:
    """A partial run must still return what it got, and still be charged for it."""

    def handler(request: httpx.Request) -> httpx.Response:
        text = json.loads(request.content)["messages"][1]["content"]
        if text == "second":
            return httpx.Response(429, headers={"retry-after": "12"}, text="slow down")
        return httpx.Response(200, text=_reply([_effect()]))

    async with _client(handler) as client:
        run = await suggest.suggest_for_chunks(
            [(1, "first"), (2, "second"), (3, "third")], settings, client=client
        )

    assert len(run.suggestions) == 2
    assert len(run.errors) == 1
    assert "chunk 2" in run.errors[0]
    assert "retry after 12s" in run.errors[0]
    # Two chunks succeeded, so two chunks' tokens were spent.
    assert run.prompt_tokens == 200


@pytest.mark.asyncio
async def test_a_provider_error_body_is_truncated(settings: Settings) -> None:
    """An error page would otherwise flood the console."""
    async with _client(lambda _: httpx.Response(500, text="x" * 5000)) as client:
        run = await suggest.suggest_for_chunks([(1, "prose")], settings, client=client)

    assert len(run.errors) == 1
    assert len(run.errors[0]) < 300


@pytest.mark.asyncio
async def test_a_transport_failure_is_recorded_rather_than_raised(settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    async with _client(handler) as client:
        run = await suggest.suggest_for_chunks([(1, "prose")], settings, client=client)

    assert run.suggestions == []
    assert "chunk 1" in run.errors[0]


# --------------------------------------------------------------------------
# Cost arithmetic
# --------------------------------------------------------------------------


def test_cost_is_computed_from_the_published_per_million_rates() -> None:
    # 1M input tokens on the 120b model is its published $0.15.
    assert llm.cost_micros("openai/gpt-oss-120b", 1_000_000, 0) == 150_000
    assert llm.cost_micros("openai/gpt-oss-120b", 0, 1_000_000) == 600_000
    assert llm.cost_micros("openai/gpt-oss-20b", 1_000_000, 1_000_000) == 75_000 + 300_000


def test_an_unknown_model_costs_nothing_rather_than_raising() -> None:
    """This runs while reporting a run that already happened. A rate card that
    has moved on must not turn a completed operation into a crash."""
    assert llm.cost_micros("gone-away", 1_000, 1_000) == 0


def test_usages_add_up() -> None:
    a = llm.TokenUsage(prompt_tokens=10, completion_tokens=2, model="openai/gpt-oss-20b")
    b = llm.TokenUsage(prompt_tokens=5, completion_tokens=1, model="openai/gpt-oss-20b")
    total = a + b
    assert (total.prompt_tokens, total.completion_tokens) == (15, 3)
    assert total.total_tokens == 18
    assert total.model == "openai/gpt-oss-20b"


def test_nothing_spent_is_reported_as_nothing_spent() -> None:
    """The ledger write is guarded on this, so an all-failed run writes no row."""
    assert not llm.TokenUsage(model="openai/gpt-oss-20b").spent
    assert llm.TokenUsage(prompt_tokens=1, model="openai/gpt-oss-20b").spent


def test_an_estimate_charges_the_system_prompt_once_per_request() -> None:
    """One request per chunk pays for the system prompt every time. Counting it
    once would understate a long script badly."""
    texts = ["a" * 400, "b" * 400]
    system = "s" * 400
    per_chunk = llm.estimate_tokens(texts, system, per_text=True)
    single = llm.estimate_tokens(texts, system, per_text=False)
    assert per_chunk == (800 + 800) // 4
    assert single == (800 + 400) // 4


def test_the_suggestion_estimate_matches_how_suggestions_are_sent() -> None:
    chunks = [(1, "a" * 100), (2, "b" * 100)]
    assert suggest.estimate_tokens(chunks) == llm.estimate_tokens(
        ["a" * 100, "b" * 100], suggest.SYSTEM_PROMPT
    )
