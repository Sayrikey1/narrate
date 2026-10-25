"""Drafting with a model: priced first, charged whatever comes back, stored unchosen."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, select

from narrate import copywrite, drafting
from narrate.db.models import EffectSlot, LedgerEntry, Script, ThumbnailBrief, TitleCandidate
from narrate.db.session import session_scope
from narrate.settings import Settings


@pytest.fixture
def keyed(settings: Settings) -> Settings:
    """A Groq key that is never sent anywhere: every test injects its client."""
    return settings.model_copy(update={"groq_api_key": SecretStr("test-key")})


def _client(payload: dict[str, Any]) -> httpx.AsyncClient:
    reply = {
        "choices": [{"message": {"content": json.dumps(payload)}}],
        "usage": {"prompt_tokens": 400, "completion_tokens": 120},
    }
    return httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=reply)),
        base_url="https://groq.test/openai/v1",
    )


def _charged(engine: Engine, kind: str) -> list[LedgerEntry]:
    with session_scope(engine) as session:
        rows = session.scalars(select(LedgerEntry).where(LedgerEntry.kind == kind)).all()
        session.expunge_all()
        return list(rows)


def test_a_quote_sends_nothing_and_counts_the_answer_too(
    engine: Engine, project_and_script: tuple[int, int], keyed: Settings
) -> None:
    _, script_id = project_and_script
    with session_scope(engine) as session:
        titles = drafting.quote(session, script_id, keyed, "titles")
        effects = drafting.quote(session, script_id, keyed, "effects")

    assert titles.requests == 2 and titles.output_tokens > 0
    assert effects.requests >= 1
    assert titles.micros > 0
    assert _charged(engine, "copy") == []


async def test_titles_are_stored_unchosen_and_the_tokens_charged(
    engine: Engine, project_and_script: tuple[int, int], keyed: Settings
) -> None:
    _, script_id = project_and_script
    payload = {
        "titles": [
            {"text": f"A Title {n}", "formula": copywrite.FORMULAS[n][0], "reason": "because"}
            for n in range(3)
        ]
    }
    async with _client(payload) as client:
        done = await drafting.draft_titles(
            engine, script_id, keyed, count=3, package=False, client=client
        )

    assert done.stored == 3
    with session_scope(engine) as session:
        rows = session.scalars(select(TitleCandidate)).all()
        script = session.get(Script, script_id)
        assert script is not None
        assert all(not r.accepted and r.source == "proposed" for r in rows)
        # Nothing chosen: the episode keeps its own title.
        assert script.title == "Episode 1"
    [charge] = _charged(engine, "copy")
    assert charge.cost_micros == done.cost_micros > 0


async def test_briefs_are_stored_unchosen(
    engine: Engine, project_and_script: tuple[int, int], keyed: Settings
) -> None:
    _, script_id = project_and_script
    payload = {
        "briefs": [
            {
                "archetype": "Face First",
                "overlay_words": ["SHE", "FAKED", "IT"],
                "subject": "a face",
                "contrast": "red on white",
                "principles": ["curiosity"],
                "reason": "the face carries it",
            }
        ]
    }
    async with _client(payload) as client:
        done = await drafting.draft_briefs(engine, script_id, keyed, count=1, client=client)

    assert done.stored == 1
    with session_scope(engine) as session:
        [brief] = session.scalars(select(ThumbnailBrief)).all()
        assert (brief.overlay_text, brief.accepted) == ("SHE FAKED IT", False)


async def test_suggestions_wait_to_be_accepted(
    engine: Engine, project_and_script: tuple[int, int], keyed: Settings
) -> None:
    _, script_id = project_and_script
    payload = {
        "effects": [
            {"description": "a door closing", "duration_seconds": 3, "loop": False, "reason": "x"}
        ]
    }
    async with _client(payload) as client:
        done = await drafting.suggest_effects(engine, script_id, keyed, client=client)

    assert done.stored >= 1
    with session_scope(engine) as session:
        slots = session.scalars(select(EffectSlot)).all()
        assert slots and all(not s.accepted and s.source == "suggested" for s in slots)
    assert _charged(engine, "suggestion")[0].cost_micros == done.cost_micros


async def test_no_key_is_refused_before_anything_is_sent(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings
) -> None:
    _, script_id = project_and_script
    assert not settings.has_groq_key
    with pytest.raises(drafting.NoGroqKey):
        await drafting.draft_titles(engine, script_id, settings)
