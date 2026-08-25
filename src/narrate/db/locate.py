"""Where the database lives, and what is in every copy of it.

A project directory is not a data directory. The default used to be
`PROJECT_ROOT / "narrate.db"`, which puts a **WAL-mode SQLite database holding
the money record** inside a tree that gets synced, backed up, duplicated in
Finder, watched by editors and copied around — and this repo already learned
that lesson once, when iCloud's handling of the project directory broke the
virtualenv badly enough to need `UV_PROJECT_ENVIRONMENT`.

The ledger is append-only and reconciliation replays it, so a torn or shadowed
copy is the worst failure this tool has. Three separate `narrate*.db` files
turned up in the project root during development; nothing in this codebase
creates them, which is precisely the problem — a database nobody's code owns is
a database anybody's tooling can duplicate.

So the default moved to a managed location, and this module can answer "which
databases exist and what is in each" without opening any of them for writing.
Nothing here migrates, moves or deletes on its own.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from narrate.settings import PROJECT_ROOT

# Tables worth counting when reporting on a database, in dependency order.
_COUNTED = ("project", "script", "chunk", "take", "effect", "effect_slot", "ledger_entry")


def _is_windows() -> bool:
    """Its own function so a test can substitute it.

    Patching `os.name` looks equivalent and is not: `pathlib` dispatches on it,
    so `Path.home()` starts raising `NotImplementedError` halfway through the
    very function under test.
    """
    return os.name == "nt"


def data_home() -> Path:
    """Where application data belongs on this machine.

    Three cases, in order:

    * `XDG_DATA_HOME` when set. It is what a Linux user expects, what a
      container usually sets, and the one override worth honouring everywhere.
    * `%LOCALAPPDATA%` on Windows. `~/.local/share` would *work* there, but it
      buries a database in a dotted directory Windows has no convention for and
      that no backup tool looks in.
    * `~/.local/share` otherwise, on macOS as well as Linux. Unusual on macOS —
      the platform convention is `~/Library/Application Support` — but keeping
      the two identical means one documented path instead of two, and it is
      where `uv` itself puts its data.
    """
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "narrate"
    if _is_windows():
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "narrate"
    return Path.home() / ".local" / "share" / "narrate"


def managed_db() -> Path:
    """The database this tool owns."""
    return data_home() / "narrate.db"


def legacy_db() -> Path:
    """Where the database used to default to, inside the project."""
    return PROJECT_ROOT / "narrate.db"


def default_db_path() -> Path:
    """The database to use when nothing overrides it.

    Prefers the managed location, but **falls back to a legacy database that
    already exists** rather than silently starting an empty one beside it. A
    tool that quietly stops seeing your ledger is worse than one that keeps
    using an awkward path and says so — `narrate db adopt` performs the move
    when the operator chooses to.
    """
    managed = managed_db()
    if managed.exists():
        return managed
    legacy = legacy_db()
    if legacy.exists():
        return legacy
    return managed


def is_inside_project(path: Path) -> bool:
    """Whether a path sits in the project tree, where a database should not."""
    try:
        path.resolve().relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class DatabaseInfo:
    """What a candidate database file turns out to be."""

    path: Path
    bytes: int
    revision: str | None
    counts: dict[str, int]
    ledger_micros: int
    real_charges: int
    readable: bool
    problem: str = ""

    @property
    def is_managed(self) -> bool:
        return self.path.resolve() == managed_db().resolve()

    @property
    def has_money(self) -> bool:
        """Whether anything in here was billed by the real provider.

        A mock-provider row cost nothing, so it is history worth keeping but not
        money worth protecting. The distinction is what makes it safe to say
        which files can go.
        """
        return self.real_charges > 0

    @property
    def sidecars(self) -> list[Path]:
        """The `-wal` and `-shm` files that belong with it.

        These matter: copying a WAL-mode database without its `-wal` loses every
        committed transaction still sitting in the log.
        """
        return [
            p
            for p in (
                self.path.with_name(self.path.name + "-wal"),
                self.path.with_name(self.path.name + "-shm"),
            )
            if p.exists()
        ]


def _read_only_uri(path: Path) -> str:
    """A URI that reads a database without leaving anything behind.

    `mode=ro` is not enough, and this is worth stating because it is a trap:
    opening a **WAL-mode** database read-only still makes SQLite build the WAL
    index, so it creates `-shm` and `-wal` files next to it. Inspecting three
    stray databases to find out whether they were safe to delete therefore
    produced six new files — from code whose entire purpose is to look without
    touching.

    `immutable=1` skips that machinery by promising the file cannot change. The
    promise is only safe when there is no pending write-ahead log: an immutable
    read sees the main database alone, so a non-empty `-wal` would be silently
    missed, and for the live database those are real committed rows. So the
    pledge is made only when there is nothing in the log to miss.
    """
    wal = path.with_name(path.name + "-wal")
    pending = wal.exists() and wal.stat().st_size > 0
    return f"file:{path}?mode=ro" if pending else f"file:{path}?immutable=1"


def inspect(path: Path) -> DatabaseInfo:
    """Read a database without writing to it or migrating it."""
    size = path.stat().st_size if path.exists() else 0
    counts: dict[str, int] = {}
    revision: str | None = None
    micros = 0
    real = 0

    try:
        connection = sqlite3.connect(_read_only_uri(path), uri=True)
    except sqlite3.Error as exc:
        return DatabaseInfo(path, size, None, {}, 0, 0, readable=False, problem=str(exc))

    try:
        try:
            row = connection.execute("select version_num from alembic_version").fetchone()
            revision = str(row[0]) if row else None
        except sqlite3.Error:
            revision = None

        for table in _COUNTED:
            try:
                counts[table] = int(
                    connection.execute(f"select count(*) from {table}").fetchone()[0]
                )
            except sqlite3.Error:
                continue

        try:
            micros = int(
                connection.execute(
                    "select coalesce(sum(cost_micros), 0) from ledger_entry"
                ).fetchone()[0]
            )
            # A `mock-` request id is the offline provider, which spent nothing.
            # Everything else was a real, billed request.
            real = int(
                connection.execute(
                    "select count(*) from ledger_entry "
                    "where request_id is not null and request_id not like 'mock-%'"
                ).fetchone()[0]
            )
        except sqlite3.Error:
            pass
    finally:
        connection.close()

    return DatabaseInfo(path, size, revision, counts, micros, real, readable=True)


def discover() -> list[DatabaseInfo]:
    """Every `narrate` database this machine appears to have.

    Looks in the managed location and across the project root, because the
    project root is where stray copies accumulate — including the ones macOS
    names `narrate 2.db` when something duplicates a file. Caches are skipped;
    `.mypy_cache` is full of files called `cache.0.db`.
    """
    seen: dict[Path, None] = {}
    candidates: list[Path] = [managed_db()]
    candidates += sorted(PROJECT_ROOT.glob("*.db"))

    out: list[DatabaseInfo] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen or not path.exists() or not path.is_file():
            continue
        seen[resolved] = None
        out.append(inspect(path))

    # The managed database first, then the largest — which is usually the one
    # with the most history, and therefore the one to keep.
    out.sort(key=lambda info: (not info.is_managed, -info.bytes))
    return out


def strays(infos: list[DatabaseInfo] | None = None) -> list[DatabaseInfo]:
    """Databases that are neither the managed one nor the active legacy one."""
    active = default_db_path().resolve()
    return [
        info
        for info in (infos if infos is not None else discover())
        if info.path.resolve() != active and not info.is_managed
    ]
