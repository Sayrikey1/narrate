"""The editing plan — `plan.md`, written beside every export.

This is the deliverable the rest of the phase exists to produce. It answers,
for someone assembling the video: what plays, when, from which file, and where
something still needs to go.

Two things it is deliberate about:

* **Planned slots are as prominent as generated ones.** A slot with no audio
  yet is a row in the same table marked as outstanding, because "an effect
  belongs here and does not exist" is exactly the guidance an editor needs.
* **Reuse is stated explicitly.** One cue placed at four points is one file,
  and the plan says so — otherwise the editor sees four filenames and assumes
  four different sounds.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from narrate import ledger
from narrate.db.models import Effect
from narrate.effects import placements
from narrate.money import fmt_usd
from narrate.script_parse import format_time
from narrate.timeline import EFFECT, NARRATION, PLANNED, Timeline, TimelineEntry

TRACK_LABEL = {
    NARRATION: "narration",
    EFFECT: "**effect**",
    PLANNED: "⬜ *planned*",
}


@dataclass(frozen=True)
class PlanContext:
    """Everything the plan reports that is not on the timeline itself."""

    cost_micros: int = 0
    waste_pct: float = 0.0
    warnings: tuple[str, ...] = ()


def gather_context(session: Session, script_id: int) -> PlanContext:
    rollup = ledger.script_rollup(session, script_id)
    return PlanContext(
        cost_micros=rollup.cost_micros,
        waste_pct=rollup.waste_pct,
    )


def render_plan(
    timeline: Timeline,
    session: Session,
    names: dict[int, str] | None = None,
    context: PlanContext | None = None,
) -> str:
    """Render `plan.md` for a timeline.

    `names` maps a timeline index to its exported filename. When absent — a
    plan asked for before exporting — the file column shows a dash rather than
    a name that does not exist yet.
    """
    ctx = context or PlanContext()
    filenames = names or {}
    lines: list[str] = []

    lines += [
        f"# {timeline.script_title} — editing plan",
        "",
        _summary_line(timeline, ctx),
        "",
        "Effects are **overlays**: they carry a timeline position but are not mixed",
        "into the narration master. Drop them on their own track at the times below.",
        "",
        "## Running order",
        "",
        "| # | Start | End | Length | Track | File | Content |",
        "|---:|---|---|---:|---|---|---|",
    ]

    for entry in timeline.entries:
        lines.append(_row(entry, filenames))

    outstanding = timeline.planned
    if outstanding:
        lines += [
            "",
            f"## Outstanding — {len(outstanding)} effect(s) not generated",
            "",
            "Each of these has a place on the timeline and no audio. Generate them",
            "with `narrate effects generate`, or source them elsewhere.",
            "",
        ]
        for entry in outstanding:
            wanted = f"{entry.duration_s:g}s" if entry.duration_s else "length unspecified"
            lines.append(f"- **{entry.start_stamp}** — {entry.label} ({wanted})")

    used = _effects_used(timeline, session)
    if used:
        lines += ["", "## Effects used", ""]
        for effect, at in used:
            times = ", ".join(at)
            once = "generated once, placed" if len(at) > 1 else "placed"
            lines.append(f"- `{effect.slug}` — {once} at {times}")

    drifted = [e for e in timeline.drifts if e.drift_s]
    if drifted:
        lines += [
            "",
            "## Anchor drift",
            "",
            "Where the script asked for a section to start, against where it landed.",
            "Speech duration cannot be dialled to a mark, so these are for judgement,",
            "not errors.",
            "",
        ]
        for entry in drifted:
            drift = entry.drift_s or 0.0
            direction = "late" if drift > 0 else "early"
            lines.append(
                f"- Chunk {entry.chunk_ordinal}: targeted {format_time(entry.target_s or 0)}, "
                f"landed {entry.start_stamp} ({drift:+.1f}s {direction})"
            )

    flagged = timeline.flagged
    if flagged:
        lines += [
            "",
            "## Takes to check",
            "",
            "`narrate verify` compared these takes with the script and found words",
            "missing, added or changed. Listen at the times below; regenerate with",
            "`narrate regenerate` if it is what it looks like.",
            "",
        ]
        for entry in flagged:
            for offset, severity, summary in entry.issues:
                mark = "⚠️" if severity == "fail" else "👂"
                lines.append(
                    f"- {mark} **{format_time(entry.start_s + offset)}** — chunk "
                    f"{entry.chunk_ordinal}: {summary}"
                )

    missing = [e for e in timeline.narration if not e.generated]
    if missing:
        lines += [
            "",
            "## Not yet generated",
            "",
            *[f"- Chunk {e.chunk_ordinal}: {e.label}" for e in missing],
            "",
            "Timings after the first of these are provisional until it is generated.",
        ]

    for warning in ctx.warnings:
        lines += ["", f"> {warning}"]

    lines.append("")
    return "\n".join(lines)


def _summary_line(timeline: Timeline, ctx: PlanContext) -> str:
    parts = [
        f"Runtime **{format_time(timeline.runtime_s)}**",
        f"{len(timeline.narration)} narration",
    ]
    if timeline.effects:
        parts.append(f"{len(timeline.effects)} effects")
    if timeline.planned:
        parts.append(f"{len(timeline.planned)} planned")
    suspect = sum(1 for e in timeline.narration if e.check == "suspect")
    if suspect:
        parts.append(f"**{suspect} take(s) flagged**")
    if ctx.cost_micros:
        parts.append(f"spent {fmt_usd(ctx.cost_micros, 2)}")
        # Divided by the runtime this document reports, not by the ledger's
        # preferred figure. The plan is usually written during the export, so
        # `ledger.cost_per_minute_micros` has no export row yet and falls back
        # to the gapless sum of the takes — which produced a rate that did not
        # divide into the runtime printed on the line above it. A delivered
        # document that disagrees with itself is worse than one that rounds.
        if timeline.runtime_s > 0:
            per_minute = int(ctx.cost_micros / (timeline.runtime_s / 60))
            parts.append(f"{fmt_usd(per_minute, 4)}/min")
    return " · ".join(parts)


def _row(entry: TimelineEntry, names: dict[int, str]) -> str:
    name = names.get(entry.index)
    file_cell = f"`{name}`" if name else "—"
    content = entry.label if entry.kind == NARRATION else f"*{entry.label}*"
    return (
        f"| {entry.index} | {entry.start_stamp} | {entry.end_stamp} "
        f"| {entry.duration_s:.2f}s | {TRACK_LABEL.get(entry.kind, entry.kind)} "
        f"| {file_cell} | {content} |"
    )


def _effects_used(timeline: Timeline, session: Session) -> list[tuple[Effect, list[str]]]:
    """Each generated effect with every timeline position it appears at."""
    by_effect: dict[int, list[str]] = {}
    for entry in timeline.effects:
        if entry.effect_id is not None:
            by_effect.setdefault(entry.effect_id, []).append(entry.start_stamp)

    out: list[tuple[Effect, list[str]]] = []
    for effect_id, times in by_effect.items():
        effect = session.get(Effect, effect_id)
        if effect is not None:
            out.append((effect, times))
    return sorted(out, key=lambda pair: pair[0].slug)


def render_timeline_json(
    timeline: Timeline, names: dict[int, str] | None = None, context: PlanContext | None = None
) -> str:
    """Machine-readable twin of the plan. Feeds the UI and any downstream tool."""
    ctx = context or PlanContext()
    filenames = names or {}
    payload = {
        "script_id": timeline.script_id,
        "title": timeline.script_title,
        "project": timeline.project_name,
        "runtime_s": round(timeline.runtime_s, 3),
        "gap_seconds": timeline.gap_seconds,
        "cost_micros": ctx.cost_micros,
        "complete": timeline.is_complete,
        "entries": [
            {
                "index": e.index,
                "kind": e.kind,
                "start_s": round(e.start_s, 3),
                "end_s": round(e.end_s, 3),
                "start": e.start_stamp,
                "end": e.end_stamp,
                "label": e.label,
                "file": filenames.get(e.index),
                "chunk_ordinal": e.chunk_ordinal,
                "take_id": e.take_id,
                "slot_id": e.slot_id,
                "effect_id": e.effect_id,
                "generated": e.generated,
                "target_s": e.target_s,
                "drift_s": e.drift_s,
            }
            for e in timeline.entries
        ],
    }
    return json.dumps(payload, indent=2)


def placement_count(session: Session, effect_id: int) -> int:
    return len(placements(session, effect_id))
