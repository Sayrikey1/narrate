"""Starter scripts must generate correctly with every comment left in place.

That is the whole promise of a template annotated in HTML comments: it explains
itself *and* it is a working script. If a single word of guidance reaches the
narration it is read aloud and billed for, so the promise is enforced here
rather than asserted in a docstring.

It caught the obvious trap immediately. The opening comment described the
comment syntax, and the closing delimiter inside it ended the comment early —
exactly as HTML says it should — so half the guidance became prose.
"""

from __future__ import annotations

import pytest

from narrate import templates
from narrate.script_parse import parse_script

# The cast a multi-voice template expects to exist. Real ids are irrelevant;
# what matters is that the names resolve, because an uncast name stays prose.
CAST = {"Morag": "voice_m", "Inspector": "voice_i"}

# Phrases that only ever appear in guidance. If any reaches the narration, a
# comment was not closed where its author thought it was.
GUIDANCE = (
    "<!--",
    "-->",
    "narrate script",
    "narrate generate",
    "REPLACE_WITH",
    "====",
    "THE ONE RULE",
    "THE THREE RULES",
    "CHECK BEFORE YOU SPEND",
    "eleven_v3 ONLY",
)

ALL = pytest.mark.parametrize("template", templates.all_templates(), ids=lambda t: t.slug)


@ALL
def test_the_file_exists_and_is_readable(template: templates.Template) -> None:
    assert template.path.is_file()
    assert len(template.read()) > 500


@ALL
def test_no_word_of_guidance_reaches_the_narration(template: templates.Template) -> None:
    """The load-bearing test. Every comment is stripped before the request, so
    a template can be dense with explanation and still cost only its prose."""
    parsed = parse_script(template.read(), CAST)
    found = [marker for marker in GUIDANCE if marker in parsed.text]
    assert not found, f"{template.slug}: guidance leaked into the narration: {found}"


@ALL
def test_the_narration_is_a_small_fraction_of_the_file(template: templates.Template) -> None:
    """A template is mostly guidance. If the narrated share creeps up, a comment
    has come unclosed — this catches it even for wording the list above misses."""
    raw = template.read()
    parsed = parse_script(raw, CAST)
    assert len(parsed.text) < len(raw) * 0.4, (
        f"{template.slug}: {len(parsed.text)} of {len(raw)} characters would be "
        "narrated, which is too much to be prose alone"
    )


@ALL
def test_it_narrates_something(template: templates.Template) -> None:
    """The other failure: a template so cautious it is all comment and no
    example, which teaches nothing and generates nothing."""
    parsed = parse_script(template.read(), CAST)
    assert len(parsed.text) > 200


@ALL
def test_it_carries_no_leftover_markdown_syntax(template: templates.Template) -> None:
    parsed = parse_script(template.read(), CAST)
    for token in ("**", "`", "# "):
        assert token not in parsed.text, f"{template.slug}: {token!r} survived"


@ALL
def test_no_marker_survives_into_the_narration(template: templates.Template) -> None:
    parsed = parse_script(template.read(), CAST)
    for token in ("[SFX:", "[@ ", "[VOICE:", "[CAST]"):
        assert token not in parsed.text, f"{template.slug}: {token!r} survived"


@ALL
def test_it_parses_without_complaint(template: templates.Template) -> None:
    """Warnings are for the operator's own scripts. A template that warns about
    itself is a template teaching the wrong thing.

    The dropped-heading note is expected and correct: both templates carry a
    title, and a title is a label rather than something to read aloud.
    """
    parsed = parse_script(template.read(), CAST)
    unexpected = [w for w in parsed.warnings if "Markdown heading" not in w]
    assert not unexpected, f"{template.slug}: {unexpected}"


# ---------------------------------------------------------------------------
# What each template is supposed to demonstrate
# ---------------------------------------------------------------------------


def test_the_single_voice_template_shows_cues_and_anchors() -> None:
    parsed = parse_script(templates.get("single-voice").read())
    assert parsed.slots, "no [SFX:] cue to learn from"
    assert parsed.anchors, "no [@ MM:SS] anchor to learn from"
    # One voice: nothing here should read as dialogue.
    assert not parsed.turns


def test_the_multi_voice_template_shows_a_cast_and_its_turns() -> None:
    parsed = parse_script(templates.get("multi-voice").read())
    # The inline [CAST] block stands on its own — the template works without a
    # project already being cast.
    assert set(parsed.cast) == {"Morag", "Inspector"}
    assert parsed.speakers == ["Morag", "Inspector"]
    assert len(parsed.turns) >= 4, "too few turns to show alternation"
    assert parsed.slots, "cues should work the same way here"


def test_every_turn_in_the_multi_voice_template_lands_on_its_words() -> None:
    """A turn offset becomes a forced chunk boundary, and a chunk is one request
    in one voice — so an offset that drifts gives a line to the wrong speaker."""
    parsed = parse_script(templates.get("multi-voice").read())
    for turn in parsed.turns:
        following = parsed.text[turn.offset : turn.offset + 40]
        assert following, f"turn for {turn.speaker} points past the end"
        assert following[0].isalnum() or following[0] in "\"'“‘([", (
            f"turn for {turn.speaker} lands mid-word: {following!r}"
        )


def test_the_mid_paragraph_voice_switch_is_demonstrated() -> None:
    """`[VOICE: Name]` is the harder half of the feature and easy to miss."""
    assert "[VOICE:" in templates.get("multi-voice").read()


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def test_lookup_by_slug() -> None:
    assert templates.get("single-voice").filename == "single-voice.md"


def test_an_unknown_slug_names_the_valid_ones() -> None:
    with pytest.raises(KeyError, match="single-voice"):
        templates.get("nope")


def test_slugs_are_unique() -> None:
    slugs = [t.slug for t in templates.all_templates()]
    assert len(slugs) == len(set(slugs))
