"""Checking takes against their script, and recording what was found.

The order of evidence matters, because each source is wrong in its own way:

1. **The transcript** is the primary signal. It finds a missing word, an extra
   word, a swapped word — and it also invents a few of its own, mostly
   low-confidence words tacked onto the end of a long sentence.
2. **A second listen** settles the inventions. An extra word whose confidence
   sits in the band phantoms also live in is re-transcribed from a short window
   around it. A word the narrator really said is there again; a phantom is not.
   That one step removed every phantom from the episode that motivated this,
   and kept both unscripted words.
3. **The waveform** is independent of both. A short loud burst walled in by
   silence is how a word gets garbled on eleven_v3 — but a burst alone is only
   worth a listen. It becomes a fail only when the transcript agrees something
   is wrong at that moment.

Transcription is slow (seconds per take) and never runs inside a database
session: the rows to check are read and detached first, the audio is checked
with nothing held open, and each result is written back in its own short
transaction. A take is never marked checked unless its findings were stored.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import Engine

from narrate import audio
from narrate.db.models import Chunk, Cut, Take
from narrate.db.session import session_scope
from narrate.registry import Registry, UnknownModel
from narrate.script_parse import spoken_text
from narrate.verify.compare import (
    FAIL,
    INFO,
    INSERT_P,
    REVIEW,
    RULES_VERSION,
    Finding,
    Word,
    canon,
    classify,
    heard_tokens,
    join_pieces,
    verdict,
)
from narrate.verify.energy import Envelope
from narrate.verify.transcribe import Transcriber

# How much audio either side of an uncertain extra word to listen to again.
CONFIRM_WINDOW_S = 4.0
# How far a re-heard word may sit from where it was first heard.
CONFIRM_TOLERANCE_S = 1.0
# A burst this close to a transcript finding is the same event.
BURST_MATCH_S = 0.5

STATUSES = ("unverified", "clear", "review", "suspect", "error")


@dataclass(frozen=True)
class Target:
    """One take to check, detached from the session."""

    take_id: int
    chunk_ordinal: int
    take_ordinal: int
    path: Path
    expected: str
    in_cut: bool


@dataclass
class TakeCheck:
    """What checking one take found."""

    take_id: int
    chunk_ordinal: int
    take_ordinal: int
    status: str
    findings: list[Finding] = field(default_factory=list)
    verifier: str = ""
    seconds: float = 0.0
    error: str | None = None
    in_cut: bool = False

    @property
    def problems(self) -> list[Finding]:
        return [f for f in self.findings if f.severity in (FAIL, REVIEW)]


def expected_text(take: Take, chunk: Chunk, registry: Registry) -> str:
    """What this take should sound like — not what was sent.

    The take's own `submitted_text`, not the chunk's current text, because the
    chunk may have been edited since. Two exceptions to "what was sent":

    * a dialogue take records `Speaker: line` for readability, but the labels
      were never sent or spoken, so the turns are used instead;
    * audio tags are directions, not words, on a model that honours them.
    """
    source = take.submitted_text
    if take.voices_json and chunk.turns_json:
        try:
            turns = json.loads(chunk.turns_json)
            source = " ".join(str(t.get("text", "")) for t in turns)
        except (ValueError, TypeError, AttributeError):
            pass
    try:
        tags = registry.get(take.model_id).audio_tags
    except UnknownModel:
        # A take from a model since dropped from the rate card. Leaving the tags
        # in would report each one as a missing word; stripping them is the
        # smaller error.
        tags = True
    return spoken_text(source, audio_tags=tags)


def targets(
    engine: Engine,
    script_id: int,
    registry: Registry,
    *,
    chunks: list[int] | None = None,
    scope: str = "cut",
    recheck: bool = False,
    verifier: str | None = None,
    take_ids: list[int] | None = None,
) -> list[Target]:
    """Takes worth checking, in running order.

    `scope` is "cut" (what the export will use) or "all". Takes already checked
    by the same verifier are skipped unless `recheck` — re-running the command
    after a regeneration then checks only the new take.
    """
    with session_scope(engine) as session:
        cut = {
            c.chunk_id: c.take_id
            for c in session.scalars(select(Cut).where(Cut.script_id == script_id)).all()
        }
        rows = session.execute(
            select(Take, Chunk)
            .join(Chunk, Chunk.id == Take.chunk_id)
            .where(Chunk.script_id == script_id, Take.status == "succeeded")
            .order_by(Chunk.ordinal, Take.ordinal)
        ).all()
        out: list[Target] = []
        for take, chunk in rows:
            if take_ids is not None and take.id not in take_ids:
                continue
            if chunks and chunk.ordinal not in chunks:
                continue
            in_cut = cut.get(chunk.id) == take.id
            if scope == "cut" and take_ids is None and not in_cut:
                continue
            if not take.asset_path:
                continue
            if not recheck and verifier and take.verifier == verifier and take.verified_at:
                continue
            # The waveform alone cannot see an extra or a swapped word, so it must
            # never replace what a transcript found — even with --recheck. A
            # suspect take would otherwise be quietly "re-checked" to unverified.
            if verifier == stamp(None) and heard_by_transcript(take.verifier):
                continue
            out.append(
                Target(
                    take_id=take.id,
                    chunk_ordinal=chunk.ordinal,
                    take_ordinal=take.ordinal,
                    path=Path(take.asset_path),
                    expected=expected_text(take, chunk, registry),
                    in_cut=in_cut,
                )
            )
        return out


def check(
    target: Target,
    transcriber: Transcriber | None,
    *,
    use_energy: bool = True,
) -> TakeCheck:
    """Check one take. Pure with respect to the database."""
    started = time.monotonic()
    result = TakeCheck(
        take_id=target.take_id,
        chunk_ordinal=target.chunk_ordinal,
        take_ordinal=target.take_ordinal,
        status="error",
        in_cut=target.in_cut,
    )
    if not target.path.is_file():
        result.error = f"audio file is missing: {target.path}"
        return result

    envelope: Envelope | None = None
    if use_energy and audio.ffmpeg_path():
        try:
            envelope = Envelope.of(target.path)
        except audio.FFmpegFailed as exc:
            result.error = f"could not decode the audio: {exc}"
            return result

    findings: list[Finding] = []
    if transcriber is not None:
        words = transcriber.transcribe(target.path)
        findings = classify(target.expected, words, voiced=envelope.voiced if envelope else None)
        findings = [_confirm(f, target.path, transcriber) for f in findings]

    if envelope is not None and envelope.has_digital_silence():
        findings = _fold_bursts(findings, envelope, transcript=transcriber is not None)

    ran = transcriber is not None or envelope is not None
    result.findings = findings
    result.verifier = stamp(transcriber) if ran else ""
    if not ran:
        result.status = "unverified"
    elif transcriber is None:
        # The waveform alone cannot clear a take: it cannot hear an extra word
        # or a swapped one. It can only raise something worth a listen.
        result.status = "review" if findings else "unverified"
    else:
        result.status = verdict(findings)
    result.seconds = round(time.monotonic() - started, 2)
    return result


# The identity recorded for a check that had no transcript, only the waveform.
ENERGY_ONLY = "energy-only"


def stamp(transcriber: Transcriber | None) -> str:
    """The `Take.verifier` value for a check — stable, so repeats can be skipped."""
    identity = transcriber.identity if transcriber is not None else ENERGY_ONLY
    return f"{identity}|rules:{RULES_VERSION}"


def heard_by_transcript(verifier: str | None) -> bool:
    """Whether a stored check listened to the words, not only the waveform.

    Compared by identity rather than against today's stamp, so a waveform check
    made under an older rules version is still recognised as one.
    """
    return bool(verifier) and not str(verifier).startswith(f"{ENERGY_ONLY}|")


def _confirm(finding: Finding, path: Path, transcriber: Transcriber) -> Finding:
    """Listen again to an uncertain extra word before believing it."""
    if not finding.confirm or finding.kind != "extra":
        return finding
    start = finding.start_s
    end = finding.end_s if finding.end_s is not None else start
    window = (max(0.0, start - CONFIRM_WINDOW_S), end + CONFIRM_WINDOW_S)
    again = transcriber.transcribe(path, window)
    # Tokenised the way the first pass was — in context — so a word that only
    # normalises correctly beside its neighbours (a number, a contraction) can
    # still be recognised as the same word the second time.
    target = canon([finding.heard])
    tokens = heard_tokens(join_pieces(again))
    # A run of up to three, not only one: the two listens can split a word
    # differently — "Cashflow" the first time, "cash flow" the second.
    match = [
        min(run, key=lambda h: h.probability)
        for i in range(len(tokens))
        for run in (tokens[i : i + n] for n in (1, 2, 3) if i + n <= len(tokens))
        if canon([h.token for h in run]) == target
        and abs(run[0].start - start) <= CONFIRM_TOLERANCE_S
    ]
    first = finding.probability or 0.0
    if not match:
        return replace(
            finding,
            severity=INFO,
            confirm=False,
            note="not heard on a second listen — a transcriber phantom",
        )
    best = max(first, *(w.probability for w in match))
    severity = FAIL if best >= INSERT_P else REVIEW
    return replace(
        finding,
        severity=severity,
        probability=round(best, 2),
        confirm=False,
        note="heard again on a second listen",
    )


def _fold_bursts(findings: list[Finding], envelope: Envelope, *, transcript: bool) -> list[Finding]:
    """Merge waveform bursts into the transcript's findings.

    A burst that coincides with a missing or garbled word confirms it and says
    what happened to it — the word was not dropped, it was reduced to a noise.
    A burst nothing else noticed is left as something to listen to.
    """
    out = list(findings)
    for burst in envelope.bursts():
        near = [
            index
            for index, f in enumerate(out)
            if f.kind in ("missing", "garbled")
            and f.severity != INFO
            and (f.start_s - BURST_MATCH_S) <= burst.end_s
            and burst.start_s <= ((f.end_s if f.end_s is not None else f.start_s) + BURST_MATCH_S)
        ]
        detail = (
            f"a {burst.duration * 1000:.0f}ms burst at {burst.start_s:.2f}s, "
            f"walled in by silence — the word was reduced to a noise"
        )
        if near:
            for index in near:
                f = out[index]
                out[index] = replace(f, severity=FAIL, note=detail)
        else:
            out.append(
                Finding(
                    severity=REVIEW,
                    kind="burst",
                    start_s=burst.start_s,
                    end_s=burst.end_s,
                    note=detail if transcript else f"{detail} (no transcript to confirm it)",
                )
            )
    return sorted(out, key=lambda f: f.start_s)


def record(engine: Engine, result: TakeCheck) -> None:
    """Store one take's result. Only called once the check finished."""
    with session_scope(engine) as session:
        take = session.get(Take, result.take_id)
        if take is None:
            return
        take.verify_status = result.status
        take.verify_findings_json = json.dumps(
            [f.as_dict() for f in result.findings if f.severity != INFO]
            + ([{"info_count": sum(1 for f in result.findings if f.severity == INFO)}]),
            separators=(",", ":"),
        )
        take.verifier = result.verifier or None
        take.verified_at = datetime.now(UTC).replace(tzinfo=None)


