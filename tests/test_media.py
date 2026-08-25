"""The media tree, and the boundary that keeps it from leaking the rest of the disk."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine

from narrate.media import (
    OutsideMediaRoot,
    disk_usage,
    human_bytes,
    media_type,
    project_media,
    resolve_media_path,
)
from narrate.registry import Registry
from narrate.settings import Settings

from .conftest import needs_ffmpeg

# -- the security boundary --------------------------------------------------


@pytest.mark.parametrize(
    "attack",
    [
        "../../.env",
        "../../../etc/passwd",
        "../.env",
        "sub/../../../.env",
        "/etc/passwd",
        "/Users/sayrikey/Desktop/projects/backend/eleven-clone/.env",
    ],
)
def test_paths_outside_the_media_root_are_refused(attack: str, settings: Settings) -> None:
    """A download endpoint that takes a path is a traversal hole unless closed.

    `.env` holds the API key, so this is the specific thing being defended.
    """
    settings.assets_dir.mkdir(parents=True, exist_ok=True)
    with pytest.raises(OutsideMediaRoot):
        resolve_media_path(attack, settings)


def test_paths_inside_the_media_root_resolve(settings: Settings) -> None:
    root = settings.assets_dir
    (root / "Ep14" / "effects").mkdir(parents=True, exist_ok=True)
    target = root / "Ep14" / "effects" / "wind.mp3"
    target.write_bytes(b"audio")

    assert resolve_media_path("Ep14/effects/wind.mp3", settings) == target.resolve()
    assert resolve_media_path(target, settings) == target.resolve()


def test_a_symlink_pointing_out_of_the_tree_is_refused(settings: Settings) -> None:
    """Resolution follows symlinks, so a link cannot be used as a back door."""
    root = settings.assets_dir
    root.mkdir(parents=True, exist_ok=True)
    outside = root.parent / "secret.txt"
    outside.write_text("key")
    link = root / "escape.txt"
    link.symlink_to(outside)

    with pytest.raises(OutsideMediaRoot):
        resolve_media_path("escape.txt", settings)


def test_the_root_itself_is_allowed(settings: Settings) -> None:
    settings.assets_dir.mkdir(parents=True, exist_ok=True)
    assert resolve_media_path(".", settings) == settings.assets_dir.resolve()


# -- listing ----------------------------------------------------------------


@needs_ffmpeg
async def test_project_media_finds_every_artifact(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    from narrate.effects import generate_slots
    from narrate.export import export_script
    from narrate.provider.mock import MockProvider, MockSFXProvider
    from narrate.runner import generate

    from .test_timeline_effects import _ingest

    script_id = _ingest(engine, registry)
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)
    await generate_slots(engine, script_id, MockSFXProvider(), settings, dry_run=False)
    result = export_script(engine, script_id, settings, gap_seconds=0.4)

    from narrate.db.session import session_scope

    with session_scope(engine) as session:
        media = project_media(session, 1, settings)

    assert media.project_name == "Ep14"
    assert media.exports and media.exports[0].files
    assert media.effects.files
    assert media.takes.files
    assert media.file_count > 0
    assert media.total_bytes > 0

    names = {f.name for f in media.exports[0].files}
    assert "plan.md" in names
    assert any(n.endswith(".wav") for n in names)
    assert set(result.names.values()) <= names

    # Working files are not artifacts.
    assert not any(f.name.startswith("_") for f in media.exports[0].files)

    # Every listed path resolves inside the root and exists.
    for group in [*media.exports, media.effects, media.takes]:
        for file in group.files:
            assert resolve_media_path(file.path, settings).exists()


def test_media_types_are_sensible() -> None:
    assert media_type(Path("a.mp3")) == "audio/mpeg"
    assert media_type(Path("a.wav")).startswith("audio/")
    assert media_type(Path("plan.md")).startswith("text/")


def test_disk_usage_counts_what_is_there(settings: Settings) -> None:
    root = settings.assets_dir
    (root / "Ep14").mkdir(parents=True, exist_ok=True)
    (root / "Ep14" / "a.mp3").write_bytes(b"x" * 100)
    (root / "Ep14" / "b.mp3").write_bytes(b"x" * 200)

    files, size = disk_usage(settings)
    assert files == 2
    assert size == 300


def test_human_bytes_reads_naturally() -> None:
    assert human_bytes(512) == "512 B"
    assert human_bytes(2048) == "2.0 KB"
    assert human_bytes(5 * 1024 * 1024) == "5.0 MB"
