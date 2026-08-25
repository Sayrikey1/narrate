"""Delivery formats, and the script upload path."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine

from narrate.audio import (
    FORMATS,
    UnknownFormat,
    codec_of,
    duration_seconds,
    encode,
    parse_formats,
)
from narrate.export import export_script
from narrate.ingest import UnsupportedScript, decode_script
from narrate.media import media_type
from narrate.provider.mock import MockProvider
from narrate.registry import Registry
from narrate.runner import generate
from narrate.settings import Settings

from .conftest import needs_ffmpeg

# -- the registry -----------------------------------------------------------


def test_parse_formats_accepts_a_comma_string_or_a_list() -> None:
    assert [f.key for f in parse_formats("wav,m4a")] == ["wav", "m4a"]
    assert [f.key for f in parse_formats(["mp3", "flac"])] == ["mp3", "flac"]


def test_parse_formats_deduplicates_but_keeps_order() -> None:
    assert [f.key for f in parse_formats("m4a, wav, m4a")] == ["m4a", "wav"]


def test_parse_formats_tolerates_a_leading_dot_and_case() -> None:
    assert [f.key for f in parse_formats(".WAV, .M4A")] == ["wav", "m4a"]


def test_an_unknown_format_names_the_alternatives() -> None:
    with pytest.raises(UnknownFormat, match="m4a"):
        parse_formats("aiff")


def test_m4a_is_aac_in_an_mp4_container() -> None:
    """The thing "mp4" means for an audio-only deliverable."""
    fmt = FORMATS["m4a"]
    assert fmt.codec == "aac"
    assert fmt.suffix == ".m4a"
    # +faststart puts the index first so it scrubs without a full read.
    assert "+faststart" in fmt.args


def test_bitrate_is_substituted_into_the_encoder_arguments() -> None:
    assert "256k" in FORMATS["mp3"].command_args("256k")
    assert "{bitrate}" not in " ".join(FORMATS["m4a"].command_args("192k"))


# -- encoding actually produces the claimed codec ---------------------------


@needs_ffmpeg
@pytest.mark.parametrize("key", sorted(FORMATS))
def test_each_format_encodes_to_its_declared_codec(key: str, tmp_path: Path) -> None:
    """A container silently holding the wrong codec is the failure worth catching."""
    from narrate.audio import make_silence

    source = make_silence(tmp_path / "src.wav", 1.5)
    fmt = FORMATS[key]
    out = encode(source, tmp_path / f"out{fmt.suffix}", fmt)

    assert out.exists() and out.stat().st_size > 0
    assert codec_of(out) == fmt.codec
    assert duration_seconds(out) == pytest.approx(1.5, abs=0.12)


@needs_ffmpeg
async def test_export_writes_every_requested_master(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    from .test_timeline_effects import _ingest

    script_id = _ingest(engine, registry)
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)

    result = export_script(
        engine, script_id, settings, formats=["wav", "mp3", "m4a", "flac", "opus"]
    )

    assert set(result.masters) == {"wav", "mp3", "m4a", "flac", "opus"}
    master_duration = duration_seconds(result.masters["wav"])
    for key, path in result.masters.items():
        assert path.exists()
        assert codec_of(path) == FORMATS[key].codec
        assert duration_seconds(path) == pytest.approx(master_duration, abs=0.12)


@needs_ffmpeg
async def test_a_lossless_master_is_always_produced(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """Every lossy encode is made from the master, never from another lossy file."""
    from .test_timeline_effects import _ingest

    script_id = _ingest(engine, registry)
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)

    result = export_script(engine, script_id, settings, formats=["mp3"])
    assert "wav" in result.masters


@needs_ffmpeg
async def test_pieces_are_written_in_the_delivery_format(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """The timeline-named files are what get dropped onto an editing track."""
    from .test_timeline_effects import _ingest

    script_id = _ingest(engine, registry)
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)

    result = export_script(engine, script_id, settings, piece_format="m4a")

    assert result.names
    for name in result.names.values():
        path = result.out_dir / name
        assert path.suffix == ".m4a"
        assert codec_of(path) == "aac"


@needs_ffmpeg
async def test_pieces_can_keep_the_source_format(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """Copying beats transcoding when the formats already match."""
    from .test_timeline_effects import _ingest

    script_id = _ingest(engine, registry)
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)

    result = export_script(engine, script_id, settings, piece_format="source")
    assert all(name.endswith(".mp3") for name in result.names.values())


# -- media types ------------------------------------------------------------


def test_m4a_and_mp4_are_served_as_playable_audio() -> None:
    """`mimetypes` says audio/mp4a-latm and video/mp4; browsers play neither."""
    assert media_type(Path("a.m4a")) == "audio/mp4"
    assert media_type(Path("a.mp4")) == "audio/mp4"


def test_the_other_formats_get_sensible_types() -> None:
    assert media_type(Path("a.wav")) == "audio/wav"
    assert media_type(Path("a.mp3")) == "audio/mpeg"
    assert media_type(Path("a.flac")) == "audio/flac"
    assert media_type(Path("a.opus")) == "audio/ogg"
    assert media_type(Path("plan.md")).startswith("text/")


@needs_ffmpeg
async def test_every_exported_file_is_listed_as_playable(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """A format the library lists but cannot play is worse than not offering it."""
    from narrate.db.session import session_scope
    from narrate.media import project_media

    from .test_timeline_effects import _ingest

    script_id = _ingest(engine, registry)
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)
    export_script(engine, script_id, settings, formats=["wav", "m4a", "flac", "opus"])

    with session_scope(engine) as session:
        media = project_media(session, 1, settings)

    audio_files = [
        f for group in media.exports for f in group.files if f.name.rsplit(".", 1)[-1] in FORMATS
    ]
    assert audio_files
    assert all(f.playable for f in audio_files)


# -- upload validation ------------------------------------------------------


@pytest.mark.parametrize("name", ["ep.txt", "ep.md", "ep.markdown", "ep.text", "EP.MD"])
def test_text_scripts_are_accepted(name: str) -> None:
    assert decode_script(name, b"The road was cleared.") == "The road was cleared."


def test_a_binary_extension_is_refused_by_name() -> None:
    with pytest.raises(UnsupportedScript, match="not a text script"):
        decode_script("payload.exe", b"MZ\x90\x00")


def test_an_oversized_file_is_refused_with_its_size() -> None:
    with pytest.raises(UnsupportedScript, match="MB"):
        decode_script("huge.txt", b"x" * 2_000_000)


def test_non_utf8_bytes_are_refused() -> None:
    """The check that matters — an extension proves nothing about the content."""
    with pytest.raises(UnsupportedScript, match="not UTF-8"):
        decode_script("ep.txt", "Hello.".encode("utf-16"))


def test_an_empty_file_is_refused() -> None:
    with pytest.raises(UnsupportedScript, match="empty"):
        decode_script("ep.txt", b"   \n  ")


def test_binary_content_wearing_a_text_extension_is_refused() -> None:
    with pytest.raises(UnsupportedScript, match="binary"):
        decode_script("ep.txt", b"Hello\x00world")
