"""Chapter lists: the rules YouTube enforces, and the traps in building one.

A chapter list is unusual among the things this tool produces in that it is
rejected *whole*. One stamp out of order, one gap under ten seconds, no line at
0:00 — and YouTube shows none of them. So the interesting tests here are not
about formatting; they are about refusing to emit a list that will silently do
nothing, and saying which rule was broken.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine

from narrate import publish
from narrate.db.models import Script, ThumbnailBrief, TitleCandidate
from narrate.db.session import session_scope
from narrate.timeline import EFFECT, NARRATION, Timeline, TimelineEntry

from .conftest import require


def _entry(
    index: int,
    start: float,
    end: float,
    *,
    chapter: str | None = None,
    kind: str = NARRATION,
    generated: bool = True,
) -> TimelineEntry:
    return TimelineEntry(
        index=index,
        kind=kind,
        start_s=start,
        end_s=end,
        label=f"chunk {index}",
        chunk_ordinal=index,
        chapter_title=chapter,
        generated=generated,
    )


def _timeline(*entries: TimelineEntry) -> Timeline:
    return Timeline(
        script_id=1,
        script_title="An Episode",
        project_name="A Channel",
        gap_seconds=0.0,
        entries=list(entries),
    )


def _three_good_chapters() -> Timeline:
    return _timeline(
        _entry(1, 0.0, 60.0, chapter="The Hook"),
        _entry(2, 60.0, 180.0, chapter="Four Ways to Earn"),
        _entry(3, 180.0, 400.0, chapter="The Employee Trap"),
    )


# --------------------------------------------------------------------------
# The happy path
# --------------------------------------------------------------------------


def test_a_valid_list_is_usable_and_pastes_as_written() -> None:
    result = publish.chapters_of(_three_good_chapters())
    assert result.usable
    assert result.problems == []
    assert result.text == "0:00 The Hook\n1:00 Four Ways to Earn\n3:00 The Employee Trap"


def test_timestamps_come_from_the_audio_not_from_the_anchors() -> None:
    """The whole reason chapters are built here rather than from the script: an
    anchor is a target and TTS cannot be dialled to it."""
    tl = _timeline(
        _entry(1, 0.0, 70.0, chapter="The Hook"),
        # The writer aimed this at 01:10; it actually landed at 01:12.
        TimelineEntry(
            index=2,
            kind=NARRATION,
            start_s=72.0,
            end_s=200.0,
            label="chunk 2",
            chunk_ordinal=2,
            target_s=70.0,
            chapter_title="Four Ways to Earn",
        ),
        _entry(3, 200.0, 400.0, chapter="The Trap"),
    )
    stamps = [c.stamp for c in publish.chapters_of(tl).chapters]
    assert stamps == ["0:00", "1:12", "3:20"]


# --------------------------------------------------------------------------
# The traps
# --------------------------------------------------------------------------


def test_an_effect_at_the_start_does_not_produce_a_second_zero_line() -> None:
    """The trap this function exists to avoid.

    An effect slot at chunk 1 also starts at 0.0. Iterating every timeline entry
    would offer two things at `0:00`, and a list with a repeated stamp is
    discarded whole — so the list would silently vanish rather than look wrong.
    """
    tl = _timeline(
        _entry(99, 0.0, 3.0, chapter="Should Not Appear", kind=EFFECT),
        _entry(1, 0.0, 60.0, chapter="The Hook"),
        _entry(2, 60.0, 180.0, chapter="Four Ways to Earn"),
        _entry(3, 180.0, 400.0, chapter="The Employee Trap"),
    )
    result = publish.chapters_of(tl)
    stamps = [c.stamp for c in result.chapters]
    assert stamps.count("0:00") == 1
    assert "Should Not Appear" not in result.text
    assert result.usable


def test_a_list_not_starting_at_zero_is_refused_with_the_reason() -> None:
    tl = _timeline(
        _entry(1, 0.0, 60.0),
        _entry(2, 60.0, 180.0, chapter="Four Ways to Earn"),
        _entry(3, 180.0, 400.0, chapter="The Employee Trap"),
        _entry(4, 400.0, 500.0, chapter="Escape Velocity"),
    )
    result = publish.chapters_of(tl)
    assert not result.usable
    assert any("0:00" in p for p in result.problems)


def test_fewer_than_three_chapters_is_refused() -> None:
    tl = _timeline(
        _entry(1, 0.0, 60.0, chapter="The Hook"),
        _entry(2, 60.0, 180.0, chapter="The End"),
    )
    result = publish.chapters_of(tl)
    assert not result.usable
    assert any("at least 3" in p for p in result.problems)


def test_chapters_closer_than_ten_seconds_are_flagged() -> None:
    tl = _timeline(
        _entry(1, 0.0, 4.0, chapter="The Hook"),
        _entry(2, 4.0, 120.0, chapter="Too Soon"),
        _entry(3, 120.0, 300.0, chapter="Fine"),
    )
    result = publish.chapters_of(tl)
    assert not result.usable
    assert any("10s between chapters" in p for p in result.problems)


def test_no_chapters_at_all_names_every_way_of_adding_one() -> None:
    """A refusal that does not say how to fix it is a dead end."""
    result = publish.chapters_of(_timeline(_entry(1, 0.0, 60.0)))
    assert not result.usable
    problem = result.problems[0]
    assert "## Heading" in problem
    assert "[CHAPTER:" in problem
    assert "[@ 03:00" in problem


def test_provisional_timings_are_refused_rather_than_published() -> None:
    """Chapters built before every chunk has audio are wrong, not approximate."""
    tl = _timeline(
        _entry(1, 0.0, 60.0, chapter="The Hook"),
        _entry(2, 60.0, 180.0, chapter="Four Ways to Earn"),
        _entry(3, 180.0, 180.0, chapter="The Trap", generated=False),
    )
    result = publish.chapters_of(tl)
    assert not result.usable
    assert any("not final" in p for p in result.problems)


def test_every_broken_rule_is_reported_not_just_the_first() -> None:
    """All three are YouTube's, and fixing one at a time is a bad loop."""
    tl = _timeline(
        _entry(1, 0.0, 2.0),
        _entry(2, 2.0, 4.0, chapter="Late And Crowded"),
        _entry(3, 4.0, 300.0, chapter="Also Crowded"),
    )
    result = publish.chapters_of(tl)
    assert len(result.problems) >= 3


