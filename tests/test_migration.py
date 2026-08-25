"""Schema migration. The property under test is that a ledger is never lost."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, select, text

from narrate.db.models import Base, LedgerEntry, Project
from narrate.db.session import init_db, make_engine, session_scope

NEW_TABLES = ("effect", "effect_slot")
NEW_CHUNK_COLUMNS = ("start_offset", "target_start_s")


def _build_pre_alembic_database(path: Path) -> None:
    """Recreate the first release's schema: no effects, no version table."""
    engine = make_engine(path)
    meta = Base.metadata
    old_tables = [t for name, t in meta.tables.items() if name not in NEW_TABLES]
    chunk = meta.tables["chunk"]
    dropped = [chunk.c[name] for name in NEW_CHUNK_COLUMNS]
    for column in dropped:
        chunk._columns.remove(column)
    try:
        meta.create_all(engine, tables=old_tables)
    finally:
        for column in dropped:
            chunk.append_column(column)

    with session_scope(engine) as session:
        session.add(Project(name="legacy", model_id="eleven_v3"))
        session.flush()
        session.add(
            LedgerEntry(project_id=1, kind="generation", billed_chars=4321, cost_micros=432_100)
        )
    engine.dispose()


def test_a_pre_alembic_database_upgrades_without_losing_its_ledger(tmp_path: Path) -> None:
    db = tmp_path / "legacy.db"
    _build_pre_alembic_database(db)

    engine = make_engine(db)
    assert "alembic_version" not in inspect(engine).get_table_names()

    init_db(engine)

    tables = set(inspect(engine).get_table_names())
    columns = {c["name"] for c in inspect(engine).get_columns("chunk")}
    assert set(NEW_TABLES) <= tables
    assert set(NEW_CHUNK_COLUMNS) <= columns

    with session_scope(engine) as session:
        rows = list(session.scalars(select(LedgerEntry)).all())
        stamped = session.execute(text("SELECT version_num FROM alembic_version")).scalar()

    # The point of the whole exercise.
    assert len(rows) == 1
    assert rows[0].cost_micros == 432_100
    assert rows[0].billed_chars == 4321
    assert stamped


def test_adoption_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "legacy.db"
    _build_pre_alembic_database(db)
    engine = make_engine(db)
    init_db(engine)
    init_db(engine)  # must not raise or duplicate anything
    with session_scope(engine) as session:
        assert len(list(session.scalars(select(LedgerEntry)).all())) == 1


def test_a_fresh_database_is_stamped_at_head(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "fresh.db")
    init_db(engine)
    with session_scope(engine) as session:
        assert session.execute(text("SELECT version_num FROM alembic_version")).scalar()


def test_the_append_only_guard_survives_migration(tmp_path: Path) -> None:
    """A trigger lost during an upgrade would silently un-protect the ledger."""
    import pytest

    db = tmp_path / "legacy.db"
    _build_pre_alembic_database(db)
    engine = make_engine(db)
    init_db(engine)

    with pytest.raises(Exception, match="append-only"), engine.begin() as conn:
        conn.execute(text("UPDATE ledger_entry SET cost_micros = 0"))


# -- the units/provider migration -------------------------------------------


def _build_previous_release(path: Path) -> None:
    """The schema as it stood before units, provider and nullable project_id.

    Built by creating the current schema and then removing what came after that
    release, since `init_db` only knows how to make the latest one. Anything a
    later migration adds has to be undone here too, or replaying that migration
    fails on an object that already exists — which is what the cast tables did.
    """
    engine = make_engine(path)
    init_db(engine)
    with engine.begin() as conn:
        for column in ("units", "unit_kind", "provider"):
            conn.execute(text(f"ALTER TABLE ledger_entry DROP COLUMN {column}"))
        # Added by 9a1c4f7be2d0, two revisions later.
        conn.execute(text("DROP TABLE IF EXISTS cast_member"))
        conn.execute(text("ALTER TABLE chunk DROP COLUMN turns_json"))
        conn.execute(text("ALTER TABLE take DROP COLUMN voices_json"))
        conn.execute(
            text(
                "INSERT INTO ledger_entry "
                "(ts, project_id, kind, model_id, billed_chars, cost_micros, credits, "
                " rate_usd_per_1k, rate_card_version, cost_source, note) "
                "VALUES "
                "('2026-08-01 10:00:00', 1, 'generation', 'eleven_v3', 4321, 432100, 4321, "
                " 0.1, '2026-08-21', 'header', ''),"
                "('2026-08-01 11:00:00', 1, 'generation', 'eleven_v3', 1000, 100000, 1000, "
                " 0.1, '2026-08-21', 'header', '')"
            )
        )
        # A pre-migration database is also stamped one revision back.
        conn.execute(text("UPDATE alembic_version SET version_num = '20f6acf94ce2'"))
    engine.dispose()


def test_units_migration_leaves_historical_totals_untouched(tmp_path: Path) -> None:
    """The property that matters: no figure already recorded may move.

    Reconciliation replays this table, so a migration that shifted a total
    would be worse than no migration at all.
    """
    db = tmp_path / "prev.db"
    _build_previous_release(db)

    engine = make_engine(db)
    # Raw SQL, because the ORM already expects the columns the migration adds.
    with engine.begin() as conn:
        before = conn.execute(
            text("SELECT cost_micros, credits, billed_chars FROM ledger_entry ORDER BY id")
        ).all()

    init_db(engine)  # runs the migration

    with session_scope(engine) as session:
        rows = list(session.scalars(select(LedgerEntry).order_by(LedgerEntry.id)).all())
        after = [(e.cost_micros, e.credits, e.billed_chars) for e in rows]

    assert after == [tuple(row) for row in before]
    assert sum(e.cost_micros for e in rows) == 532_100


def test_units_migration_backfills_the_new_columns(tmp_path: Path) -> None:
    db = tmp_path / "prev.db"
    _build_previous_release(db)
    engine = make_engine(db)
    init_db(engine)

    with session_scope(engine) as session:
        for entry in session.scalars(select(LedgerEntry)).all():
            # Everything recorded before this change was ElevenLabs speech.
            assert entry.units == entry.billed_chars
            assert entry.unit_kind == "characters"
            assert entry.provider == "elevenlabs"


def test_account_level_entries_may_have_no_project(tmp_path: Path) -> None:
    """A diagnostic probe belongs to no project; the column has to allow it."""
    engine = make_engine(tmp_path / "fresh.db")
    init_db(engine)
    with session_scope(engine) as session:
        session.add(
            LedgerEntry(
                project_id=None,
                kind="probe",
                units=59,
                unit_kind="characters",
                cost_micros=5900,
            )
        )
    with session_scope(engine) as session:
        entry = session.scalars(select(LedgerEntry)).one()
        assert entry.project_id is None
