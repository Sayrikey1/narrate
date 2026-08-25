"""Chunker behaviour. These are the golden tests — the boundaries are audible."""

from __future__ import annotations

import pytest

from narrate.chunking import (
    Chunk,
    apply_prefix,
    chunk_script,
    find_markers,
    merge_chunks,
    snap_to_sentence,
    split_chunk,
    split_sentences,
)
from narrate.registry import Registry


@pytest.fixture
def registry() -> Registry:
    return Registry.load()


# -- sentence splitting -----------------------------------------------------


def test_splits_on_terminal_punctuation() -> None:
    assert split_sentences("One. Two! Three?") == ["One.", "Two!", "Three?"]


@pytest.mark.parametrize(
    "text",
    [
        "Dr. Halvorsen kept a record.",
        "The result was 3.5 degrees colder.",
        "See e.g. the second volume.",
        "Published by Acme Inc. in the spring.",
        "J. R. R. Tolkien wrote it.",
        "The meeting is at 9 a.m. tomorrow.",
    ],
)
def test_does_not_split_inside_an_abbreviation_or_number(text: str) -> None:
    """Every one of these has a period that does not end a sentence."""
    assert split_sentences(text) == [text]


def test_keeps_closing_quotes_with_the_sentence() -> None:
    result = split_sentences('"Get out," he said. She did not move.')
    assert result == ['"Get out," he said.', "She did not move."]


def test_lowercase_after_period_is_not_a_boundary() -> None:
    assert split_sentences("It arrived at 4 p.m. and left again.") == [
        "It arrived at 4 p.m. and left again."
    ]


# -- markers ----------------------------------------------------------------


def test_explicit_markers_are_honoured() -> None:
    text = "Intro line.\n\n## [CHUNK 1]\n\nFirst body.\n\n## [CHUNK 2]\n\nSecond body."
    assert find_markers(text) == ["Intro line.", "First body.", "Second body."]


def test_marker_chunks_are_labelled_as_such(registry: Registry) -> None:
    spec = registry.get("eleven_multilingual_v2")
    text = "A.\n\n## [CHUNK 1]\n\nB.\n\n## [CHUNK 2]\n\nC."
    chunks = chunk_script(text, spec)
    assert [c.source for c in chunks] == ["marker"] * 3


def test_oversized_marked_segment_is_still_subdivided(registry: Registry) -> None:
    """Honouring the writer's intent cannot extend past what the API accepts."""
    spec = registry.get("eleven_v3")
    huge = " ".join(f"Sentence number {i}." for i in range(1200))
    chunks = chunk_script(f"## [CHUNK 1]\n\n{huge}", spec)
    assert len(chunks) > 1
    assert all(c.char_count <= spec.chunk_ceiling for c in chunks)


# -- packing ----------------------------------------------------------------


def test_no_chunk_exceeds_the_ceiling(registry: Registry) -> None:
    spec = registry.get("eleven_v3")
    text = "\n\n".join(f"Paragraph {i}. " + ("word " * 200) for i in range(40))
    chunks = chunk_script(text, spec)
    assert chunks
    for c in chunks:
        assert c.char_count <= spec.chunk_ceiling, f"chunk {c.ordinal} is {c.char_count}"


def test_v3_produces_more_chunks_than_v2_for_the_same_script(registry: Registry) -> None:
    """The 5,000 vs 10,000 character limit is the whole reason model choice matters."""
    text = "\n\n".join(f"Paragraph {i}. " + ("word " * 150) for i in range(60))
    v2 = chunk_script(text, registry.get("eleven_multilingual_v2"))
    v3 = chunk_script(text, registry.get("eleven_v3"))
    assert len(v3) > len(v2)


def test_ordinals_are_contiguous_from_one(registry: Registry) -> None:
    spec = registry.get("eleven_v3")
    text = "\n\n".join(f"Paragraph {i}. " + ("word " * 100) for i in range(20))
    chunks = chunk_script(text, spec)
    assert [c.ordinal for c in chunks] == list(range(1, len(chunks) + 1))


def test_paragraph_splitting_does_not_cut_mid_sentence(registry: Registry) -> None:
    spec = registry.get("eleven_v3")
    text = "\n\n".join(f"Paragraph {i} says something complete." for i in range(200))
    chunks = chunk_script(text, spec)
    for c in chunks:
        assert c.text.rstrip().endswith("."), f"chunk {c.ordinal} ends mid-sentence"


def test_single_enormous_sentence_falls_back_to_clauses(registry: Registry) -> None:
    spec = registry.get("eleven_v3")
    sentence = ", ".join(f"clause number {i}" for i in range(900)) + "."
    chunks = chunk_script(sentence, spec)
    assert len(chunks) > 1
    assert all(c.char_count <= spec.chunk_ceiling for c in chunks)
    assert {c.source for c in chunks} <= {"clause", "hard", "sentence"}


# -- prefix tags ------------------------------------------------------------


def test_prefix_tags_count_toward_the_character_budget() -> None:
    """PRD F4: tags are billable and must be in the estimate, not stripped from it."""
    tags = "[fast-paced][urgent]"
    chunk = Chunk(1, "Hello.", "paragraph", tags)
    assert chunk.char_count == len(tags) + 1 + len("Hello.")
    assert chunk.tag_chars == len(tags) + 1
    assert chunk.submitted_text == f"{tags} Hello."


