"""The preflight lint: narrow on purpose, because a warning that fires on every
script teaches its reader to skip warnings."""

from __future__ import annotations

from narrate.lint import lint_chunks, stranded_words
from narrate.registry import Registry

LONG = "Which means every dollar of growth requires more of the one thing that cannot scale."


def test_the_shape_that_lost_a_word_is_flagged() -> None:
    assert stranded_words(f"{LONG} Them.") == ["Them."]


def test_a_one_word_reply_after_a_short_line_is_ordinary_rhythm() -> None:
    assert stranded_words("Did it work? No.") == []


def test_a_one_word_sentence_mid_paragraph_is_left_alone() -> None:
    assert stranded_words(f"{LONG} Them. And then it gets worse.") == []


def test_audio_tags_do_not_count_as_words() -> None:
    assert stranded_words(f"{LONG} [whispers] Them.") == ["Them."]


def test_the_warning_names_the_chunk_and_a_fix(registry: Registry) -> None:
    [warning] = lint_chunks([(5, f"{LONG} Them.")], registry.get("eleven_v3"))
    assert "'Them.' (chunk 5)" in warning
    assert "narrate verify" in warning


def test_a_stable_long_form_model_is_not_warned(registry: Registry) -> None:
    """The rule comes from an expressive model's failure; it is not evidence
    about the others."""
    assert lint_chunks([(5, f"{LONG} Them.")], registry.get("eleven_multilingual_v2")) == []


def test_tags_on_a_model_that_reads_them_aloud_are_flagged(registry: Registry) -> None:
    [warning] = lint_chunks(
        [(1, "Plain."), (2, "[urgent] Go now.")], registry.get("eleven_multilingual_v2")
    )
    assert "Chunk(s) 2" in warning
    assert "read them aloud" in warning


def test_tags_on_v3_are_what_they_are_for(registry: Registry) -> None:
    assert lint_chunks([(2, "[urgent] Go now.")], registry.get("eleven_v3")) == []