# --------------------------------------------------------------------------
# Titles
# --------------------------------------------------------------------------


def test_a_writers_own_numbering_is_removed() -> None:
    """YouTube numbers the list itself, so `2. The Trap` would be numbered twice."""
    tl = _timeline(
        _entry(1, 0.0, 60.0, chapter="1. The Hook"),
        _entry(2, 60.0, 180.0, chapter="2) Four Ways to Earn"),
        _entry(3, 180.0, 400.0, chapter="3 - The Employee Trap"),
    )
    titles = [c.title for c in publish.chapters_of(tl).chapters]
    assert titles == ["The Hook", "Four Ways to Earn", "The Employee Trap"]


def test_a_title_that_is_only_a_number_survives_as_itself() -> None:
    """Stripping it to nothing would be worse than leaving it odd."""
    tl = _timeline(
        _entry(1, 0.0, 60.0, chapter="1984"),
        _entry(2, 60.0, 180.0, chapter="Two"),
        _entry(3, 180.0, 400.0, chapter="Three"),
    )
    assert publish.chapters_of(tl).chapters[0].title == "1984"


def test_a_title_spanning_lines_is_collapsed_to_one() -> None:
    tl = _timeline(
        _entry(1, 0.0, 60.0, chapter="The   Hook\nand  its\tpromise"),
        _entry(2, 60.0, 180.0, chapter="Two"),
        _entry(3, 180.0, 400.0, chapter="Three"),
    )
    assert publish.chapters_of(tl).chapters[0].title == "The Hook and its promise"


# --------------------------------------------------------------------------
# Choosing a title, and what that writes through to
# --------------------------------------------------------------------------


def test_adding_a_title_by_hand_chooses_it_and_renames_the_script(
    engine: Engine, project_and_script: tuple[int, int]
) -> None:
    """`Script.title` is the cut. A candidate does not need a second table to
    record which one won, because that column already does."""
    _, script_id = project_and_script
    with session_scope(engine) as session:
        publish.add_title(session, script_id, "The Only Title", formula="Clear Promise")

    with session_scope(engine) as session:
        script = require(session.get(Script, script_id))
        assert script.title == "The Only Title"
        rows = list(session.scalars(select(TitleCandidate)).all())
        assert [r.accepted for r in rows] == [True]


def test_a_proposed_title_never_becomes_the_script_title(
    engine: Engine, project_and_script: tuple[int, int]
) -> None:
    """The safety property, in the shape `EffectSlot` established: the effect
    must not happen, not merely a flag be unset."""
    _, script_id = project_and_script
    with session_scope(engine) as session:
        before = require(session.get(Script, script_id)).title
        publish.add_title(
            session, script_id, "Proposed And Unchosen", source="proposed", accepted=False
        )

    with session_scope(engine) as session:
        assert require(session.get(Script, script_id)).title == before
        row = require(session.scalars(select(TitleCandidate)).one_or_none())
        assert row.accepted is False
        assert row.source == "proposed"


