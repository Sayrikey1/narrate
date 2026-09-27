"""Writing a script outline first, then filling it in a beat at a time.

The alternative was one request for a whole episode. A 25-minute script is about
20,000 characters, which is slow, exceeds the free tier's per-minute budget, and
— the real objection — cannot be steered. Getting the third section wrong means
paying for the other nine again.

So: **an outline is cheap and a beat is small.** `propose_outline` returns a beat
sheet with a time budget per beat and no prose at all, which is the thing worth
arguing with. `expand_beat` then writes one beat, told what comes before and
after it so the prose joins up, and charged for on its own.

The beat sheet is the script's source rather than a note about it. `render_script`
turns the beats into Markdown that the existing parser already understands, which
buys two things for free:

* the heading becomes a `## Heading`, which becomes a chapter, so outlining and
  chaptering turn out to be one feature;
* `intent` is rendered as an HTML comment, which `strip_formatting` removes
  before anything is billed — so the writer's notes about a section travel with
  the script and are never narrated.

How long a paragraph takes to read is measured, not assumed: `words_per_second`
reads the project's own takes, so the outline's timings get more accurate as the
channel produces episodes. The voice-over artist teaches the script writer how
long a section runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from narrate.db.models import Chunk, Script, ScriptBeat, Take
from narrate.llm import GroqError, TokenUsage, chat_json, client_for, resolve_model
from narrate.settings import Settings

# 150 words a minute is the usual reading pace for narration, and the fallback
# when a project has no audio to measure. Documented rather than tuned: it is
# only used until the first episode exists.
DEFAULT_WORDS_PER_SECOND = 2.5

# Beats shorter than this are not sections, they are paragraphs — and a chapter
# list needs ten seconds between entries anyway.
MIN_BEAT_SECONDS = 30.0

OUTLINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["beats"],
    "properties": {
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["heading", "intent", "share"],
                "properties": {
                    "heading": {
                        "type": "string",
                        "description": (
                            "A short section name, three to six words. It becomes a "
                            "YouTube chapter, so it is a label and not a sentence."
                        ),
                    },
                    "intent": {
                        "type": "string",
                        "description": (
                            "What this section has to accomplish, and the one idea it "
                            "turns on. Two sentences at most. Not the prose itself."
                        ),
                    },
                    "share": {
                        "type": "number",
                        "minimum": 1,
                        "maximum": 100,
                        "description": (
                            "Roughly what percentage of the total runtime this section "
                            "should take. All the shares should add to about 100."
                        ),
                    },
                },
            },
        }
    },
}

EXPAND_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["prose"],
    "properties": {
        "prose": {
            "type": "string",
            "description": (
                "The narration for this section, as plain paragraphs separated by "
                "blank lines. Spoken words only — no heading, no stage directions, "
                "no bullet points, nothing in brackets."
            ),
        }
    },
}

OUTLINE_PROMPT = """You are structuring a narration script for a YouTube video.

You produce a beat sheet, not prose. Each beat is a section: a short heading, what
that section has to accomplish, and roughly what share of the runtime it deserves.

Rules:
- Aim for {beats} beats. The first is the hook and gets a small share — it has to
  earn the next thirty seconds, not explain everything.
- Headings become YouTube chapters, so they are labels: "The Employee Trap", not
  "Now let us consider the trap that employment represents".
- Front-load the substance. A viewer who leaves at 40% should already have had
  the thing they came for.
- The shares should add up to about 100.
"""

EXPAND_PROMPT = """You are writing one section of a narration script. It will be
read aloud by a single voice.

Write only the words to be spoken. No heading, no speaker labels, no stage
directions, no bracketed asides, no bullet points, no markdown. Plain paragraphs
separated by blank lines.

Target about {words} words, which is roughly {seconds:.0f} seconds when read
aloud. Getting close matters: the whole episode is budgeted from these numbers.