def findings_of(take: Take) -> list[Finding]:
    """The stored problems for a take, without the info tally."""
    if not take.verify_findings_json:
        return []
    try:
        raw = json.loads(take.verify_findings_json)
    except ValueError:
        return []
    return [Finding.from_dict(item) for item in raw if isinstance(item, dict) and "kind" in item]


def stored(
    engine: Engine,
    script_id: int,
    *,
    chunks: list[int] | None = None,
    scope: str = "cut",
) -> list[TakeCheck]:
    """What is already known about each take in scope, without checking anything.

    So a repeated `narrate verify` reports the state of the episode, not just
    what it happened to re-check this time — a take found suspect yesterday is
    still suspect today until something replaces it.
    """
    with session_scope(engine) as session:
        cut = {
            c.chunk_id: c.take_id
            for c in session.scalars(select(Cut).where(Cut.script_id == script_id)).all()
        }
        rows = session.execute(
            select(Take, Chunk)
            .join(Chunk, Chunk.id == Take.chunk_id)
            .where(Chunk.script_id == script_id, Take.status == "succeeded")
            .order_by(Chunk.ordinal, Take.ordinal)
        ).all()
        out: list[TakeCheck] = []
        for take, chunk in rows:
            if chunks and chunk.ordinal not in chunks:
                continue
            in_cut = cut.get(chunk.id) == take.id
            if scope == "cut" and not in_cut:
                continue
            out.append(
                TakeCheck(
                    take_id=take.id,
                    chunk_ordinal=chunk.ordinal,
                    take_ordinal=take.ordinal,
                    status=take.verify_status,
                    findings=findings_of(take),
                    verifier=take.verifier or "",
                    in_cut=in_cut,
                )
            )
        return out


