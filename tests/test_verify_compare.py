"""The detector: which differences between script and transcript matter.

Built from word lists rather than audio, so every rule is pinned exactly and
runs in milliseconds. Two kinds of test, and the first kind matters more:

* **Quiet on noise.** 23 of the 28 raw differences in the episode that
  motivated this were the transcriber's spelling, not the narrator's mistake.
  Each pattern observed there is pinned here as producing nothing — a detector
  that cries wolf teaches its user to ignore it.
* **Loud on defects.** The four real problems — a word reduced to a noise, two
  unscripted words, a swapped word — each produce the finding a person would
  act on, from the word timings the transcriber actually returned.
"""

from __future__ import annotations

import pytest

from narrate.script_parse import spoken_text
from narrate.verify.compare import (
    FAIL,
    INFO,
    REVIEW,
    Finding,
    Word,
    canon,
    classify,
    heard_tokens,
    normalise,
    verdict,
)


def words(*spec: tuple[str, float, float, float]) -> list[Word]:
    return [Word(text=f" {t}", start=s, end=e, probability=p) for t, s, e, p in spec]


def evenly(text: str, *, start: float = 0.0, step: float = 0.3, p: float = 0.99) -> list[Word]:
    """A transcript that heard exactly `text`, one word every `step` seconds."""
    return [
        Word(text=f" {w}", start=start + i * step, end=start + i * step + step * 0.9, probability=p)
        for i, w in enumerate(text.split())
    ]


def problems(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity != INFO]


# --------------------------------------------------------------------------
# Quiet on noise — every false-positive pattern from the real episode
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "heard"),
    [
        ("It costs $10 million.", "It costs ten million dollars."),
        ("We need 200,000 of them.", "We need two hundred thousand of them."),
        ("It works 100% of the time.", "It works one hundred percent of the time."),
        ("That was in 1998.", "That was in nineteen ninety-eight."),
        ("I love the catalogue.", "I love the catalog."),
        ("A personalised favour.", "A personalized favor."),
        ("Here's the Cashflow Quadrant.", "Here is the cash flow quadrant."),
        ("He used O.P.T. for it.", "He used OPT for it."),
        ("The B quadrant.", "The bee quadrant."),
        ("I read it again.", "I read it again."),
    ],
)
def test_a_transcriber_spelling_is_not_a_defect(written: str, heard: str) -> None:
    assert problems(classify(written, evenly(heard))) == []


def test_a_name_heard_as_two_words_is_not_a_defect() -> None:
    """ "Ardnamurchan" came back as "Ardna merchant" on held-out audio; the two
    sides are 0.88 alike as strings, which is one name, not an extra word."""
    found = classify(
        "We sailed past Ardnamurchan at dawn.", evenly("We sailed past Ardna merchant at dawn.")
    )
    assert problems(found) == []


def test_a_low_confidence_phantom_after_a_long_word_is_info() -> None:
    """small.en tacks a near-zero-confidence word onto a sentence-final word
    ("product." + "act"). It is a known phantom, not something to act on."""
    heard = words(
        ("The", 0.0, 0.2, 0.99),
        ("entire", 0.2, 0.6, 0.99),
        ("product.", 0.6, 1.1, 0.99),
        ("act,", 1.1, 1.2, 0.002),
    )
    found = classify("The entire product.", heard)
    assert problems(found) == []
    assert [f.kind for f in found] == ["extra"]


def test_the_same_script_heard_perfectly_finds_nothing() -> None:
    text = "Which means every dollar of growth requires more of the one thing that cannot scale."
    assert classify(text, evenly(text)) == []
    assert verdict([]) == "clear"


# --------------------------------------------------------------------------
# Loud on defects — the episode's real problems
# --------------------------------------------------------------------------


def test_the_dropped_them_is_a_fail_from_the_real_word_timings() -> None:
    """Chunk 5, as faster-whisper small.en actually heard it: "scale." ends at
    82.90s, then nothing is recognised until "So" at 83.82s."""
    heard = words(
        ("that", 81.92, 82.16, 1.00),
        ("cannot", 82.16, 82.44, 0.99),
        ("scale.", 82.44, 82.90, 1.00),
        ("So", 83.82, 84.34, 0.73),
        ("they", 84.34, 84.52, 0.85),
        ("raise", 84.52, 84.76, 0.99),
        ("rates.", 84.76, 85.12, 1.00),
    )
    found = problems(classify("that cannot scale. Them. So they raise rates.", heard))
    assert len(found) == 1
    [missing] = found
    assert missing.severity == FAIL
    assert missing.kind == "missing"
    assert missing.expected == "them"
    assert missing.gap_s == pytest.approx(0.92, abs=0.01)
    assert missing.start_s == pytest.approx(82.90)