def test_tagged_chunks_still_respect_the_ceiling(registry: Registry) -> None:
    spec = registry.get("eleven_v3")
    tags = "[fast-paced][urgent][whispers]"
    text = "\n\n".join(f"Paragraph {i}. " + ("word " * 200) for i in range(40))
    chunks = chunk_script(text, spec, prefix_tags=tags)
    for c in chunks:
        assert c.char_count <= spec.chunk_ceiling


def test_apply_prefix_is_a_noop_without_tags() -> None:
    assert apply_prefix("Hello.", "") == "Hello."
    assert apply_prefix("Hello.", "   ") == "Hello."


def test_tags_that_leave_no_room_are_rejected(registry: Registry) -> None:
    spec = registry.get("eleven_v3")
    with pytest.raises(ValueError, match="leave no room"):
        chunk_script("Hello.", spec, prefix_tags="x" * (spec.chunk_ceiling + 10))


# -- manual editing ---------------------------------------------------------


def test_merge_joins_adjacent_chunks_and_renumbers() -> None:
    chunks = [Chunk(1, "A.", "paragraph"), Chunk(2, "B.", "paragraph"), Chunk(3, "C.", "paragraph")]
    merged = merge_chunks(chunks, 1, 2)
    assert [c.ordinal for c in merged] == [1, 2]
    assert merged[0].text == "A.\n\nB."
    assert merged[1].text == "C."


def test_merge_refuses_non_adjacent_chunks() -> None:
    chunks = [Chunk(i, f"{i}.", "paragraph") for i in (1, 2, 3)]
    with pytest.raises(ValueError, match="not adjacent"):
        merge_chunks(chunks, 1, 3)


def test_split_divides_at_an_offset_and_renumbers() -> None:
    chunks = [Chunk(1, "AAAA BBBB", "paragraph"), Chunk(2, "C.", "paragraph")]
    result = split_chunk(chunks, 1, 5)
    assert [c.ordinal for c in result] == [1, 2, 3]
    assert result[0].text == "AAAA"
    assert result[1].text == "BBBB"


def test_split_refuses_an_offset_that_would_empty_a_side() -> None:
    chunks = [Chunk(1, "AAAA BBBB", "paragraph")]
    with pytest.raises(ValueError):
        split_chunk(chunks, 1, 0)


# -- forced boundaries (effect markers) -------------------------------------


def test_a_forced_boundary_starts_a_new_chunk(registry: Registry) -> None:
    """An effect marker must land on a chunk edge, where the time is exact."""
    spec = registry.get("eleven_multilingual_v2")
    text = "First sentence. Second sentence. Third sentence."
    at = text.index("Second")

    chunks = chunk_script(text, spec, boundaries=[at])

    assert len(chunks) == 2
    assert chunks[0].text == "First sentence."
    assert chunks[1].text == "Second sentence. Third sentence."
    assert chunks[1].start_offset == at


def test_a_mid_sentence_boundary_snaps_to_a_sentence_start(registry: Registry) -> None:
    """A marker dropped mid-sentence must not cut the narration in half."""
    spec = registry.get("eleven_multilingual_v2")
    text = "First sentence. Second sentence runs on for a while. Third sentence."
    mid = text.index("runs")

    chunks = chunk_script(text, spec, boundaries=[mid])

    assert all(c.text.rstrip().endswith(".") for c in chunks)
    assert "Second sentence runs on for a while." in chunks[1].text


def test_boundaries_compose_with_paragraph_packing(registry: Registry) -> None:
    spec = registry.get("eleven_v3")
    text = "\n\n".join(f"Paragraph {i}. " + ("word " * 200) for i in range(20))
    at = snap_to_sentence(text, len(text) // 2)

    chunks = chunk_script(text, spec, boundaries=[at])

    assert all(c.char_count <= spec.chunk_ceiling for c in chunks)
    assert [c.ordinal for c in chunks] == list(range(1, len(chunks) + 1))
    assert any(c.start_offset == at for c in chunks)


def test_no_boundaries_behaves_exactly_as_before(registry: Registry) -> None:
    spec = registry.get("eleven_multilingual_v2")
    text = "\n\n".join(f"Paragraph {i}. Something happens here." for i in range(8))
    assert [c.text for c in chunk_script(text, spec)] == [
        c.text for c in chunk_script(text, spec, boundaries=[])
    ]


def test_first_chunk_always_knows_its_offset(registry: Registry) -> None:
    spec = registry.get("eleven_multilingual_v2")
    chunks = chunk_script("One. Two. Three.", spec)
    assert chunks[0].start_offset == 0


def test_a_boundary_at_the_very_start_or_end_is_ignored(registry: Registry) -> None:
    """There is nothing to split off at either extreme."""
    spec = registry.get("eleven_multilingual_v2")
    text = "First sentence. Second sentence."
    assert len(chunk_script(text, spec, boundaries=[0, len(text)])) == 1


def test_snap_picks_the_nearest_sentence_start() -> None:
    text = "First sentence. Second sentence. Third."
    second = text.index("Second")
    third = text.index("Third")
    assert snap_to_sentence(text, second + 2) == second
    assert snap_to_sentence(text, third - 1) == third
