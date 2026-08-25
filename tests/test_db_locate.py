"""Where the database lives, and reporting on copies of it without touching them.

Three stray `narrate*.db` files accumulated in the project root during
development. Nothing in this codebase created them — which was the point: a
database that lives in a project tree is a database that sync services, editors
and Finder all feel free to duplicate, and the ledger is the one file here that
cannot afford a shadow copy.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from narrate.db import locate


def _make_db(
    path: Path, *, rows: list[tuple[str, int]] | None = None, revision: str | None = None
) -> Path:
    """A minimal database with a ledger, built without the ORM.

    Deliberately hand-rolled: these tests are about reading files that may be
    old, partial or from a schema that no longer exists, so building them
    through the current models would defeat the purpose.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("create table project (id integer primary key, name text)")
        connection.execute("create table script (id integer primary key)")
        connection.execute(
            "create table ledger_entry ("
            " id integer primary key, kind text, cost_micros integer, request_id text)"
        )
        connection.execute("insert into project (name) values ('P')")
        for request_id, micros in rows or []:
            connection.execute(
                "insert into ledger_entry (kind, cost_micros, request_id) values (?, ?, ?)",
                ("generation", micros, request_id),
            )
        if revision:
            connection.execute("create table alembic_version (version_num text)")
            connection.execute("insert into alembic_version values (?)", (revision,))
        connection.commit()
    finally:
        connection.close()
    return path


# ---------------------------------------------------------------------------
# Where it lives
# ---------------------------------------------------------------------------


def test_the_managed_location_is_outside_the_project() -> None:
    """The whole point. A project directory is not a data directory."""
    assert not locate.is_inside_project(locate.managed_db())


def test_the_legacy_location_is_inside_the_project() -> None:
    assert locate.is_inside_project(locate.legacy_db())


def test_xdg_data_home_is_honoured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    assert locate.data_home() == tmp_path / "share" / "narrate"


def test_without_xdg_it_falls_back_to_local_share(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(locate, "_is_windows", lambda: False)
    assert locate.data_home() == Path.home() / ".local" / "share" / "narrate"


def test_windows_uses_localappdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`~/.local/share` would work on Windows but buries a database in a dotted
    directory the platform has no convention for and no backup tool looks in."""
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(locate, "_is_windows", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    assert locate.data_home() == tmp_path / "AppData" / "Local" / "narrate"


def test_windows_without_localappdata_still_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(locate, "_is_windows", lambda: True)
    assert locate.data_home() == Path.home() / "AppData" / "Local" / "narrate"


def test_xdg_wins_on_every_platform(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    for windows in (False, True):
        monkeypatch.setattr(locate, "_is_windows", lambda w=windows: w)
        assert locate.data_home() == tmp_path / "narrate"


def test_an_existing_legacy_database_keeps_being_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A tool that quietly stops seeing your ledger is worse than one that keeps
    using an awkward path and says so."""
    monkeypatch.setattr(locate, "managed_db", lambda: tmp_path / "managed" / "narrate.db")
    legacy = _make_db(tmp_path / "project" / "narrate.db")
    monkeypatch.setattr(locate, "legacy_db", lambda: legacy)

    assert locate.default_db_path() == legacy


def test_the_managed_database_wins_once_it_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    managed = _make_db(tmp_path / "managed" / "narrate.db")
    monkeypatch.setattr(locate, "managed_db", lambda: managed)
    monkeypatch.setattr(locate, "legacy_db", lambda: _make_db(tmp_path / "project" / "narrate.db"))

    assert locate.default_db_path() == managed


def test_with_neither_it_names_the_managed_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(locate, "managed_db", lambda: tmp_path / "managed" / "narrate.db")
    monkeypatch.setattr(locate, "legacy_db", lambda: tmp_path / "project" / "narrate.db")

    assert locate.default_db_path() == tmp_path / "managed" / "narrate.db"


# ---------------------------------------------------------------------------
# Reading without touching — the part that had a bug
# ---------------------------------------------------------------------------


def test_inspecting_a_database_leaves_no_new_files(tmp_path: Path) -> None:
    """The trap this module exists to avoid.

    `mode=ro` is not enough: opening a WAL-mode database read-only still makes
    SQLite build the WAL index, so it creates `-shm` and `-wal` beside it.
    Inspecting three strays to decide whether they were safe to delete produced
    six new files — from code whose whole purpose is to look without touching.
    """
    db = _make_db(tmp_path / "narrate.db", rows=[("mock-a", 100)])
    sqlite3.connect(db).execute("PRAGMA journal_mode=WAL").close()
    for sidecar in tmp_path.glob("*.db-*"):
        sidecar.unlink()
    before = sorted(p.name for p in tmp_path.iterdir())

    locate.inspect(db)

    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_a_pending_write_ahead_log_is_still_read(tmp_path: Path) -> None:
    """The other half of that trade.

    An immutable read sees the main file alone, so it would miss rows sitting in
    the log — and for the live database those are committed charges. So the
    immutable pledge is only made when the log is empty.
    """
    db = tmp_path / "narrate.db"
    _make_db(db)
    connection = sqlite3.connect(db)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        "insert into ledger_entry (kind, cost_micros, request_id) "
        "values ('generation', 4200, 'real')"
    )
    connection.commit()
    try:
        assert (db.with_name("narrate.db-wal")).stat().st_size > 0, "expected a pending log"
        info = locate.inspect(db)
        assert info.ledger_micros == 4200
        assert info.real_charges == 1
    finally:
        connection.close()


