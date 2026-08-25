"""Marker extraction. The tests here are what stop markers reaching a billed request."""

from __future__ import annotations

import pytest

from narrate.script_parse import (
    format_time,
    parse_script,
    parse_time,
    strip_markers,
)

# -- the money-protecting property ------------------------------------------


def test_markers_never_survive_into_the_narration() -> None:
    """A marker left inline would be spoken aloud and billed for."""
    text = "The road vanished. [SFX: wind howling, 4s] [@ 02:15] The birds left."
    clean = strip_markers(text)
    assert "[SFX" not in clean
    assert "[@" not in clean
    assert "4s" not in clean
    assert clean == "The road vanished. The birds left."


def test_removing_an_inline_marker_leaves_one_space() -> None:
    """Billed characters include whitespace, and double spaces read oddly."""
    assert strip_markers("It stopped. [SFX: silence] Then it began.") == (
        "It stopped. Then it began."
    )


def test_a_marker_on_its_own_line_does_not_create_a_paragraph_break() -> None:
    text = "First para.\n\n[SFX: wind]\n\nSecond para."
    assert strip_markers(text) == "First para.\n\nSecond para."


def test_prose_with_brackets_is_left_alone() -> None:
    """The grammar has to be narrow enough that ordinary writing survives."""
    for text in (
        "He wrote [sic] in the margin.",
        "The result [see figure 3] was clear.",
        "An audio tag like [whispers] belongs to v3, not to us.",
        "[CHUNK 2]",
    ):
        assert strip_markers(text) == text


# -- effect slots -----------------------------------------------------------


def test_bare_effect_slot_leaves_duration_to_the_provider() -> None:
    slot = parse_script("Thunder. [SFX: distant thunder]").slots[0]
    assert slot.description == "distant thunder"
    assert slot.duration_s is None
    assert slot.loop is False


def test_effect_slot_with_duration() -> None:
    slot = parse_script("[SFX: wind howling, 4s]").slots[0]
    assert slot.description == "wind howling"
    assert slot.duration_s == 4.0


def test_effect_slot_with_duration_and_loop() -> None:
    slot = parse_script("[SFX: soft rain, 30s, loop]").slots[0]
    assert slot.description == "soft rain"
    assert slot.duration_s == 30.0
    assert slot.loop is True


def test_a_description_may_contain_commas() -> None:
    """Parsed from the right, so a comma in prose is not read as a field."""
    slot = parse_script("[SFX: footsteps on gravel, then a door opens, 6s]").slots[0]
    assert slot.description == "footsteps on gravel, then a door opens"
    assert slot.duration_s == 6.0


@pytest.mark.parametrize("form", ["4s", "4 s", "4sec", "4 seconds", "4.5s"])
def test_duration_spellings(form: str) -> None:
    slot = parse_script(f"[SFX: wind, {form}]").slots[0]
    assert slot.duration_s == pytest.approx(4.5 if "." in form else 4.0)


def test_out_of_range_duration_is_dropped_with_a_warning() -> None:
    """The API rejects anything outside 0.5-30s; better to let it choose."""
    parsed = parse_script("[SFX: very long drone, 90s]")
    assert parsed.slots[0].duration_s is None
    assert "90s is outside" in parsed.warnings[0]


def test_an_effect_marker_with_no_description_is_ignored() -> None:
    parsed = parse_script("Nothing here. [SFX: ]")
    assert parsed.slots == []
    assert "no description" in parsed.warnings[0]


def test_slug_is_stable_and_filesystem_safe() -> None:
    slot = parse_script("[SFX: Wind Howling Over Snow!, 4s]").slots[0]
    assert slot.slug == "wind-howling-over-snow-4s"
    assert parse_script("[SFX: soft rain, 30s, loop]").slots[0].slug == "soft-rain-30s-loop"


def test_identical_effects_share_a_slug_so_they_can_be_reused() -> None:
    parsed = parse_script("A. [SFX: wind howling, 4s] B. [SFX: wind howling, 4s] C.")
    assert len({s.slug for s in parsed.slots}) == 1
    assert len(parsed.slots) == 2


# -- anchors ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("00:00", 0.0), ("02:15", 135.0), ("1:02:15", 3735.0), ("02:15.500", 135.5)],
)
def test_anchor_times(raw: str, expected: float) -> None:
    assert parse_time(raw) == expected
    assert parse_script(f"[@ {raw}] Line.").anchors[0].target_s == expected


