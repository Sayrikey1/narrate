"""Outline-first drafting: the beat sheet, its budget, and what it renders to.

The property that matters most here is the round trip. A beat sheet is not a note
about a script, it *is* the script's source — so `render_script` has to produce
Markdown the existing parser already understands, and two things have to come out
of that without any special casing:

* each beat's heading becomes a chapter, so outlining and chaptering are one
  feature rather than two;
* each beat's intent becomes an HTML comment, so the writer's notes travel with
  the script and are never narrated or billed.

That second one is the same property `tests/test_templates.py` checks for the
shipped templates, and it caught a real bug there on its first run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.engine import Engine

from narrate import outline
from narrate.db.models import ScriptBeat
from narrate.db.session import session_scope
from narrate.script_parse import parse_script
from narrate.settings import Settings


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://groq.test/openai/v1"
    )


def _reply(payload: dict[str, Any], *, prompt: int = 300, completion: int = 500) -> httpx.Response:
    return httpx.Response(
        200,
        text=json.dumps(
            {
                "choices": [{"message": {"content": json.dumps(payload)}}],
                "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
            }
        ),
    )


def _beat(
    ordinal: int,
    heading: str,
    *,
    intent: str = "",
    body: str = "",
    seconds: float = 60.0,
) -> ScriptBeat:
    return ScriptBeat(
        script_id=1,
        ordinal=ordinal,
        heading=heading,
        intent=intent,
        target_seconds=seconds,
        body=body,
    )


# --------------------------------------------------------------------------
# The round trip — the reason the beat sheet is the source
# --------------------------------------------------------------------------


def test_every_beat_becomes_a_chapter() -> None:
    """Outlining and chaptering turn out to be one feature."""
    beats = [
        _beat(1, "The Hook", intent="Earn the next minute.", body="Opening words here."),
        _beat(2, "The Middle", intent="Explain it.", body="More words here."),
        _beat(3, "The End", intent="Land it.", body="Closing words here."),
    ]
    parsed = parse_script(outline.render_script(beats, "An Episode"))
    assert [c.title for c in parsed.chapters] == ["The Hook", "The Middle", "The End"]


def test_no_word_of_the_intent_is_ever_narrated() -> None:
    """Intent is rendered as a comment, which is stripped before anything is
    billed. If this fails, the writer's private notes are being read aloud and
    charged for."""
    beats = [
        _beat(1, "The Hook", intent="SECRET NOTE TO SELF", body="Spoken words only."),
        _beat(2, "The End", intent="ANOTHER SECRET", body="More spoken words."),
    ]
    parsed = parse_script(outline.render_script(beats, "An Episode"))
    assert "SECRET" not in parsed.text
    assert "ANOTHER" not in parsed.text
    assert parsed.text == "Spoken words only.\n\nMore spoken words."


def test_the_episode_title_is_not_a_chapter() -> None:
    """`#` is the episode's own name. A chapter named after the episode is noise."""
    beats = [_beat(1, "The Hook", body="Words."), _beat(2, "The End", body="More.")]
    parsed = parse_script(outline.render_script(beats, "An Episode"))
    assert "An Episode" not in [c.title for c in parsed.chapters]


def test_an_unexpanded_beat_renders_its_heading_but_no_prose() -> None:
    """The outline is reviewable before a word of it is paid for."""
    body = outline.render_script([_beat(1, "The Hook", intent="Do the thing.")], "An Episode")
    assert "## The Hook" in body
    assert parse_script(body).text == ""


def test_the_time_budget_reaches_the_rendered_comment() -> None:
    """So a writer editing the file by hand can see what each section has."""
    body = outline.render_script([_beat(1, "The Hook", intent="Go.", seconds=70)], "Ep")
    assert "~70s" in body


# --------------------------------------------------------------------------
# Reading pace
# --------------------------------------------------------------------------


def test_with_no_takes_the_pace_falls_back_to_the_documented_constant(
    engine: Engine, project_and_script: tuple[int, int]
) -> None:
    project_id, _ = project_and_script
    with session_scope(engine) as session:
        assert outline.words_per_second(session, project_id) == outline.DEFAULT_WORDS_PER_SECOND


def test_words_for_scales_with_the_measured_pace() -> None:
    assert outline.words_for(60, 2.5) == 150
    assert outline.words_for(60, 3.0) == 180
    # Never zero: a request for no words is not a useful request.
    assert outline.words_for(0, 2.5) == 1


# --------------------------------------------------------------------------
# Proposing an outline
# --------------------------------------------------------------------------


def _outline_payload(shares: list[float]) -> dict[str, Any]:
    return {
        "beats": [
            {"heading": f"Beat {n}", "intent": f"Do thing {n}.", "share": share}
            for n, share in enumerate(shares, start=1)
        ]
    }


@pytest.mark.asyncio
async def test_the_budget_adds_up_to_the_requested_runtime(settings: Settings) -> None:
    """The model returns shares; the seconds are computed here. A beat sheet
    whose parts do not add up to the episode is not a budget."""
    async with _client(lambda _: _reply(_outline_payload([10, 20, 30, 40]))) as client:
        run = await outline.propose_outline("a brief", settings, minutes=10, client=client)

    total = sum(b.target_seconds for b in run.beats)
    assert total == pytest.approx(600.0, abs=0.5)


