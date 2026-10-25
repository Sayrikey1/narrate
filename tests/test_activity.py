"""An episode's history, read back from the records its work left."""

from __future__ import annotations

from sqlalchemy import Engine, select

from narrate import activity
from narrate.db.models import Chunk
from narrate.db.session import session_scope
from narrate.export import export_script
from narrate.provider.mock import MockProvider
from narrate.registry import Registry
from narrate.runner import generate
from narrate.settings import Settings

from .conftest import needs_ffmpeg

pytestmark = needs_ffmpeg


def _texts(engine: Engine, script_id: int) -> list[str]:
    with session_scope(engine) as session:
        return [e.text for e in activity.history(session, script_id)]


async def test_a_new_episode_has_one_event(
    engine: Engine, project_and_script: tuple[int, int]
) -> None:
    _, script_id = project_and_script
    texts = _texts(engine, script_id)
    assert len(texts) == 1
    assert texts[0].startswith("Script added — ")


async def test_generating_and_exporting_are_told_newest_first(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    await generate(engine, script_id, MockProvider(latency_s=0), registry, settings, dry_run=False)
    export_script(engine, script_id, settings)

    texts = _texts(engine, script_id)
    assert texts[0].startswith("Exported ")
    assert texts[1].startswith("Generated ")
    assert texts[-1].startswith("Script added")


async def test_runs_in_one_sitting_are_one_event_and_a_failure_says_why(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """A run that ran out of credits, then the rest generated straight after."""
    _, script_id = project_and_script
    with session_scope(engine) as session:
        texts = [c.text for c in session.scalars(select(Chunk).where(Chunk.script_id == script_id))]

    dry = MockProvider(fail_containing={texts[-1][:20]}, failure_mode="credits", latency_s=0)
    await generate(engine, script_id, dry, registry, settings, dry_run=False, concurrency=1)
    await generate(engine, script_id, MockProvider(latency_s=0), registry, settings, dry_run=False)

    [run] = [t for t in _texts(engine, script_id) if "take" in t]
    assert "over 2 runs" in run
    assert "ElevenLabs ran out of credits" in run