def test_format_time_round_trips() -> None:
    assert format_time(0) == "00:00:00.000"
    assert format_time(135.5) == "00:02:15.500"
    assert format_time(3735.0) == "01:02:15.000"
    # Rounding must carry rather than produce a 1000ms field.
    assert format_time(1.9999) == "00:00:02.000"


# -- offsets ----------------------------------------------------------------


def test_offsets_point_into_the_cleaned_text() -> None:
    """Offsets survive stripping, which is what makes them usable as boundaries."""
    parsed = parse_script("First sentence. [SFX: wind] Second sentence.")
    offset = parsed.slots[0].offset
    assert parsed.text[:offset] == "First sentence."
    assert parsed.text[offset:].strip() == "Second sentence."


def test_boundaries_exclude_the_very_start() -> None:
    """A marker before any text is not a split point — there is nothing to split."""
    parsed = parse_script("[SFX: wind] Opening line.")
    assert parsed.slots
    assert parsed.boundaries == []


def test_boundaries_are_sorted_and_deduplicated() -> None:
    parsed = parse_script("A. [SFX: one] B. [SFX: two] C.")
    assert parsed.boundaries == sorted(set(parsed.boundaries))
    assert len(parsed.boundaries) == 2


def test_a_script_with_no_markers_is_unchanged() -> None:
    text = "Just prose.\n\nTwo paragraphs of it."
    parsed = parse_script(text)
    assert parsed.text == text
    assert parsed.has_markers is False
    assert parsed.boundaries == []


# ---------------------------------------------------------------------------
# Markdown, which is layout rather than speech. A `.md` script that keeps its
# syntax gets that syntax narrated and billed for.
# ---------------------------------------------------------------------------


def test_a_heading_is_not_narrated() -> None:
    parsed = parse_script("# The Keeper's Log\n\nThe light turned for the last time.")
    assert parsed.text == "The light turned for the last time."


def test_dropping_a_heading_is_reported_rather_than_silent() -> None:
    parsed = parse_script("## Act Two\nShe wrote her final entry.")
    assert len(parsed.warnings) == 1
    assert "Act Two" in parsed.warnings[0]


def test_emphasis_keeps_the_words_and_drops_the_marks() -> None:
    assert strip_markers("It was **genuinely** cold, and _quietly_ so.") == (
        "It was genuinely cold, and quietly so."
    )


def test_a_link_narrates_its_text_not_its_url() -> None:
    assert strip_markers("See [the report](https://example.com/x) for more.") == (
        "See the report for more."
    )


def test_an_image_is_dropped_entirely() -> None:
    assert strip_markers("![a lighthouse](img.png)\nThe tower stood.") == "The tower stood."


def test_bullets_and_quote_marks_go_but_their_text_stays() -> None:
    assert strip_markers("- first point\n- second\n1. third\n> she wrote it") == (
        "first point\nsecond\nthird\nshe wrote it"
    )


def test_a_horizontal_rule_becomes_a_paragraph_break() -> None:
    assert strip_markers("One.\n\n---\n\nTwo.") == "One.\n\nTwo."


@pytest.mark.parametrize(
    "text",
    [
        "5 * 3 is fifteen, not thirty.",
        "Keep output_format and snake_case names intact.",
        "The cost was $0.19 (about a fifth of a dollar).",
        "She said: it is done. Then nothing.",
    ],
)
def test_ordinary_prose_is_left_exactly_alone(text: str) -> None:
    assert strip_markers(text) == text


def test_an_escaped_asterisk_survives_as_an_asterisk() -> None:
    assert strip_markers(r"An escaped \*asterisk\* stays.") == "An escaped *asterisk* stays."


def test_inline_code_keeps_its_contents() -> None:
    assert strip_markers("Set `output_format` to mp3.") == "Set output_format to mp3."


def test_offsets_survive_formatting_removal() -> None:
    """The money-adjacent one: a slot offset is handed to the chunker as a
    forced boundary, so it has to point into the text the chunker will see."""
    parsed = parse_script("# Log\n\n[SFX: foghorn, 4s] The light turned.")
    assert parsed.text == "The light turned."
    assert parsed.slots[0].offset == 0


def test_a_marker_left_at_the_start_does_not_strand_a_space() -> None:
    parsed = parse_script("# Log\n\n[SFX: foghorn] Opening line.")
    assert not parsed.text.startswith(" ")


# ---------------------------------------------------------------------------
# A cast of voices. The load-bearing rule is that a speaker prefix only counts
# when the name is cast — everything else follows from it.
# ---------------------------------------------------------------------------

CAST = {"Morag": "voice_m", "Keeper": "voice_k"}


