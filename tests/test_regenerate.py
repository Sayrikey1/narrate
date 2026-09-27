"""Generating a chunk again: the price, the refusals, and where the cut ends up.

A regeneration is a fresh sample and can introduce a defect as easily as fix
one, so the rule under test throughout is that the cut moves only when the new
take has *earned* it — and that nothing is ever deleted, so a regeneration that
turned out worse costs only its own price.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import Engine, select

from narrate import produce as produce_mod
from narrate import regenerate as regen
from narrate.db.models import Chunk, Cut, Project, Take
from narrate.db.session import session_scope
from narrate.provider.mock import MockProvider, MockSFXProvider
from narrate.registry import Registry
from narrate.runner import generate
from narrate.settings import Settings
from narrate.verify import service

from .conftest import needs_ffmpeg, require
from .verify_support import HearsTheScript

pytestmark = needs_ffmpeg

CHUNK = 2
FIRST = f"chunk-{CHUNK:03d}-take-01.mp3"
SECOND = f"chunk-{CHUNK:03d}-take-02.mp3"
THIRD = f"chunk-{CHUNK:03d}-take-03.mp3"


async def _episode(
    engine: Engine, script_id: int, settings: Settings, registry: Registry, hears: HearsTheScript
) -> None:
    """Generate the script and check every take, as a user would before regenerating."""
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)
    service.verify_script(engine, script_id, hears, registry)


async def _regenerate(
    engine: Engine,
    script_id: int,
    settings: Settings,
    registry: Registry,
    hears: HearsTheScript,
    provider: MockProvider | None = None,
    **options: object,
) -> regen.ChunkResult:
    [result] = await regen.regenerate(
        engine,
        script_id,
        [CHUNK],
        provider or MockProvider(),
        MockSFXProvider(),
        registry,
        settings,
        transcriber=hears,
        **options,  # type: ignore[arg-type]
    )
    return result


def _cut_take_ordinal(engine: Engine, script_id: int) -> int:
    with session_scope(engine) as session:
        chunk = require(
            session.scalar(
                select(Chunk).where(Chunk.script_id == script_id, Chunk.ordinal == CHUNK)
            )
        )
        cut = require(session.get(Cut, chunk.id))
        return require(session.get(Take, cut.take_id)).ordinal


def _takes(engine: Engine) -> int:
    with session_scope(engine) as session:
        return len(session.scalars(select(Take)).all())


# --------------------------------------------------------------------------
# The price, before anything is sent
# --------------------------------------------------------------------------


async def test_a_forced_rerun_of_a_finished_chunk_is_priced_not_skipped(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """The bug this fixes: the confirmation gate priced a re-roll of a chunk
    that already had a take at nothing, concluded there was nothing to do, and
    exited without sending anything."""
    _, script_id = project_and_script
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)

    idle = produce_mod.project(engine, script_id, registry, with_effects=False)
    forced = produce_mod.project(
        engine, script_id, registry, with_effects=False, only=[CHUNK], force=True
    )

    assert idle.is_empty
    assert forced.chunks == 1
    assert forced.speech_micros > 0


async def test_the_plan_prices_each_chunk_and_spends_nothing(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)
    before = _takes(engine)

    [plan] = regen.plan(engine, script_id, registry, chunks=[CHUNK])

    assert plan.ordinal == CHUNK
    assert plan.price_micros == registry.get(plan.model_id).cost_micros(plan.chars)
    assert plan.cut_take == 1
    assert _takes(engine) == before


def test_an_unknown_chunk_is_refused_by_name(
    engine: Engine, project_and_script: tuple[int, int], registry: Registry
) -> None:
    _, script_id = project_and_script
    with pytest.raises(regen.RegenerateRefused, match="no chunk 99"):
        regen.plan(engine, script_id, registry, chunks=[99])


# --------------------------------------------------------------------------
# Where the cut ends up
# --------------------------------------------------------------------------


async def test_a_flagged_take_is_replaced_by_a_clean_one(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    hears = HearsTheScript(engine, drops={FIRST: "halvorsen"})
    await _episode(engine, script_id, settings, registry, hears)

    result = await _regenerate(engine, script_id, settings, registry, hears)

    assert result.statuses == ["clear"]
    assert result.moved
    assert _cut_take_ordinal(engine, script_id) == 2
    with session_scope(engine) as session:
        # Nothing is deleted: the flagged take is still there to go back to.
        ordinals = sorted(
            t.ordinal
            for t in session.scalars(select(Take).join(Chunk).where(Chunk.ordinal == CHUNK))
        )
    assert ordinals == [1, 2]


async def test_a_take_you_simply_did_not_like_stays_until_you_choose(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """Two clean takes differ in things no check can judge — delivery, emphasis.
    Listening is the only way to choose, so the choice stays with the listener."""
    _, script_id = project_and_script
    hears = HearsTheScript(engine)
    await _episode(engine, script_id, settings, registry, hears)

    result = await _regenerate(engine, script_id, settings, registry, hears)

    assert result.statuses == ["clear"]
    assert not result.moved
    assert _cut_take_ordinal(engine, script_id) == 1


async def test_use_new_moves_the_cut_unless_the_new_take_is_worse(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    hears = HearsTheScript(engine)
    await _episode(engine, script_id, settings, registry, hears)

    result = await _regenerate(
        engine, script_id, settings, registry, hears, move_cut=regen.MOVE_NEW
    )

    assert result.moved
    assert _cut_take_ordinal(engine, script_id) == 2


async def test_a_worse_new_take_never_replaces_a_clean_one(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    hears = HearsTheScript(engine, drops={SECOND: "halvorsen"})
    await _episode(engine, script_id, settings, registry, hears)

    result = await _regenerate(
        engine, script_id, settings, registry, hears, move_cut=regen.MOVE_NEW
    )

    assert result.statuses == ["suspect"]
    assert not result.moved
    assert _cut_take_ordinal(engine, script_id) == 1


async def test_keep_cut_never_moves_it(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    hears = HearsTheScript(engine, drops={FIRST: "halvorsen"})
    await _episode(engine, script_id, settings, registry, hears)

    result = await _regenerate(
        engine, script_id, settings, registry, hears, move_cut=regen.MOVE_NEVER
    )

    assert result.statuses == ["clear"]
    assert _cut_take_ordinal(engine, script_id) == 1


async def test_retrying_stops_at_the_first_clean_take(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """Each try is a separate, billed request; it stops as soon as one is clean."""
    _, script_id = project_and_script
    hears = HearsTheScript(engine, drops={FIRST: "halvorsen", SECOND: "halvorsen"})
    await _episode(engine, script_id, settings, registry, hears)
    provider = MockProvider()

    result = await _regenerate(
        engine, script_id, settings, registry, hears, provider=provider, attempts=3
    )

    assert result.statuses == ["suspect", "clear"]
    assert len(provider.calls) == 2
    assert _cut_take_ordinal(engine, script_id) == 3


async def test_attempts_are_capped_however_they_are_asked_for(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    always = {f"chunk-{CHUNK:03d}-take-{n:02d}.mp3": "halvorsen" for n in range(1, 10)}
    hears = HearsTheScript(engine, drops=always)
    await _episode(engine, script_id, settings, registry, hears)
    provider = MockProvider()

    result = await _regenerate(
        engine, script_id, settings, registry, hears, provider=provider, attempts=50
    )

    assert len(provider.calls) == regen.MAX_ATTEMPTS
    assert result.statuses == ["suspect"] * regen.MAX_ATTEMPTS
    assert _cut_take_ordinal(engine, script_id) == 1


# --------------------------------------------------------------------------
# The refusals that stand between a button and a double charge
# --------------------------------------------------------------------------


async def test_a_request_with_an_unknown_outcome_blocks_regeneration(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """Sent but never answered, it may already have been billed; regenerating
    on top of it could pay twice for one line."""
    _, script_id = project_and_script
    hears = HearsTheScript(engine, drops={FIRST: "halvorsen"})
    await _episode(engine, script_id, settings, registry, hears)
    with session_scope(engine) as session:
        chunk = require(session.scalar(select(Chunk).where(Chunk.ordinal == CHUNK)))
        marker = chunk.text.split()[0]
    await generate(
        engine,
        script_id,
        MockProvider(fail_containing={marker}, failure_mode="unknown"),
        registry,
        settings,
        dry_run=False,
        only=[CHUNK],
        force=True,
    )
    provider = MockProvider()

    result = await _regenerate(engine, script_id, settings, registry, hears, provider=provider)

    assert provider.calls == []
    assert result.stopped and "reconcile" in result.stopped
    [plan] = regen.plan(engine, script_id, registry, chunks=[CHUNK])
    assert plan.blocker is not None


async def test_a_spending_limit_below_the_price_sends_nothing(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    hears = HearsTheScript(engine, drops={FIRST: "halvorsen"})
    await _episode(engine, script_id, settings, registry, hears)
    provider = MockProvider()

    result = await _regenerate(
        engine, script_id, settings, registry, hears, provider=provider, max_spend_micros=1
    )

    assert provider.calls == []
    assert result.spent_micros == 0
    assert result.stopped and "limit" in result.stopped


async def test_resuming_after_a_regeneration_spends_nothing(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    hears = HearsTheScript(engine, drops={FIRST: "halvorsen"})
    await _episode(engine, script_id, settings, registry, hears)
    await _regenerate(engine, script_id, settings, registry, hears)
    provider = MockProvider()

    await generate(engine, script_id, provider, registry, settings, dry_run=False)

    assert provider.calls == []


# --------------------------------------------------------------------------
# Rewording a chunk first
# --------------------------------------------------------------------------


async def test_rewording_is_refused_where_it_would_rebill_the_neighbours(
    engine: Engine, project_and_script: tuple[int, int], registry: Registry
) -> None:
    """The fixture's model sends neighbouring text with each request, so
    changing one chunk changes what the next run would send for its neighbours."""
    _, script_id = project_and_script
    assert registry.get("eleven_multilingual_v2").continuity_mode != "none"
    with pytest.raises(regen.RegenerateRefused, match="would change what chunk"):
        regen.edit_text(engine, script_id, CHUNK, "Entirely new words.", registry)
    with pytest.raises(regen.RegenerateRefused, match="would change what chunk"):
        regen.plan(engine, script_id, registry, chunks=[CHUNK], texts={CHUNK: "New."})


def _on_v3(engine: Engine) -> None:
    with session_scope(engine) as session:
        require(session.scalar(select(Project))).model_id = "eleven_v3"


async def test_rewording_on_v3_replaces_the_words_and_is_priced_on_them(
    engine: Engine, project_and_script: tuple[int, int], registry: Registry
) -> None:
    _, script_id = project_and_script
    _on_v3(engine)
    new = "[whispers] Them. It was always them."

    [plan] = regen.plan(engine, script_id, registry, chunks=[CHUNK], texts={CHUNK: new})
    assert plan.chars == len(new)

    old = regen.edit_text(engine, script_id, CHUNK, new, registry)
    with session_scope(engine) as session:
        chunk = require(session.scalar(select(Chunk).where(Chunk.ordinal == CHUNK)))
        # Audio tags survive, exactly as they would on ingest.
        assert chunk.text == new
    assert old != new


async def test_a_marker_cannot_be_smuggled_into_one_chunk(
    engine: Engine, project_and_script: tuple[int, int], registry: Registry
) -> None:
    """Effect cues, anchors, speakers and chapters are the script's structure."""
    _, script_id = project_and_script
    _on_v3(engine)
    with pytest.raises(regen.RegenerateRefused, match="marker"):
        regen.edit_text(engine, script_id, CHUNK, "Words. [SFX: thunder] More.", registry)


