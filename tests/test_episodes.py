"""Deleting and replacing episodes without losing a cent or a take.

The properties that matter: spend never leaves the record, a deleted episode
can never spend again, its id is never handed on, and a replaced script pays
only for what changed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select

from narrate import episodes, ledger
from narrate import regenerate as regen
from narrate.archive import Archived
from narrate.db.models import Chunk, Cut, LedgerEntry, Project, Run, Script, Take
from narrate.db.session import session_scope
from narrate.export import export_script
from narrate.ingest import ingest_script
from narrate.provider.mock import MockProvider
from narrate.registry import Registry
from narrate.runner import generate
from narrate.settings import Settings

from .conftest import needs_ffmpeg, require

pytestmark = needs_ffmpeg

TEXT = (
    "The first paragraph sets the scene, and it runs for two sentences.\n\n"
    "## [CHUNK 2]\n\n"
    "Dr. Halvorsen kept a record. She had kept one since 1987.\n\n"
    "## [CHUNK 3]\n\n"
    "The jackdaws had not fled the cold. They had fled the wet.\n\n"
    "## [CHUNK 4]\n\n"
    "A final paragraph closes it out. Nothing more to say."
)


def _new(
    engine: Engine,
    registry: Registry,
    name: str = "Show",
    text: str = TEXT,
    model: str = "eleven_v3",
) -> int:
    spec = registry.get(model)
    with session_scope(engine) as session:
        project = session.scalar(select(Project).where(Project.name == name))
        if project is None:
            project = Project(name=name, voice_id="voice-1", model_id=spec.model_id)
            session.add(project)
            session.flush()
        script = Script(
            project_id=project.id,
            title="Episode",
            source_text=text,
            source_sha256=hashlib.sha256(text.encode()).hexdigest(),
        )
        session.add(script)
        session.flush()
        ingest_script(session, script, spec)
        return script.id


async def _generated(engine: Engine, registry: Registry, settings: Settings, **kw: str) -> int:
    script_id = _new(engine, registry, **kw)
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)
    return script_id


def _spend(engine: Engine) -> int:
    with session_scope(engine) as session:
        return int(session.scalar(select(func.sum(LedgerEntry.cost_micros))) or 0)


def _takes(engine: Engine, script_id: int) -> list[int]:
    with session_scope(engine) as session:
        return list(
            session.scalars(
                select(Take.id)
                .join(Chunk, Chunk.id == Take.chunk_id)
                .where(Chunk.script_id == script_id)
                .order_by(Take.id)
            ).all()
        )


# -- deleting ---------------------------------------------------------------------


async def test_asking_what_a_delete_would_do_changes_nothing(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = await _generated(engine, registry, settings)

    plan = episodes.delete_episode(engine, script_id, settings)

    assert not plan.done and plan.takes == 4 and plan.spend_micros == _spend(engine) > 0
    with session_scope(engine) as session:
        assert require(session.get(Script, script_id)).archived_at is None


async def test_a_deleted_episode_keeps_its_spend_and_its_id(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """Its id must never go to the next episode, which would inherit its spend."""
    script_id = await _generated(engine, registry, settings)
    before = _spend(engine)
    with session_scope(engine) as session:
        project_id = require(session.get(Script, script_id)).project_id
        month = ledger.month_spend_micros(session, project_id, ledger.month_start())

    episodes.delete_episode(engine, script_id, settings, confirm=True)
    after_id = _new(engine, registry)

    assert after_id != script_id
    assert _spend(engine) == before
    with session_scope(engine) as session:
        assert ledger.month_spend_micros(session, project_id, ledger.month_start()) == month
        # Its cut is still its cut: deleting did not turn finished audio into waste.
        assert len(session.scalars(select(Cut).where(Cut.script_id == script_id)).all()) == 4


async def test_a_deleted_episode_can_never_spend_again(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = await _generated(engine, registry, settings)
    episodes.delete_episode(engine, script_id, settings, confirm=True)

    with pytest.raises(Archived):
        await generate(
            engine, script_id, MockProvider(), registry, settings, dry_run=False, force=True
        )
    with pytest.raises(regen.RegenerateRefused, match="deleted"):
        regen.plan(engine, script_id, registry, chunks=[1])


async def test_a_delete_waits_for_a_running_generation(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = await _generated(engine, registry, settings)
    with session_scope(engine) as session:
        session.add(Run(script_id=script_id, model_id="eleven_v3", status="running"))

    with pytest.raises(episodes.EpisodeRefused, match="still running"):
        episodes.delete_episode(engine, script_id, settings, confirm=True)
    assert episodes.delete_episode(engine, script_id, settings, confirm=True, force=True).done


async def test_files_go_only_when_asked_and_shared_effects_stay(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = await _generated(engine, registry, settings)
    export = export_script(engine, script_id, settings, formats=["mp3"])
    with session_scope(engine) as session:
        paths = [
            Path(require(t.asset_path))
            for t in session.scalars(
                select(Take).join(Chunk).where(Chunk.script_id == script_id)
            ).all()
        ]

    kept = episodes.delete_episode(engine, script_id, settings, confirm=True)
    assert kept.files_deleted == 0 and all(p.exists() for p in paths)
    episodes.restore_episode(engine, script_id)

    gone = episodes.delete_episode(engine, script_id, settings, confirm=True, delete_files=True)
    assert gone.files_deleted > 0
    assert not any(p.exists() for p in paths)
    assert not export.out_dir.exists()


async def test_restoring_an_episode_brings_it_back_whole(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = await _generated(engine, registry, settings)
    takes = _takes(engine, script_id)
    episodes.delete_episode(engine, script_id, settings, confirm=True)

    episodes.restore_episode(engine, script_id)

    assert _takes(engine, script_id) == takes
    plan = regen.plan(engine, script_id, registry, chunks=[1])
    assert plan[0].cut_take == 1


async def test_deleting_a_project_frees_its_name_and_restores_what_it_took(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    first = await _generated(engine, registry, settings)
    second = _new(engine, registry)
    # Deleted on its own, before the project: a restore must leave it deleted.
    episodes.delete_episode(engine, second, settings, confirm=True)
    with session_scope(engine) as session:
        project_id = require(session.get(Script, first)).project_id

    plan = episodes.delete_project(engine, project_id, settings, confirm=True)
    assert plan.episodes == [first]
    # The name is free again.
    again = _new(engine, registry, name="Show")
    with session_scope(engine) as session:
        assert require(session.get(Script, again)).project_id != project_id
        session.delete(require(session.get(Script, again)))
        other = session.scalar(select(Project).where(Project.name == "Show"))
        session.delete(require(other))

    restored = episodes.restore_project(engine, project_id)

    assert restored.name == "Show"
    with session_scope(engine) as session:
        assert require(session.get(Script, first)).archived_at is None
        assert require(session.get(Script, second)).archived_at is not None


# -- replacing ------------------------------------------------------------------


async def test_an_identical_upload_changes_nothing(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = await _generated(engine, registry, settings)
    # Line endings and a byte-order mark are an editor's, not a change.
    windows = "﻿" + TEXT.replace("\n", "\r\n")

    plan = episodes.replace_episode(engine, script_id, windows, registry, settings, confirm=True)

    assert plan.unchanged and not plan.done


async def test_an_edited_script_pays_only_for_what_changed(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = await _generated(engine, registry, settings)
    takes = _takes(engine, script_id)
    edited = TEXT.replace("They had fled the wet.", "They had fled the rain.")

    quote = episodes.replace_episode(engine, script_id, edited, registry, settings)
    assert quote.kept == 3 and quote.new == 1 and quote.retired == 1
    assert quote.chunks_to_generate == 1 and quote.quote_micros > 0
    # The quote changed nothing.
    assert _takes(engine, script_id) == takes

    done = episodes.replace_episode(engine, script_id, edited, registry, settings, confirm=True)
    assert done.done

    report = await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)
    assert len(report.succeeded) == 1
    with session_scope(engine) as session:
        script = require(session.get(Script, script_id))
        assert script.source_text == edited
        chunk3 = require(
            session.scalar(select(Chunk).where(Chunk.script_id == script_id, Chunk.ordinal == 3))
        )
        assert "rain" in chunk3.text


async def test_a_dropped_chunk_keeps_its_paid_takes_in_an_archived_copy(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = await _generated(engine, registry, settings)
    before = _spend(engine)
    shorter = TEXT.split("\n\n## [CHUNK 4]")[0]

    plan = episodes.replace_episode(engine, script_id, shorter, registry, settings, confirm=True)

    assert plan.retired == 1 and plan.retired_takes == 1 and plan.retired_to is not None
    with session_scope(engine) as session:
        copy = require(session.get(Script, plan.retired_to))
        assert copy.archived_at is not None
        moved = session.scalars(select(Chunk).where(Chunk.script_id == copy.id)).all()
        assert len(moved) == 1 and moved[0].takes
        # Its cut went with it, so it is not counted as the live episode's.
        cut = require(session.scalar(select(Cut).where(Cut.chunk_id == moved[0].id)))
        assert cut.script_id == copy.id
    assert _spend(engine) == before
    assert len(_takes(engine, script_id)) == 3


async def test_a_reordered_script_reuses_every_take(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """eleven_v3 carries nothing between chunks: a moved paragraph is the same request."""
    script_id = await _generated(engine, registry, settings)
    parts = TEXT.split("\n\n## [CHUNK 4]\n\n")
    reordered = parts[1] + "\n\n## [CHUNK 2]\n\n" + parts[0]

    plan = episodes.replace_episode(engine, script_id, reordered, registry, settings)

    assert plan.kept == 4 and plan.new == 0 and plan.chunks_to_generate == 0


async def test_a_new_take_never_overwrites_a_moved_takes_file(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """Take files are named by chunk position, and positions move on a replace."""
    script_id = await _generated(engine, registry, settings)
    with session_scope(engine) as session:
        files = {
            Path(require(t.asset_path)): Path(require(t.asset_path)).read_bytes()
            for t in session.scalars(select(Take)).all()
        }
    edited = "A brand new opening paragraph, nothing like the old one.\n\n## [CHUNK 2]\n\n" + TEXT

    episodes.replace_episode(engine, script_id, edited, registry, settings, confirm=True)
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)

    for path, audio in files.items():
        assert path.read_bytes() == audio, path


async def test_on_a_stitched_model_the_chunks_after_an_edit_are_made_again(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """multilingual_v2 conditions each request on the chunks before it: after an
    edit, the next ones' audio was made for different neighbours. Their cut must
    not keep playing it while new takes are paid for and never used."""
    script_id = _new(engine, registry, model="eleven_multilingual_v2")
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)
    edited = TEXT.replace("Dr. Halvorsen kept a record.", "Dr. Halvorsen kept a diary.")

    plan = episodes.replace_episode(engine, script_id, edited, registry, settings, confirm=True)

    assert plan.new == 1 and plan.kept == 3
    assert plan.kept_regenerated == 2  # chunks 3 and 4 follow the edit
    assert plan.chunks_to_generate == 3
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)
    with session_scope(engine) as session:
        for chunk in session.scalars(select(Chunk).where(Chunk.script_id == script_id)).all():
            cut = require(session.get(Cut, chunk.id))
            newest = max(t.id for t in chunk.takes if t.status == "succeeded")
            if chunk.ordinal >= 2:
                assert cut.take_id == newest, chunk.ordinal


# -- found in review ---------------------------------------------------------------

UNMARKED = "\n\n".join(
    f"Paragraph {n} tells part of the story, and it goes on for a while so that "
    f"several of them fit in one chunk. " * 6
    for n in range(1, 13)
)


async def test_an_edit_to_an_unmarked_script_does_not_repack_the_rest(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """Without markers the chunker packs paragraphs greedily; cutting one must not
    shift every later chunk's contents and bill the whole episode again."""
    script_id = await _generated(engine, registry, settings, text=UNMARKED)
    with session_scope(engine) as session:
        before = len(session.scalars(select(Chunk).where(Chunk.script_id == script_id)).all())
    assert before >= 3
    paragraphs = UNMARKED.split("\n\n")
    shorter = "\n\n".join(paragraphs[:1] + paragraphs[2:])

    plan = episodes.replace_episode(engine, script_id, shorter, registry, settings)

    assert plan.kept >= before - 1
    assert plan.chunks_to_generate <= 1


