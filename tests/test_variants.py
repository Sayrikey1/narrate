"""Two performances of one episode, from the takes already on record.

Nothing new is stored. A take has always recorded the voice that produced it, so
generating a script twice in two voices leaves both sets side by side — and a
"variant" is only a different way of choosing between them. That is the whole
design, and these tests are what hold it to it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine, select

from narrate.audio import ffmpeg_path, make_silence, to_mp3
from narrate.db.models import Chunk, Cut, Project, Script, Take
from narrate.db.session import session_scope
from narrate.export import NothingToExport, export_script
from narrate.settings import Settings
from narrate.timeline import build_timeline, takes_by_voice, voices_used

from .conftest import needs_ffmpeg


def _script_with_takes(
    engine: Engine, tmp_path: Path, coverage: dict[str, list[int]], chunks: int = 3
) -> None:
    """A script of `chunks` chunks, with takes in each voice for the given ordinals.

    The audio is real mp3 rather than placeholder bytes: export genuinely stitches
    it with ffmpeg, so a fake file fails at the point the test is trying to
    exercise. Built once and shared, since the contents are irrelevant here.
    """
    audio = tmp_path / "take.mp3"
    audio.parent.mkdir(parents=True, exist_ok=True)
    if not audio.exists():
        if ffmpeg_path() is None:
            # Only the export tests decode this, and those are skipped without
            # ffmpeg. Building it unconditionally would fail every test in the
            # file on a machine that has not installed it yet.
            audio.write_bytes(b"ID3placeholder")
        else:
            wav = tmp_path / "take.wav"
            make_silence(wav, 1.0)
            to_mp3(wav, audio)
            wav.unlink(missing_ok=True)

    with session_scope(engine) as session:
        session.add(Project(name="P", model_id="eleven_v3", voice_id="original"))
        session.flush()
        session.add(Script(project_id=1, title="Ep", source_text="x", source_sha256="h"))
        session.flush()
        for i in range(1, chunks + 1):
            session.add(Chunk(script_id=1, ordinal=i, text=f"Chunk {i} says something."))
        session.flush()

        rows = {c.ordinal: c for c in session.scalars(select(Chunk)).all()}
        ordinal_counter = 0
        for voice, ordinals in coverage.items():
            for chunk_ordinal in ordinals:
                ordinal_counter += 1
                session.add(
                    Take(
                        chunk_id=rows[chunk_ordinal].id,
                        ordinal=ordinal_counter,
                        idempotency_key=f"{voice}-{chunk_ordinal}",
                        model_id="eleven_v3",
                        voice_id=voice,
                        submitted_text=rows[chunk_ordinal].text,
                        submitted_chars=len(rows[chunk_ordinal].text),
                        billed_chars=len(rows[chunk_ordinal].text),
                        cost_micros=100,
                        asset_path=str(audio),
                        duration_s=1.0,
                        status="succeeded",
                    )
                )


# ---------------------------------------------------------------------------
# Selecting by voice
# ---------------------------------------------------------------------------


def test_takes_by_voice_returns_only_that_voice(engine: Engine, tmp_path: Path) -> None:
    _script_with_takes(engine, tmp_path, {"original": [1, 2, 3], "cloned": [1, 2, 3]})
    with session_scope(engine) as session:
        for voice in ("original", "cloned"):
            picked = takes_by_voice(session, 1, voice)
            assert len(picked) == 3
            assert {t.voice_id for t in picked.values()} == {voice}


def test_the_newest_take_in_a_voice_wins(engine: Engine, tmp_path: Path) -> None:
    """The same rule the cut follows: re-rolling a line supersedes the earlier
    attempt rather than leaving the choice ambiguous."""
    _script_with_takes(engine, tmp_path, {"cloned": [1, 2, 3]})
    with session_scope(engine) as session:
        chunk = session.scalars(select(Chunk).where(Chunk.ordinal == 1)).one()
        session.add(
            Take(
                chunk_id=chunk.id,
                ordinal=99,
                idempotency_key="cloned-1-reroll",
                model_id="eleven_v3",
                voice_id="cloned",
                submitted_text="x",
                submitted_chars=1,
                asset_path="/tmp/x.mp3",
                status="succeeded",
            )
        )
        session.flush()
        assert takes_by_voice(session, 1, "cloned")[chunk.id].ordinal == 99


def test_a_failed_take_is_never_selected(engine: Engine, tmp_path: Path) -> None:
    _script_with_takes(engine, tmp_path, {"cloned": [1, 2, 3]})
    with session_scope(engine) as session:
        chunk = session.scalars(select(Chunk).where(Chunk.ordinal == 1)).one()
        session.add(
            Take(
                chunk_id=chunk.id,
                ordinal=50,
                idempotency_key="cloned-1-failed",
                model_id="eleven_v3",
                voice_id="cloned",
                submitted_text="x",
                submitted_chars=1,
                status="failed",
            )
        )
        session.flush()
        # The failure is newer, and must not displace the take that worked.
        assert takes_by_voice(session, 1, "cloned")[chunk.id].status == "succeeded"


def test_voices_used_reports_coverage_worst_last(engine: Engine, tmp_path: Path) -> None:
    _script_with_takes(engine, tmp_path, {"original": [1, 2, 3], "cloned": [1]})
    with session_scope(engine) as session:
        assert voices_used(session, 1) == [("original", 3), ("cloned", 1)]


# ---------------------------------------------------------------------------
# The timeline, which everything downstream derives from
# ---------------------------------------------------------------------------


def test_a_variant_timeline_uses_that_voices_takes(engine: Engine, tmp_path: Path) -> None:
    _script_with_takes(engine, tmp_path, {"original": [1, 2, 3], "cloned": [1, 2, 3]})
    with session_scope(engine) as session:
        variant = build_timeline(session, 1, gap_seconds=0.4, voice_id="cloned")
        assert len(variant.narration) == 3
        takes = {e.take_id for e in variant.narration}
        stored = {
            t.id for t in session.scalars(select(Take).where(Take.voice_id == "cloned")).all()
        }
        assert takes <= stored


def test_without_a_voice_the_timeline_still_follows_the_cut(engine: Engine, tmp_path: Path) -> None:
    """The default path must not change. A variant is an addition, not a
    replacement — the cut is still what `narrate export` means by default."""
    _script_with_takes(engine, tmp_path, {"original": [1, 2, 3], "cloned": [1, 2, 3]})
    with session_scope(engine) as session:
        chunks = session.scalars(select(Chunk).order_by(Chunk.ordinal)).all()
        for chunk in chunks:
            take = takes_by_voice(session, 1, "original")[chunk.id]
            session.add(Cut(chunk_id=chunk.id, script_id=1, take_id=take.id))
        session.flush()

        by_cut = build_timeline(session, 1, gap_seconds=0.4)
        assert {e.take_id for e in by_cut.narration} == {
            takes_by_voice(session, 1, "original")[c.id].id for c in chunks
        }


def test_an_incomplete_variant_is_not_complete(engine: Engine, tmp_path: Path) -> None:
    _script_with_takes(engine, tmp_path, {"cloned": [1, 3]})
    with session_scope(engine) as session:
        assert not build_timeline(session, 1, voice_id="cloned").is_complete


# ---------------------------------------------------------------------------
# Export — where the two masters actually appear
# ---------------------------------------------------------------------------


@needs_ffmpeg
def test_two_voices_export_to_separate_folders(engine: Engine, tmp_path: Path) -> None:
    """The point of the whole feature: one generation history, two masters,
    neither overwriting the other."""
    _script_with_takes(engine, tmp_path, {"original": [1, 2, 3], "cloned": [1, 2, 3]})
    settings = Settings(api_key=None, db_path=tmp_path / "d.db", assets_dir=tmp_path / "assets")

    first = export_script(engine, 1, settings, voice_id="original", variant_label="Brian")
    second = export_script(engine, 1, settings, voice_id="cloned", variant_label="My Voice")

    assert first.out_dir != second.out_dir
    assert Path(first.master).exists()
    assert Path(second.master).exists()
    # The voice is in the filename, so the two are distinguishable on disk.
    assert "brian" in Path(first.master).name.lower()
    assert "my-voice" in Path(second.master).name.lower()


@needs_ffmpeg
def test_a_variant_needs_no_cut_at_all(engine: Engine, tmp_path: Path) -> None:
    """A variant is selected by voice, so it exports even when no take has been
    promoted into the cut — which is the normal state right after a second run."""
    _script_with_takes(engine, tmp_path, {"cloned": [1, 2, 3]})
    settings = Settings(api_key=None, db_path=tmp_path / "d.db", assets_dir=tmp_path / "a")

    with session_scope(engine) as session:
        assert not session.scalars(select(Cut)).all(), "fixture should have no cut"

    result = export_script(engine, 1, settings, voice_id="cloned")
    assert Path(result.master).exists()


def test_a_partial_variant_is_refused_and_names_the_missing_lines(
    engine: Engine, tmp_path: Path
) -> None:
    """Shipping an episode with a silent hole is worse than shipping nothing, and
    the useful thing to say is which lines are missing *in that voice*."""
    _script_with_takes(engine, tmp_path, {"original": [1, 2, 3], "cloned": [1]})
    settings = Settings(api_key=None, db_path=tmp_path / "d.db", assets_dir=tmp_path / "a")

    with pytest.raises(NothingToExport) as raised:
        export_script(engine, 1, settings, voice_id="cloned")

    message = str(raised.value)
    assert "[2, 3]" in message
    assert "--only 2,3" in message


def test_a_voice_with_no_takes_is_refused(engine: Engine, tmp_path: Path) -> None:
    _script_with_takes(engine, tmp_path, {"original": [1, 2, 3]})
    settings = Settings(api_key=None, db_path=tmp_path / "d.db", assets_dir=tmp_path / "a")

    with pytest.raises(NothingToExport, match="never-generated"):
        export_script(engine, 1, settings, voice_id="never-generated")