def test_a_cast_block_declares_who_is_speaking() -> None:
    parsed = parse_script("[CAST] Morag = voice_m · Keeper = voice_k\n\nMorag: Hello.")
    assert parsed.cast == CAST
    assert parsed.speakers == ["Morag"]


def test_a_cast_block_can_be_written_one_per_line() -> None:
    parsed = parse_script("[CAST]\nMorag = voice_m\nKeeper = voice_k\n\nMorag: Hello.")
    assert parsed.cast == CAST


def test_the_cast_block_itself_is_never_narrated() -> None:
    parsed = parse_script("[CAST] Morag = voice_m\n\nMorag: The light went out.")
    assert parsed.text == "The light went out."


def test_a_speaker_prefix_is_stripped_not_spoken() -> None:
    """Left inline, `Morag:` is read aloud as "Morag colon" and billed for —
    the same failure the markers and the Markdown syntax are stripped to
    avoid."""
    parsed = parse_script("Morag: The light went out.", CAST)
    assert parsed.text == "The light went out."
    assert parsed.turns[0].speaker == "Morag"
    assert parsed.turns[0].voice_id == "voice_m"


def test_each_turn_gets_the_right_voice() -> None:
    parsed = parse_script("Morag: One.\n\nKeeper: Two.\n\nMorag: Three.", CAST)
    assert [(t.speaker, t.voice_id) for t in parsed.turns] == [
        ("Morag", "voice_m"),
        ("Keeper", "voice_k"),
        ("Morag", "voice_m"),
    ]


def test_a_speaker_prefix_is_recognised_whatever_its_case() -> None:
    parsed = parse_script("MORAG: One.\n\nmorag: Two.", CAST)
    # Reported under the declared spelling, not as written.
    assert [t.speaker for t in parsed.turns] == ["Morag", "Morag"]


# -- the false-positive guard, which is the whole reason for the cast rule --


@pytest.mark.parametrize(
    "line",
    [
        "Note: this is not dialogue.",
        "Warning: mind the step.",
        "12:30 : the train leaves.",
        "Chapter one: the beginning.",
        "One thing: it never worked.",
        "https://example.com/a: a url in prose.",
    ],
)
def test_a_colon_in_prose_is_never_a_speaker(line: str) -> None:
    """No heuristic about capitalisation is involved. A name is a speaker if it
    is cast and otherwise it is words, which is why this cannot silently edit
    somebody's narration."""
    parsed = parse_script(line, CAST)
    assert parsed.text == line
    assert parsed.turns == []


def test_an_uncast_name_is_left_alone_but_mentioned() -> None:
    parsed = parse_script("Ada: I was not cast.", CAST)
    assert parsed.text == "Ada: I was not cast."
    assert not parsed.turns
    assert any("Ada" in w for w in parsed.warnings)


def test_with_no_cast_at_all_nothing_is_a_speaker() -> None:
    text = "Morag: One.\n\nKeeper: Two."
    assert strip_markers(text) == text


# -- the [VOICE:] marker, for a change mid-paragraph -----------------------


def test_a_voice_marker_switches_speaker() -> None:
    parsed = parse_script("She spoke. [VOICE: Keeper] And he answered.", CAST)
    assert parsed.text == "She spoke. And he answered."
    assert parsed.turns[0].speaker == "Keeper"


def test_a_voice_marker_naming_nobody_warns_and_changes_nothing() -> None:
    parsed = parse_script("[VOICE: Ada] Words.", CAST)
    assert parsed.text == "Words."
    assert not parsed.turns
    assert any("not in the cast" in w for w in parsed.warnings)


# -- offsets, which is what makes a turn its own chunk ---------------------


def test_every_turn_offset_lands_on_the_words_it_names() -> None:
    """The money-adjacent one. A turn offset becomes a forced chunk boundary,
    and a chunk is one request with one voice — so an offset that drifts by a
    couple of characters gives a line to the wrong speaker."""
    parsed = parse_script(
        "Morag: One two three.\n\n[SFX: foghorn, 4s]\n\nMorag: Four five six.\n\n"
        "[VOICE: Keeper] Seven eight.",
        CAST,
    )
    assert parsed.text == "One two three.\n\nFour five six.\n\nSeven eight."
    assert parsed.turns[0].offset == 0
    assert parsed.text[parsed.turns[1].offset :].startswith("Four five six.")
    assert parsed.text[parsed.turns[2].offset :].startswith("Seven eight.")


def test_a_marker_on_its_own_line_leaves_no_stranded_space() -> None:
    parsed = parse_script("One.\n\n[SFX: foghorn, 4s]\n\nTwo.")
    assert parsed.text == "One.\n\nTwo."


