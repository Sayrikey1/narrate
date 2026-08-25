# narrate — task runner. `just` with no arguments lists everything by group.
set shell := ["bash", "-uc"]

# Keep the virtualenv OUT of the project directory.
#
# This repo lives under ~/Desktop, which macOS syncs to iCloud Drive. iCloud
# sets the UF_HIDDEN flag on files inside a venv, and Python 3.12's `site`
# module skips hidden .pth files — so `src/` never reaches sys.path and every
# `import narrate` fails, while `uv sync` still reports success. Putting the
# venv outside the synced tree avoids the whole problem.
#
# The database learned the same lesson: it now defaults to
# ~/.local/share/narrate/narrate.db rather than the project root, because three
# stray copies of it accumulated here. See `just db-status` and
# src/narrate/db/locate.py.
#
# Add this to your shell profile so a bare `uv run narrate` works too:
#   export UV_PROJECT_ENVIRONMENT="$HOME/.venvs/narrate"
export UV_PROJECT_ENVIRONMENT := env_var_or_default("UV_PROJECT_ENVIRONMENT", env_var("HOME") + "/.venvs/narrate")

# Every recipe below carries a [doc] attribute rather than relying on the
# comment above it. `just --list` uses only the *last* comment line, so a recipe
# explained in three lines was listed as a sentence fragment — "demo #
# production, for nothing." was a real entry in this file's own help output.

[private]
default:
    @just --list

# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------

[group('setup')]
[doc('Sync the venv from the lockfile.')]
sync:
    uv sync
    @uv run python scripts/repair_editable.py

[group('setup')]
[doc('Re-resolve dependencies, then sync.')]
lock:
    uv lock
    uv sync
    @uv run python scripts/repair_editable.py

[group('setup')]
[doc("Fix ModuleNotFoundError: No module named 'narrate'.")]
repair:
    uv run python scripts/repair_editable.py

[group('setup')]
[doc('Install the frontend dependencies.')]
ui-install:
    cd frontend && npm install

[group('setup')]
[doc('Check the environment: ffmpeg, database, API key, provider reachability.')]
doctor:
    uv run narrate doctor

# ---------------------------------------------------------------------------
# quality
# ---------------------------------------------------------------------------

[group('quality')]
[doc('Everything green, in the order that fails fastest.')]
check: lint typecheck test ui-test

[group('quality')]
[doc("Adds the frontend's production build to `check`.")]
check-all: check ui-build

[group('quality')]
[doc('Lint and format-check the Python.')]
lint:
    uv run ruff check .
    uv run ruff format --check .

[group('quality')]
[doc('Format and autofix the Python.')]
fmt:
    uv run ruff format .
    uv run ruff check --fix .

[group('quality')]
[doc('Type-check with mypy, strict.')]
typecheck:
    uv run mypy src tests

[group('quality')]
[doc('Python tests. Offline only — never touches the network, never spends.')]
test *ARGS:
    uv run pytest -m "not live" {{ARGS}}

[group('quality')]
[doc('Frontend tests. Skipped with a notice if the UI is not installed.')]
ui-test *ARGS:
    #!/usr/bin/env bash
    set -uo pipefail
    if [ ! -x frontend/node_modules/.bin/vitest ]; then
      echo "skipping UI tests — run: just ui-install" >&2
      exit 0
    fi
    cd frontend && npm test -- {{ARGS}}

# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------

[group('run')]
[doc('Both services for development: API on :8420, Vite on :5173 with hot reload.')]
up:
    #!/usr/bin/env bash
    set -uo pipefail

    if [ ! -x frontend/node_modules/.bin/vite ]; then
      echo "The UI is not installed. Run: just ui-install" >&2
      exit 1
    fi

    # Kill the two children by pid rather than `kill 0`. Signalling the whole
    # process group would also take out a parent shell when `just up` is run
    # without job control, which is a nasty way to learn about job control.
    cleanup() { kill "${API:-}" "${UI:-}" 2>/dev/null || true; }
    trap cleanup INT TERM EXIT

    echo "backend  http://127.0.0.1:8420"
    echo "frontend http://127.0.0.1:5173   <- open this one"
    echo "Ctrl+C stops both."
    echo

    uv run narrate serve --port 8420 &
    API=$!
    # `exec` so the subshell *becomes* vite: otherwise $! is the subshell and
    # killing it orphans the real server, still holding the port.
    ( cd frontend && exec node_modules/.bin/vite --port 5173 ) &
    UI=$!

    # macOS ships bash 3.2, which has no `wait -n`. Polling both is what lets
    # one service crashing tear down the other instead of leaving half a stack
    # up and apparently working.
    while kill -0 "$API" 2>/dev/null && kill -0 "$UI" 2>/dev/null; do
      sleep 1
    done

