"""Audience-retention targets for an episode, from its measured runtime.

Costs nothing and calls nothing. Everything here is arithmetic over a timeline
narrate already built, which is the whole reason it is worth having: the targets
depend on runtime, and runtime is the one number a script writer does not know
until the audio exists.

The published guidance is a table of five rows — 8 minutes wanting 45%/50%
average percentage viewed, then 15, 30, 60 and 120 minutes each wanting five
points less. Stored as a table it would need interpolating between rows and would
refuse to say anything about a 21-minute episode, which is exactly the length
people make.

It does not need interpolating, because the table is a formula. Each **doubling**
of runtime costs five points:

    good = 60 - 5 * log2(minutes)

That reproduces all five published rows exactly once truncated, and it is
continuous, so a 21-minute episode gets a real answer rather than the nearest
row's. It also keeps going outside 8 to 120 minutes, where the document says
nothing — so `Target.extrapolated` marks that, and the CLI says so rather than
presenting a guess with the same confidence as a published figure.

A percentage is not actionable on its own, so the useful output is the other
form of the same number: how long a viewer must actually stay.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import log2

from narrate.script_parse import format_youtube_time
from narrate.timeline import EFFECT, Timeline, TimelineEntry

# The published rows, as (minutes, good_pct, great_pct). Kept so a test can
# assert the formula still reproduces them, rather than as a lookup.
PUBLISHED: tuple[tuple[int, int, int], ...] = (
    (8, 45, 50),
    (15, 40, 45),
    (30, 35, 40),
    (60, 30, 35),
    (120, 25, 30),
)

# Where the published guidance starts and stops.
SHORTEST_PUBLISHED_MIN = 8
LONGEST_PUBLISHED_MIN = 120

# The gap between "good" and "great" is a flat five points at every length.
GREAT_MARGIN = 5

# The document's retention advice is mostly about the opening: this is the
# window it treats as decisive.
OPENING_SECONDS = 30.0


@dataclass(frozen=True)
class Target:
    """What this episode's length implies it should hold."""

    runtime_s: float
    good_pct: int
    great_pct: int
    extrapolated: bool

    @property
    def minutes(self) -> float:
        return self.runtime_s / 60

    @property
    def good_hold_s(self) -> float:
        """How long a viewer must stay for the episode to be doing well."""
        return hold_seconds(self.runtime_s, self.good_pct)

    @property
    def great_hold_s(self) -> float:
        return hold_seconds(self.runtime_s, self.great_pct)


def targets(runtime_s: float) -> Target:
    """The average-percentage-viewed this runtime should be aiming at.

    Seconds in, to match every other duration in the codebase. A runtime of zero
    — nothing generated yet — has no meaningful target, and returning the
    shortest row's figures would be a quiet lie, so it reports zero and lets the
    caller say why.
    """
    if runtime_s <= 0:
        return Target(runtime_s=0.0, good_pct=0, great_pct=0, extrapolated=True)

    minutes = runtime_s / 60
    good = int(60 - GREAT_MARGIN * log2(minutes))
    return Target(
        runtime_s=runtime_s,
        good_pct=good,
        great_pct=good + GREAT_MARGIN,
        extrapolated=not SHORTEST_PUBLISHED_MIN <= minutes <= LONGEST_PUBLISHED_MIN,
    )


def hold_seconds(runtime_s: float, pct: float) -> float:
    """The absolute watch time a percentage of a runtime amounts to."""
    return max(0.0, runtime_s * pct / 100)


def hold_lands_in(timeline: Timeline, seconds: float) -> TimelineEntry | None:
    """Which chunk is playing at a given moment.

    This is the part of a retention target that nothing else can tell you. A
    percentage is abstract and a hold time is only slightly less so, but "the
    average viewer leaves during chunk 9" points at a specific paragraph — and it
    is only answerable because the timeline is built from measured durations
    rather than estimates.

    Returns `None` past the end of the narration, which is the good problem to
    have: it means the target is longer than the episode.
    """
    for entry in timeline.narration:
        if entry.start_s <= seconds < entry.end_s:
            return entry
    return None


def pacing_notes(timeline: Timeline) -> list[str]:
    """Observations about where this episode's retention is most at risk.

    Deliberately observations rather than scores. Each one names something
    measurable about the audio that the guidance says matters, and leaves the
    judgement to whoever wrote the script — a 40-second chunk is a problem in one
    episode and the best passage in another.
    """
    notes: list[str] = []
    narration = [e for e in timeline.narration if e.duration_s > 0]
    if not narration:
        return ["Nothing generated yet, so there is no pacing to look at."]

    if not timeline.is_complete:
        notes.append(
            "Some chunks have no audio yet, so the runtime below is short of what "
            "the finished episode will be — and so is every figure drawn from it."
        )

    opening = [e for e in narration if e.start_s < OPENING_SECONDS]
    notes.append(
        f"The first {OPENING_SECONDS:.0f} seconds span {len(opening)} chunk(s). "
        "This is the stretch that decides whether anybody sees the rest."
    )

    longest = max(narration, key=lambda e: e.duration_s)
    notes.append(
        f"The longest single chunk is {longest.duration_s:.1f}s, at "
        f"{format_youtube_time(longest.start_s)} — one continuous stretch with no "
        "change of pace."
    )

    effects = [e for e in timeline.entries if e.kind == EFFECT]
    if not effects:
        notes.append(
            "No sound effects are placed. Nothing requires them, but an episode "
            "with no texture has only the voice to hold attention."
        )

    return notes
