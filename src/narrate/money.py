"""Money handling for the ledger.

Costs are stored as integer **micro-USD** (millionths of a dollar), never as
floats. A ledger that sums thousands of sub-cent amounts cannot afford binary
rounding drift, and integers make the append-only totals exactly reproducible.

A single 150-character probe at $0.10/1k is $0.015 — 15,000 micros — so the
unit has three orders of magnitude of headroom below the smallest real charge.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

MICROS_PER_USD = 1_000_000


def micros_from_chars(chars: int, usd_per_1k: float | Decimal) -> int:
    """Cost of `chars` characters at a per-1,000-character USD rate."""
    rate = Decimal(str(usd_per_1k))
    exact = Decimal(chars) * rate / Decimal(1000) * MICROS_PER_USD
    return int(exact.to_integral_value(rounding=ROUND_HALF_UP))


def micros_from_seconds(seconds: float, usd_per_minute: float | Decimal) -> int:
    """Cost of `seconds` of audio at a per-minute USD rate.

    Sound effects are billed by duration rather than by character, so they need
    their own conversion. Whether the provider rounds partial seconds is
    undocumented; this bills the exact duration, which `narrate probe --live
    --effects` can be reconciled against.
    """
    rate = Decimal(str(usd_per_minute))
    exact = Decimal(str(seconds)) * rate / Decimal(60) * MICROS_PER_USD
    return int(exact.to_integral_value(rounding=ROUND_HALF_UP))


def usd(micros: int) -> Decimal:
    """Micro-USD as a Decimal number of dollars, for display."""
    return (Decimal(micros) / MICROS_PER_USD).quantize(Decimal("0.000001"))


def fmt_usd(micros: int, places: int = 4) -> str:
    """Render micro-USD as a `$0.0000` string.

    Four places by default because a single chunk often costs under a cent, and
    rounding those to `$0.01` in a per-take table would hide exactly the
    re-roll spend the ledger exists to expose.
    """
    quantum = Decimal(1).scaleb(-places)
    amount = (Decimal(micros) / MICROS_PER_USD).quantize(quantum, rounding=ROUND_HALF_UP)
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount)}"


def pct(part: int, whole: int) -> float:
    """Percentage, with the zero-denominator case defined as 0.0."""
    if whole == 0:
        return 0.0
    return round(part / whole * 100, 1)
