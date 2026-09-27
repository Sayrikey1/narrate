"""Retention targets: the published table, and the formula that replaces it.

The source guidance is five rows — 8 minutes wanting 45%/50% of its length
watched on average, then 15, 30, 60 and 120 minutes each wanting five points
less. Kept as a lookup it would need interpolating and could say nothing about a
21-minute episode, so `retention.targets` computes it instead.

The first test is therefore the important one: the formula has to agree with the
published figures *exactly*, or it is not implementing the guidance, it is
inventing some.
"""

from __future__ import annotations

from narrate import retention
from narrate.timeline import EFFECT, NARRATION, Timeline, TimelineEntry


def _entry(
    index: int, start: float, end: float, *, kind: str = NARRATION, label: str = ""
) -> TimelineEntry:
    return TimelineEntry(
        index=index,
        kind=kind,
        start_s=start,
        end_s=end,
        label=label or f"chunk {index}",
        chunk_ordinal=index,
    )


def _timeline(*entries: TimelineEntry) -> Timeline:
    return Timeline(
        script_id=1,
        script_title="An Episode",
        project_name="A Channel",
        gap_seconds=0.0,
        entries=list(entries),
    )


# --------------------------------------------------------------------------
# The table
# --------------------------------------------------------------------------


def test_the_formula_reproduces_every_published_row_exactly() -> None:
    """If this fails, the formula is no longer implementing the guidance."""
    for minutes, good, great in retention.PUBLISHED:
        target = retention.targets(minutes * 60)
        assert (target.good_pct, target.great_pct) == (good, great), f"{minutes} min"


def test_a_published_length_is_not_reported_as_extrapolated() -> None:
    for minutes, _, _ in retention.PUBLISHED:
        assert not retention.targets(minutes * 60).extrapolated


def test_lengths_outside_the_published_range_are_marked() -> None:
    """The document says nothing below 8 or above 120 minutes. The number is
    still computed, but the caller has to be able to say it is a guess."""
    assert retention.targets(4 * 60).extrapolated
    assert retention.targets(200 * 60).extrapolated


def test_a_target_between_published_rows_falls_between_them() -> None:
    """The reason the formula exists: 21 minutes is a real length and the table
    has no row for it."""
    target = retention.targets(21.5 * 60)
    assert 35 < target.good_pct < 40
    assert target.great_pct == target.good_pct + 5


def test_targets_never_rise_as_an_episode_gets_longer() -> None:
    """Monotonicity is the one property the guidance is unambiguous about."""
    previous = 101
    for minutes in range(1, 201):
        good = retention.targets(minutes * 60).good_pct
        assert good <= previous, f"{minutes} min rose to {good}"
        previous = good


def test_each_doubling_of_length_costs_five_points() -> None:
    for minutes in (8, 10, 15, 30, 60):
        here = retention.targets(minutes * 60).good_pct
        doubled = retention.targets(minutes * 120).good_pct
        assert here - doubled == 5, f"{minutes} min -> {minutes * 2} min"


def test_nothing_generated_yet_has_no_target_rather_than_a_default() -> None:
    """Reporting the shortest row's figures for a runtime of zero would be a
    quiet lie about an episode that does not exist yet."""
    target = retention.targets(0)
    assert (target.good_pct, target.great_pct) == (0, 0)
    assert target.extrapolated


# --------------------------------------------------------------------------
# Hold times — the actionable form of the same number
# --------------------------------------------------------------------------


def test_the_hold_time_is_the_percentage_of_the_runtime() -> None:
    target = retention.targets(15 * 60)
    assert target.good_pct == 40
    assert target.good_hold_s == 900 * 0.40
    assert target.great_hold_s == 900 * 0.45


def test_hold_seconds_never_goes_negative() -> None:
    assert retention.hold_seconds(600, -10) == 0.0


def test_the_hold_time_names_the_chunk_the_viewer_leaves_in() -> None:
    """The output nothing else can produce, because it needs measured durations."""
    tl = _timeline(
        _entry(1, 0.0, 60.0),
        _entry(2, 60.0, 120.0, label="the second mistake"),
        _entry(3, 120.0, 180.0),
    )
    landing = retention.hold_lands_in(tl, 90.0)
    assert landing is not None
    assert landing.chunk_ordinal == 2
    assert landing.label == "the second mistake"


def test_a_hold_time_past_the_end_lands_nowhere() -> None:
    """The good problem: the target is longer than the episode."""
    tl = _timeline(_entry(1, 0.0, 60.0))
    assert retention.hold_lands_in(tl, 120.0) is None


def test_a_boundary_moment_belongs_to_the_chunk_that_is_starting() -> None:
    tl = _timeline(_entry(1, 0.0, 60.0), _entry(2, 60.0, 120.0))
    landing = retention.hold_lands_in(tl, 60.0)
    assert landing is not None and landing.chunk_ordinal == 2


# --------------------------------------------------------------------------
# Pacing notes
# --------------------------------------------------------------------------


def test_an_empty_timeline_says_so_rather_than_inventing_notes() -> None:
    assert retention.pacing_notes(_timeline()) == [
        "Nothing generated yet, so there is no pacing to look at."
    ]


def test_the_notes_name_the_opening_and_the_longest_chunk() -> None:
    tl = _timeline(
        _entry(1, 0.0, 12.0),
        _entry(2, 12.0, 24.0),
        _entry(3, 24.0, 99.0),
    )
    notes = " ".join(retention.pacing_notes(tl))
    # Three chunks start inside the first 30 seconds.
    assert "span 3 chunk(s)" in notes
    assert "75.0s" in notes


def test_an_incomplete_timeline_is_flagged_before_anything_else() -> None:
    """Every figure drawn from a partial runtime is short, so this has to come
    first — a target computed from half an episode is wrong, not approximate."""
    generated = _entry(1, 0.0, 60.0)
    missing = TimelineEntry(
        index=2, kind=NARRATION, start_s=60.0, end_s=60.0, label="todo", generated=False
    )
    notes = retention.pacing_notes(_timeline(generated, missing))
    assert "no audio yet" in notes[0]


def test_an_episode_with_no_effects_is_mentioned_once() -> None:
    bare = retention.pacing_notes(_timeline(_entry(1, 0.0, 60.0)))
    assert any("no sound effects" in n.lower() for n in bare)

    with_effect = retention.pacing_notes(
        _timeline(_entry(1, 0.0, 60.0), _entry(2, 0.0, 3.0, kind=EFFECT))
    )
    assert not any("no sound effects" in n.lower() for n in with_effect)