def test_a_dropped_function_word_with_no_hole_is_left_alone() -> None:
    """A "the" that simply ran into its neighbour is more often the
    transcriber's than the narrator's."""
    found = classify("She went to the shop today.", evenly("She went to shop today."))
    assert [f.severity for f in found] == [INFO]


def test_a_dropped_content_word_fails_even_with_no_hole() -> None:
    """Spliced out mid-sentence, a real word leaves no gap at all — the gap rule
    alone missed this in the reviewer's built-in defect set."""
    found = problems(
        classify("It is a decision about what you want.", evenly("It is a about what you want."))
    )
    assert [(f.severity, f.kind, f.expected) for f in found] == [(FAIL, "missing", "decision")]


def test_a_neighbour_stretched_over_the_hole_still_fails() -> None:
    """base.en stretched "So" 0.8s over the missing "Them" — no gap, but a long,
    unsure word where a short one belongs."""
    heard = words(
        ("cannot", 0.0, 0.3, 0.99),
        ("scale.", 0.3, 0.7, 0.99),
        ("So", 0.7, 1.5, 0.05),
        ("they", 1.5, 1.7, 0.99),
    )
    found = problems(classify("cannot scale. Them. So they", heard))
    assert [(f.severity, f.expected) for f in found] == [(FAIL, "them")]


def test_an_unscripted_word_said_confidently_is_a_fail_to_confirm() -> None:
    """The episode's "God," — p=0.94, nowhere in the script."""
    heard = words(
        ("ever", 25.0, 25.3, 0.99),
        ("been.", 25.3, 25.64, 0.99),
        ("God,", 26.30, 26.80, 0.94),
        ("the", 26.80, 26.94, 0.99),
        ("modern", 26.94, 27.3, 0.99),
        ("S", 27.3, 27.5, 0.99),
    )
    found = problems(classify("ever been. The modern S", heard))
    assert [(f.severity, f.kind, f.heard, f.confirm) for f in found] == [
        (FAIL, "extra", "god", True)
    ]


def test_an_extra_word_merged_with_its_neighbour_is_still_seen() -> None:
    """ "Good hears the" for "here's the": a plain diff folded the extra "Good"
    into a substitution and hid it. The scored alignment keeps it an insertion."""
    heard = words(
        ("Good", 36.0, 36.13, 0.96),
        ("hears", 36.14, 36.40, 0.90),
        ("the", 36.42, 36.55, 0.99),
        ("honest", 36.55, 36.9, 0.99),
        ("bit", 36.9, 37.1, 0.99),
    )
    found = problems(classify("Here's the honest bit", heard))
    assert any(f.kind == "extra" and f.heard == "good" and f.severity == FAIL for f in found)


def test_an_uncertain_extra_word_asks_for_a_second_listen() -> None:
    heard = words(("The", 0.0, 0.2, 0.99), ("end.", 0.2, 0.6, 0.99), ("However", 0.6, 0.9, 0.21))
    [finding] = problems(classify("The end.", heard))
    assert (finding.severity, finding.confirm) == (REVIEW, True)


def test_a_confidently_swapped_word_is_worth_a_listen() -> None:
    """Chunk 3 said "Our problem" for "The problem" — similarity 0.0, p=0.98."""
    heard = evenly("Our problem isn't the job.", p=0.98)
    [finding] = problems(classify("The problem isn't the job.", heard))
    assert (finding.severity, finding.kind, finding.expected, finding.heard) == (
        REVIEW,
        "reworded",
        "the",
        "our",
    )
    assert finding.summary == 'said "our" where the script says "the"'


def test_a_plural_or_tense_change_is_hidden_by_design() -> None:
    """Documented blind spot: "instrument" spoken as "instruments" looks
    exactly like the transcriber's own inflection errors, so it stays info."""
    found = classify(
        "Those are not the same instrument.", evenly("Those are not the same instruments.")
    )
    assert problems(found) == []