async def test_markdown_in_new_words_is_stripped_like_a_script(
    engine: Engine, project_and_script: tuple[int, int], registry: Registry
) -> None:
    _, script_id = project_and_script
    _on_v3(engine)
    regen.edit_text(engine, script_id, CHUNK, "It was **always** them.", registry)
    with session_scope(engine) as session:
        chunk = require(session.scalar(select(Chunk).where(Chunk.ordinal == CHUNK)))
        assert chunk.text == "It was always them."


# --------------------------------------------------------------------------
# Found in review
# --------------------------------------------------------------------------


async def test_a_v3_chunk_with_stitched_neighbours_cannot_be_reworded(
    engine: Engine, project_and_script: tuple[int, int], registry: Registry
) -> None:
    """The edited chunk's own model is not the whole story: its neighbours'
    requests carry its words when *their* model stitches."""
    _, script_id = project_and_script
    with session_scope(engine) as session:
        chunk = require(session.scalar(select(Chunk).where(Chunk.ordinal == CHUNK)))
        chunk.model_id = "eleven_v3"
    with pytest.raises(regen.RegenerateRefused, match="chunk\\(s\\) 3"):
        regen.plan(engine, script_id, registry, chunks=[CHUNK], texts={CHUNK: "New words."})


async def test_a_conversation_cannot_be_reworded_as_one_block(
    engine: Engine, project_and_script: tuple[int, int], registry: Registry
) -> None:
    """Its words live in its speaker turns; rewording the block would send the
    old lines again, priced as the new ones."""
    _, script_id = project_and_script
    _on_v3(engine)
    with session_scope(engine) as session:
        chunk = require(session.scalar(select(Chunk).where(Chunk.ordinal == CHUNK)))
        chunk.turns_json = '[{"speaker": "Ada", "voice_id": "v1", "text": "Hello."}]'
    with pytest.raises(regen.RegenerateRefused, match="conversation"):
        regen.edit_text(engine, script_id, CHUNK, "Goodbye.", registry)