@pytest.mark.asyncio
async def test_shares_that_do_not_add_to_a_hundred_are_normalised(
    settings: Settings,
) -> None:
    """Asking the model for shares that sum to 100 is a request, not a
    guarantee. Normalising against what came back is what keeps the budget
    honest."""
    async with _client(lambda _: _reply(_outline_payload([5, 5, 5]))) as client:
        run = await outline.propose_outline("a brief", settings, minutes=6, client=client)

    assert sum(b.target_seconds for b in run.beats) == pytest.approx(360.0, abs=0.5)
    assert all(b.target_seconds == pytest.approx(120.0, abs=0.5) for b in run.beats)


@pytest.mark.asyncio
async def test_a_failed_outline_reports_rather_than_raises(settings: Settings) -> None:
    async with _client(lambda _: httpx.Response(500, text="boom")) as client:
        run = await outline.propose_outline("a brief", settings, minutes=10, client=client)
    assert run.beats == []
    assert run.errors


# --------------------------------------------------------------------------
# Expanding
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_request_is_made_per_beat(settings: Settings) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _reply({"prose": "Some narration."})

    beats = [_beat(1, "One"), _beat(2, "Two"), _beat(3, "Three")]
    async with _client(handler) as client:
        run = await outline.expand_beats(beats, settings, title="Ep", client=client)

    assert calls == 3
    assert run.written == {1: "Some narration.", 2: "Some narration.", 3: "Some narration."}


@pytest.mark.asyncio
async def test_each_beat_is_told_its_neighbours_so_the_prose_joins_up(
    settings: Settings,
) -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["messages"][1]["content"])
        return _reply({"prose": "Words."})

    beats = [_beat(1, "First"), _beat(2, "Middle"), _beat(3, "Last")]
    async with _client(handler) as client:
        await outline.expand_beats(beats, settings, title="Ep", client=client)

    assert "this is the opening" in sent[0]
    assert "First" in sent[1] and "Last" in sent[1]
    assert "this is the end" in sent[2]


@pytest.mark.asyncio
async def test_a_failing_beat_costs_only_that_beat(settings: Settings) -> None:
    """The reason expansion is one request per section rather than one per script."""

    def handler(request: httpx.Request) -> httpx.Response:
        # `Section:` specifically — every beat's prompt also *names* its
        # neighbours, so a bare "Two" would match all three.
        if "Section: Two" in json.loads(request.content)["messages"][1]["content"]:
            return httpx.Response(429, headers={"retry-after": "9"}, text="slow down")
        return _reply({"prose": "Words."})

    beats = [_beat(1, "One"), _beat(2, "Two"), _beat(3, "Three")]
    async with _client(handler) as client:
        run = await outline.expand_beats(beats, settings, title="Ep", client=client)

    assert sorted(run.written) == [1, 3]
    assert len(run.errors) == 1
    assert "beat 2" in run.errors[0]
    # Two beats succeeded, so two beats' tokens are charged.
    assert run.usage.prompt_tokens == 600


@pytest.mark.asyncio
async def test_the_word_target_follows_the_measured_pace(settings: Settings) -> None:
    """A faster reader needs more words to fill the same section."""
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["messages"][0]["content"])
        return _reply({"prose": "Words."})

    beat = [_beat(1, "One", seconds=60.0)]
    async with _client(handler) as client:
        await outline.expand_beats(beat, settings, title="Ep", rate=2.0, client=client)
        await outline.expand_beats(beat, settings, title="Ep", rate=4.0, client=client)

    assert "120 words" in sent[0]
    assert "240 words" in sent[1]


def test_an_outline_for_a_deleted_project_is_refused_before_groq_is_called(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refused after the call, the tokens were spent and — the command dying
    before the ledger write — recorded nowhere."""
    from typer.testing import CliRunner

    from narrate import cli
    from narrate.settings import get_settings

    db = tmp_path / "cli.db"
    monkeypatch.setenv("NARRATE_DB_PATH", str(db))
    monkeypatch.setenv("NARRATE_DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setenv("NARRATE_ASSETS_DIR", str(tmp_path / "assets"))
    monkeypatch.setenv("NARRATE_PROVIDER", "mock")
    monkeypatch.setenv("GROQ_API_KEY", "not-a-real-key")
    calls: list[str] = []

    async def spy(brief: str, *args: Any, **kwargs: Any) -> Any:
        calls.append(brief)
        raise AssertionError("Groq must not be called for a deleted project")

    monkeypatch.setattr(outline, "propose_outline", spy)
    get_settings.cache_clear()
    try:
        runner = CliRunner()
        runner.invoke(cli.app, ["project", "new", "Show", "--voice", "v1"])
        runner.invoke(cli.app, ["project", "delete", "Show", "--yes"])
        result = runner.invoke(
            cli.app,
            ["write", "outline", "-p", "1", "--title", "Ep", "--brief", "A lighthouse.", "--go"],
        )
    finally:
        get_settings.cache_clear()

    assert result.exit_code == 1 and "deleted" in result.output
    assert calls == []