Do not open by announcing what the section is about, and do not close by
summarising it. It is the middle of a continuous piece of narration — join on to
what came before and hand off to what comes next.
"""


@dataclass(frozen=True)
class Beat:
    """One proposed section, before it is written to the database."""

    heading: str
    intent: str
    target_seconds: float


@dataclass
class OutlineRun:
    beats: list[Beat] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    errors: list[str] = field(default_factory=list)

    @property
    def cost_micros(self) -> int:
        return self.usage.cost_micros

    @property
    def model(self) -> str:
        return self.usage.model


@dataclass
class ExpandRun:
    """One pass of expansion, over one or more beats."""

    written: dict[int, str] = field(default_factory=dict)
    usage: TokenUsage = field(default_factory=TokenUsage)
    errors: list[str] = field(default_factory=list)

    @property
    def cost_micros(self) -> int:
        return self.usage.cost_micros

    @property
    def model(self) -> str:
        return self.usage.model


def words_per_second(session: Session, project_id: int) -> float:
    """How fast this project's voice actually reads, from its own takes.

    Falls back to a documented constant when there is nothing to measure — the
    same shape as `ledger.billing_ratio`: prefer observation, and say plainly
    what is assumed when there is none.

    A chunk with several takes contributes its words once per take, alongside
    each take's duration, which is the correct weighted average rather than a
    double count.
    """
    rows = session.execute(
        select(Chunk.text, Take.duration_s)
        .join(Take, Take.chunk_id == Chunk.id)
        .join(Script, Script.id == Chunk.script_id)
        .where(Script.project_id == project_id, Take.duration_s.is_not(None))
    ).all()
    total_seconds = sum(float(duration) for _, duration in rows)
    words = sum(len(text.split()) for text, _ in rows)
    if total_seconds <= 0 or words <= 0:
        return DEFAULT_WORDS_PER_SECOND
    return words / total_seconds


def words_for(seconds: float, rate: float = DEFAULT_WORDS_PER_SECOND) -> int:
    """How many words fill a stretch of time at a given reading pace."""
    return max(1, round(seconds * max(rate, 0.1)))


def render_script(beats: list[ScriptBeat], title: str) -> str:
    """Turn a beat sheet into a script the existing parser understands.

    The headings become chapters and the intents become comments, both by virtue
    of what the parser already does — nothing here is a special case.
    """
    lines = [f"# {title}", ""]
    for beat in beats:
        lines += [f"## {beat.heading}", ""]
        if beat.intent:
            note = " ".join(beat.intent.split())
            budget = f" Target ~{beat.target_seconds:.0f}s." if beat.target_seconds else ""
            lines += [f"<!-- {note}{budget} -->", ""]
        if beat.body:
            lines += [beat.body.strip(), ""]
    return "\n".join(lines).rstrip() + "\n"


async def propose_outline(
    brief: str,
    settings: Settings,
    *,
    minutes: float,
    beats: int = 8,
    model: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> OutlineRun:
    """Propose a beat sheet for an episode of a given length.

    The model returns *shares* rather than seconds, and the seconds are computed
    here. Asking for both would let them disagree, and a beat sheet whose parts
    do not add up to the episode is not a budget.
    """
    chosen = resolve_model(model, settings)
    run = OutlineRun(usage=TokenUsage(model=chosen))
    total_seconds = max(minutes, 0.5) * 60

    owns_client = client is None
    http = client or client_for(settings)
    try:
        content, usage = await chat_json(
            http,
            chosen,
            system=OUTLINE_PROMPT.format(beats=beats),
            user=f"{brief}\n\nTarget length: {minutes:g} minutes.",
            schema=OUTLINE_SCHEMA,
            name="script_outline",
            temperature=0.7,
        )
        run.usage += usage
        raw = content.get("beats", [])
        shares = [max(float(item["share"]), 0.0) for item in raw]
        total_share = sum(shares) or 1.0
        for item, share in zip(raw, shares, strict=True):
            run.beats.append(
                Beat(
                    heading=" ".join(str(item["heading"]).split()),
                    intent=" ".join(str(item["intent"]).split()),
                    # Normalised against what the shares actually add up to, not
                    # against the 100 the model was asked for.
                    target_seconds=round(total_seconds * share / total_share, 1),
                )
            )
    except (httpx.HTTPError, GroqError) as exc:
        run.errors.append(str(exc))
    finally:
        if owns_client:
            await http.aclose()

    return run


async def expand_beats(
    beats: list[ScriptBeat],
    settings: Settings,
    *,
    title: str,
    rate: float = DEFAULT_WORDS_PER_SECOND,
    model: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> ExpandRun:
    """Write the prose for each given beat, one request at a time.

    Sequential, like every other loop that talks to Groq here: the free tier's
    limits are per minute. Each beat is told the headings either side of it so
    the prose joins up, which is cheaper and steadier than sending the whole
    script so far.
    """
    chosen = resolve_model(model, settings)
    run = ExpandRun(usage=TokenUsage(model=chosen))

    ordered = sorted(beats, key=lambda b: b.ordinal)
    headings = [b.heading for b in ordered]

    owns_client = client is None
    http = client or client_for(settings)
    try:
        for index, beat in enumerate(ordered):
            seconds = beat.target_seconds or MIN_BEAT_SECONDS
            before = headings[index - 1] if index else "(this is the opening)"
            after = headings[index + 1] if index + 1 < len(headings) else "(this is the end)"
            user = (
                f"Episode: {title}\n"
                f"Section: {beat.heading}\n"
                f"What it has to do: {beat.intent}\n"
                f"The section before it: {before}\n"
                f"The section after it: {after}"
            )
            try:
                content, usage = await chat_json(
                    http,
                    chosen,
                    system=EXPAND_PROMPT.format(words=words_for(seconds, rate), seconds=seconds),
                    user=user,
                    schema=EXPAND_SCHEMA,
                    name="section_prose",
                    temperature=0.7,
                )
            except (httpx.HTTPError, GroqError) as exc:
                run.errors.append(f"beat {beat.ordinal}: {exc}")
                continue
            run.usage += usage
            prose = str(content.get("prose", "")).strip()
            if prose:
                run.written[beat.ordinal] = prose
    finally:
        if owns_client:
            await http.aclose()

    return run
