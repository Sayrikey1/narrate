"""The ElevenLabs HTTP client — the only module that touches the network.

Raw `httpx` rather than the official SDK, deliberately. The SDK retries twice
by default on 429/408/409/5xx without telling the caller, which is precisely
the failure mode that double-charges a per-request ledger, and its `ApiError`
drops response headers so `request-id` has to be read from two different places
depending on whether the call succeeded. The surface we need is five endpoints;
owning the transport removes that class of bug by construction.

Response headers are the cost primitive. `character-cost` is what the provider
says it billed, and recording that instead of `len(text)` sidesteps the one
question the docs never answer — whether audio-tag characters are billed.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import httpx

from narrate.provider.base import (
    DialogueRequest,
    FatalError,
    InsufficientCredits,
    ProviderError,
    RetryableError,
    SFXRequest,
    SFXResult,
    TTSRequest,
    TTSResult,
    UnknownOutcomeError,
)
from narrate.settings import Settings, get_settings

# Statuses where the request was rejected before generating anything, so a
# retry is free. 409 is deliberately absent: the SDK treats it as retryable,
# but a conflict on a generation endpoint is not something we can reason about
# safely, so it is fatal here and surfaced.
RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


class ElevenLabsProvider:
    """Async client for the endpoints this tool needs."""

    name = "elevenlabs"

    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._settings.base_url,
            headers={"xi-api-key": self._settings.require_api_key()},
            timeout=httpx.Timeout(
                connect=self._settings.connect_timeout_s,
                read=self._settings.read_timeout_s,
                write=self._settings.read_timeout_s,
                pool=self._settings.connect_timeout_s,
            ),
            # Sized to the concurrency ceiling so the pool never becomes the
            # thing that queues requests behind the provider's own limit.
            limits=httpx.Limits(max_connections=max(4, self._settings.concurrency * 2)),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # -- generation --------------------------------------------------------

    async def synthesize(self, request: TTSRequest) -> TTSResult:
        body: dict[str, Any] = {"text": request.text, "model_id": request.model_id}

        # Only send settings the model honours — the caller has already
        # filtered through the registry, so an empty dict means "use defaults".
        if request.settings:
            body["voice_settings"] = request.settings
        if request.seed is not None:
            body["seed"] = request.seed

        # Request ids beat text context when both are sent, so never send both.
        if request.previous_request_ids:
            body["previous_request_ids"] = list(request.previous_request_ids[-3:])
        elif request.previous_text:
            body["previous_text"] = request.previous_text
        if request.next_request_ids:
            body["next_request_ids"] = list(request.next_request_ids[:3])
        elif request.next_text:
            body["next_text"] = request.next_text

        try:
            response = await self._client.post(
                f"/v1/text-to-speech/{request.voice_id}",
                params={"output_format": request.output_format},
                json=body,
            )
        except httpx.ConnectError as exc:
            raise RetryableError(f"Could not reach the provider: {exc}") from exc
        except httpx.ConnectTimeout as exc:
            raise RetryableError(f"Connection timed out: {exc}") from exc
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError) as exc:
            # The request went out. Whether it generated and billed is unknown.
            raise UnknownOutcomeError(
                f"Request was sent but no response was read ({type(exc).__name__}). "
                "It may have been billed — check the provider's usage before retrying."
            ) from exc

        if response.status_code >= 400:
            raise _classify(response)

        headers = dict(response.headers)
        billed = headers.get("character-cost")
        return TTSResult(
            audio=response.content,
            request_id=headers.get("request-id"),
            billed_chars=int(billed) if billed and billed.isdigit() else None,
            headers=headers,
        )

    async def generate_dialogue(self, request: DialogueRequest) -> TTSResult:
        """`POST /v1/text-to-dialogue` — several speakers in one generation.

        Shape differs from the speech endpoint in two ways worth naming: the
        voice lives in each `inputs[]` item rather than in the path, and there
        is no continuity field to send. `output_format` is a **query**
        parameter here as it is everywhere else on this API.
        """
        body: dict[str, Any] = {
            "model_id": request.model_id,
            "inputs": [{"text": t.text, "voice_id": t.voice_id} for t in request.turns],
        }
        if request.settings:
            body["settings"] = request.settings
        if request.seed is not None:
            body["seed"] = request.seed
        if request.language_code:
            body["language_code"] = request.language_code

        try:
            response = await self._client.post(
                "/v1/text-to-dialogue",
                params={"output_format": request.output_format},
                json=body,
            )
        except httpx.ConnectError as exc:
            raise RetryableError(f"Could not reach the provider: {exc}") from exc
        except httpx.ConnectTimeout as exc:
            raise RetryableError(f"Connection timed out: {exc}") from exc
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError) as exc:
            # Same rule as speech: the request went out, so whether it was
            # billed is unknown and a retry could double-charge.
            raise UnknownOutcomeError(
                f"Dialogue request was sent but no response was read ({type(exc).__name__}). "
                "It may have been billed — check the provider's usage before retrying."
            ) from exc

        if response.status_code >= 400:
            raise _classify(response)

        headers = dict(response.headers)
        billed = headers.get("character-cost")
        return TTSResult(
            audio=response.content,
            request_id=headers.get("request-id"),
            billed_chars=int(billed) if billed and billed.isdigit() else None,
            headers=headers,
        )

    # -- sound effects ------------------------------------------------------

    async def generate_effect(self, request: SFXRequest) -> SFXResult:
        """`POST /v1/sound-generation`.

        Note `output_format` is a **query** parameter here, not a body field —
        the same shape as the TTS endpoint, and easy to get wrong because
        everything else about the request differs.
        """
        body: dict[str, Any] = {
            "text": request.prompt,
            "model_id": request.model_id,
            "prompt_influence": request.prompt_influence,
            "loop": request.loop,
        }
        if request.duration_s is not None:
            body["duration_seconds"] = request.duration_s

        try:
            response = await self._client.post(
                "/v1/sound-generation",
                params={"output_format": request.output_format},
                json=body,
            )
        except httpx.ConnectError as exc:
            raise RetryableError(f"Could not reach the provider: {exc}") from exc
        except httpx.ConnectTimeout as exc:
            raise RetryableError(f"Connection timed out: {exc}") from exc
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError) as exc:
            raise UnknownOutcomeError(
                f"Effect request was sent but no response was read ({type(exc).__name__}). "
                "It may have been billed — check usage before retrying."
            ) from exc

        if response.status_code >= 400:
            raise _classify(response)

        headers = dict(response.headers)
        observed = headers.get("character-cost")
        return SFXResult(
            audio=response.content,
            request_id=headers.get("request-id"),
            observed_cost=int(observed) if observed and observed.isdigit() else None,
            headers=headers,
        )

    # -- metadata (all of these cost zero characters) -----------------------

    async def list_models(self) -> list[dict[str, Any]]:
        response = await self._client.get("/v1/models")
        if response.status_code >= 400:
            raise _classify(response)
        payload = response.json()
        return payload if isinstance(payload, list) else []

    async def list_voices(
        self, search: str | None = None, page_size: int = 100
    ) -> list[dict[str, Any]]:
        """Page through `GET /v2/voices`.

        v2 rather than v1: the older endpoint has no pagination at all and
        stops working once a workspace exceeds 500 voices.
        """
        voices: list[dict[str, Any]] = []
        params: dict[str, Any] = {"page_size": min(page_size, 100), "include_total_count": False}
        if search:
            params["search"] = search

        while True:
            response = await self._client.get("/v2/voices", params=params)
            if response.status_code >= 400:
                raise _classify(response)
            payload = response.json()
            voices.extend(payload.get("voices", []))
            if not payload.get("has_more") or not payload.get("next_page_token"):
                return voices
            params["next_page_token"] = payload["next_page_token"]

    async def get_voice(self, voice_id: str) -> dict[str, Any] | None:
        """One voice, without paging the workspace.

        `GET /v2/voices?voice_ids=…` filters server-side, which matters on an
        account with hundreds of voices: auditioning one should not cost several
        round trips through a paginated list.
        """
        response = await self._client.get("/v2/voices", params={"voice_ids": voice_id})
        if response.status_code >= 400:
            raise _classify(response)
        voices = response.json().get("voices") or []
        for voice in voices:
            if voice.get("voice_id") == voice_id:
                return dict(voice)
        return None

    async def subscription(self) -> dict[str, Any]:
        response = await self._client.get("/v1/user/subscription")
        if response.status_code >= 400:
            raise _classify(response)
        result: dict[str, Any] = response.json()
        return result

    async def usage_by_product(
        self,
        start: datetime,
        end: datetime,
        group_by: list[str] | None = None,
        interval_seconds: int = 86400,
    ) -> dict[str, Any]:
        """Reconciliation source (C5).

        A POST with a JSON body, not a GET with query params, and the
        timestamps are **milliseconds** — the deprecated endpoint's docs show
        second-resolution examples that are simply wrong. Getting this unit
        backwards silently returns an empty window rather than an error, which
        would look like zero provider usage and a 100% drift.
        """
        response = await self._client.post(
            "/v1/workspace/analytics/query/usage-by-product-over-time",
            json={
                "start_time": int(start.timestamp() * 1000),
                "end_time": int(end.timestamp() * 1000),
                "interval_seconds": interval_seconds,
                "group_by": group_by or ["model"],
            },
        )
        if response.status_code >= 400:
            raise _classify(response)
        result: dict[str, Any] = response.json()
        return result


def _classify(response: httpx.Response) -> ProviderError:
    """Turn an error response into the right exception type.

    Branches on HTTP status, never on the error `code` string: the docs publish
    two different vocabularies for 429 (`rate_limit_exceeded` /
    `concurrent_limit_exceeded` in the API reference,
    `too_many_concurrent_requests` / `system_busy` in the help centre), so the
    code is advisory only.
    """
    status = response.status_code
    code: str | None = None
    message = response.text[:500]
    request_id: str | None = response.headers.get("request-id")

    try:
        detail = response.json().get("detail")
        if isinstance(detail, dict):
            code = detail.get("code") or detail.get("status")
            message = detail.get("message", message)
            # On a failure the header may be absent, but the body carries it.
            request_id = detail.get("request_id") or request_id
        elif isinstance(detail, str):
            message = detail
    except (json.JSONDecodeError, ValueError, AttributeError):
        pass

    retry_after = _retry_after(response)
    kwargs: dict[str, Any] = {
        "status": status,
        "code": code,
        "request_id": request_id,
        "retry_after_s": retry_after,
    }

    if status == 402:
        return InsufficientCredits(
            f"Out of credits: {message}. Top up or lower the run's scope.", **kwargs
        )
    if status == 401:
        return FatalError(f"Authentication failed: {message}. Check ELEVEN_API in .env.", **kwargs)
    if status == 404:
        return FatalError(f"Not found: {message}. Check the voice id.", **kwargs)
    if status in RETRYABLE_STATUSES:
        return RetryableError(message or f"HTTP {status}", **kwargs)
    return FatalError(message or f"HTTP {status}", **kwargs)


def _retry_after(response: httpx.Response) -> float | None:
    """Read a backoff hint. Not contractually guaranteed, so all three forms."""
    headers = response.headers

    raw_ms = headers.get("retry-after-ms")
    if raw_ms:
        try:
            return float(raw_ms) / 1000
        except ValueError:
            pass

    raw = headers.get("retry-after")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass  # An HTTP-date form; fall through to jittered backoff.

    reset = headers.get("x-ratelimit-reset")
    if reset:
        try:
            return max(0.0, float(reset))
        except ValueError:
            pass

    return None
