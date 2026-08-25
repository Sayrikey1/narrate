"""Voice previews, served from this app rather than from the provider's CDN.

A voice preview is a static sample ElevenLabs already hosts, so auditioning one
**costs zero characters**. The reason it is proxied rather than linked is that
the direct link fails in four different ways, and every one of them looks
identical in the browser — a play button that does nothing:

1. `preview_url` is documented as *optional and nullable* on `GET /v2/voices`,
   so a perfectly good voice may simply not have one.
2. The URLs point at `storage.googleapis.com` with a lifetime nobody documents.
3. Anything the browser declines to load cross-origin fails silently in an
   `<audio>` element — no error, no event worth catching.
4. Offline stand-in voices have no provider to link to at all.

Proxying turns all four into a server-side outcome that can be logged, cached
and reported. A failure becomes an HTTP status with a reason instead of a dead
control.

Cached under `assets/previews/`, because the same sample is auditioned
repeatedly while choosing and re-fetching it each time is rude to a CDN that is
doing us a favour.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import httpx

from narrate.audio import make_tone

# A voice id is provider-generated and opaque. Anything outside this set has no
# business becoming part of a filename, so it is rejected rather than sanitised
# — a sanitised id would silently address the wrong cache entry.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

PREVIEW_SECONDS = 2.5

# Spread the offline tones across a musical fifth so two stand-ins are
# audibly different from each other, which is the whole point of auditioning.
_TONE_RANGE = (330, 660)


class PreviewUnavailable(RuntimeError):
    """No sample could be produced, with a reason fit to show a user."""


def is_safe_voice_id(voice_id: str) -> bool:
    return bool(_SAFE_ID.match(voice_id))


def preview_dir(assets_dir: Path) -> Path:
    return assets_dir / "previews"


def preview_path(assets_dir: Path, voice_id: str) -> Path:
    """Where a voice's cached sample lives.

    The id is validated rather than escaped, so this can never resolve outside
    `previews/` — the same rule `media.resolve_media_path` applies to artifacts.
    """
    if not is_safe_voice_id(voice_id):
        raise PreviewUnavailable(f"{voice_id!r} is not a valid voice id.")
    return preview_dir(assets_dir) / f"{voice_id}.mp3"


def _tone_for(voice_id: str) -> int:
    """A stable frequency per voice, so a stand-in sounds like itself."""
    digest = hashlib.sha256(voice_id.encode("utf-8")).digest()
    low, high = _TONE_RANGE
    return low + digest[0] * (high - low) // 255


def synthesize_stub(assets_dir: Path, voice_id: str) -> Path:
    """The audition for an offline stand-in voice."""
    target = preview_path(assets_dir, voice_id)
    if not target.exists():
        make_tone(target, PREVIEW_SECONDS, frequency=_tone_for(voice_id))
    return target


async def fetch_preview(
    assets_dir: Path,
    voice_id: str,
    preview_url: str,
    *,
    timeout_s: float = 15.0,
    client: httpx.AsyncClient | None = None,
) -> Path:
    """Download and cache a provider sample. Returns the cached path."""
    target = preview_path(assets_dir, voice_id)
    if target.exists() and target.stat().st_size > 0:
        return target

    owned = client is None
    http = client or httpx.AsyncClient(timeout=timeout_s, follow_redirects=True)
    try:
        response = await http.get(preview_url)
    except httpx.HTTPError as exc:
        raise PreviewUnavailable(
            f"Could not fetch the sample for {voice_id}: {type(exc).__name__}."
        ) from exc
    finally:
        if owned:
            await http.aclose()

    if response.status_code >= 400:
        raise PreviewUnavailable(
            f"The provider returned {response.status_code} for {voice_id}'s sample. "
            "Preview links expire; reloading the voice list refreshes them."
        )
    if not response.content:
        raise PreviewUnavailable(f"The sample for {voice_id} came back empty.")

    target.parent.mkdir(parents=True, exist_ok=True)
    # Written via a temporary name so an interrupted download cannot leave a
    # truncated file that the cache check above would then trust forever.
    staging = target.with_suffix(".part")
    staging.write_bytes(response.content)
    staging.replace(target)
    return target


def preview_url_of(voice: dict[str, Any]) -> str | None:
    """The sample link on a `/v2/voices` entry, if it has one.

    Falls back to the first sample's own url: some cloned voices carry
    `samples` while `preview_url` is null.
    """
    direct = voice.get("preview_url")
    if isinstance(direct, str) and direct:
        return direct
    samples = voice.get("samples")
    if isinstance(samples, list):
        for sample in samples:
            if isinstance(sample, dict):
                url = sample.get("preview_url") or sample.get("url")
                if isinstance(url, str) and url:
                    return url
    return None