def test_choosing_a_title_unchooses_the_others(
    engine: Engine, project_and_script: tuple[int, int]
) -> None:
    """An episode has one title. Two marked would make the pack's headline
    depend on row order."""
    _, script_id = project_and_script
    with session_scope(engine) as session:
        publish.add_title(session, script_id, "First")
        second = publish.add_title(session, script_id, "Second", accepted=False)
        publish.choose_title(session, second)

    with session_scope(engine) as session:
        rows = {r.text: r.accepted for r in session.scalars(select(TitleCandidate)).all()}
        assert rows == {"First": False, "Second": True}
        assert require(session.get(Script, script_id)).title == "Second"


def test_the_same_title_twice_is_refused(
    engine: Engine, project_and_script: tuple[int, int]
) -> None:
    _, script_id = project_and_script
    with session_scope(engine) as session:
        publish.add_title(session, script_id, "Only Once")
        with pytest.raises(publish.DuplicateTitle):
            publish.add_title(session, script_id, "Only  Once")


def test_choosing_a_brief_unchooses_the_others(
    engine: Engine, project_and_script: tuple[int, int]
) -> None:
    _, script_id = project_and_script
    with session_scope(engine) as session:
        for n in (1, 2, 3):
            session.add(
                ThumbnailBrief(
                    script_id=script_id, ordinal=n, archetype="Face First", overlay_text=f"T{n}"
                )
            )
        session.flush()
        rows = list(session.scalars(select(ThumbnailBrief).order_by(ThumbnailBrief.ordinal)).all())
        publish.choose_brief(session, rows[1])

    with session_scope(engine) as session:
        chosen = [
            r.overlay_text for r in session.scalars(select(ThumbnailBrief)).all() if r.accepted
        ]
        assert chosen == ["T2"]


# --------------------------------------------------------------------------
# The rendered pack
# --------------------------------------------------------------------------


def test_the_pack_renders_without_a_groq_key_or_anything_generated() -> None:
    """Chapters and retention need no key, so the document has to be useful
    before any LLM has been near it — and say what would fill the blanks."""
    body = publish.render_publish_pack(_three_good_chapters(), publish.PublishContext())
    assert "## Title" in body
    assert "## Chapters" in body
    assert "## Retention target" in body
    assert "narrate publish draft" in body
    assert "narrate publish brief" in body


def test_rendering_is_deterministic() -> None:
    tl = _three_good_chapters()
    ctx = publish.PublishContext(title="A Title", tags=("one", "two"))
    assert publish.render_publish_pack(tl, ctx) == publish.render_publish_pack(tl, ctx)


def test_an_over_long_title_is_flagged_with_its_length() -> None:
    long_title = "A" * (publish.TITLE_COMFORTABLE_CHARS + 12)
    ctx = publish.PublishContext(
        titles=(
            publish.TitleOption(
                id=1, text=long_title, formula="", rationale="", accepted=True, proposed=False
            ),
        )
    )
    body = publish.render_publish_pack(_three_good_chapters(), ctx)
    assert str(len(long_title)) in body


def test_channel_tags_follow_the_episodes_own_without_repeating() -> None:
    ctx = publish.PublishContext(tags=("wealth", "money"), project_tags=("money", "investing"))
    assert ctx.all_tags == ("wealth", "money", "investing")


def test_unusable_chapters_are_kept_out_of_the_pasted_description() -> None:
    """A description carrying a chapter block YouTube rejects is worse than one
    without it — the block would just sit there as text."""
    tl = _timeline(
        _entry(1, 0.0, 60.0),
        _entry(2, 60.0, 180.0, chapter="Two"),
        _entry(3, 180.0, 400.0, chapter="Three"),
        _entry(4, 400.0, 500.0, chapter="Four"),
    )
    ctx = publish.PublishContext(description="Some words.")
    body = publish.render_publish_pack(tl, ctx)
    description = body.split("## Description")[1].split("## Tags")[0]
    assert "Chapters:" not in description
    assert "would not be accepted" in description


def test_the_json_twin_carries_what_the_ui_needs() -> None:
    """The UI reads this, never the markdown — re-parsing a rendered document is
    how a display and its source drift apart."""
    ctx = publish.PublishContext(title="A Title", tags=("one",))
    data = json.loads(publish.render_publish_json(_three_good_chapters(), ctx))
    assert data["chapters"]["usable"] is True
    assert len(data["chapters"]["entries"]) == 3
    assert data["chapters"]["entries"][0]["stamp"] == "0:00"
    assert data["retention"]["good_pct"] > 0
    assert data["tags"] == ["one"]
    assert data["thumbnail"] is None