def test_an_empty_selection_is_refused(
    engine: Engine, project_and_script: tuple[int, int], registry: Registry
) -> None:
    _, script_id = project_and_script
    with pytest.raises(regen.RegenerateRefused, match="at least one chunk"):
        regen.plan(engine, script_id, registry, chunks=[])


async def test_an_unchecked_take_never_replaces_a_flagged_one(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """Without a speech model the new take is unverified — not evidence that it
    is better, so under the default rule the cut stays."""
    _, script_id = project_and_script
    hears = HearsTheScript(engine, drops={FIRST: "halvorsen"})
    await _episode(engine, script_id, settings, registry, hears)

    [result] = await regen.regenerate(
        engine, script_id, [CHUNK], MockProvider(), MockSFXProvider(), registry, settings
    )

    assert result.new_takes
    assert not result.moved
    assert _cut_take_ordinal(engine, script_id) == 1


async def test_the_quoted_maximum_is_the_limit(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """Nothing can cost more than the figure confirmed: with no explicit limit,
    the quote is the limit."""
    _, script_id = project_and_script
    hears = HearsTheScript(engine, drops={FIRST: "halvorsen"})
    await _episode(engine, script_id, settings, registry, hears)
    [plan] = regen.plan(engine, script_id, registry, chunks=[CHUNK])

    result = await _regenerate(engine, script_id, settings, registry, hears)

    assert 0 < result.spent_micros <= plan.price_micros


async def test_a_zero_limit_is_a_limit(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    hears = HearsTheScript(engine, drops={FIRST: "halvorsen"})
    await _episode(engine, script_id, settings, registry, hears)
    provider = MockProvider()

    result = await _regenerate(
        engine, script_id, settings, registry, hears, provider=provider, max_spend_micros=0
    )

    assert provider.calls == []
    assert result.spent_micros == 0


async def test_a_chunks_own_tags_are_in_its_price(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """The price is the request the runner will send — its own model and tags."""
    _, script_id = project_and_script
    _on_v3(engine)
    [before] = regen.plan(engine, script_id, registry, chunks=[CHUNK], settings=settings)
    with session_scope(engine) as session:
        chunk = require(session.scalar(select(Chunk).where(Chunk.ordinal == CHUNK)))
        chunk.prefix_tags = "[whispers][slowly, with long pauses between the phrases]"
    [after] = regen.plan(engine, script_id, registry, chunks=[CHUNK], settings=settings)
    assert (
        after.chars
        == before.chars + len("[whispers][slowly, with long pauses between the phrases]") + 1
    )


class _Broken:
    identity = "broken"

    def transcribe(self, path: object, window: object = None) -> list[object]:
        raise RuntimeError("the model file is truncated")


async def test_a_speech_model_failure_after_a_paid_take_is_recorded_not_raised(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """The take is already paid for; a crash would hide what was spent."""
    _, script_id = project_and_script
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)

    [result] = await regen.regenerate(
        engine,
        script_id,
        [CHUNK],
        MockProvider(),
        MockSFXProvider(),
        registry,
        settings,
        transcriber=_Broken(),  # type: ignore[arg-type]
    )

    assert result.statuses == ["error"]
    assert result.spent_micros > 0
    assert not result.moved


async def test_a_cut_the_runner_had_to_move_is_reported_as_moved(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """With the cut's audio gone the runner uses the new take — there is
    nothing else to play — and the report must say so, even under keep-cut."""
    _, script_id = project_and_script
    hears = HearsTheScript(engine)
    await _episode(engine, script_id, settings, registry, hears)
    with session_scope(engine) as session:
        chunk = require(session.scalar(select(Chunk).where(Chunk.ordinal == CHUNK)))
        cut = require(session.get(Cut, chunk.id))
        require(session.get(Take, cut.take_id)).asset_path = "/nowhere/gone.mp3"

    result = await _regenerate(
        engine, script_id, settings, registry, hears, move_cut=regen.MOVE_NEVER
    )

    assert result.moved
    assert result.cut_note and "no playable take" in result.cut_note


# --------------------------------------------------------------------------
# Found checking the review fixes
# --------------------------------------------------------------------------


async def test_a_take_only_the_waveform_checked_never_counts_as_better(
    engine: Engine,
    project_and_script: tuple[int, int],
    settings: Settings,
    registry: Registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a speech model the waveform check can still mark a new take
    "review" — it found a burst. That says nothing about whether the words are
    right (the burst is itself the shape of a garble), so it must not replace a
    take a transcript flagged."""
    _, script_id = project_and_script
    hears = HearsTheScript(engine, drops={FIRST: "halvorsen"})
    await _episode(engine, script_id, settings, registry, hears)

    def burst_found(
        engine: Engine, script_id: int, take_id: int, transcriber: object, registry: Registry
    ) -> str:
        with session_scope(engine) as session:
            take = require(session.get(Take, take_id))
            take.verify_status = "review"
            take.verifier = service.stamp(None)
        return "review"

    monkeypatch.setattr(regen, "_check", burst_found)
    [result] = await regen.regenerate(
        engine, script_id, [CHUNK], MockProvider(), MockSFXProvider(), registry, settings
    )

    assert result.statuses == ["review"]
    assert not result.moved
    assert _cut_take_ordinal(engine, script_id) == 1


async def test_new_words_no_take_was_made_with_are_put_back(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """Otherwise the chunk claims words no take says, and the next ordinary run
    sends them — a second charge for the line after an unanswered request."""
    _, script_id = project_and_script
    _on_v3(engine)
    hears = HearsTheScript(engine)
    await _episode(engine, script_id, settings, registry, hears)
    with session_scope(engine) as session:
        original = require(session.scalar(select(Chunk).where(Chunk.ordinal == CHUNK))).text

    [result] = await regen.regenerate(
        engine,
        script_id,
        [CHUNK],
        MockProvider(fail_containing={"Replacement"}),
        MockSFXProvider(),
        registry,
        settings,
        transcriber=hears,
        texts={CHUNK: "Replacement words for the chunk."},
    )

    assert not result.new_takes
    assert result.stopped
    assert result.words_restored and not result.reworded
    with session_scope(engine) as session:
        assert require(session.scalar(select(Chunk).where(Chunk.ordinal == CHUNK))).text == original
    ordinary = produce_mod.project(
        engine, script_id, registry, with_effects=False, settings=settings
    )
    assert ordinary.chunks == 0


async def test_new_words_are_kept_once_a_take_has_them(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    _on_v3(engine)
    hears = HearsTheScript(engine)
    await _episode(engine, script_id, settings, registry, hears)

    result = await _regenerate(
        engine, script_id, settings, registry, hears, texts={CHUNK: "It was always them."}
    )

    assert result.new_takes and result.reworded and not result.words_restored
    with session_scope(engine) as session:
        chunk = require(session.scalar(select(Chunk).where(Chunk.ordinal == CHUNK)))
        assert chunk.text == "It was always them."


async def test_new_words_are_not_applied_when_the_limit_forbids_a_try(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    _on_v3(engine)
    hears = HearsTheScript(engine)
    await _episode(engine, script_id, settings, registry, hears)
    with session_scope(engine) as session:
        original = require(session.scalar(select(Chunk).where(Chunk.ordinal == CHUNK))).text

    result = await _regenerate(
        engine,
        script_id,
        settings,
        registry,
        hears,
        texts={CHUNK: "It was always them."},
        max_spend_micros=0,
    )

    assert not result.new_takes and result.stopped
    assert not result.reworded and not result.words_restored
    with session_scope(engine) as session:
        assert require(session.scalar(select(Chunk).where(Chunk.ordinal == CHUNK))).text == original


async def test_an_interruption_after_a_paid_take_keeps_its_new_words(
    engine: Engine,
    project_and_script: tuple[int, int],
    settings: Settings,
    registry: Registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ctrl-C can land after the runner committed a take but before it was
    reported. The take says the new words, so the chunk must too — putting the
    old ones back would have the next run pay for them again."""
    _, script_id = project_and_script
    _on_v3(engine)
    hears = HearsTheScript(engine)
    await _episode(engine, script_id, settings, registry, hears)
    real = produce_mod.produce

    async def then_interrupted(*args: object, **kwargs: object) -> object:
        await real(*args, **kwargs)  # type: ignore[arg-type]
        raise asyncio.CancelledError

    monkeypatch.setattr(produce_mod, "produce", then_interrupted)
    with pytest.raises(asyncio.CancelledError):
        await _regenerate(
            engine, script_id, settings, registry, hears, texts={CHUNK: "It was always them."}
        )

    with session_scope(engine) as session:
        chunk = require(session.scalar(select(Chunk).where(Chunk.ordinal == CHUNK)))
        assert chunk.text == "It was always them."


async def test_an_unanswered_request_is_reported_and_never_sent_twice(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    _on_v3(engine)
    hears = HearsTheScript(engine)
    await _episode(engine, script_id, settings, registry, hears)

    result = await _regenerate(
        engine,
        script_id,
        settings,
        registry,
        hears,
        provider=MockProvider(fail_containing={"always"}, failure_mode="unknown"),
        texts={CHUNK: "It was always them."},
    )

    assert result.unknown_takes and not result.new_takes
    assert result.words_restored
    # The ordinary run finds nothing to do: the unanswered words are not sent
    # again behind the user's back.
    ordinary = produce_mod.project(
        engine, script_id, registry, with_effects=False, settings=settings
    )
    assert ordinary.chunks == 0


def test_rewording_a_chunk_that_cannot_be_generated_is_refused_not_a_crash(
    engine: Engine, project_and_script: tuple[int, int], registry: Registry
) -> None:
    _, script_id = project_and_script
    _on_v3(engine)
    with session_scope(engine) as session:
        require(session.scalar(select(Project))).voice_id = None
    with pytest.raises(regen.RegenerateRefused, match="cannot be generated"):
        regen.plan(engine, script_id, registry, chunks=[CHUNK], texts={CHUNK: "New words."})


async def test_a_regeneration_the_monthly_cap_blocks_says_so_in_words(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    hears = HearsTheScript(engine, drops={FIRST: "halvorsen"})
    await _episode(engine, script_id, settings, registry, hears)
    with session_scope(engine) as session:
        require(session.scalar(select(Project))).monthly_cap_micros = 1

    result = await _regenerate(engine, script_id, settings, registry, hears)

    assert not result.new_takes
    assert result.stopped and "monthly cap" in result.stopped
    assert "--monthly-cap" in result.stopped
