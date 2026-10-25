"""Where each episode is: script, audio, check, export, publish.

The pipeline bars on the Projects page and the Workbench's stepper ask the same
questions of an episode; answered once, here, they cannot disagree. Everything
comes from records that already exist — the cut, what the check found, the
export's cut fingerprint, the publish pack — so nothing new is stored, and an
episode made before this existed has a status too.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from narrate import ledger
from narrate.db.models import (
    Chunk,
    EffectSlot,
    Export,
    LedgerEntry,
    Script,
    Take,
    ThumbnailBrief,
    TitleCandidate,
    as_utc,
)
from narrate.export import export_is_current
from narrate.publish import chapters_of
from narrate.timeline import build_timeline

CHECKED = ("clear", "review", "suspect")


@dataclass(frozen=True)
class EpisodeStatus:
    script_id: int
    chunks: int
    with_audio: int
    # Chunks with no audio whose every attempt failed: the ones to look at.
    failed: list[int]
    # In-cut takes the check flagged and nobody has accepted by ear.
    flagged: int
    review: int
    unchecked: int
    exported_at: datetime | None
    export_current: bool
    title_chosen: bool
    chapters_usable: bool
    runtime_s: float
    spent_micros: int
    outstanding_chunks: int
    outstanding_micros: int
    last_activity: datetime

    @property
    def needs_you(self) -> bool:
        """Something only a person can settle: a failure, a flag, a stale export."""
        exported_stale = self.exported_at is not None and not self.export_current
        return bool(self.failed or self.flagged or self.review or exported_stale)

    @property
    def done(self) -> bool:
        return self.exported_at is not None and self.export_current and not self.needs_you


def episode_status(
    session: Session, script_id: int, registry: object | None = None, gap_seconds: float = 0.0
) -> EpisodeStatus:
    script = session.get(Script, script_id)
    if script is None:
        raise ValueError(f"No script {script_id}.")
    timeline = build_timeline(session, script_id, gap_seconds=gap_seconds)
    narration = timeline.narration
    heard = [e for e in narration if e.generated]

    with_audio = {e.chunk_ordinal for e in heard}
    failed_rows = session.execute(
        select(Chunk.ordinal, Take.status)
        .join(Take, Take.chunk_id == Chunk.id)
        .where(Chunk.script_id == script_id)
    ).all()
    attempted: dict[int, set[str]] = {}
    for ordinal, status in failed_rows:
        attempted.setdefault(ordinal, set()).add(status)
    failed = sorted(
        ordinal
        for ordinal, statuses in attempted.items()
        if ordinal not in with_audio and "succeeded" not in statuses
    )

    exported_at = session.scalar(
        select(func.max(Export.created_at)).where(Export.script_id == script_id)
    )
    todo = ledger.outstanding(session, script_id, registry)

    return EpisodeStatus(
        script_id=script_id,
        chunks=len(narration),
        with_audio=len(heard),
        failed=failed,
        flagged=sum(1 for e in heard if e.check == "suspect" and not e.accepted),
        review=sum(1 for e in heard if e.check == "review" and not e.accepted),
        unchecked=sum(1 for e in heard if e.check not in CHECKED),
        exported_at=as_utc(exported_at) if exported_at else None,
        export_current=exported_at is not None and export_is_current(session, script_id),
        title_chosen=(
            session.scalar(
                select(func.count())
                .select_from(TitleCandidate)
                .where(TitleCandidate.script_id == script_id, TitleCandidate.accepted.is_(True))
            )
            or 0
        )
        > 0,
        chapters_usable=chapters_of(timeline).usable,
        runtime_s=timeline.runtime_s,
        spent_micros=ledger.script_rollup(session, script_id).cost_micros,
        outstanding_chunks=todo.chunks,
        outstanding_micros=todo.cost_micros,
        last_activity=last_activity(session, script),
    )


def last_activity(session: Session, script: Script) -> datetime:
    """The latest thing that happened to an episode, from the records it left.

    Generating, checking, accepting, exporting, placing an effect, drafting a
    title or a brief, and every charge — whichever is newest.
    """
    chunk_ids = select(Chunk.id).where(Chunk.script_id == script.id)
    moments = [
        script.created_at,
        session.scalar(select(func.max(Take.created_at)).where(Take.chunk_id.in_(chunk_ids))),
        session.scalar(select(func.max(Take.verified_at)).where(Take.chunk_id.in_(chunk_ids))),
        session.scalar(
            select(func.max(Take.verify_accepted_at)).where(Take.chunk_id.in_(chunk_ids))
        ),
        session.scalar(select(func.max(Export.created_at)).where(Export.script_id == script.id)),
        session.scalar(
            select(func.max(EffectSlot.created_at)).where(EffectSlot.script_id == script.id)
        ),
        session.scalar(
            select(func.max(TitleCandidate.created_at)).where(TitleCandidate.script_id == script.id)
        ),
        session.scalar(
            select(func.max(ThumbnailBrief.created_at)).where(ThumbnailBrief.script_id == script.id)
        ),
        session.scalar(select(func.max(LedgerEntry.ts)).where(LedgerEntry.script_id == script.id)),
    ]
    return max(as_utc(m) for m in moments if m is not None)
