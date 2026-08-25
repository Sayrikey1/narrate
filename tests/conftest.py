"""Shared fixtures. Nothing here touches the network."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from narrate.db.models import Chunk, Project, Script
from narrate.db.session import init_db, make_engine, session_scope
from narrate.registry import Registry
from narrate.settings import Settings

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not installed")


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = make_engine(":memory:")
    init_db(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def registry() -> Registry:
    return Registry.load()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        api_key=None,
        db_path=tmp_path / "test.db",
        assets_dir=tmp_path / "assets",
        concurrency=3,
    )


@pytest.fixture
def sample_text() -> str:
    """Four explicitly-marked chunks.

    Markers rather than length: they give a deterministic four chunks on every
    model, so continuity, resume and partial-cut behaviour are all genuinely
    exercised. A short unmarked script collapses to a single chunk on v2 and
    silently turns those tests into no-ops.
    """
    return (
        "The first paragraph establishes the scene. It runs for two sentences.\n\n"
        "## [CHUNK 2]\n\n"
        "Dr. Halvorsen kept a record. She had kept one since 1987, and the entries "
        "grew shorter. The temperature fell 3.5 degrees overnight.\n\n"
        "## [CHUNK 3]\n\n"
        "The jackdaws had not fled the cold. They had fled the wet.\n\n"
        "## [CHUNK 4]\n\n"
        "A final paragraph closes it out. Nothing more to say."
    )


@pytest.fixture
def project_and_script(engine: Engine, sample_text: str) -> tuple[int, int]:
    """A project with one script, chunked, ready to generate."""
    from narrate.chunking import chunk_script

    reg = Registry.load()
    spec = reg.get("eleven_multilingual_v2")

    with session_scope(engine) as session:
        project = Project(name="test", voice_id="voice-1", model_id=spec.model_id)
        session.add(project)
        session.flush()
        script = Script(
            project_id=project.id,
            title="Episode 1",
            source_text=sample_text,
            source_sha256="abc",
        )
        session.add(script)
        session.flush()
        for c in chunk_script(sample_text, spec):
            session.add(Chunk(script_id=script.id, ordinal=c.ordinal, text=c.text, source=c.source))
        return project.id, script.id


def require[T](value: T | None) -> T:
    """Narrow an Optional in a test, failing loudly if the row is missing."""
    assert value is not None, "expected a row, got None"
    return value


def chunks_of(session: Session, script_id: int) -> list[Chunk]:
    return list(
        session.scalars(
            select(Chunk).where(Chunk.script_id == script_id).order_by(Chunk.ordinal)
        ).all()
    )
