"""Provider failures: classified by what happened, and explained in plain words."""

from __future__ import annotations

import json

import httpx

from narrate.failures import explain
from narrate.provider.base import FatalError, InsufficientCredits
from narrate.provider.elevenlabs import _classify

QUOTA = (
    "This request exceeds your quota of 48194. You have 45 credits remaining, while 312 "
    "credits are required for this request."
)


def _response(status: int, code: str, message: str) -> httpx.Response:
    return httpx.Response(status, json={"detail": {"status": code, "message": message}})


def test_an_exhausted_quota_is_out_of_credits_not_a_bad_key() -> None:
    """ElevenLabs answers 401 when the account runs dry - the same status as a
    bad key - and only the code tells them apart."""
    error = _classify(_response(401, "quota_exceeded", QUOTA))

    assert isinstance(error, InsufficientCredits)
    assert "Authentication" not in error.message
    assert error.status == 401


def test_a_402_is_out_of_credits_too() -> None:
    assert isinstance(
        _classify(_response(402, "payment_required", "No credits")), InsufficientCredits
    )


def test_a_bad_key_is_still_an_authentication_failure() -> None:
    error = _classify(_response(401, "invalid_api_key", "Invalid API key"))

    assert isinstance(error, FatalError)
    assert not isinstance(error, InsufficientCredits)
    assert "Authentication failed" in error.message


def _stored(message: str, status: int | None = None, code: str | None = None) -> str:
    return json.dumps({"message": message, "status": status, "code": code, "request_id": None})


def test_running_out_says_how_many_credits_were_left_and_needed() -> None:
    # As it was recorded before the classifier knew better: a 401, "authentication".
    reason = explain(
        _stored(f"Authentication failed: {QUOTA}. Check ELEVEN_API in .env.", 401, "quota_exceeded")
    )

    assert reason is not None
    assert reason.headline == "ElevenLabs ran out of credits"
    assert "45 credits were left, and it needed 312" in reason.detail
    assert "Nothing was charged" in reason.detail


def test_being_offline_is_named_as_such() -> None:
    reason = explain(
        _stored(
            "Could not reach the provider: [Errno 8] nodename nor servname provided, or not known"
        )
    )

    assert reason is not None
    assert reason.headline == "Couldn't reach ElevenLabs"
    assert "connection" in reason.fix


def test_an_unknown_outcome_warns_it_may_have_been_billed() -> None:
    reason = explain(_stored("Read timeout"), status="unknown")

    assert reason is not None
    assert "may have been billed" in reason.detail
    assert "reconcile" in reason.fix


def test_a_bad_key_points_at_the_env_file() -> None:
    reason = explain(
        _stored(
            "Authentication failed: Invalid API key. Check ELEVEN_API in .env.",
            401,
            "invalid_api_key",
        )
    )

    assert reason is not None
    assert reason.headline == "ElevenLabs didn't accept the API key"
    assert reason.detail.startswith("Invalid API key")
    assert "ELEVEN_API" in reason.fix


def test_a_take_that_worked_has_no_reason() -> None:
    assert explain(None, status="succeeded") is None


def test_an_unrecognised_failure_still_says_something() -> None:
    reason = explain(_stored("Something new"))

    assert reason is not None
    assert reason.detail == "Something new"
