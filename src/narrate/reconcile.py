"""Reconciliation (PRD C5).

Local computation is an estimate; the provider's counter is authoritative.
Silent divergence between the two would undermine the entire cost feature, so
this compares them over a window and records the result.

Targets `POST /v1/workspace/analytics/query/usage-by-product-over-time`. The
older `/v1/usage/character-stats` is deprecated. Two traps in that endpoint
which this module handles explicitly:

* Timestamps are **milliseconds**. The deprecated endpoint's docs show
  second-resolution examples that are wrong, and passing seconds returns an
  empty window rather than an error — which reads as "the provider says you
  used nothing" and looks like 100% drift.
* The response is tabular (`columns` / `rows`), not a keyed map, and the
  usage column's name is not guaranteed, so it is located rather than assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine

from narrate.db.models import Reconciliation
from narrate.db.session import session_scope
from narrate.ledger import ledger_total_micros
from narrate.money import pct

# PRD §10 targets estimate accuracy within 2%.
DRIFT_THRESHOLD_PCT = 2.0

# Column names that have carried the usage figure, most specific first.
_USAGE_COLUMN_HINTS = ("credits", "characters", "tts_characters", "usage", "value", "count")


class UnreadableUsageResponse(RuntimeError):
    pass


@dataclass(frozen=True)
class ReconcileResult:
    window_start: datetime
    window_end: datetime
    local_chars: int
    provider_chars: int
    local_micros: int
    drift_pct: float
    within_threshold: bool
    note: str = ""

    @property
    def coverage_pct(self) -> float:
        """Share of provider-reported usage the ledger accounts for."""
        return pct(self.local_chars, self.provider_chars)

    @property
    def unaccounted_chars(self) -> int:
        return max(0, self.provider_chars - self.local_chars)

    @property
    def verdict(self) -> str:
        if self.provider_chars == 0 and self.local_chars > 0:
            return "provider reported no usage — check the window and the workspace scope"
        if self.within_threshold:
            return f"within {DRIFT_THRESHOLD_PCT}% — ledger agrees with the provider"
        if self.local_chars > self.provider_chars:
            return (
                f"DRIFT {self.drift_pct:+.1f}% — the ledger records MORE than the provider "
                "billed, which suggests double-counting"
            )
        return (
            f"{self.unaccounted_chars:,} characters unaccounted for — the ledger covers "
            f"{self.coverage_pct}% of what the provider billed"
        )


def extract_usage_total(payload: dict[str, Any]) -> int:
    """Sum the usage column out of the tabular analytics response."""
    columns = payload.get("columns")
    rows = payload.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise UnreadableUsageResponse(
            f"Unexpected analytics response shape: keys={sorted(payload)}. "
            "The endpoint contract may have changed."
        )

    lowered = [str(c).lower() for c in columns]
    index: int | None = None
    for hint in _USAGE_COLUMN_HINTS:
        for i, name in enumerate(lowered):
            if hint in name:
                index = i
                break
        if index is not None:
            break

    if index is None:
        # Fall back to the last numeric column — tabular analytics responses
        # conventionally put the measure last.
        types = payload.get("column_types") or []
        numeric = [i for i, t in enumerate(types) if str(t) in ("Int", "Float")]
        if not numeric:
            raise UnreadableUsageResponse(
                f"No usage column found in analytics response. Columns: {columns}"
            )
        index = numeric[-1]

    total = 0.0
    for row in rows:
        if isinstance(row, list) and len(row) > index:
            value = row[index]
            if isinstance(value, (int, float)):
                total += float(value)
    return round(total)


def reconcile(
    engine: Engine,
    provider_usage: dict[str, Any],
    window_start: datetime,
    window_end: datetime,
    persist: bool = True,
) -> ReconcileResult:
    """Compare the local ledger against the provider's own counter."""
    provider_chars = extract_usage_total(provider_usage)

    with session_scope(engine) as session:
        local_chars, local_micros = ledger_total_micros(session, window_start, window_end)

    # Signed, relative to the provider — the authoritative side. A positive
    # figure means the ledger over-counted, negative that it missed spend.
    if provider_chars == 0:
        drift = 0.0 if local_chars == 0 else 100.0
    else:
        drift = round((local_chars - provider_chars) / provider_chars * 100, 2)

    within = abs(drift) <= DRIFT_THRESHOLD_PCT

    note = ""
    if provider_chars > local_chars:
        # Almost always benign, and worth saying so plainly: the provider counts
        # everything on the account, while the ledger only knows what this tool
        # generated. Anything from the web UI, another client, or `narrate probe`
        # shows up here as drift.
        note = (
            f"The provider counts all account usage; this ledger only records what this "
            f"tool generated. {provider_chars - local_chars:,} character(s) in this window "
            "came from somewhere else — the web UI, another client, or `narrate probe`."
        )
    elif local_chars > provider_chars:
        note = (
            "The ledger claims more usage than the provider billed. Check for takes "
            "recorded twice, or a window that straddles a billing boundary."
        )

    result = ReconcileResult(
        window_start=window_start,
        window_end=window_end,
        local_chars=local_chars,
        provider_chars=provider_chars,
        local_micros=local_micros,
        drift_pct=drift,
        within_threshold=within,
        note=note,
    )

    if persist:
        with session_scope(engine) as session:
            session.add(
                Reconciliation(
                    window_start=window_start,
                    window_end=window_end,
                    local_chars=local_chars,
                    provider_chars=provider_chars,
                    local_micros=local_micros,
                    drift_pct=drift,
                    within_threshold=within,
                    note=note,
                )
            )
    return result