[group('run')]
[doc('Backend alone: API plus the built UI from frontend/dist, on :8420.')]
serve:
    uv run narrate serve

[group('run')]
[doc('Vite alone against a running backend. Prefer `just up`.')]
dev:
    cd frontend && npm run dev

[group('run')]
[doc('Type-check and build the UI. `narrate serve` picks up frontend/dist.')]
ui-build:
    cd frontend && npm run build

# ---------------------------------------------------------------------------
# database
# ---------------------------------------------------------------------------

[group('database')]
[doc('Which database is in use, and whether it is somewhere sensible.')]
db-status:
    uv run narrate db status

[group('database')]
[doc('Every narrate database on this machine, and what is in each.')]
db-list:
    uv run narrate db list

[group('database')]
[doc('Copy the database into ~/.local/share/narrate, verified. Deletes nothing.')]
db-adopt:
    uv run narrate db adopt

[group('database')]
[doc('Apply pending schema migrations.')]
db-migrate:
    uv run alembic upgrade head

[group('database')]
[doc('The current schema revision.')]
db-revision:
    uv run alembic current

# ---------------------------------------------------------------------------
# demos — these run the real pipeline and spend nothing
# ---------------------------------------------------------------------------

[group('offline')]
[doc('The whole pipeline end to end against a scratch database. Spends nothing.')]
demo:
    #!/usr/bin/env bash
    set -euo pipefail

    # A scratch database and asset tree, so a demo never touches real work or
    # real ledger rows.
    root="$(mktemp -d)"
    trap 'rm -rf "$root"' EXIT
    export NARRATE_PROVIDER=mock
    export NARRATE_DB_PATH="$root/demo.db"
    export NARRATE_ASSETS_DIR="$root/assets"

    echo "== scratch tree: $root"
    uv run narrate project new "Demo" --voice demo-voice
    uv run narrate script add examples/the-keepers-log.md --project Demo --title "The Keeper's Log"
    uv run narrate estimate 1
    uv run narrate generate 1 --go --yes
    uv run narrate timeline 1
    uv run narrate export 1
    uv run narrate cost report
    echo
    echo "== plan.md"
    find "$root/assets" -name plan.md -exec head -25 {} +

[group('offline')]
[doc('Two speakers, both ways: per-turn voices and one conversation. Spends nothing.')]
demo-dialogue:
    #!/usr/bin/env bash
    set -euo pipefail

    root="$(mktemp -d)"
    trap 'rm -rf "$root"' EXIT
    export NARRATE_PROVIDER=mock
    export NARRATE_DB_PATH="$root/demo.db"
    export NARRATE_ASSETS_DIR="$root/assets"

    uv run narrate project new "Argument" --model eleven_v3 --voice mock-voice-1
    uv run narrate cast set Argument Morag --voice mock-voice-1
    uv run narrate cast set Argument Inspector --voice mock-voice-2
    uv run narrate cast list Argument

    echo
    echo "== one chunk per turn (works on every model)"
    uv run narrate script add examples/the-lighthouse-argument.md -p Argument --title "Per turn"
    uv run narrate generate 1 --go --yes

    echo
    echo "== one conversation per group (eleven_v3 only)"
    uv run narrate script add examples/the-lighthouse-argument.md -p Argument --title "As dialogue" --dialogue
    uv run narrate generate 2 --go --yes
    uv run narrate cost report

# ---------------------------------------------------------------------------
# spends real money
# ---------------------------------------------------------------------------

[group('live')]
[doc('Settle what the docs do not say about billing. Spends about two cents.')]
probe:
    uv run narrate probe --live
