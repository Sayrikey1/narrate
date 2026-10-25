"""What went wrong with a request, in words somebody can act on.

A failed take keeps the provider's own error — status, code and message — in
`Take.error_json`. That is the right record and the wrong thing to show: "HTTP
401: This request exceeds your quota of 48194" says *authentication* to anyone
who reads the status, when the account simply ran out of credits. This turns
the record into a headline, the detail that matters, and what to do next, so
the web page and the command line say the same thing about the same failure.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

# "You have 45 credits remaining, while 312 credits are required for this request."
_CREDITS = re.compile(
    r"You have (?P<have>[\d,]+) credits? remaining, while (?P<need>[\d,]+) credits? are required"
)

_NOTHING_CHARGED = "Nothing was charged for it."


@dataclass(frozen=True)
class Explanation:
    headline: str
    detail: str
    fix: str


def explain(error_json: str | None, status: str = "failed") -> Explanation | None:
    """The plain-language reason a take did not produce audio, or `None` for one
    that did."""
    if status == "succeeded":
        return None
    error: dict[str, Any] = {}
    if error_json:
        try:
            loaded = json.loads(error_json)
            if isinstance(loaded, dict):
                error = loaded
        except json.JSONDecodeError:
            error = {"message": error_json}

    message = str(error.get("message") or "")
    http = error.get("status")
    code = str(error.get("code") or "")

    if status == "unknown":
        return Explanation(
            "Sent, but no answer came back",
            "The request reached ElevenLabs and the reply never arrived, so it may have "
            "been billed.",
            "Check `narrate cost reconcile` before generating it again.",
        )

    if code == "quota_exceeded" or http == 402 or "exceeds your quota" in message:
        found = _CREDITS.search(message)
        detail = (
            f"{int(found['have'].replace(',', '')):,} credits were left, and it needed "
            f"{int(found['need'].replace(',', '')):,}. {_NOTHING_CHARGED}"
            if found
            else f"The account had no credits left for it. {_NOTHING_CHARGED}"
        )
        return Explanation(
            "ElevenLabs ran out of credits",
            detail,
            "Top up on ElevenLabs or wait for the monthly reset, then generate it again.",
        )

    if "Could not reach the provider" in message:
        return Explanation(
            "Couldn't reach ElevenLabs",
            f"The computer was offline, or ElevenLabs could not be found. {_NOTHING_CHARGED}",
            "Check the connection, then generate it again.",
        )

    if http == 401:
        return Explanation(
            "ElevenLabs didn't accept the API key",
            f"{_reason(message)} {_NOTHING_CHARGED}".strip(),
            "Check ELEVEN_API in .env, then generate it again.",
        )

    if http == 404:
        return Explanation(
            "ElevenLabs doesn't know this voice",
            f"The voice may have been removed from the account. {_NOTHING_CHARGED}",
            "Choose another voice in the project's settings, then generate it again.",
        )

    if http == 429:
        return Explanation(
            "ElevenLabs was too busy",
            f"It turned the request away rather than queue it. {_NOTHING_CHARGED}",
            "Generate it again in a minute.",
        )

    if isinstance(http, int) and http >= 500:
        return Explanation(
            "ElevenLabs had a problem of its own",
            f"{_reason(message)} {_NOTHING_CHARGED}".strip(),
            "Generate it again shortly.",
        )

    if http in (400, 422):
        return Explanation(
            "ElevenLabs refused the request",
            _reason(message) or "It did not say why.",
            "Check the chunk's text and the project's model, then generate it again.",
        )

    return Explanation(
        "It didn't generate",
        _reason(message) or "No reason was recorded.",
        "Generate it again.",
    )


def _reason(message: str) -> str:
    """The provider's message without the prefixes this app added to it."""
    for prefix in ("Authentication failed: ", "Out of credits: ", "Not found: "):
        if message.startswith(prefix):
            message = message[len(prefix) :]
    return message.strip().removesuffix(". Check ELEVEN_API in .env.").strip()