def test_turn_offsets_become_chunk_boundaries() -> None:
    parsed = parse_script("Morag: One.\n\nKeeper: Two.", CAST)
    # A turn sharing a chunk with the previous speaker could not be given its
    # own voice, because a chunk is one request.
    assert parsed.text.index("Two.") in parsed.boundaries


def test_slot_and_turn_boundaries_are_merged() -> None:
    parsed = parse_script("Morag: One. [SFX: wind] Still Morag.\n\nKeeper: Two.", CAST)
    assert len(parsed.boundaries) == 2


def test_voice_at_reports_who_holds_the_floor() -> None:
    parsed = parse_script("Morag: One two.\n\nKeeper: Three four.", CAST)
    assert parsed.voice_at(0) == "voice_m"
    assert parsed.voice_at(5) == "voice_m"
    assert parsed.voice_at(len(parsed.text) - 1) == "voice_k"


def test_a_script_declaring_its_own_cast_overrides_the_project() -> None:
    parsed = parse_script("[CAST] Morag = special\n\nMorag: One.", CAST)
    assert parsed.turns[0].voice_id == "special"
    # The project's other names survive alongside it.
    assert parsed.cast["Keeper"] == "voice_k"


def test_casting_the_same_name_twice_warns_and_takes_the_later() -> None:
    parsed = parse_script("[CAST] Morag = one · Morag = two\n\nMorag: Hi.")
    assert parsed.cast["Morag"] == "two"
    assert any("cast twice" in w for w in parsed.warnings)


def test_markdown_bold_around_a_speaker_still_reads_as_a_speaker() -> None:
    """Scripts get written in Markdown, and `**Morag:**` is how a person bolds
    a name. Formatting is stripped first, so by the time speakers are read the
    prefix looks ordinary."""
    parsed = parse_script("**Morag:** The light went out.", CAST)
    assert parsed.text == "The light went out."
    assert parsed.turns[0].speaker == "Morag"


# ---------------------------------------------------------------------------
# Comments. The downloadable script template is mostly guidance in comments, so
# every word of it would be read aloud and billed if these survived.
# ---------------------------------------------------------------------------


def test_a_comment_is_never_narrated() -> None:
    assert strip_markers("<!-- delete me -->\n\nThe light turned.") == "The light turned."


def test_a_comment_may_span_lines() -> None:
    """The useful kind — a paragraph of guidance above the line it explains."""
    text = "<!--\nWrite the hook here.\nKeep it under two sentences.\n-->\n\nThe light turned."
    assert strip_markers(text) == "The light turned."


def test_a_mid_sentence_comment_leaves_one_space_not_two() -> None:
    """A stranded space is a billed character."""
    assert strip_markers("The light turned. <!-- note --> Then it stopped.") == (
        "The light turned. Then it stopped."
    )


def test_a_comment_at_the_end_of_a_line_leaves_nothing() -> None:
    assert strip_markers("The light turned. <!-- note -->\n\nNext.") == "The light turned.\n\nNext."


def test_several_comments_on_one_line() -> None:
    assert strip_markers("A. <!-- one --> B. <!-- two --> C.") == "A. B. C."


def test_a_comment_wrapping_a_marker_removes_both() -> None:
    parsed = parse_script("<!-- [SFX: not wanted, 4s] -->\n\nThe light turned.")
    assert parsed.text == "The light turned."
    # Commented out means commented out — no slot is created.
    assert parsed.slots == []


def test_an_unterminated_comment_keeps_the_script_and_warns() -> None:
    """HTML would swallow everything to the end of the document, and a Markdown
    renderer will too — which here deletes the rest of the episode. Losing words
    is the one mistake that cannot be undone downstream, so this reports instead
    of guessing."""
    text = "The light turned.\n\n<!-- oops I never closed this\n\nAnd this must survive."
    parsed = parse_script(text)

    assert "And this must survive." in parsed.text
    assert any("unterminated" in w for w in parsed.warnings)


def test_comments_do_not_disturb_the_offsets_around_them() -> None:
    """Comments are removed before anything is measured, so a marker after one
    still lands on its own words."""
    parsed = parse_script(
        "<!-- guidance -->\n\n[SFX: foghorn, 4s] The light turned.\n\n"
        "<!-- more guidance -->\n\n[SFX: waves, 6s] The sea answered."
    )
    assert parsed.text == "The light turned.\n\nThe sea answered."
    assert parsed.text[parsed.slots[0].offset :].startswith("The light turned.")
    assert parsed.text[parsed.slots[1].offset :].startswith("The sea answered.")
