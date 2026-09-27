"""Titles, descriptions, tags and thumbnail briefs.

The taxonomies these features work from — nine title formulas, twelve thumbnail
compositions, seven principles — are somebody else's, transcribed from
`docs/Copy of YT Automation.md`. So the tests worth having are mostly about the
*schema*: an enumerated field cannot come back off-taxonomy, and `overlay_words`
cannot come back with six words, because constrained decoding will not produce
them. Asserting the schema shape is asserting the guarantee.

The rest is the usual pair: tokens are charged whether or not the output is
useful, and nothing proposed is chosen.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from narrate import copywrite, llm
from narrate.settings import Settings


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://groq.test/openai/v1"
    )


def _reply(payload: dict[str, Any], *, prompt: int = 400, completion: int = 120) -> httpx.Response:
    return httpx.Response(
        200,
        text=json.dumps(
            {
                "choices": [{"message": {"content": json.dumps(payload)}}],
                "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
            }
        ),
    )


# --------------------------------------------------------------------------
# The taxonomies, and the schemas that enforce them
# --------------------------------------------------------------------------


def test_the_document_taxonomies_are_transcribed_completely() -> None:
    """These are counts from the source document. If one changes, the prompts
    and the schemas have drifted from what they claim to implement."""
    assert len(copywrite.FORMULAS) == 9
    assert len(copywrite.ARCHETYPES) == 12
    assert len(copywrite.PRINCIPLES) == 7


def test_a_formula_cannot_come_back_off_taxonomy() -> None:
    """An enum, not free text. Recording which formula a title uses is only
    worth doing if the same formula gets the same name every run."""
    formula = copywrite.TITLE_SCHEMA["properties"]["titles"]["items"]["properties"]["formula"]
    assert formula["enum"] == [name for name, _ in copywrite.FORMULAS]


def test_the_five_word_rule_is_enforced_by_the_schema_not_by_checking_after() -> None:
    """The published guidance is three to five words. As an array with
    `maxItems`, a six-word answer is not a possible reply — so nothing has to
    decide what to do about one."""
    words = copywrite.THUMBNAIL_SCHEMA["properties"]["briefs"]["items"]["properties"][
        "overlay_words"
    ]
    assert words["type"] == "array"
    assert words["maxItems"] == copywrite.MAX_OVERLAY_WORDS == 5
    assert words["minItems"] == 1


def test_an_archetype_cannot_come_back_off_taxonomy() -> None:
    archetype = copywrite.THUMBNAIL_SCHEMA["properties"]["briefs"]["items"]["properties"][
        "archetype"
    ]
    assert sorted(archetype["enum"]) == sorted(copywrite.ARCHETYPES)


@pytest.mark.parametrize(
    "schema",
    [copywrite.TITLE_SCHEMA, copywrite.PACKAGE_SCHEMA, copywrite.THUMBNAIL_SCHEMA],
)
def test_every_schema_is_strict_mode_legal(schema: dict[str, Any]) -> None:
    """Strict mode has no optional fields: every property must be required, and
    `additionalProperties` false at every level."""

    def check(node: dict[str, Any]) -> None:
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                check(child)
        if node.get("type") == "array":
            check(node["items"])

    check(schema)


def test_the_prompts_name_every_formula_so_the_model_can_spread_across_them() -> None:
    prompt = copywrite.TITLE_PROMPT
    for name, _ in copywrite.FORMULAS:
        assert name in prompt


# --------------------------------------------------------------------------
# Titles
# --------------------------------------------------------------------------


def _titles_payload(count: int = 3) -> dict[str, Any]:
    return {
        "titles": [
            {
                "text": f"  A Title Number {n}  ",
                "formula": copywrite.FORMULAS[n % len(copywrite.FORMULAS)][0],
                "reason": "because",
            }
            for n in range(count)
        ]
    }


@pytest.mark.asyncio
async def test_titles_are_parsed_tidied_and_attributed(settings: Settings) -> None:
    async with _client(lambda _: _reply(_titles_payload())) as client:
        run = await copywrite.propose_copy(
            "a brief", settings, titles=3, want_package=False, client=client
        )

    assert [t.text for t in run.titles] == [
        "A Title Number 0",
        "A Title Number 1",
        "A Title Number 2",
    ]
    assert all(t.formula in dict(copywrite.FORMULAS) for t in run.titles)
    assert not run.errors


@pytest.mark.asyncio
async def test_quotation_marks_around_a_title_are_stripped(settings: Settings) -> None:
    """A model that quotes its own answer would publish the quotes."""
    payload = {"titles": [{"text": '"Quoted Title"', "formula": "Concise", "reason": "x"}]}
    async with _client(lambda _: _reply(payload)) as client:
        run = await copywrite.propose_copy("a brief", settings, want_package=False, client=client)
    assert run.titles[0].text == "Quoted Title"


@pytest.mark.asyncio
async def test_more_titles_than_asked_for_are_discarded(settings: Settings) -> None:
    async with _client(lambda _: _reply(_titles_payload(9))) as client:
        run = await copywrite.propose_copy(
            "a brief", settings, titles=4, want_package=False, client=client
        )
    assert len(run.titles) == 4


@pytest.mark.asyncio
async def test_titles_are_asked_for_at_a_higher_temperature(settings: Settings) -> None:
    """Nine formulas at the default 0.4 produce six rephrasings of one idea."""
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return _reply(_titles_payload())

    async with _client(handler) as client:
        await copywrite.propose_copy("a brief", settings, want_package=False, client=client)

    assert sent[0]["temperature"] > 0.4


# --------------------------------------------------------------------------
# Description and tags
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_description_and_tags_come_back_together(settings: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        name = json.loads(request.content)["response_format"]["json_schema"]["name"]
        if name == "episode_package":
            return _reply({"description": "  Two lines.  ", "tags": ["Wealth", " CASHFLOW ", ""]})
        return _reply(_titles_payload())

    async with _client(handler) as client:
        run = await copywrite.propose_copy("a brief", settings, client=client)

    assert run.description == "Two lines."
    # Lowercased and trimmed, because a tag is a search term.
    assert run.tags == ("wealth", "cashflow")
    assert len(run.titles) == 3


@pytest.mark.asyncio
async def test_titles_only_makes_one_request(settings: Settings) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _reply(_titles_payload())

    async with _client(handler) as client:
        await copywrite.propose_copy("a brief", settings, want_package=False, client=client)
    assert calls == 1


@pytest.mark.asyncio
async def test_a_failed_description_still_delivers_the_titles(settings: Settings) -> None:
    """Split into two requests precisely so one failing does not lose the other."""

    def handler(request: httpx.Request) -> httpx.Response:
        name = json.loads(request.content)["response_format"]["json_schema"]["name"]
        if name == "episode_package":
            return httpx.Response(429, headers={"retry-after": "30"}, text="slow down")
        return _reply(_titles_payload())

    async with _client(handler) as client:
        run = await copywrite.propose_copy("a brief", settings, client=client)

    assert len(run.titles) == 3
    assert run.description == ""
    assert len(run.errors) == 1
    assert "description" in run.errors[0]
    # The titles request succeeded, so its tokens were spent and are charged.
    assert run.cost_micros > 0


# --------------------------------------------------------------------------
# Thumbnail briefs
# --------------------------------------------------------------------------


def _brief_payload(words: list[str]) -> dict[str, Any]:
    return {
        "briefs": [
            {
                "archetype": "Face First",
                "overlay_words": words,
                "subject": "an exhausted commuter under harsh overhead light",
                "contrast": "cold blue against hot amber",
                "principles": ["curiosity", "emotion"],
                "reason": "the face carries the feeling the title withholds",
            }
        ]
    }


@pytest.mark.asyncio
async def test_a_brief_is_parsed_with_its_words_joined(settings: Settings) -> None:
    async with _client(lambda _: _reply(_brief_payload(["THE", "9-5", "TRAP"]))) as client:
        run = await copywrite.propose_thumbnails("a brief", settings, client=client)

    brief = run.briefs[0]
    assert brief.overlay_text == "THE 9-5 TRAP"
    assert brief.archetype == "Face First"
    assert brief.principles == "curiosity, emotion"
    assert not run.errors


@pytest.mark.asyncio
async def test_overlay_words_are_capped_even_if_the_schema_is_ignored(
    settings: Settings,
) -> None:
    """Belt and braces. The schema forbids a sixth word, but a cap that exists
    only in a remote service's decoder is not a cap this code can rely on."""
    words = ["ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN"]
    async with _client(lambda _: _reply(_brief_payload(words))) as client:
        run = await copywrite.propose_thumbnails("a brief", settings, client=client)

    assert len(run.briefs[0].overlay_text.split()) == copywrite.MAX_OVERLAY_WORDS
    assert run.briefs[0].overlay_text == "ONE TWO THREE FOUR FIVE"


@pytest.mark.asyncio
async def test_no_image_is_ever_requested(settings: Settings) -> None:
    """The brief is the deliverable. If this ever starts hitting an image
    endpoint, a second billable provider has appeared without a rate card."""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return _reply(_brief_payload(["A", "B"]))

    async with _client(handler) as client:
        await copywrite.propose_thumbnails("a brief", settings, client=client)

    assert paths == ["/openai/v1/chat/completions"]


# --------------------------------------------------------------------------
# Cost
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tokens_are_counted_across_both_requests(settings: Settings) -> None:
    async with _client(lambda _: _reply(_titles_payload(), prompt=400, completion=120)) as client:
        run = await copywrite.propose_copy("a brief", settings, client=client)

    assert run.usage.prompt_tokens == 800
    assert run.usage.completion_tokens == 240
    assert run.cost_micros == llm.cost_micros(run.model, 800, 240)
    assert run.usage.spent


@pytest.mark.asyncio
async def test_an_unknown_model_is_refused_before_anything_is_sent(
    settings: Settings,
) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _reply(_titles_payload())

    async with _client(handler) as client:
        with pytest.raises(llm.GroqError):
            await copywrite.propose_copy("a brief", settings, model="gpt-4", client=client)
    assert calls == []