def test_a_difference_in_numbers_alone_is_never_more_than_review() -> None:
    """Numbers were the largest single source of false alarms."""
    found = classify("It cost 40 dollars.", evenly("It cost 14 dollars."))
    assert all(f.severity != FAIL for f in found)


def test_a_long_voiced_word_is_flagged_only_when_the_audio_agrees() -> None:
    """A repeated phrase the transcriber collapsed into one long word. Without a
    measure of voiced time the rule is skipped rather than guessed."""
    heard = words(
        ("what", 0.0, 0.3, 0.99),
        ("you're", 0.3, 0.5, 0.99),
        ("willing", 0.5, 0.9, 0.99),
        ("to", 0.9, 2.1, 0.51),
        ("do.", 2.1, 2.4, 0.99),
    )
    text = "what you're willing to do."
    assert problems(classify(text, heard)) == []
    loud = problems(classify(text, heard, voiced=lambda a, b: b - a))
    assert [f.kind for f in loud] == ["stretched"]


# --------------------------------------------------------------------------
# The plumbing the rules depend on
# --------------------------------------------------------------------------


def test_audio_tags_are_directions_not_words() -> None:
    """A v3 take is sent "[fast-paced][urgent] …"; none of that is spoken."""
    sent = "[fast-paced][urgent] Your job is expensive. [building energy] Them."
    expected = spoken_text(sent, audio_tags=True)
    assert classify(expected, evenly("Your job is expensive. Them.")) == []


def test_a_model_without_tags_reads_them_aloud_and_is_expected_to() -> None:
    """On a model that does not honour tags the brackets are spoken, so the
    honest expectation keeps them."""
    assert spoken_text("[urgent] Go.", audio_tags=False) == "[urgent] Go."


def test_timings_survive_a_number_phrase_that_shrinks() -> None:
    """ "a hundred and five" normalises to one token; mapping tokens to words by
    position used to shift every later timestamp by one."""
    heard = words(
        ("It", 0.0, 0.2, 0.99),
        ("was", 0.2, 0.4, 0.99),
        ("a", 0.4, 0.5, 0.99),
        ("hundred", 0.5, 0.8, 0.99),
        ("and", 0.8, 0.9, 0.99),
        ("five", 0.9, 1.2, 0.99),
        ("degrees.", 1.2, 1.8, 0.99),
    )
    tokens = heard_tokens(heard)
    assert tokens[-1].token == "degrees"
    assert tokens[-1].start == pytest.approx(1.2)


def test_canon_joins_what_the_normaliser_leaves_apart() -> None:
    assert canon(normalise("O.P.T.")) == canon(normalise("OPT"))
    assert canon(normalise("cash flow")) == canon(normalise("cashflow"))


@pytest.mark.parametrize(
    ("severities", "expected"),
    [((), "clear"), ((INFO,), "clear"), ((REVIEW, INFO), "review"), ((FAIL, REVIEW), "suspect")],
)
def test_a_takes_verdict_is_its_worst_finding(severities: tuple[str, ...], expected: str) -> None:
    assert verdict([Finding(severity=s, kind="x") for s in severities]) == expected


def test_a_finding_round_trips_through_storage() -> None:
    finding = Finding(severity=FAIL, kind="missing", expected="them", start_s=82.9, gap_s=0.92)
    assert Finding.from_dict(finding.as_dict()) == finding


def test_a_finding_at_the_very_start_keeps_its_time() -> None:
    """`0.0 == False` in Python; filtering empty fields by equality dropped the
    time of any finding at the start of a take, and the UI showed NaN:NaN."""
    stored = Finding(severity=FAIL, kind="missing", expected="them", start_s=0.0).as_dict()
    assert stored["start_s"] == 0.0


def test_a_long_missing_span_is_summarised_by_its_ends() -> None:
    """A truncated take can miss hundreds of words; the summary has to say
    where, not recite them."""
    span = " ".join(f"word{i}" for i in range(40))
    summary = Finding(severity=FAIL, kind="missing", expected=span, gap_s=28.6).summary
    assert summary.startswith('"word0 word1')
    assert "word39" in summary
    assert "word20" not in summary
    assert summary.endswith("40 words")