def test_inspect_reports_what_is_in_a_database(tmp_path: Path) -> None:
    db = _make_db(
        tmp_path / "narrate.db",
        rows=[("mock-a", 100), ("mock-b", 200), ("J9jAKwSUNrHZiB7dQqWa", 700)],
        revision="9a1c4f7be2d0",
    )
    info = locate.inspect(db)

    assert info.readable
    assert info.revision == "9a1c4f7be2d0"
    assert info.counts["ledger_entry"] == 3
    assert info.ledger_micros == 1000


def test_only_a_real_request_id_counts_as_money(tmp_path: Path) -> None:
    """A mock-provider row is history worth keeping but not money worth
    protecting, and that distinction is what makes it safe to say which files
    can go."""
    offline = locate.inspect(_make_db(tmp_path / "a.db", rows=[("mock-x", 500), ("mock-y", 500)]))
    real = locate.inspect(_make_db(tmp_path / "b.db", rows=[("mock-x", 500), ("UlC87wRq", 500)]))

    assert offline.ledger_micros == 1000
    assert not offline.has_money
    assert real.has_money
    assert real.real_charges == 1


def test_an_unmigrated_database_reports_no_revision(tmp_path: Path) -> None:
    info = locate.inspect(_make_db(tmp_path / "narrate.db"))
    assert info.revision is None
    assert info.readable


def test_a_missing_file_is_reported_rather_than_raising(tmp_path: Path) -> None:
    info = locate.inspect(tmp_path / "nope.db")
    assert not info.readable or info.counts == {}


def test_a_file_that_is_not_a_database_is_reported_rather_than_raising(tmp_path: Path) -> None:
    junk = tmp_path / "narrate.db"
    junk.write_text("this is not a database")
    info = locate.inspect(junk)
    # Either unreadable, or readable with nothing in it — never an exception.
    assert info.counts == {}


def test_sidecars_are_listed_when_they_exist(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "narrate.db")
    assert locate.inspect(db).sidecars == []
    db.with_name("narrate.db-wal").write_bytes(b"")
    assert [p.name for p in locate.inspect(db).sidecars] == ["narrate.db-wal"]


# ---------------------------------------------------------------------------
# The observed-model cache moved for the same reason the database did
# ---------------------------------------------------------------------------


def test_the_observed_cache_lives_with_the_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`narrate models --sync` writes one account's view of the API. Generated
    and account-specific, so it belongs beside the ledger — not in the project,
    where it was neither gitignored nor anybody's to commit."""
    from narrate import registry

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(registry, "_LEGACY_OBSERVED", tmp_path / "absent.json")

    assert registry.observed_path() == tmp_path / "narrate" / "observed.json"
    assert not locate.is_inside_project(registry.observed_path())


def test_an_existing_in_project_cache_keeps_being_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Same courtesy the database gets: an older checkout keeps its cache until
    the next `--sync` writes to the new place."""
    from narrate import registry

    legacy = tmp_path / "narrate.observed.json"
    legacy.write_text('{"fetched_at": "x", "models": {}}')
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setattr(registry, "_LEGACY_OBSERVED", legacy)

    assert registry.observed_path() == legacy


def test_the_managed_cache_wins_once_it_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from narrate import registry

    legacy = tmp_path / "narrate.observed.json"
    legacy.write_text("{}")
    managed = tmp_path / "share" / "narrate" / "observed.json"
    managed.parent.mkdir(parents=True)
    managed.write_text("{}")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setattr(registry, "_LEGACY_OBSERVED", legacy)

    assert registry.observed_path() == managed


def test_saving_the_cache_creates_its_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The managed directory may not exist on a first sync."""
    from narrate import registry

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "fresh"))
    monkeypatch.setattr(registry, "_LEGACY_OBSERVED", tmp_path / "absent.json")

    written = registry.save_observed(
        [{"model_id": "eleven_v3", "maximum_text_length_per_request": 5000}], "2026-01-01"
    )
    assert written.exists()
    assert "eleven_v3" in written.read_text()
