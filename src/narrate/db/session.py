"""Engine, pragmas, schema creation, and the append-only enforcement.

The ledger's immutability is enforced by SQLite triggers rather than by
convention. PRD §7 makes reconciliation depend on an immutable history, and a
rule that lives only in a code review is not a rule — any future FastAPI
handler or stray `session.merge()` would quietly break it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

if TYPE_CHECKING:
    from alembic.config import Config

from narrate.db.models import Base
from narrate.db.triggers import APPEND_ONLY_TRIGGERS
from narrate.settings import PROJECT_ROOT, get_settings


def _set_pragmas(dbapi_connection: Any, _record: Any) -> None:
    """SQLite-only session settings. Attached per engine, never globally."""
    cursor = dbapi_connection.cursor()
    # WAL lets the reader in `cost report` run while a generation run writes.
    cursor.execute("PRAGMA journal_mode=WAL")
    # Off by default in SQLite; without it the cascades in the schema are decorative.
    cursor.execute("PRAGMA foreign_keys=ON")
    # Concurrent takes finishing at once should wait, not raise "database is locked".
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def make_engine(db_path: Path | str | None = None, echo: bool = False) -> Engine:
    """Build the engine.

    Accepts a path (the usual case), a full SQLAlchemy URL, or nothing — in
    which case `NARRATE_DATABASE_URL` wins over `db_path`. The dialect-specific
    setup below is conditional so that pointing this at another database is a
    configuration change rather than a code change.
    """
    settings = get_settings()
    target = str(db_path) if db_path is not None else None

    if target == ":memory:":
        # Every connection in a test must see the same in-memory database.
        engine = create_engine(
            "sqlite://",
            echo=echo,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    else:
        if target is None:
            url = settings.sqlalchemy_url
        elif "://" in target:
            url = target
        else:
            url = f"sqlite:///{target}"

        if url.startswith("sqlite:///"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, echo=echo)

    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _set_pragmas)
    return engine


def _alembic_config(engine: Engine) -> Config:
    from alembic.config import Config

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", str(engine.url))
    return cfg


def _adopt_pre_alembic(engine: Engine) -> None:
    """Bring a database created before Alembic up to head without losing it.

    The first release created its schema with `create_all` and no version
    table, so Alembic has no idea where such a database stands. Creating the
    missing tables and columns directly, then stamping head, upgrades it in
    place — the alternative would be recreating it, which would discard the
    ledger this whole tool exists to keep.

    One-time path: once stamped, every future change is an ordinary migration.
    """
    from alembic import command

    Base.metadata.create_all(engine)  # adds tables the old schema lacked

    # `create_all` above adds missing *tables* but never a missing column, so
    # every column added since the pre-Alembic release has to be named here.
    # Anything a later migration adds to an existing table has to be added to
    # this map too, or adoption stamps head on a database that lacks it.
    additions: dict[str, dict[str, str]] = {
        "chunk": {
            "start_offset": "INTEGER",
            "target_start_s": "FLOAT",
            "turns_json": "TEXT",
            "chapter_title": "VARCHAR(120)",
        },
        "project": {
            "description_boilerplate": "TEXT NOT NULL DEFAULT ''",
            "default_tags": "TEXT NOT NULL DEFAULT ''",
            "archived_at": "DATETIME",
        },
        "script": {
            "description": "TEXT NOT NULL DEFAULT ''",
            "tags": "TEXT NOT NULL DEFAULT ''",
            "target_seconds": "FLOAT",
            "archived_at": "DATETIME",
        },
        "take": {
            "voices_json": "TEXT",
            "verify_status": "VARCHAR(16) NOT NULL DEFAULT 'unverified'",
            "verify_findings_json": "TEXT",
            "verifier": "VARCHAR(96)",
            "verified_at": "DATETIME",
        },
    }
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, columns in additions.items():
            existing = {c["name"] for c in inspector.get_columns(table)}
            for column, sql_type in columns.items():
                if column not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))

    command.stamp(_alembic_config(engine), "head")


def init_db(engine: Engine, migrate: bool | None = None) -> None:
    """Bring the schema to head and install the append-only guards. Idempotent.

    `migrate` defaults to skipping Alembic for in-memory databases: there is
    nothing to migrate in a database that did not exist a moment ago, and the
    test suite creates hundreds of them.
    """
    from alembic import command

    if migrate is None:
        migrate = engine.url.database not in (None, ":memory:")

    tables = set(inspect(engine).get_table_names())

    if not tables:
        # Fresh database. `create_all` produces exactly what the migrations
        # would, without paying to replay them.
        Base.metadata.create_all(engine)
        if migrate:
            command.stamp(_alembic_config(engine), "head")
    elif "alembic_version" not in tables:
        _adopt_pre_alembic(engine)
    elif migrate:
        command.upgrade(_alembic_config(engine), "head")

    # The append-only guard is written in SQLite's trigger dialect. On another
    # backend it has to be re-expressed rather than skipped — better to say so
    # loudly than to run without the protection the ledger depends on.
    if engine.dialect.name == "sqlite":
        with engine.begin() as conn:
            for statement in APPEND_ONLY_TRIGGERS:
                conn.execute(text(statement))
    else:
        raise NotImplementedError(
            f"The append-only ledger guard is SQLite-specific; {engine.dialect.name} "
            "needs its own trigger definitions before it can be used."
        )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """A transactional session that commits on success and rolls back on error."""
    session = make_session_factory(engine)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def open_db(db_path: Path | str | None = None) -> Engine:
    """The one call every CLI command makes: engine, schema, guards."""
    engine = make_engine(db_path)
    init_db(engine)
    return engine