# --------------------------------------------------------------------------
# Found in review
# --------------------------------------------------------------------------


def test_words_in_parentheses_are_expected_to_be_spoken() -> None:
    """Whisper's normaliser deletes bracketed text — right for its own sound
    annotations, wrong for an aside a narrator reads aloud."""
    assert (
        problems(classify("He said (and meant it) no.", evenly("He said and meant it no."))) == []
    )


def test_a_symbol_read_as_a_word_is_not_an_extra_word() -> None:
    found = classify("Research & development matters.", evenly("Research and development matters."))
    assert problems(found) == []


def test_an_extra_word_beside_a_respelled_compound_is_still_caught() -> None:
    """ "Cashflow" heard as "cash flow" is spelling; "cash flow god" hides a real
    extra word inside it, which the similarity rule alone would swallow."""
    found = problems(
        classify("Here's the Cashflow Quadrant.", evenly("Here is the cash flow god quadrant."))
    )
    assert [(f.kind, f.heard) for f in found] == [("extra", "god")]


def test_a_missing_word_beside_a_respelled_compound_is_still_caught() -> None:
    found = problems(classify("The Cashflow decision Quadrant.", evenly("The cash flow quadrant.")))
    assert [(f.kind, f.expected) for f in found] == [("missing", "decision")]


def test_a_number_phrase_is_long_without_being_a_merged_repeat() -> None:
    """ "nineteen ninety eight" is one token from three words; its length is
    not a sign of a collapsed repeat."""
    heard = words(
        ("It", 0.0, 0.2, 0.99),
        ("was", 0.2, 0.4, 0.99),
        ("nineteen", 0.4, 0.9, 0.5),
        ("ninety", 0.9, 1.3, 0.5),
        ("eight", 1.3, 1.6, 0.5),
        ("then.", 1.6, 1.9, 0.99),
    )
    assert problems(classify("It was 1998 then.", heard, voiced=lambda a, b: b - a)) == []


# --------------------------------------------------------------------------
# Found checking the review fixes
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("script", "spoken"),
    [
        ("It costs $100/hour to run.", "It costs $100 an hour to run."),
        ("It costs $100/hour to run.", "It costs a hundred dollars per hour to run."),
        ("We took 3-4 days.", "We took three to four days."),
        ("We took 3–4 days.", "We took 3 to 4 days."),
        ("They raised $5M.", "They raised five million dollars."),
        ("They raised £5m.", "They raised five million pounds."),
        ("A $50k budget.", "A fifty thousand dollar budget."),
        ("It cost $2bn.", "It cost two billion dollars."),
        ("Book a 1:1 meeting.", "Book a one-on-one meeting."),
        ("Cars, boats, etc.", "Cars, boats, et cetera."),
        ("The #1 mistake.", "The number one mistake."),
    ],
)
def test_written_shorthand_read_out_in_full_is_not_a_defect(script: str, spoken: str) -> None:
    assert problems(classify(script, evenly(spoken))) == []


def test_shorthand_is_not_expanded_inside_a_name() -> None:
    """ "B2B" is not "B two billion", and a 401k is a pension, not 401,000."""
    assert problems(classify("A B2B firm.", evenly("A B2B firm."))) == []
    assert problems(classify("Your 401(k) plan.", evenly("Your 401k plan."))) == []


def test_a_year_is_one_token_but_several_spoken_words() -> None:
    """Whisper writes "twenty twenty-four" as one token, "2024"; its length is
    not a sign of a stretched word."""
    heard = words(
        ("In", 0.0, 0.2, 0.99),
        ("2024", 0.2, 1.7, 0.55),
        ("we", 1.8, 2.0, 0.99),
        ("doubled.", 2.0, 2.4, 0.99),
    )
    assert problems(classify("In 2024 we doubled.", heard, voiced=lambda a, b: b - a)) == []


def test_a_stretched_word_is_still_one_to_listen_to() -> None:
    heard = words(("So", 0.0, 1.2, 0.3), ("we", 1.3, 1.5, 0.99), ("left.", 1.5, 1.9, 0.99))
    found = problems(classify("So we left.", heard, voiced=lambda a, b: b - a))
    assert [f.kind for f in found] == ["stretched"]


