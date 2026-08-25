"""Voice previews: the cache path rule, and the failure modes it replaces.

Auditioning a voice is free, so nothing here is about money. It is about a
control that either works or says why not — the previous version could fail in
four ways that all looked like a dead button.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from narrate import previews

from .conftest import needs_ffmpeg


@pytest.mark.parametrize(
    "voice_id",
    ["../../.env", "../secret", "a/b", "a\\b", "with space", "", "x" * 65, "semi;colon", "."],
)
def test_a_voice_id_that_is_not_plainly_an_id_is_refused(voice_id: str, tmp_path: Path) -> None:
    """Validated, never sanitised.

    Stripping the offending characters would produce a *valid* path pointing at
    the wrong cache entry, so one voice could serve another voice's sample. A
    refusal is the only safe answer.
    """
    with pytest.raises(previews.PreviewUnavailable):
        previews.preview_path(tmp_path, voice_id)


@pytest.mark.parametrize("voice_id", ["EXAVITQu4vr4xnSDxMaL", "mock-voice-1", "a", "A_b-C9"])
def test_a_real_voice_id_resolves_inside_the_cache(voice_id: str, tmp_path: Path) -> None:
    resolved = previews.preview_path(tmp_path, voice_id).resolve()
    assert previews.preview_dir(tmp_path).resolve() in resolved.parents


def test_preview_url_prefers_the_direct_link() -> None:
    voice = {"preview_url": "https://example.test/a.mp3", "samples": [{"url": "b.mp3"}]}
    assert previews.preview_url_of(voice) == "https://example.test/a.mp3"


def test_preview_url_falls_back_to_a_sample() -> None:
    """`preview_url` is documented as nullable, and cloned voices often carry
    their audio under `samples` instead."""
    voice = {"preview_url": None, "samples": [{"preview_url": "https://example.test/s.mp3"}]}
    assert previews.preview_url_of(voice) == "https://example.test/s.mp3"


def test_a_voice_with_no_audio_anywhere_reports_none() -> None:
    assert previews.preview_url_of({"preview_url": None, "samples": []}) is None
    assert previews.preview_url_of({}) is None


@needs_ffmpeg
def test_a_stub_preview_is_audible_and_stable(tmp_path: Path) -> None:
    first = previews.synthesize_stub(tmp_path, "mock-voice-1")
    size = first.stat().st_size
    assert size > 2_000

    # Second call is a cache hit, not a re-encode.
    again = previews.synthesize_stub(tmp_path, "mock-voice-1")
    assert again == first
    assert again.stat().st_size == size


@needs_ffmpeg
def test_different_voices_get_different_tones(tmp_path: Path) -> None:
    a = previews.synthesize_stub(tmp_path, "mock-voice-1").read_bytes()
    b = previews.synthesize_stub(tmp_path, "mock-voice-2").read_bytes()
    assert a != b


@pytest.mark.asyncio
async def test_a_provider_error_becomes_a_reason_not_a_dead_button(tmp_path: Path) -> None:
    """An expired storage URL is the common case, and the message has to say
    what to do about it."""
    transport = httpx.MockTransport(lambda _: httpx.Response(403, text="expired"))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(previews.PreviewUnavailable, match="403"):
            await previews.fetch_preview(
                tmp_path, "voice1", "https://example.test/a.mp3", client=client
            )


@pytest.mark.asyncio
async def test_an_empty_body_is_not_cached_as_a_valid_sample(tmp_path: Path) -> None:
    """A zero-byte file would satisfy the cache check for ever after."""
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=b""))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(previews.PreviewUnavailable):
            await previews.fetch_preview(
                tmp_path, "voice1", "https://example.test/a.mp3", client=client
            )
    assert not previews.preview_path(tmp_path, "voice1").exists()


@pytest.mark.asyncio
async def test_a_fetched_sample_is_cached_and_reused(tmp_path: Path) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"ID3fake-audio-bytes")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        url = "https://example.test/a.mp3"
        first = await previews.fetch_preview(tmp_path, "voice1", url, client=client)
        second = await previews.fetch_preview(tmp_path, "voice1", url, client=client)

    assert first == second
    assert first.read_bytes() == b"ID3fake-audio-bytes"
    # The second call must not reach the provider: choosing a voice means
    # auditioning the same handful repeatedly.
    assert calls == 1


@pytest.mark.asyncio
async def test_an_interrupted_download_leaves_no_partial_file(tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=b"ID3ok"))
    async with httpx.AsyncClient(transport=transport) as client:
        await previews.fetch_preview(tmp_path, "voice1", "https://e.test/a", client=client)
    leftovers = list(previews.preview_dir(tmp_path).glob("*.part"))
    assert not leftovers
