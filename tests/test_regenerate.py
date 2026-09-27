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


# --------------------------------------------------------------------------
# Regenerating until the chunk is clean
# --------------------------------------------------------------------------


def _chunk_settings(engine: Engine) -> str | None:
    with session_scope(engine) as session:
        return require(session.scalar(select(Chunk).where(Chunk.ordinal == CHUNK))).settings_json


def _take_stability(engine: Engine, take_id: int) -> object:
    import json

    with session_scope(engine) as session:
        take = require(session.get(Take, take_id))
        return json.loads(take.settings_json or "{}").get("stability")


async def test_a_flagged_try_is_followed_by_a_steadier_one_until_clean(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """The first retry is plain. When it comes back flagged too, the next is made
    with the steadiest delivery — and it stops at the first clean take."""
    _, script_id = project_and_script
    hears = HearsTheScript(engine, drops={FIRST: "halvorsen", SECOND: "halvorsen"})
    await _episode(engine, script_id, settings, registry, hears)

    result = await _regenerate(engine, script_id, settings, registry, hears, attempts=3)

    assert result.statuses == ["suspect", "clear"]
    first_try, second_try = result.new_takes
    assert _take_stability(engine, first_try) != regen.STEADY_STABILITY
    assert result.steady_takes == [second_try]
    assert _take_stability(engine, second_try) == regen.STEADY_STABILITY
    assert result.moved and result.cut_after == second_try


async def test_the_steadier_setting_is_never_saved_on_the_chunk(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """It is how one take was made, not a new setting for the chunk: the next
    ordinary run finds the chunk's own takes and sends nothing."""
    _, script_id = project_and_script
    hears = HearsTheScript(engine, drops={FIRST: "halvorsen", SECOND: "halvorsen"})
    await _episode(engine, script_id, settings, registry, hears)
    before = _chunk_settings(engine)

    await _regenerate(engine, script_id, settings, registry, hears, attempts=3)

    assert _chunk_settings(engine) == before
    ordinary = produce_mod.project(
        engine, script_id, registry, with_effects=False, settings=settings
    )
    assert ordinary.chunks == 0


async def test_a_chunk_flagged_twice_already_starts_with_the_steadier_try(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """Two plain takes with problems is the evidence; a third plain one would be
    the same dice again."""
    _, script_id = project_and_script
    hears = HearsTheScript(engine, drops={FIRST: "halvorsen", SECOND: "halvorsen"})
    await _episode(engine, script_id, settings, registry, hears)
    plain = await _regenerate(engine, script_id, settings, registry, hears, attempts=1)
    assert plain.statuses == ["suspect"] and not plain.steady_takes

    again = await _regenerate(engine, script_id, settings, registry, hears, attempts=1)

    assert again.steady_takes == again.new_takes


async def test_a_take_to_review_is_worth_another_try(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """A "review" is usually a real slip; the aim is a take with nothing to hear."""
    _, script_id = project_and_script
    hears = HearsTheScript(engine, drops={FIRST: "halvorsen"}, extras={SECOND: ("truly", 0.3)})
    await _episode(engine, script_id, settings, registry, hears)

    result = await _regenerate(engine, script_id, settings, registry, hears, attempts=3)

    assert result.statuses == ["review", "clear"]


async def test_a_chunk_already_at_the_steadiest_is_retried_plainly(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    with session_scope(engine) as session:
        chunk = require(session.scalar(select(Chunk).where(Chunk.ordinal == CHUNK)))
        chunk.settings_json = '{"stability": 1.0}'
    hears = HearsTheScript(engine, drops={FIRST: "halvorsen", SECOND: "halvorsen"})
    await _episode(engine, script_id, settings, registry, hears)

    result = await _regenerate(engine, script_id, settings, registry, hears, attempts=3)

    assert not result.steady_takes


async def test_flagged_selects_suspect_and_review_alike(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    await _episode(engine, script_id, settings, registry, HearsTheScript(engine))
    with session_scope(engine) as session:
        for chunk in session.scalars(select(Chunk)).all():
            for take in chunk.takes:
                take.verify_status = {1: "suspect", 2: "review"}.get(chunk.ordinal, "clear")
    assert [p.ordinal for p in regen.plan(engine, script_id, registry, flagged_only=True)] == [
        1,
        2,
    ]
    assert [p.ordinal for p in regen.plan(engine, script_id, registry, suspect_only=True)] == [1]


# --------------------------------------------------------------------------
# Found in review: retries only where a retry can help
# --------------------------------------------------------------------------


async def test_rewording_starts_plain_so_the_chunk_keeps_a_take_of_its_own(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """Earlier flagged takes were of the old words. If the new words were only
    ever made steadied, no take would match the chunk's own request, and the
    next ordinary run would bill the line again."""
    _, script_id = project_and_script
    _on_v3(engine)
    hears = HearsTheScript(engine, drops={FIRST: "halvorsen", SECOND: "halvorsen"})
    await _episode(engine, script_id, settings, registry, hears)
    await _regenerate(engine, script_id, settings, registry, hears, attempts=1)

    result = await _regenerate(
        engine, script_id, settings, registry, hears, attempts=1, texts={CHUNK: "It was them."}
    )

    assert result.new_takes and not result.steady_takes
    ordinary = produce_mod.project(
        engine, script_id, registry, with_effects=False, settings=settings
    )
    assert ordinary.chunks == 0


async def test_without_speech_to_text_a_flagged_take_is_not_retried(
    engine: Engine,
    project_and_script: tuple[int, int],
    settings: Settings,
    registry: Registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A waveform-only verdict can never earn the cut, so more paid tries would
    buy nothing."""
    _, script_id = project_and_script
    hears = HearsTheScript(engine, drops={FIRST: "halvorsen"})
    await _episode(engine, script_id, settings, registry, hears)

    def waveform_review(
        engine: Engine, script_id: int, take_id: int, transcriber: object, registry: Registry
    ) -> str:
        with session_scope(engine) as session:
            take = require(session.get(Take, take_id))
            take.verify_status = "review"
            take.verifier = service.stamp(None)
        return "review"

    monkeypatch.setattr(regen, "_check", waveform_review)
    [result] = await regen.regenerate(
        engine,
        script_id,
        [CHUNK],
        MockProvider(),
        MockSFXProvider(),
        registry,
        settings,
        attempts=3,
    )

    assert result.statuses == ["review"]


async def test_a_burst_alone_is_not_retried(
    engine: Engine,
    project_and_script: tuple[int, int],
    settings: Settings,
    registry: Registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A burst with every word heard may be the voice itself — a clipped "So."
    — and would return on every try."""
    _, script_id = project_and_script
    hears = HearsTheScript(engine, drops={FIRST: "halvorsen"})
    await _episode(engine, script_id, settings, registry, hears)

    def burst_only(
        engine: Engine, script_id: int, take_id: int, transcriber: object, registry: Registry
    ) -> str:
        with session_scope(engine) as session:
            take = require(session.get(Take, take_id))
            take.verify_status = "review"
            take.verifier = hears.identity + "|rules:2"
            take.verify_findings_json = (
                '[{"severity": "review", "kind": "burst", "start_s": 1.0}, {"info_count": 0}]'
            )
        return "review"

    monkeypatch.setattr(regen, "_check", burst_only)
    result = await _regenerate(engine, script_id, settings, registry, hears, attempts=3)

    assert result.statuses == ["review"]


async def test_one_chunks_retries_leave_the_next_chunk_its_first_try(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    _, script_id = project_and_script
    hears = HearsTheScript(
        engine,
        drops={
            "chunk-001-take-01.mp3": "the",
            "chunk-001-take-02.mp3": "the",
            FIRST: "halvorsen",
        },
    )
    await _episode(engine, script_id, settings, registry, hears)
    plans = regen.plan(engine, script_id, registry, chunks=[1, CHUNK])
    one_each = sum(p.price_micros for p in plans)

    first, second = await regen.regenerate(
        engine,
        script_id,
        [1, CHUNK],
        MockProvider(),
        MockSFXProvider(),
        registry,
        settings,
        transcriber=hears,
        attempts=3,
        max_spend_micros=one_each,
    )

    assert len(first.new_takes) == 1 and first.stopped
    assert len(second.new_takes) == 1


async def test_on_a_tie_the_cut_takes_the_plain_try(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """It follows the script's audio tags; the steadied one plays them down."""
    _, script_id = project_and_script
    hears = HearsTheScript(
        engine,
        drops={FIRST: "halvorsen"},
        extras={SECOND: ("truly", 0.3), THIRD: ("truly", 0.3)},
    )
    await _episode(engine, script_id, settings, registry, hears)

    result = await _regenerate(engine, script_id, settings, registry, hears, attempts=2)

    plain, steadied = result.new_takes
    assert result.statuses == ["review", "review"]
    assert result.steady_takes == [steadied]
    assert result.cut_after == plain


async def test_under_the_monthly_cap_the_next_chunk_still_gets_its_first_try(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    from narrate import ledger

    _, script_id = project_and_script
    hears = HearsTheScript(
        engine,
        drops={
            "chunk-001-take-01.mp3": "the",
            "chunk-001-take-02.mp3": "the",
            FIRST: "halvorsen",
        },
    )
    await _episode(engine, script_id, settings, registry, hears)
    plans = regen.plan(engine, script_id, registry, chunks=[1, CHUNK])
    with session_scope(engine) as session:
        project = require(session.scalar(select(Project)))
        spent = ledger.project_budget(session, project.id).spent_micros
        # Room for one try of each chunk, and not a retry more.
        project.monthly_cap_micros = spent + sum(p.price_micros for p in plans) + 1

    first, second = await regen.regenerate(
        engine,
        script_id,
        [1, CHUNK],
        MockProvider(),
        MockSFXProvider(),
        registry,
        settings,
        transcriber=hears,
        attempts=3,
    )

    assert len(first.new_takes) == 1 and first.stopped
    assert len(second.new_takes) == 1


# --------------------------------------------------------------------------
# A problem at a paragraph break: join it, change no word
# --------------------------------------------------------------------------

TWO_PARAGRAPHS = "Dr. Halvorsen kept a careful record.\n\nThe entries grew shorter each year."
# "The" is word 6 of the text as sent; an invented word lands just before it.
AT_THE_BREAK = (6, "great")


def _two_paragraphs(engine: Engine) -> None:
    with session_scope(engine) as session:
        chunk = require(session.scalar(select(Chunk).where(Chunk.ordinal == CHUNK)))
        chunk.text = TWO_PARAGRAPHS


async def test_a_word_invented_at_a_paragraph_break_is_fixed_by_joining_it(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """What fixed chunk 3 of the real episode: the model filled the pause after a
    punchline with a word of its own, and one fewer break left it nothing to
    fill. Tried before the steadiest delivery, which would soften the tags."""
    _, script_id = project_and_script
    _on_v3(engine)
    _two_paragraphs(engine)
    hears = HearsTheScript(engine, inserts={FIRST: AT_THE_BREAK, SECOND: AT_THE_BREAK})
    await _episode(engine, script_id, settings, registry, hears)

    result = await _regenerate(engine, script_id, settings, registry, hears, attempts=3)

    _plain, joined = result.new_takes
    assert result.statuses == ["suspect", "clear"]
    assert result.joined_takes == [joined] and not result.steady_takes
    assert result.cut_after == joined and result.break_joined
    with session_scope(engine) as session:
        chunk = require(session.scalar(select(Chunk).where(Chunk.ordinal == CHUNK)))
        assert chunk.text == TWO_PARAGRAPHS.replace("\n\n", " ")
    # Not a word changed, and the next ordinary run finds the take it plays.
    ordinary = produce_mod.project(
        engine, script_id, registry, with_effects=False, settings=settings
    )
    assert ordinary.chunks == 0


async def test_a_joined_break_is_undone_when_its_take_does_not_win(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """The chunk's words describe the take it plays."""
    _, script_id = project_and_script
    _on_v3(engine)
    _two_paragraphs(engine)
    hears = HearsTheScript(
        engine, inserts={FIRST: AT_THE_BREAK, SECOND: AT_THE_BREAK, THIRD: (5, "great")}
    )
    await _episode(engine, script_id, settings, registry, hears)

    result = await _regenerate(engine, script_id, settings, registry, hears, attempts=2)

    assert result.joined_takes and not result.moved and not result.break_joined
    with session_scope(engine) as session:
        chunk = require(session.scalar(select(Chunk).where(Chunk.ordinal == CHUNK)))
        assert chunk.text == TWO_PARAGRAPHS


async def test_a_break_is_never_joined_where_rewording_would_rebill_neighbours(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """On a stitched model a chunk's text travels with its neighbours' requests."""
    _, script_id = project_and_script
    _two_paragraphs(engine)
    hears = HearsTheScript(engine, inserts={FIRST: AT_THE_BREAK, SECOND: AT_THE_BREAK})
    await _episode(engine, script_id, settings, registry, hears)

    result = await _regenerate(engine, script_id, settings, registry, hears, attempts=2)

    assert not result.joined_takes
    assert result.steady_takes == result.new_takes[1:]


# Found in review: the join must never cost a word


async def test_joining_a_break_keeps_every_word_of_a_numbered_list(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """The chunk's text was parsed once already; a second pass would read a line
    starting "2." as a list marker and drop the number."""
    _, script_id = project_and_script
    _on_v3(engine)
    listed = "Two rules run this whole game.\n\n1. Never lose money.\n\n2. Never forget rule one."
    with session_scope(engine) as session:
        require(session.scalar(select(Chunk).where(Chunk.ordinal == CHUNK))).text = listed
    hears = HearsTheScript(engine, inserts={FIRST: (6, "great"), SECOND: (6, "great")})
    await _episode(engine, script_id, settings, registry, hears)

    result = await _regenerate(engine, script_id, settings, registry, hears, attempts=3)

    assert result.joined_takes and result.break_joined
    with session_scope(engine) as session:
        text = require(session.scalar(select(Chunk).where(Chunk.ordinal == CHUNK))).text
    assert text == listed.replace("game.\n\n1.", "game. 1.")


async def test_an_extra_word_mid_sentence_is_not_a_reason_to_join(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """ "…careful great record." is one word short of the break, inside a sentence."""
    _, script_id = project_and_script
    _on_v3(engine)
    _two_paragraphs(engine)
    hears = HearsTheScript(engine, inserts={FIRST: (5, "great"), SECOND: (5, "great")})
    await _episode(engine, script_id, settings, registry, hears)

    result = await _regenerate(engine, script_id, settings, registry, hears, attempts=2)

    assert not result.joined_takes


def test_what_counts_as_at_a_paragraph_break() -> None:
    from narrate.verify.compare import FAIL, Finding

    def extra(word: int) -> Finding:
        return Finding(severity=FAIL, kind="extra", heard="great", word=word)

    def missing(words: str, word: int, end: int | None = None) -> Finding:
        last = end if end is not None else word + len(words.split()) - 1
        return Finding(severity=FAIL, kind="missing", expected=words, word=word, word_end=last)

    script = "doctor halvorsen kept a careful record the entries grew shorter".split()
    # The break sits before script word 6.
    assert regen._at_break(extra(6), 6)
    assert not regen._at_break(extra(5), 6)
    assert regen._at_break(missing("the", 6), 6)
    assert regen._at_break(missing("record", 5), 6)
    assert regen._at_break(missing("careful record", 4), 6)
    assert not regen._at_break(missing("kept", 2), 6)
    # Dropped words need not be next to each other: "a … record", with
    # "careful" misheard between them, ends at the break.
    assert regen._at_break(missing("a record", 3, end=5), 6)
    # The paragraph's last word echoed across the pause.
    echo = Finding(severity=FAIL, kind="extra", heard="record", word=5)
    assert regen._at_break(echo, 6, script)
    assert not regen._at_break(extra(5), 6, script)


async def test_an_interruption_while_the_joined_take_is_checked_leaves_the_words_as_played(
    engine: Engine,
    project_and_script: tuple[int, int],
    settings: Settings,
    registry: Registry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, script_id = project_and_script
    _on_v3(engine)
    _two_paragraphs(engine)
    hears = HearsTheScript(engine, inserts={FIRST: AT_THE_BREAK, SECOND: AT_THE_BREAK})
    await _episode(engine, script_id, settings, registry, hears)
    real = regen._check
    calls: list[int] = []

    def interrupted_on_the_joined_take(
        engine: Engine, script_id: int, take_id: int, transcriber: object, registry: Registry
    ) -> str:
        calls.append(take_id)
        if len(calls) == 2:
            raise asyncio.CancelledError
        return real(engine, script_id, take_id, transcriber, registry)  # type: ignore[arg-type]

    monkeypatch.setattr(regen, "_check", interrupted_on_the_joined_take)
    with pytest.raises(asyncio.CancelledError):
        await _regenerate(engine, script_id, settings, registry, hears, attempts=3)

    with session_scope(engine) as session:
        chunk = require(session.scalar(select(Chunk).where(Chunk.ordinal == CHUNK)))
        assert chunk.text == TWO_PARAGRAPHS


async def test_a_paragraph_ending_in_an_ellipsis_can_still_be_joined(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """ "…record..." tokenises with a stray "." when it ends the text, which
    once made every break of the chunk look out of line."""
    _, script_id = project_and_script
    _on_v3(engine)
    with session_scope(engine) as session:
        require(
            session.scalar(select(Chunk).where(Chunk.ordinal == CHUNK))
        ).text = TWO_PARAGRAPHS.replace("record.", "record...")
    hears = HearsTheScript(engine, inserts={FIRST: AT_THE_BREAK, SECOND: AT_THE_BREAK})
    await _episode(engine, script_id, settings, registry, hears)

    result = await _regenerate(engine, script_id, settings, registry, hears, attempts=3)

    assert result.joined_takes and result.break_joined


async def test_breaks_around_a_tag_on_its_own_are_joined_together(
    engine: Engine, project_and_script: tuple[int, int], settings: Settings, registry: Registry
) -> None:
    """Which of the two pauses around "[sighs]" the model filled cannot be told."""
    _, script_id = project_and_script
    _on_v3(engine)
    with session_scope(engine) as session:
        require(
            session.scalar(select(Chunk).where(Chunk.ordinal == CHUNK))
        ).text = TWO_PARAGRAPHS.replace("\n\n", "\n\n[sighs]\n\n")
    # "[sighs]" is a word of the text as sent, so "The" is word 7 here.
    hears = HearsTheScript(engine, inserts={FIRST: (7, "great"), SECOND: (7, "great")})
    await _episode(engine, script_id, settings, registry, hears)

    result = await _regenerate(engine, script_id, settings, registry, hears, attempts=3)

    assert result.joined_takes and result.break_joined
    with session_scope(engine) as session:
        text = require(session.scalar(select(Chunk).where(Chunk.ordinal == CHUNK))).text
    assert "\n\n" not in text and "[sighs]" in text
