"""The one command that deliberately spends characters.

Several things the ledger would like to know are not stated anywhere in the
provider's documentation. Rather than encode a guess and let it quietly skew
figures, this runs four tiny generations — under 100 characters in total, well
under two cents — and reports what actually happened.

What it settles:

* **Are audio-tag characters billed?** No primary source says either way. The
  `character-cost` header for `"Hello world."` versus `"[whispers] Hello
  world."` answers it in one diff.
* **Is context text billed?** Same method, with `previous_text` set.
* **Does v3 accept `speed`?** The docs say speed is unavailable on v3, but the
  JSON schema has no per-model conditional and no page states whether sending
  it 400s or is silently ignored.
* **What headers actually come back?** They are documented in prose and code
  samples but absent from the OpenAPI response schema, so they cannot be
  generated — they have to be observed.

Results are written to `docs/probe-results.md` so the answers outlive the run.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from narrate.provider.base import ProviderError, TTSProvider, TTSRequest
from narrate.registry import Registry
from narrate.settings import PROJECT_ROOT

BASELINE_TEXT = "Hello world."
TAGGED_TEXT = "[whispers] Hello world."
CONTEXT_TEXT = "This is the sentence that came before."


@dataclass
class ProbeCase:
    name: str
    question: str
    submitted_chars: int
    billed_chars: int | None = None
    status: str = "ok"
    error: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def billed_display(self) -> str:
        return "—" if self.billed_chars is None else str(self.billed_chars)


@dataclass
class ProbeReport:
    model_id: str
    voice_id: str
    cases: list[ProbeCase] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)

    @property
    def total_submitted(self) -> int:
        return sum(c.submitted_chars for c in self.cases)

    @property
    def total_billed(self) -> int:
        return sum(c.billed_chars or 0 for c in self.cases)


def estimate_probe_chars(model_supports_tags: bool) -> int:
    """What `probe --live` will submit, so the cost can be shown before it runs."""
    total = len(BASELINE_TEXT) * 2  # baseline + context case
    if model_supports_tags:
        total += len(TAGGED_TEXT)
    total += len(BASELINE_TEXT)  # the speed case
    return total


async def _run_case(provider: TTSProvider, case: ProbeCase, request: TTSRequest) -> ProbeCase:
    try:
        result = await provider.synthesize(request)
    except ProviderError as exc:
        case.status = f"HTTP {exc.status}" if exc.status else "error"
        case.error = exc.message
        return case
    case.billed_chars = result.billed_chars
    case.headers = result.headers
    return case


async def run_probe(
    provider: TTSProvider,
    registry: Registry,
    voice_id: str,
    model_id: str,
) -> ProbeReport:
    spec = registry.get(model_id)
    report = ProbeReport(model_id=model_id, voice_id=voice_id)

    base = TTSRequest(text=BASELINE_TEXT, voice_id=voice_id, model_id=model_id)

    baseline = await _run_case(
        provider,
        ProbeCase("baseline", "What does plain text cost?", len(BASELINE_TEXT)),
        base,
    )
    report.cases.append(baseline)

    tagged: ProbeCase | None = None
    if spec.audio_tags:
        tagged = await _run_case(
            provider,
            ProbeCase("audio_tag", "Are audio-tag characters billed?", len(TAGGED_TEXT)),
            TTSRequest(text=TAGGED_TEXT, voice_id=voice_id, model_id=model_id),
        )
        report.cases.append(tagged)

    context = await _run_case(
        provider,
        ProbeCase("previous_text", "Is context text billed?", len(BASELINE_TEXT)),
        TTSRequest(
            text=BASELINE_TEXT,
            voice_id=voice_id,
            model_id=model_id,
            previous_text=CONTEXT_TEXT,
        ),
    )
    report.cases.append(context)

    # Deliberately bypasses the registry's settings filter — the whole point is
    # to find out what the API does with a field we normally decline to send.
    speed = await _run_case(
        provider,
        ProbeCase("speed_field", "Does this model accept `speed`?", len(BASELINE_TEXT)),
        TTSRequest(
            text=BASELINE_TEXT,
            voice_id=voice_id,
            model_id=model_id,
            settings={"speed": 0.8},
        ),
    )
    report.cases.append(speed)

    # -- findings ---------------------------------------------------------

    if baseline.billed_chars is None:
        report.findings.append(
            "No `character-cost` header was returned. The ledger will fall back to "
            "estimating from text length, and entries will be marked `estimated`."
        )
    else:
        report.findings.append(
            f"Plain text: {baseline.submitted_chars} submitted, {baseline.billed_chars} billed."
        )

    if tagged and tagged.billed_chars is not None and baseline.billed_chars is not None:
        delta = tagged.billed_chars - baseline.billed_chars
        tag_len = len(TAGGED_TEXT) - len(BASELINE_TEXT)
        if delta >= tag_len:
            report.findings.append(
                f"**Audio-tag characters ARE billed** (+{delta} for {tag_len} tag characters). "
                "Prefix tags must stay in the estimate."
            )
        elif delta == 0:
            report.findings.append(
                "**Audio-tag characters are NOT billed** — the tag was free. "
                "Estimates that include tags will read high."
            )
        else:
            report.findings.append(
                f"Audio tags billed partially: +{delta} for {tag_len} tag characters."
            )

    if context.billed_chars is not None and baseline.billed_chars is not None:
        delta = context.billed_chars - baseline.billed_chars
        if delta == 0:
            report.findings.append("`previous_text` context is **not** billed.")
        else:
            report.findings.append(
                f"`previous_text` context **is** billed (+{delta} characters). "
                "Continuity has a running cost on this model."
            )

    if context.status != "ok":
        report.findings.append(
            f"**`previous_text` is rejected** by {model_id} ({context.status}: {context.error}) "
            "— this model has no text-conditioning continuity either."
        )

    if speed.status != "ok":
        report.findings.append(
            f"`speed` is **rejected** by {model_id} ({speed.status}: {speed.error}). "
            "The registry is right to filter it out."
        )
    else:
        report.findings.append(
            f"`speed` is **accepted** (not rejected) by {model_id}. "
            "Whether it has any audible effect is a separate question — "
            "the docs say it does not on v3."
        )

    headers = baseline.headers or {}
    if headers.get("maximum-concurrent-requests"):
        report.findings.append(
            f"Concurrency ceiling for this model family: "
            f"{headers['maximum-concurrent-requests']}. Safe to raise `concurrency` to it."
        )

    return report


def write_report(report: ProbeReport, path: Path | None = None) -> Path:
    target = path or PROJECT_ROOT / "docs" / "probe-results.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Probe results",
        "",
        f"Run {datetime.now(tz=UTC).isoformat(timespec='seconds')} against "
        f"`{report.model_id}` with voice `{report.voice_id}`.",
        "",
        f"Submitted {report.total_submitted} characters, billed {report.total_billed}.",
        "",
        "## Findings",
        "",
    ]
    lines += [f"- {f}" for f in report.findings]
    lines += [
        "",
        "## Cases",
        "",
        "| Case | Question | Submitted | Billed | Result |",
        "|---|---|---|---|---|",
    ]
    for case in report.cases:
        result = case.status if case.error is None else f"{case.status} — {case.error}"
        lines.append(
            f"| `{case.name}` | {case.question} | {case.submitted_chars} | "
            f"{case.billed_display} | {result} |"
        )

    observed = next((c.headers for c in report.cases if c.headers), {})
    if observed:
        lines += ["", "## Response headers observed", "", "```"]
        lines += [f"{k}: {v}" for k, v in sorted(observed.items())]
        lines += ["```"]

    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def probe_headers_summary(report: ProbeReport) -> dict[str, Any]:
    return {
        "model_id": report.model_id,
        "submitted": report.total_submitted,
        "billed": report.total_billed,
        "findings": report.findings,
    }


# ---------------------------------------------------------------------------
# Sound effects
# ---------------------------------------------------------------------------

# Deliberately short and spaced so the billing behaviour is legible: if cost
# is linear in duration, 1.0s costs twice 0.5s; if partial seconds round up,
# 0.5s and 1.0s cost the same.
EFFECT_DURATIONS = (0.5, 1.0, 1.5)
EFFECT_PROMPT = "a single soft click, one-shot"


@dataclass
class EffectProbeCase:
    duration_s: float
    observed_cost: int | None = None
    actual_duration_s: float | None = None
    status: str = "ok"
    error: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class EffectProbeReport:
    cases: list[EffectProbeCase] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)

    @property
    def total_seconds(self) -> float:
        return sum(c.duration_s for c in self.cases)


async def run_effect_probe(provider: Any, output_format: str) -> EffectProbeReport:
    """Settle what `character-cost` means for a product billed per second.

    The header is declared on the sound-effects endpoint, but nothing
    documents its unit — and the docs say the cost "is not influenced by the
    text input", which rules out it being `len(text)`. Three short generations
    at known durations answer it empirically, which is the only way to get an
    answer that can be trusted in a ledger.
    """
    from narrate.provider.base import SFXRequest

    report = EffectProbeReport()

    for seconds in EFFECT_DURATIONS:
        case = EffectProbeCase(duration_s=seconds)
        try:
            result = await provider.generate_effect(
                SFXRequest(
                    prompt=EFFECT_PROMPT,
                    duration_s=seconds,
                    output_format=output_format,
                )
            )
        except ProviderError as exc:
            case.status = f"HTTP {exc.status}" if exc.status else "error"
            case.error = exc.message
            report.cases.append(case)
            continue
        case.observed_cost = result.observed_cost
        case.headers = result.headers
        report.cases.append(case)

    measured = [c for c in report.cases if c.observed_cost is not None]
    if not measured:
        report.findings.append(
            "No `character-cost` header came back for sound effects. Cost is computed "
            "from duration, which is the documented basis, so the ledger is unaffected."
        )
        return report

    for case in measured:
        per_second = (case.observed_cost or 0) / case.duration_s
        report.findings.append(
            f"{case.duration_s:g}s billed `character-cost: {case.observed_cost}` "
            f"({per_second:.0f} per second)."
        )

    fitted = fit_rate([(c.duration_s, c.observed_cost or 0) for c in measured])
    if fitted is None:
        observed = sorted({round((c.observed_cost or 0) / c.duration_s, 2) for c in measured})
        report.findings.append(
            f"No single per-second rate explains these ({observed}). There may be a "
            "minimum charge. Reconcile against the provider's usage analytics before "
            "trusting duration-based estimates for very short effects."
        )
        return report

    rate, rounds_up = fitted
    rounding = "rounded up to the next whole unit" if rounds_up else "not rounded"
    report.findings.append(
        f"**`character-cost` is {rate} per second, {rounding}.** "
        f"That is not the 40 credits/second the subscription docs quote, which is "
        f"consistent with API generations being discounted — the same pattern speech "
        f"shows, where a 12-character request bills 3."
    )
    report.findings.append(
        "The ledger is unaffected: effect cost is computed from duration at the "
        "published $0.12/minute API rate, and this header is recorded beside it for "
        "reconciliation rather than used to charge."
    )
    return report


def fit_rate(samples: list[tuple[float, int]]) -> tuple[int, bool] | None:
    """Find a per-second rate that explains every observation.

    Tries exact linear first, then ceiling — a provider billing whole units of
    a per-second rate rounds partial seconds up, and telling the two apart
    matters for anyone budgeting lots of very short cues.
    """
    if not samples:
        return None
    for rate in range(1, 201):
        if all(round(duration * rate, 6) == cost for duration, cost in samples):
            return rate, False
        if all(math.ceil(round(duration * rate, 6)) == cost for duration, cost in samples):
            return rate, True
    return None


def write_effect_report(report: EffectProbeReport, path: Path | None = None) -> Path:
    target = path or PROJECT_ROOT / "docs" / "probe-effects.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Sound-effect probe",
        "",
        f"Run {datetime.now(tz=UTC).isoformat(timespec='seconds')}. "
        f"Generated {report.total_seconds:g} seconds of audio.",
        "",
        "## Findings",
        "",
        *[f"- {f}" for f in report.findings],
        "",
        "## Cases",
        "",
        "| Requested | `character-cost` | Per second | Result |",
        "|---:|---:|---:|---|",
    ]
    for case in report.cases:
        per_second = (
            f"{(case.observed_cost or 0) / case.duration_s:.0f}"
            if case.observed_cost is not None
            else "—"
        )
        billed = "—" if case.observed_cost is None else str(case.observed_cost)
        detail = f" — {case.error}" if case.error else ""
        lines.append(f"| {case.duration_s:g}s | {billed} | {per_second} | {case.status}{detail} |")

    observed = next((c.headers for c in report.cases if c.headers), {})
    if observed:
        lines += ["", "## Response headers observed", "", "```"]
        lines += [f"{k}: {v}" for k, v in sorted(observed.items())]
        lines += ["```"]

    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Dialogue
#
# `POST /v1/text-to-dialogue` has no published price. Third-party sources quote
# roughly $0.184/1k against $0.10/1k for plain speech — a 1.84x difference that
# would make every estimate wrong by nearly double if it is right, and wrong by
# nearly half if it is not. Neither is acceptable in a tool whose whole claim is
# that it can tell you what an episode cost.
#
# So the rate card declares a multiplier of 1.0, flags it unverified, and this
# probe settles it the same way `run_effect_probe` settled the per-second
# question for effects: send the smallest useful request and read the
# `character-cost` header the provider actually returns.
# ---------------------------------------------------------------------------

# Two short turns. Long enough to be a real dialogue — the endpoint rejects a
# single input on some models — and short enough to cost a fraction of a cent.
DIALOGUE_TURNS = (
    ("You knew it would end.", "A"),
    ("I hoped not.", "B"),
)


@dataclass
class DialogueProbeReport:
    model_id: str
    submitted_chars: int
    billed_chars: int | None
    turns: int
    voices: int
    multiplier: float | None
    findings: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    error: str | None = None


async def run_dialogue_probe(
    provider: Any, model_id: str, voice_ids: list[str], output_format: str
) -> DialogueProbeReport:
    """Measure what a dialogue request is billed, against what was submitted.

    The comparison that matters is `character-cost` divided by the characters in
    `inputs[].text`. Plain speech bills below the submitted length (12 submitted,
    3 billed, per docs/probe-results.md), so a ratio is only meaningful next to
    the speech probe's — which is why both reports record submitted *and* billed
    rather than a single number.
    """
    from narrate.provider.base import DialogueRequest, DialogueTurn

    turns = tuple(
        DialogueTurn(text=text, voice_id=voice_ids[index % len(voice_ids)], speaker=speaker)
        for index, (text, speaker) in enumerate(DIALOGUE_TURNS)
    )
    request = DialogueRequest(turns=turns, model_id=model_id, output_format=output_format)

    report = DialogueProbeReport(
        model_id=model_id,
        submitted_chars=request.char_count,
        billed_chars=None,
        turns=len(turns),
        voices=len(set(t.voice_id for t in turns)),
        multiplier=None,
    )

    try:
        result = await provider.generate_dialogue(request)
    except ProviderError as exc:
        report.error = exc.message
        report.findings.append(
            f"The endpoint refused the request: {exc.message}. "
            "The declared multiplier stays unverified."
        )
        return report

    report.headers = {k.lower(): v for k, v in result.headers.items()}
    report.billed_chars = result.billed_chars

    if result.billed_chars is None:
        report.findings.append(
            "No `character-cost` header came back for dialogue, so there is nothing "
            "to calibrate against. The declared multiplier stays unverified."
        )
        return report

    report.multiplier = round(result.billed_chars / request.char_count, 4)
    report.findings.append(
        f"Submitted {request.char_count} characters across {len(turns)} turns in "
        f"{report.voices} voices; billed {result.billed_chars}."
    )
    report.findings.append(
        f"Billed/submitted ratio is {report.multiplier:g}. Compare it with the same "
        "ratio from `narrate probe --live` on plain speech: dialogue is priced "
        "differently only if the two differ."
    )
    if "maximum-concurrent-requests" in report.headers:
        report.findings.append(
            f"Concurrency ceiling for this endpoint: "
            f"{report.headers['maximum-concurrent-requests']}."
        )
    return report


def write_dialogue_report(report: DialogueProbeReport, path: Path | None = None) -> Path:
    target = path or PROJECT_ROOT / "docs" / "probe-dialogue.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Dialogue probe",
        "",
        f"Run against `{report.model_id}` with {report.turns} turns in {report.voices} voice(s).",
        "",
        "## Findings",
        "",
    ]
    lines += [f"- {finding}" for finding in report.findings]
    billed = report.billed_chars if report.billed_chars is not None else "—"
    ratio = report.multiplier if report.multiplier is not None else "—"
    lines += [
        "",
        "## Numbers",
        "",
        "| | |",
        "|---|---|",
        f"| Submitted characters | {report.submitted_chars} |",
        f"| Billed (`character-cost`) | {billed} |",
        f"| Billed / submitted | {ratio} |",
        "",
        "`dialogue_cost_multiplier` in `config/models.toml` is the *rate* multiplier",
        "against `usd_per_1k`, not this ratio. Set it only if the ratio here differs",
        "from the plain-speech ratio in [probe-results.md](probe-results.md) — a",
        "difference in how many characters are billed is not the same thing as a",
        "difference in the price of one.",
        "",
    ]
    if report.headers:
        lines += ["## Response headers observed", "", "```"]
        lines += [f"{k}: {v}" for k, v in sorted(report.headers.items())]
        lines += ["```", ""]

    target.write_text("\n".join(lines), encoding="utf-8")
    return target