def test_a_dropped_word_beside_an_inflection_is_still_caught() -> None:
    """ "instrument them" heard as "instruments" and a hole: the plural is the
    transcriber's, the hole is the missing word."""
    heard = evenly("He never paid the instruments.") + evenly("So we left.", start=2.4)
    found = problems(classify("He never paid the instrument them. So we left.", heard))
    assert [(f.kind, f.expected) for f in found] == [("missing", "them")]
    assert found[0].gap_s and found[0].gap_s >= 0.8


def test_an_extra_word_beside_a_name_spelled_nearly_alike_is_still_caught() -> None:
    found = problems(
        classify(
            "We sailed past Ardnamurchan point.", evenly("We sailed past Ardna merchant so point.")
        )
    )
    assert [(f.kind, f.heard) for f in found] == [("extra", "so")]


def test_a_name_spelled_nearly_alike_is_only_spelling() -> None:
    for spoken in ("We sailed past Ardna merchant point.", "We sailed past Ardnamurcan point."):
        assert problems(classify("We sailed past Ardnamurchan point.", evenly(spoken))) == []


# --------------------------------------------------------------------------
# Found reviewing the shorthand rules: narrow, or they cry wolf
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("script", "spoken"),
    [
        ("It was a 50-50 split.", "It was a fifty-fifty split."),
        ("We are open 24-7.", "We are open twenty-four seven."),
        ("They won 2-0 at home.", "They won two-nil at home."),
        ("Call 555-1234 today.", "Call five five five one two three four today."),
        ("I was in seat 12B on the flight.", "I was in seat 12 B on the flight."),
        ("Companies like 3M grew.", "Companies like three M grew."),
        ("A hundred years of solitude.", "A Hundred Years of Solitude."),
        ("It weighed a hundred pounds.", "It weighed a 100 pounds."),
        ("A thousand years ago.", "A 1000 years ago."),
    ],
)
def test_clean_readings_of_numbers_stay_quiet(script: str, spoken: str) -> None:
    assert [f for f in classify(script, evenly(spoken)) if f.severity == FAIL] == []


@pytest.mark.parametrize(
    ("script", "misread"),
    [
        ("Companies like 3M and GE grew.", "Companies like three million and GE grew."),
        ("I was in seat 12B on the flight.", "I was in seat twelve billion on the flight."),
    ],
)
def test_an_identifier_read_as_a_quantity_is_still_worth_a_listen(
    script: str, misread: str
) -> None:
    assert problems(classify(script, evenly(misread)))


def test_two_dropped_words_are_not_hidden_behind_an_inflection() -> None:
    found = problems(
        classify("He paid the instrument them in full.", evenly("He paid instruments in full."))
    )
    assert any(f.severity == FAIL and f.kind == "missing" for f in found)


def test_a_stretched_single_digit_is_still_worth_a_listen() -> None:
    heard = words(
        ("I", 0.0, 0.2, 0.99),
        ("have", 0.2, 0.5, 0.99),
        ("2", 0.5, 1.8, 0.3),
        ("kids.", 1.8, 2.2, 0.99),
    )
    found = problems(classify("I have 2 kids.", heard, voiced=lambda a, b: b - a))
    assert [f.kind for f in found] == ["stretched"]


def test_a_bare_figure_with_a_letter_is_worth_a_listen_not_a_verdict() -> None:
    """ "50k views" read as "fifty thousand" is right; "3M" read as "three
    million" is wrong. Without a currency sign the text cannot say which, so
    either is a listen, never a suspect."""
    for script, spoken in (
        ("It has 50k views.", "It has fifty thousand views."),
        ("Companies like 3M grew.", "Companies like three million grew."),
    ):
        found = problems(classify(script, evenly(spoken)))
        assert found and all(f.severity == REVIEW for f in found)


def test_a_word_dropped_either_side_of_a_respelled_name_is_caught() -> None:
    found = problems(
        classify("We sailed past them Ardnamurchan now.", evenly("We sailed past Ardna merchant."))
    )
    assert [(f.severity, f.kind, f.expected) for f in found] == [(FAIL, "missing", "them now")]


def test_a_score_read_aloud_is_not_a_missing_word() -> None:
    assert [
        f
        for f in classify("They won 2-0 at home.", evenly("They won two-zero at home."))
        if f.severity == FAIL
    ] == []