def verify_script(
    engine: Engine,
    script_id: int,
    transcriber: Transcriber | None,
    registry: Registry,
    *,
    chunks: list[int] | None = None,
    scope: str = "cut",
    recheck: bool = False,
    take_ids: list[int] | None = None,
    on_result: Callable[[TakeCheck], None] | None = None,
) -> list[TakeCheck]:
    """Check a script's takes, recording each as soon as it is done.

    Stored per take rather than at the end, so an interrupted run keeps what it
    finished and the next run picks up where it stopped.
    """
    todo = targets(
        engine,
        script_id,
        registry,
        chunks=chunks,
        scope=scope,
        recheck=recheck,
        verifier=stamp(transcriber),
        take_ids=take_ids,
    )
    results: list[TakeCheck] = []
    for target in todo:
        result = check(target, transcriber)
        if result.verifier or result.error:
            record(engine, result)
        results.append(result)
        if on_result:
            on_result(result)
    return results


def words_from(raw: list[dict[str, object]]) -> list[Word]:
    """Rebuild transcriber words from plain dicts — for fixtures and caches."""
    return [
        Word(
            text=str(w["text"]),
            start=float(str(w["start"])),
            end=float(str(w["end"])),
            probability=float(str(w["probability"])),
        )
        for w in raw
    ]