async def test_a_joined_break_beside_an_insertion_keeps_its_takes(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = await _generated(engine, registry, settings)
    with session_scope(engine) as session:
        # As regeneration's paragraph join leaves it: same words, one break fewer.
        chunk = require(
            session.scalar(select(Chunk).where(Chunk.script_id == script_id, Chunk.ordinal == 2))
        )
        chunk.text = chunk.text.replace(". She", ".  She")
    inserted = TEXT.replace(
        "## [CHUNK 3]", "## [CHUNK 3]\n\nA brand new paragraph.\n\n## [CHUNK 4]", 1
    )

    plan = episodes.replace_episode(engine, script_id, inserted, registry, settings)

    assert plan.kept == 4 and plan.new == 1 and plan.retired == 0


async def test_a_line_that_loses_its_speaker_is_generated_again(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """Same words, but no longer Morag's: keeping her take would keep her voice."""
    from narrate.db.models import CastMember

    text = "Morag: The light is out.\n\n## [CHUNK 2]\n\nThe keeper wrote it down."
    with session_scope(engine) as session:
        project = Project(name="Cast", voice_id="narrator", model_id="eleven_multilingual_v2")
        session.add(project)
        session.flush()
        session.add(CastMember(project_id=project.id, name="Morag", voice_id="voice-morag"))
        script = Script(project_id=project.id, title="Ep", source_text=text, source_sha256="x")
        session.add(script)
        session.flush()
        ingest_script(
            session, script, registry.get("eleven_multilingual_v2"), cast={"Morag": "voice-morag"}
        )
        script_id = script.id
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)

    plan = episodes.replace_episode(
        engine, script_id, text.replace("Morag: ", ""), registry, settings
    )

    assert plan.new >= 1


async def test_deleting_mid_run_stops_the_effects_that_follow(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    from narrate.effects import add_slot, generate_slots
    from narrate.provider.mock import MockSFXProvider

    script_id = await _generated(engine, registry, settings)
    with session_scope(engine) as session:
        add_slot(session, script_id, 1, "thunder", 1.0)
        add_slot(session, script_id, 2, "rain", 1.0)

    class DeletedAfterTheFirst(MockSFXProvider):
        async def generate_effect(self, request):  # type: ignore[no-untyped-def]
            result = await super().generate_effect(request)
            if len(self.calls) == 1:
                episodes.delete_episode(engine, script_id, settings, confirm=True)
            return result

    provider = DeletedAfterTheFirst()
    await generate_slots(engine, script_id, provider, settings, dry_run=False)

    assert len(provider.calls) == 1


async def test_a_new_project_with_a_deleted_ones_name_keeps_off_its_effect_audio(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    from narrate.db.models import Effect
    from narrate.effects import add_slot, generate_slots
    from narrate.provider.mock import MockSFXProvider

    first = await _generated(engine, registry, settings)
    with session_scope(engine) as session:
        add_slot(session, first, 1, "thunder", 1.0)
    await generate_slots(engine, first, MockSFXProvider(), settings, dry_run=False)
    with session_scope(engine) as session:
        old = require(session.scalars(select(Effect)).first())
        old_path = Path(require(old.asset_path))
        project_id = old.project_id
    old_path.write_bytes(b"paid audio")
    episodes.delete_project(engine, project_id, settings, confirm=True)

    second = await _generated(engine, registry, settings)
    with session_scope(engine) as session:
        add_slot(session, second, 1, "thunder", 1.0)
    await generate_slots(engine, second, MockSFXProvider(), settings, dry_run=False)

    assert old_path.read_bytes() == b"paid audio"


async def test_a_slot_placed_by_hand_follows_its_edited_paragraph(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    from narrate.db.models import EffectSlot
    from narrate.effects import add_slot

    script_id = await _generated(engine, registry, settings)
    with session_scope(engine) as session:
        add_slot(session, script_id, 3, "wind", 1.0)

    plan = episodes.replace_episode(
        engine,
        script_id,
        TEXT.replace("They had fled the wet.", "They had fled the rain."),
        registry,
        settings,
        confirm=True,
    )

    assert plan.slots_dropped == 0
    with session_scope(engine) as session:
        slot = require(session.scalar(select(EffectSlot).where(EffectSlot.source == "manual")))
        assert slot.at_chunk_ordinal == 3


async def test_a_marker_slot_the_user_deleted_is_not_brought_back(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    from narrate.db.models import EffectSlot

    text = TEXT.replace("## [CHUNK 3]", "[SFX: heavy rain, 2s]\n\n## [CHUNK 3]")
    script_id = await _generated(engine, registry, settings, text=text)
    with session_scope(engine) as session:
        for slot in session.scalars(select(EffectSlot)).all():
            session.delete(slot)

    plan = episodes.replace_episode(
        engine, script_id, text.replace("1987", "1988"), registry, settings
    )

    assert plan.effects_to_generate == 0


async def test_a_title_only_change_keeps_the_outline(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    from narrate.db.models import ScriptBeat

    script_id = await _generated(engine, registry, settings)
    with session_scope(engine) as session:
        session.add(ScriptBeat(script_id=script_id, ordinal=1, heading="Open", body="paid"))

    plan = episodes.replace_episode(
        engine, script_id, TEXT, registry, settings, title="New title", confirm=True
    )

    assert plan.beats_detached == 0
    with session_scope(engine) as session:
        assert len(session.scalars(select(ScriptBeat)).all()) == 1


async def test_replacing_a_deleted_episode_is_refused_not_a_crash(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = await _generated(engine, registry, settings)
    episodes.delete_episode(engine, script_id, settings, confirm=True)
    with pytest.raises(episodes.EpisodeRefused, match="deleted"):
        episodes.replace_episode(engine, script_id, TEXT + " More.", registry, settings)


def test_a_long_project_name_comes_back_whole(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    name = "N" * 125
    _new(engine, registry, name=name)
    with session_scope(engine) as session:
        project_id = require(session.scalar(select(Project).where(Project.name == name))).id
    episodes.delete_project(engine, project_id, settings, confirm=True)
    assert episodes.restore_project(engine, project_id).name == name


async def test_a_project_delete_can_reclaim_every_episodes_files(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """An episode deleted earlier, on its own, still has audio on disk."""
    first = await _generated(engine, registry, settings)
    second = await _generated(engine, registry, settings)
    episodes.delete_episode(engine, second, settings, confirm=True)
    with session_scope(engine) as session:
        paths = [
            Path(require(t.asset_path))
            for t in session.scalars(select(Take).join(Chunk).where(Chunk.script_id == second))
        ]
        project_id = require(session.get(Script, first)).project_id

    episodes.delete_project(engine, project_id, settings, confirm=True, delete_files=True)

    assert not any(p.exists() for p in paths)


async def test_a_dropped_chunk_stays_counted_as_its_episodes_cut(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = await _generated(engine, registry, settings)
    episodes.replace_episode(
        engine, script_id, TEXT.split("\n\n## [CHUNK 4]")[0], registry, settings, confirm=True
    )
    with session_scope(engine) as session:
        assert ledger.script_rollup(session, script_id).wasted_micros == 0


DASHED = (
    "The first paragraph sets the scene, and it runs for two sentences.\n\n"
    "## [CHUNK 2]\n\n"
    "Dr. Halvorsen kept a record. She had kept one since 1987 —\n\n"
    "## [CHUNK 3]\n\n"
    "The jackdaws had not fled the cold. They had fled the wet…\n\n"
    "## [CHUNK 4]\n\n"
    "A final paragraph closes it out. Nothing more to say."
)


async def test_chunks_ending_in_a_dash_are_not_split_and_billed_again(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """No sentence rule sees a break after "—" or "…"; a kept chunk's edge
    snapped to one would land back inside it and split it."""
    script_id = await _generated(engine, registry, settings, text=DASHED)

    renamed = episodes.replace_episode(engine, script_id, DASHED, registry, settings, title="New")
    edited = episodes.replace_episode(
        engine, script_id, DASHED.replace("more to say", "more to add"), registry, settings
    )

    assert (renamed.kept, renamed.new, renamed.quote_micros) == (4, 0, 0)
    assert (edited.kept, edited.new, edited.chunks_to_generate) == (3, 1, 1)


async def test_an_unmarked_edit_beside_trailing_ellipses_bills_one_chunk(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    body = " ".join(["The keeper counted the ships that passed the point that year."] * 9)
    paragraphs = [f"Paragraph {i} begins here. {body} And then…" for i in range(1, 13)]
    text = "\n\n".join(paragraphs)
    script_id = await _generated(engine, registry, settings, text=text)
    with session_scope(engine) as session:
        before = len(session.scalars(select(Chunk).where(Chunk.script_id == script_id)).all())

    plan = episodes.replace_episode(
        engine,
        script_id,
        text.replace("Paragraph 12 begins", "Paragraph 12 starts"),
        registry,
        settings,
    )

    assert before >= 3
    assert plan.kept == before - 1 and plan.chunks_to_generate == 1


async def test_cutting_one_line_of_a_dialogue_regroups_nothing_else(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    lines = [
        f"{('Morag', 'Keeper')[i % 2]}: Line {i} is an ordinary line of talk, the kind "
        "that goes back and forth between two people for a while."
        for i in range(1, 61)
    ]
    text = "[CAST] Morag = voice_morag · Keeper = voice_keeper\n\n" + "\n\n".join(lines)
    spec = registry.get("eleven_v3")
    with session_scope(engine) as session:
        project = Project(name="Talk", voice_id="narrator", model_id=spec.model_id)
        session.add(project)
        session.flush()
        script = Script(project_id=project.id, title="Ep", source_text=text, source_sha256="x")
        session.add(script)
        session.flush()
        ingest_script(session, script, spec, dialogue=True)
        script_id = script.id
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)

    plan = episodes.replace_episode(
        engine, script_id, text.replace(lines[1] + "\n\n", ""), registry, settings
    )

    assert plan.retired == 1 and plan.new == 1 and plan.kept >= 3


async def test_an_unmarked_line_that_loses_its_label_leaves_its_speaker(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """Without markers a speaker's chunk is stored as a paragraph, so the row
    cannot say its voice came from the script; the old script can."""
    from narrate.db.models import CastMember

    text = (
        "Morag: You knew it would end. Everyone knew, and nobody said it aloud.\n\n"
        "Keeper: I hoped not. Hoping is a different thing from knowing."
    )
    with session_scope(engine) as session:
        project = Project(name="Cast", voice_id="narrator", model_id="eleven_multilingual_v2")
        session.add(project)
        session.flush()
        for name in ("Morag", "Keeper"):
            session.add(CastMember(project_id=project.id, name=name, voice_id=f"voice-{name}"))
        script = Script(project_id=project.id, title="Ep", source_text=text, source_sha256="x")
        session.add(script)
        session.flush()
        cast = {"Morag": "voice-Morag", "Keeper": "voice-Keeper"}
        ingest_script(session, script, registry.get("eleven_multilingual_v2"), cast=cast)
        script_id = script.id
    await generate(engine, script_id, MockProvider(), registry, settings, dry_run=False)

    plan = episodes.replace_episode(
        engine, script_id, text.replace("Morag: ", ""), registry, settings, confirm=True
    )

    assert (plan.kept, plan.new) == (1, 1)
    with session_scope(engine) as session:
        rows = session.scalars(
            select(Chunk).where(Chunk.script_id == script_id).order_by(Chunk.ordinal)
        ).all()
        assert [row.voice_id for row in rows] == [None, "voice-Keeper"]


async def test_a_hand_set_voice_on_an_unchanged_line_is_kept(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    script_id = await _generated(engine, registry, settings)
    with session_scope(engine) as session:
        chunk = require(
            session.scalar(select(Chunk).where(Chunk.script_id == script_id, Chunk.ordinal == 2))
        )
        chunk.voice_id = "chosen-by-hand"

    plan = episodes.replace_episode(
        engine, script_id, TEXT.replace("more to say", "more to add"), registry, settings
    )

    assert (plan.kept, plan.new) == (3, 1)


async def test_a_slot_on_a_dropped_passage_goes_rather_than_moving_to_a_neighbour(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    from narrate.db.models import EffectSlot
    from narrate.effects import add_slot

    script_id = await _generated(engine, registry, settings)
    with session_scope(engine) as session:
        add_slot(session, script_id, 2, "pen scratching", 1.0, source="manual")
        add_slot(session, script_id, 3, "jackdaws taking flight", 1.0, source="manual")
    edited = TEXT.replace(
        "## [CHUNK 2]\n\nDr. Halvorsen kept a record.",
        "## [CHUNK]\n\nA new opening line.\n\n## [CHUNK 2]\n\nDr. Halvorsen kept a diary.",
    ).replace("## [CHUNK 3]\n\nThe jackdaws had not fled the cold. They had fled the wet.\n\n", "")

    plan = episodes.replace_episode(engine, script_id, edited, registry, settings, confirm=True)

    assert plan.slots_dropped == 1
    with session_scope(engine) as session:
        slots = session.scalars(select(EffectSlot).where(EffectSlot.script_id == script_id)).all()
        placed = {
            s.description: require(
                session.scalar(
                    select(Chunk.text).where(
                        Chunk.script_id == script_id, Chunk.ordinal == s.at_chunk_ordinal
                    )
                )
            )
            for s in slots
        }
    assert list(placed) == ["pen scratching"]
    assert placed["pen scratching"].startswith("Dr. Halvorsen kept a diary.")


async def test_the_archived_copy_holding_an_outline_has_the_old_words(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    from narrate.db.models import ScriptBeat

    script_id = _new(engine, registry)
    with session_scope(engine) as session:
        session.add(ScriptBeat(script_id=script_id, ordinal=1, heading="Open", body="paid"))

    plan = episodes.replace_episode(
        engine, script_id, TEXT.replace("1987", "1988"), registry, settings, confirm=True
    )

    with session_scope(engine) as session:
        copy = require(session.get(Script, require(plan.retired_to)))
        assert copy.source_text == TEXT
        assert copy.source_sha256 == hashlib.sha256(TEXT.encode()).hexdigest()


async def test_forcing_past_a_crashed_run_closes_it(
    engine: Engine, registry: Registry, settings: Settings
) -> None:
    """Forcing says the run crashed. Left open, it would demand forcing again
    on every later delete and replace of the episode."""
    script_id = await _generated(engine, registry, settings)
    with session_scope(engine) as session:
        session.add(Run(script_id=script_id, model_id="eleven_v3", status="running"))

    with pytest.raises(episodes.StillRunning):
        episodes.delete_episode(engine, script_id, settings, confirm=True)
    episodes.delete_episode(engine, script_id, settings, confirm=True, force=True)
    episodes.restore_episode(engine, script_id)

    assert episodes.delete_episode(engine, script_id, settings).running == []
    with session_scope(engine) as session:
        assert session.scalar(select(Run.status).where(Run.status != "completed")) == "failed"
