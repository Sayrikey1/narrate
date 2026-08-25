# 🛠️ Install and setup

Getting `narrate` running on **macOS**, **Linux** or **Windows**, from a fresh
clone to a generated episode — without spending a cent until you choose to.

> 📋 **Tested on:** macOS 15 (Apple Silicon). The Linux and Windows paths follow
> from the same Python, `uv` and ffmpeg tooling and the code has no Unix-only
> calls, but they have not been run end to end by the author. If something here
> is wrong on your platform, that is a bug worth reporting.

---

## 📦 What you need

| Tool | Why | Minimum |
| --- | --- | --- |
| **Python** | The pipeline | 3.12 (and *only* 3.12 — see the note below) |
| **[uv](https://docs.astral.sh/uv/)** | Dependency and venv management | any recent |
| **ffmpeg + ffprobe** | Stitching, duration measurement, format conversion | any recent |
| **Node.js** | The web UI. Skip it if you only want the CLI | 20+ |
| **[just](https://github.com/casey/just)** | Task shortcuts. Optional — every recipe is a plain command underneath | 1.27+ |
| **An ElevenLabs API key** | Only for real audio. The offline provider needs nothing | — |

> ⚠️ **Python 3.12 exactly.** `pyproject.toml` pins `>=3.12,<3.13`, which is
> what the project is developed and tested against. `uv` will fetch 3.12 for you
> if you do not have it, so this is not something you need to install by hand.
> Nothing is known to break on 3.13 — the upper bound is caution, not a
> diagnosed incompatibility.

---

## 🍎 macOS

```bash
# 1. Tooling
brew install ffmpeg node just
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Clone
git clone <your-fork-url> narrate
cd narrate

# 3. Keep the virtualenv outside the project — see the warning below
export UV_PROJECT_ENVIRONMENT="$HOME/.venvs/narrate"
echo 'export UV_PROJECT_ENVIRONMENT="$HOME/.venvs/narrate"' >> ~/.zshrc

# 4. Install
uv sync
just ui-install     # skip if you only want the CLI

# 5. Check
just doctor
```

> 🚨 **If the project lives under `~/Desktop` or `~/Documents` and you have
> iCloud Drive syncing them, the virtualenv must go elsewhere.** iCloud sets the
> `UF_HIDDEN` flag on files inside the venv, and Python 3.12's `site` module
> deliberately skips hidden `.pth` files — so `src/` never reaches `sys.path`
> and every `import narrate` fails, while `uv sync` still reports success. It is
> a genuinely misleading failure: the file is there, `cat` shows the right
> contents, `open()` reads it fine, and only `site` refuses it, silently.
>
> `UV_PROJECT_ENVIRONMENT` above avoids it entirely. If you hit it anyway, run
> `just repair` — it clears the flag and is a harmless no-op on any other
> platform.

---

## 🐧 Linux

```bash
# 1. Tooling — Debian / Ubuntu
sudo apt update && sudo apt install -y ffmpeg nodejs npm
curl -LsSf https://astral.sh/uv/install.sh | sh
# `just` is often packaged; otherwise: cargo install just
sudo apt install -y just

# Fedora:  sudo dnf install ffmpeg nodejs just
# Arch:    sudo pacman -S ffmpeg nodejs npm just

# 2. Clone and install
git clone <your-fork-url> narrate
cd narrate
uv sync
just ui-install     # skip if you only want the CLI

# 3. Check
just doctor
```

> 💡 On Linux the venv can live in the project — there is no sync service
> mangling it — so `UV_PROJECT_ENVIRONMENT` is optional. Set it anyway if the
> project sits in a Dropbox, Nextcloud or OneDrive folder, for exactly the
> reason described in the macOS section.

> ⚠️ Debian and Ubuntu sometimes ship `ffmpeg` without the `libmp3lame` encoder.
> `just doctor` will not catch that, but an export will. If `mp3` output fails,
> check `ffmpeg -encoders | grep mp3` and install `ffmpeg` from a fuller source.

---

## 🪟 Windows

Two supported shapes. Pick one and stay in it.

### Option A — PowerShell (native)

```powershell
# 1. Tooling
winget install Gyan.FFmpeg
winget install OpenJS.NodeJS
winget install Casey.Just
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2. Clone and install
git clone <your-fork-url> narrate
cd narrate
uv sync
just ui-install     # skip if you only want the CLI

# 3. Check
uv run narrate doctor
```

> ⚠️ **`just` recipes need a POSIX shell.** The `justfile` sets
> `set shell := ["bash", "-uc"]`, and several recipes are bash scripts. In
> PowerShell, `just check`, `just up`, `just demo` and `just demo-dialogue` will
> not run. Everything else in this project will — every recipe is a thin wrapper
> around a plain command, and the [equivalents are listed
> below](#-without-just). Git for Windows installs a usable `bash`; putting it
> on `PATH` makes the recipes work.

### Option B — WSL 2 (recommended)

The whole Linux path works unchanged, and you get a POSIX shell for `just`:

```bash
wsl --install -d Ubuntu     # in PowerShell, once
# then inside WSL, follow the Linux section above
```

> 💡 Keep the clone **inside** the WSL filesystem (`~/narrate`), not on
> `/mnt/c/...`. Cross-filesystem I/O is slow enough to be noticeable when ffmpeg
> is stitching a 30-minute episode, and file-watching does not work reliably
> across the boundary.

> 🔊 Verify ffmpeg is really on `PATH` before anything else — `winget`'s ffmpeg
> package has historically not added it. `ffmpeg -version` in a **new** terminal
> is the test. `narrate doctor` reports it either way.

---

## 🔑 Providing an API key

Only needed for real audio. Everything else — chunking, cost estimates, the
whole UI, voice previews of the offline stand-ins — works without one.

Copy the template and fill it in:

```bash
cp .env.example .env        # macOS/Linux
Copy-Item .env.example .env # PowerShell
```

```ini
ELEVEN_API=sk_your_key_here

# Optional: only if you want `narrate effects suggest`, which asks an LLM
# where effect cues might belong. Nothing it proposes is ever generated
# without you accepting it.
GROQ_API_KEY=gsk_your_key_here
```

`ELEVENLABS_API_KEY` and `NARRATE_API_KEY` are accepted as aliases.

> 🔒 The key is read from the environment only. It is never written to the
> database, never logged, and never returned by the API. `.env` is gitignored;
> `.env.example` is the committed template.

---

## 🗄️ Provisioning the database

**You do not have to do anything.** The database is created on first use, and
migrations run automatically. This section is for when you want to know where it
went, or move it.

### Where it lives

| Platform | Default |
| --- | --- |
| macOS, Linux | `~/.local/share/narrate/narrate.db` |
| Windows | `%LOCALAPPDATA%\narrate\narrate.db` |
| Any, if set | `$XDG_DATA_HOME/narrate/narrate.db` |

It is **deliberately outside the project directory.** A project tree gets
synced, backed up, duplicated in Finder and watched by editors, and this is a
WAL-mode SQLite file holding an append-only ledger that reconciliation replays.
Three stray copies of it accumulated in the project root during development, and
nothing in the codebase created them — which is exactly the problem. A database
nobody's code owns is one anybody's tooling can duplicate.

### Inspecting it

```bash
narrate db status    # which database is in use, and whether that is sensible
narrate db list      # every narrate database on the machine, and what is in each
```

`db list` answers the question you will eventually have — *why do I have three
of these, and which can I delete?* It distinguishes a file whose charges all
came from the offline provider (cost nothing, safe to bin) from one with real
request ids (money, keep it):

```text
                                  Databases
┏━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━┓
┃ file         ┃ role   ┃ ledger rows ┃ recorded ┃ real money ┃ revision     ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━┩
│ narrate.db   │ in use │          21 │  $0.4927 │ yes (6)    │ 9a1c4f7be2d0 │
│ narrate 2.db │ stray  │           0 │  $0.0000 │ no         │ —            │
└──────────────┴────────┴─────────────┴──────────┴────────────┴──────────────┘
```

Reading a database is genuinely side-effect free, which took some care:
`mode=ro` is **not** enough, because a read-only open of a WAL database still
builds the WAL index and creates `-shm`/`-wal` files beside it.

### Moving one

Upgrading from a version that kept the database in the project root? It keeps
being used, and `narrate db status` says so. Move it when you are ready:

```bash
narrate db adopt
```

It checkpoints the write-ahead log first — copying a WAL database without
folding in its `-wal` silently drops every transaction still in the log, which
here means dropping charges — then copies, verifies row counts match, and
**leaves your original untouched**. Nothing in this tool deletes a database.

### What else lives there

`narrate models --sync` caches one account's view of `GET /v1/models` beside the
database, as `observed.json`. It narrows declared limits — never rewrites a rate
— and is regenerated on demand, so it is safe to delete and pointless to commit.

### Putting it somewhere else

```bash
export NARRATE_DB_PATH=/mnt/backed-up/narrate.db     # macOS/Linux
$env:NARRATE_DB_PATH = "D:\narrate\narrate.db"       # PowerShell
```

### Schema migrations

```bash
narrate doctor          # reports the revision
just db-revision        # or: uv run alembic current
just db-migrate         # or: uv run alembic upgrade head
```

Run automatically on first connection, so you only need these to check or to
migrate a database you moved in by hand.

> 🐘 **Postgres?** No. `NARRATE_DATABASE_URL` exists as a seam so moving is
> configuration rather than a rewrite, but no dialect other than SQLite is
> supported or tested. For a single operator, SQLite is the right answer.

---

## ✅ Verify the install

```bash
just doctor
```

```text
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Check           ┃ Status    ┃ Detail                                        ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ ffmpeg          │ ok        │ /opt/homebrew/bin/ffmpeg                      │
│ ffprobe         │ ok        │ /opt/homebrew/bin/ffprobe                     │
│ database        │ ok        │ ~/.local/share/narrate/narrate.db             │
│ api key         │ set       │ ELEVEN_API in .env                            │
│ media           │ empty     │ ./assets                                      │
│ rate card       │ ok        │ 5 models, version 2026-08-21                  │
│ provider        │ reachable │ tier=payg used=7,243/48,461                    │
└─────────────────┴───────────┴───────────────────────────────────────────────┘
```

Then run the whole pipeline **without spending anything** — it uses an offline
provider that produces real, playable audio and reports costs the way the API
does, so the ledger, chunker, runner and export all take the identical code
path:

```bash
just demo               # one voice, five chunks, five cues, exported
just demo-dialogue      # two speakers, both modes
just check              # 496 Python + 67 frontend tests
```

---

## ✍️ Writing your first script

Do not start from a blank file. Write a template and edit it:

```bash
narrate script templates                              # single-voice, multi-voice
narrate script template single-voice -o my-episode.md
```

Every explanation in it sits in an HTML comment, which is stripped before
anything reaches the provider — so you can generate from the file as it stands,
then replace the prose with your own. The same two templates are download
buttons beside the upload box in the web UI.

---

## ▶️ Running it

### The web UI

```bash
just up
#   backend  http://127.0.0.1:8420
#   frontend http://127.0.0.1:5173   <- open this one
#   Ctrl+C stops both
```

For normal use, build the UI once and let the backend serve it on one port:

```bash
just ui-build
just serve              # http://127.0.0.1:8420
```

### Spending nothing while you click around

```bash
NARRATE_PROVIDER=mock just up            # macOS/Linux
$env:NARRATE_PROVIDER="mock"; just serve # PowerShell
```

### 🧰 Without `just`

Every recipe is a thin wrapper. The full list is in the `justfile`; these are
the ones you will want:

| Instead of | Run |
| --- | --- |
| `just doctor` | `uv run narrate doctor` |
| `just serve` | `uv run narrate serve` |
| `just ui-build` | `cd frontend && npm run build` |
| `just ui-install` | `cd frontend && npm install` |
| `just test` | `uv run pytest -m "not live"` |
| `just lint` | `uv run ruff check . && uv run ruff format --check .` |
| `just typecheck` | `uv run mypy src tests` |
| `just db-status` | `uv run narrate db status` |
| `just db-migrate` | `uv run alembic upgrade head` |
| `just up` | Two terminals: `uv run narrate serve --port 8420` and `cd frontend && npm run dev` |

---

## 🩹 Troubleshooting

<details>
<summary><strong><code>ModuleNotFoundError: No module named 'narrate'</code></strong></summary>

The editable install is not on `sys.path`. On macOS with iCloud syncing the
project folder, this is the `UF_HIDDEN` problem described above.

```bash
just repair                                        # clears the flag
export UV_PROJECT_ENVIRONMENT="$HOME/.venvs/narrate"
uv sync                                            # then re-sync
```

Elsewhere, `uv sync` on its own usually fixes it.
</details>

<details>
<summary><strong><code>ffmpeg is not on PATH</code></strong></summary>

`narrate doctor` reports this. Install it, then open a **new** terminal — a
running shell will not pick up a `PATH` change.

```bash
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Debian/Ubuntu
winget install Gyan.FFmpeg   # Windows
```

Generation works without ffmpeg; measuring durations, stitching and exporting do
not.
</details>

<details>
<summary><strong><code>Address already in use</code></strong></summary>

A previous run is still alive.

```bash
# macOS/Linux
pkill -f "narrate serve"; pkill -f "bin/vite"
lsof -nP -iTCP:8420 -iTCP:5173 -sTCP:LISTEN     # confirm both are free

# Windows PowerShell
Get-Process -Name python,node | Stop-Process
netstat -ano | Select-String ":8420|:5173"
```

Or use different ports: `uv run narrate serve --port 9000`.
</details>

<details>
<summary><strong>The UI loads but every page is blank</strong></summary>

`narrate serve` serves `frontend/dist`, which does not exist until you build
it. Run `just ui-build`. The API answers regardless, and says so.
</details>

<details>
<summary><strong><code>just: command not found</code>, or recipes fail on Windows</strong></summary>

`just` is optional — see [Without `just`](#-without-just). On Windows the
recipes need a POSIX shell; install Git for Windows and put its `bash` on
`PATH`, or use WSL.
</details>

<details>
<summary><strong>Playback is silent</strong></summary>

If the audio was generated with `NARRATE_PROVIDER=mock`, it *is* silent — the
offline provider writes real, playable mp3 containing silence, by design.

```bash
narrate cost log            # a `mock-` prefix in the note means offline
ffmpeg -i <file> -af volumedetect -f null -   # -91 dB is the silence floor
```

Offline **voice previews** play an audible tone instead, precisely so a working
preview button cannot be mistaken for a broken one.
</details>

<details>
<summary><strong>I have several <code>narrate*.db</code> files</strong></summary>

```bash
narrate db list
```

It tells you which is live, what is in each, and whether any of it was real
money. Then delete the ones you do not want — the tool will not do it for you.
</details>

---

## 💸 What costs money

Almost nothing does, and nothing does by accident.

| | |
| --- | --- |
| ✅ **Free** | `models`, `voices`, `estimate`, `chunk review`, `timeline`, `plan`, `formats`, `media`, `cost *`, `db *`, every voice audition, and the entire UI with `NARRATE_PROVIDER=mock` |
| ⚠️ **Asks first** | `narrate generate` dry-runs unless you pass `--go`, and even then shows the projected split and waits |
| 💰 **Spends** | `narrate probe --live` — about two cents, and the only command whose whole purpose is to spend |

Set a ceiling per project and the tool enforces it:

```bash
narrate project set "My Channel" --monthly-cap 20
```

It warns at 80%, blocks at 100%, and counts **everything on the ledger that
month** — not just the current run. One `generate` covers narration *and* effect
cues, and the cap is checked against both together, because checked per phase it
would see roughly half the spend.

---

## 📚 Next

- **[FEATURES.md](FEATURES.md)** — everything the tool does, and what it does not
- **[../README.md](../README.md)** — the tour, and the reasoning behind the design
- **[probe-results.md](probe-results.md)**, **[probe-effects.md](probe-effects.md)** — what live probing settled about billing that the API docs do not say
