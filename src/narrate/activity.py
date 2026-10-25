"""An episode's history, read back from the records its work left behind.

Nothing is written to make this: a run leaves its takes, a check leaves
`verified_at` on each take it heard, an export leaves a row per file, a draft
leaves its candidates. Reading those back gives every episode a history,
including the ones made before this existed, and it can never disagree with the
records it describes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from narrate.db.models import (
    Chunk,
    Effect,
    EffectSlot,
    Export,
    Run,
    Script,
    Take,
    ThumbnailBrief,
    TitleCandidate,
    as_utc,
)
from narrate.failures import explain
from narrate.money import fmt_usd

# Takes checked, or sounds made, this close together were one sitting.
SITTING = timedelta(minutes=10)


@dataclass(frozen=True)
class Event:
    at: datetime
    kind: str
    icon: str
    text: str


def history(session: Session, script_id: int, limit: int = 40) -> list[Event]:
    """What happened to an episode, newest first."""
    script = session.get(Script, script_id)
    if script is None:
        raise ValueError(f"No script {script_id}.")
    chunks = {
        c.id: c.ordinal
        for c in session.scalars(select(Chunk).where(Chunk.script_id == script_id)).all()
    }
    takes = list(session.scalars(select(Take).where(Take.chunk_id.in_(chunks))).all())

    events = [
        Event(
            as_utc(script.created_at),
            "created",
            "📝",
            f"Script added — {len(chunks)} chunk{_s(len(chunks))}",
        )
    ]
    events += _runs(session, script_id, takes)
    events += _checks(takes)
    events += [
        Event(
            as_utc(t.verify_accepted_at),
            "accepted",
            "👂",
            f"Chunk {chunks[t.chunk_id]} take {t.ordinal} marked fine by ear",
        )
        for t in takes
        if t.verify_accepted_at is not None
    ]
    events += _exports(session, script_id)
    events += _effects(session, script_id, since=as_utc(script.created_at))
    events += _drafts(session, script_id)
    events.sort(key=lambda e: e.at, reverse=True)
    return events[:limit]


def _runs(session: Session, script_id: int, takes: list[Take]) -> list[Event]:
    """Generation runs, one event per sitting: regenerating a dozen chunks one at
    a time is one piece of work, not twelve."""
    finished = [
        run
        for run in session.scalars(
            select(Run)
            .where(Run.script_id == script_id, Run.dry_run.is_(False))
            .order_by(Run.started_at)
        ).all()
        # A run still going is the top bar's business, not history yet.
        if run.status != "running" and any(t.run_id == run.id for t in takes)
    ]
    out: list[Event] = []
    group: list[Run] = []
    for run in finished:
        if group and as_utc(run.started_at) - _ended(group[-1]) > SITTING:
            out.append(_run_event(group, takes))
            group = []
        group.append(run)
    if group:
        out.append(_run_event(group, takes))
    return out


def _ended(run: Run) -> datetime:
    return as_utc(run.finished_at or run.started_at)


def _run_event(group: list[Run], takes: list[Take]) -> Event:
    ids = {run.id for run in group}
    made = [t for t in takes if t.run_id in ids]
    ok = [t for t in made if t.status == "succeeded"]
    failed = [t for t in made if t.status != "succeeded"]
    spent = fmt_usd(sum(t.cost_micros for t in made), 2)
    reason = explain(failed[-1].error_json, failed[-1].status) if failed else None
    why = reason.headline if reason else ""
    runs = f" over {len(group)} runs" if len(group) > 1 else ""
    if ok and not failed:
        icon, text = "🎙️", f"Generated {len(ok)} take{_s(len(ok))}{runs} · {spent}"
    elif ok:
        icon = "⚠️"
        text = (
            f"Generated {len(ok)} take{_s(len(ok))}{runs}; {len(failed)} didn't — {why} · {spent}"
        )
    else:
        icon, text = "⛔", f"{len(failed)} take{_s(len(failed))} didn't generate — {why}"
    if any(run.status == "stopped_by_budget" for run in group):
        text += " — stopped at the spending cap"
    return Event(_ended(group[-1]), "generated", icon, text)


def _checks(takes: list[Take]) -> list[Event]:
    """Takes heard within ten minutes of each other were one check."""
    heard = sorted(
        (as_utc(t.verified_at), t.verify_status) for t in takes if t.verified_at is not None
    )
    out: list[Event] = []
    group: list[tuple[datetime, str]] = []
    for moment, verdict in heard:
        if group and moment - group[-1][0] > SITTING:
            out.append(_check_event(group))
            group = []
        group.append((moment, verdict))
    if group:
        out.append(_check_event(group))
    return out


def _check_event(group: list[tuple[datetime, str]]) -> Event:
    flagged = sum(1 for _, verdict in group if verdict == "suspect")
    review = sum(1 for _, verdict in group if verdict == "review")
    found = ", ".join(
        part
        for part in (flagged and f"{flagged} flagged", review and f"{review} worth a listen")
        if part
    )
    return Event(
        group[-1][0],
        "checked",
        "🔍",
        f"Checked {len(group)} take{_s(len(group))} — {found or 'all clear'}",
    )


def _exports(session: Session, script_id: int) -> list[Event]:
    """One export writes a row per file, all in the same moment."""
    rows = session.scalars(
        select(Export).where(Export.script_id == script_id).order_by(Export.created_at)
    ).all()
    out: list[Event] = []
    batch: list[Export] = []
    for row in rows:
        if batch and as_utc(row.created_at) - as_utc(batch[0].created_at) > timedelta(seconds=5):
            out.append(_export_event(batch))
            batch = []
        batch.append(row)
    if batch:
        out.append(_export_event(batch))
    return out


def _export_event(batch: list[Export]) -> Event:
    plain = sorted({r.fmt for r in batch if not r.fmt.endswith("_fx")})
    mixed = any(r.fmt.endswith("_fx") for r in batch)
    formats = ", ".join(f.upper() for f in plain) or "the masters"
    return Event(
        as_utc(batch[-1].created_at),
        "exported",
        "📦",
        f"Exported {formats}{' with an effects mix' if mixed else ''}",
    )


def _effects(session: Session, script_id: int, since: datetime) -> list[Event]:
    """Sounds made for this episode, by when they were made. One made earlier,
    for another episode, was reused here rather than made."""
    made = sorted(
        {
            effect.id: effect
            for effect in session.scalars(
                select(Effect)
                .join(EffectSlot, EffectSlot.effect_id == Effect.id)
                .where(EffectSlot.script_id == script_id, Effect.status == "succeeded")
            ).all()
        }.values(),
        key=lambda e: e.created_at,
    )
    made = [e for e in made if as_utc(e.created_at) >= since]
    out: list[Event] = []
    group: list[Effect] = []
    for effect in made:
        if group and as_utc(effect.created_at) - as_utc(group[-1].created_at) > SITTING:
            out.append(_effect_event(group))
            group = []
        group.append(effect)
    if group:
        out.append(_effect_event(group))
    return out


def _effect_event(group: list[Effect]) -> Event:
    spent = fmt_usd(sum(e.cost_micros for e in group), 2)
    return Event(
        as_utc(group[-1].created_at),
        "effects",
        "✨",
        f"Made {len(group)} sound effect{_s(len(group))} · {spent}",
    )


def _drafts(session: Session, script_id: int) -> list[Event]:
    out = []
    titles = session.scalars(
        select(TitleCandidate).where(TitleCandidate.script_id == script_id)
    ).all()
    for title in titles:
        if title.source == "manual":
            out.append(
                Event(as_utc(title.created_at), "titles", "✍️", f"Wrote a title: “{title.text}”")
            )
    proposed = [t for t in titles if t.source != "manual"]
    if proposed:
        latest = max(as_utc(t.created_at) for t in proposed)
        out.append(
            Event(latest, "titles", "🪄", f"{len(proposed)} title{_s(len(proposed))} drafted")
        )
    briefs = session.scalars(
        select(ThumbnailBrief).where(ThumbnailBrief.script_id == script_id)
    ).all()
    if briefs:
        latest = max(as_utc(b.created_at) for b in briefs)
        out.append(
            Event(latest, "briefs", "🖼️", f"{len(briefs)} thumbnail brief{_s(len(briefs))} drafted")
        )
    return out


def _s(n: int) -> str:
    return "" if n == 1 else "s"
