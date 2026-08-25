"""Command line interface.

Spending is opt-in throughout. `generate` dry-runs unless `--go` is passed, and
even then it prints the projected cost and asks. Everything that reads metadata
— models, voices, subscription — costs zero characters and is safe to run
freely.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from narrate import audio, ledger, templates
from narrate import media as media_mod
from narrate import probe as probe_mod
from narrate import produce as produce_mod
from narrate import reconcile as reconcile_mod
from narrate import voices as voices_mod
from narrate.chunking import Chunk as ChunkObj
from narrate.chunking import merge_chunks, split_chunk
from narrate.db import locate
from narrate.db.models import (
    CastMember,
    Chunk,
    Cut,
    Effect,
    EffectSlot,
    Project,
    Script,
    Take,
)
from narrate.db.session import open_db, session_scope
from narrate.effects import (
    EffectOutcome,
    EffectRates,
    add_slot,
    generate_slots,
    library,
    placements,
)
from narrate.export import NothingToExport, export_script, write_plan_only
from narrate.ingest import ScriptHasTakes, ingest_script
from narrate.money import fmt_usd
from narrate.provider.base import ProviderError, SFXProvider, TTSProvider
from narrate.provider.elevenlabs import ElevenLabsProvider
from narrate.provider.mock import MockProvider, MockSFXProvider
from narrate.registry import ModelSpec, Registry, detect_drift, save_observed
from narrate.runner import generate as run_generate
from narrate.script_parse import format_time, parse_script
from narrate.settings import MissingAPIKey, Settings, get_settings, resolve_provider
from narrate.suggest import MODEL_RATES, SuggestionRun, estimate_tokens, suggest_for_chunks
from narrate.timeline import build_timeline

console = Console()

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Turn a script into a stitched narration track, with every character accounted for.",
)
project_app = typer.Typer(no_args_is_help=True, help="Projects and their customisation profile.")
cost_app = typer.Typer(no_args_is_help=True, help="Cost rollups and reconciliation.")
cut_app = typer.Typer(no_args_is_help=True, help="Choose which take is used for each chunk.")
effects_app = typer.Typer(no_args_is_help=True, help="Sound effects and where they sit.")
app.add_typer(project_app, name="project")
app.add_typer(cost_app, name="cost")
app.add_typer(cut_app, name="cut")
app.add_typer(effects_app, name="effects")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _engine() -> Engine:
    return open_db()


def _registry() -> Registry:
    return Registry.load()


def _provider(settings: Settings, override: str | None = None) -> TTSProvider:
    name = resolve_provider(override)
    if name == "mock":
        console.print("[dim]Using the mock provider — nothing will be spent.[/dim]")
        return MockProvider()
    return ElevenLabsProvider(settings)


def _die(message: str) -> NoReturn:
    """Print and exit. Typed `NoReturn` so callers narrow correctly after it."""
    console.print(f"[red]{message}[/red]")
    raise typer.Exit(1)


def _require_script(session: Session, script_id: int) -> Script:
    script = session.get(Script, script_id)
    if script is None:
        _die(f"No script with id {script_id}. Run `narrate script list` to see what exists.")
    return script


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@app.command()
def doctor() -> None:
    """Check the environment: ffmpeg, database, API key, provider reachability."""
    settings = get_settings()
    table = Table("Check", "Status", "Detail", show_header=True, header_style="bold")

    ff = audio.ffmpeg_path()
    table.add_row(
        "ffmpeg",
        "[green]ok[/green]" if ff else "[red]missing[/red]",
        ff or "brew install ffmpeg",
    )
    fp = audio.ffprobe_path()
    table.add_row(
        "ffprobe",
        "[green]ok[/green]" if fp else "[red]missing[/red]",
        fp or "ships with ffmpeg",
    )

    try:
        engine = _engine()
        with session_scope(engine) as session:
            projects = len(list(session.scalars(select(Project)).all()))
        # A database inside the project tree is the failure mode that produced
        # three stray copies of this one — see db/locate.py.
        inside = locate.is_inside_project(settings.db_path)
        table.add_row(
            "database",
            "[yellow]in the project tree[/yellow]" if inside else "[green]ok[/green]",
            f"{settings.db_path} ({projects} project(s))"
            + ("  ← run `narrate db adopt`" if inside else ""),
        )
    except Exception as exc:
        table.add_row("database", "[red]error[/red]", str(exc))

    extra = locate.strays()
    if extra:
        table.add_row(
            "stray databases",
            f"[yellow]{len(extra)}[/yellow]",
            ", ".join(i.path.name for i in extra) + "  ← run `narrate db list`",
        )

    # Presence only. The value is never printed.
    table.add_row(
        "api key",
        "[green]set[/green]" if settings.has_api_key else "[yellow]not set[/yellow]",
        "ELEVEN_API in .env" if settings.has_api_key else "add ELEVEN_API to .env",
    )

    files, size = media_mod.disk_usage(settings)
    table.add_row(
        "media",
        "[green]ok[/green]" if files else "[dim]empty[/dim]",
        f"{settings.assets_dir} ({files} file(s), {media_mod.human_bytes(size)})",
    )

    registry = _registry()
    table.add_row(
        "rate card",
        "[green]ok[/green]",
        f"{len(registry.all())} models, version {registry.rate_card_version}",
    )

    if settings.has_api_key and resolve_provider() == "elevenlabs":
        try:
            info = asyncio.run(_subscription_check(settings))
            table.add_row("provider", "[green]reachable[/green]", info)
        except Exception as exc:
            table.add_row("provider", "[red]unreachable[/red]", str(exc)[:80])
    else:
        table.add_row("provider", "[dim]skipped[/dim]", "no key, or mock provider selected")

    console.print(table)
    if not ff:
        console.print(
            "\n[yellow]Export needs ffmpeg. Install it with:[/yellow]  brew install ffmpeg"
        )


async def _subscription_check(settings: Settings) -> str:
    provider = ElevenLabsProvider(settings)
    try:
        sub = await provider.subscription()
        used = sub.get("character_count", 0)
        limit = sub.get("character_limit", 0)
        remaining = max(0, limit - used)
        return f"tier={sub.get('tier')} used={used:,}/{limit:,} remaining={remaining:,}"
    finally:
        await provider.aclose()


# ---------------------------------------------------------------------------
# models / voices
# ---------------------------------------------------------------------------


@app.command()
def models(
    sync: bool = typer.Option(
        False, "--sync", help="Refresh from the API (costs zero characters)."
    ),
    all_models: bool = typer.Option(False, "--all", help="Include deprecated models."),
) -> None:
    """Compare models: rate, request ceiling, and what each one supports."""
    settings = get_settings()
    registry = _registry()

    if sync:
        try:
            fetched = asyncio.run(_sync_models(settings))
        except MissingAPIKey as exc:
            _die(str(exc))
        except ProviderError as exc:
            _die(f"Could not fetch models: {exc.message}")
        else:
            drift = detect_drift(registry, fetched)
            save_observed(fetched, datetime.now(tz=UTC).isoformat())
            registry = _registry()
            if drift:
                console.print(
                    Panel(
                        "\n".join(f"• {d}" for d in drift),
                        title="[yellow]Rate card differs from the API[/yellow]",
                        subtitle="rates are never auto-updated — "
                        "edit config/models.toml deliberately",
                    )
                )
            else:
                console.print("[green]Rate card agrees with the API.[/green]")

    table = Table(title=f"Models (rate card {registry.rate_card_version})")
    table.add_column("model_id")
    table.add_column("$/1k", justify="right")
    table.add_column("max chars", justify="right")
    table.add_column("chunk target", justify="right")
    table.add_column("stitching", justify="center")
    table.add_column("audio tags", justify="center")
    table.add_column("long form")

    for spec in registry.all(include_deprecated=all_models):
        low, high = spec.chunk_target
        label = spec.model_id + (" [dim](deprecated)[/dim]" if spec.deprecated else "")
        table.add_row(
            label,
            f"${spec.usd_per_1k:.2f}",
            f"{spec.effective_max_chars:,}",
            f"{low:,}-{high:,}",
            "[green]yes[/green]" if spec.request_stitching else "[red]no[/red]",
            "[green]yes[/green]" if spec.audio_tags else "[dim]no[/dim]",
            spec.long_form,
        )
    console.print(table)

    best = registry.recommended()
    console.print(
        f"\n[bold]For long-form narration:[/bold] {best.model_id} — {best.note}\n"
        "[dim]Request stitching conditions each chunk on the audio of the ones before it, "
        "which is what keeps a long track from sounding like separate recordings. "
        "Models without it fall back to text context.[/dim]"
    )


async def _sync_models(settings: Settings) -> list[dict[str, Any]]:
    provider = ElevenLabsProvider(settings)
    try:
        return await provider.list_models()
    finally:
        await provider.aclose()


@app.command()
def voices(
    search: str | None = typer.Option(None, "--search", "-s", help="Filter by name."),
    limit: int = typer.Option(30, "--limit", "-n"),
) -> None:
    """List the account's voices. Costs zero characters."""
    settings = get_settings()
    try:
        found = asyncio.run(_fetch_voices(settings, search))
    except MissingAPIKey as exc:
        _die(str(exc))
    except ProviderError as exc:
        _die(f"Could not fetch voices: {exc.message}")

    table = Table(title=f"{len(found)} voice(s)")
    table.add_column("voice_id")
    table.add_column("name")
    table.add_column("category")
    table.add_column("labels")
    for voice in found[:limit]:
        labels = ", ".join(f"{k}={v}" for k, v in (voice.get("labels") or {}).items())
        table.add_row(
            voice.get("voice_id", ""),
            voice.get("name", ""),
            voice.get("category", ""),
            labels[:50],
        )
    console.print(table)


def _short_id(value: str, width: int = 13) -> str:
    """Shorten an id from the middle, keeping both ends.

    Truncating the front is the obvious choice and the wrong one: ids that share
    a prefix collapse to the same string, which defeats the column entirely —
    `mock-voice-1` and `mock-voice-2` both became `mock-voice-1…`. Provider ids
    are random enough that either end identifies them, so keeping both is safe
    and handles the shared-prefix case too.
    """
    if len(value) <= width:
        return value
    head = (width - 1) // 2
    tail = width - 1 - head
    return f"{value[:head]}…{value[-tail:]}"


async def _fetch_voices(
    settings: Settings, search: str | None, voice_type: str | None = None
) -> list[dict[str, Any]]:
    provider = ElevenLabsProvider(settings)
    try:
        return await provider.list_voices(search=search, voice_type=voice_type)
    finally:
        await provider.aclose()


# ---------------------------------------------------------------------------
# project
# ---------------------------------------------------------------------------


@project_app.command("new")
def project_new(
    name: str,
    voice: str = typer.Option(
        None, "--voice", help="Voice id, or a name from `narrate voice register`."
    ),
    model: str = typer.Option(None, "--model", help="Default model id."),
    tags: str = typer.Option("", "--tags", help="Prefix tags applied to every chunk."),
    cap: float = typer.Option(None, "--monthly-cap", help="Monthly spend cap in USD."),
) -> None:
    """Create a project."""
    registry = _registry()
    model_id = model or registry.recommended().model_id
    if model_id not in registry:
        _die(f"Unknown model {model_id!r}. Run `narrate models` to see the options.")

    spec = registry.get(model_id)
    if tags and not spec.audio_tags:
        _die(
            f"{spec.label} does not support audio tags. Either drop --tags or choose a "
            "model that does (`narrate models`)."
        )

    engine = _engine()
    with session_scope(engine) as session:
        if session.scalar(select(Project).where(Project.name == name)):
            _die(f"A project named {name!r} already exists.")
        project = Project(
            name=name,
            voice_id=voices_mod.resolve(session, voice),
            model_id=model_id,
            prefix_tags=tags,
            monthly_cap_micros=int(cap * 1_000_000) if cap else None,
        )
        session.add(project)
        session.flush()
        console.print(f"[green]Created project {project.id}: {name}[/green] (model {model_id})")


@project_app.command("list")
def project_list() -> None:
    """List projects."""
    engine = _engine()
    table = Table("id", "name", "model", "voice", "tags", "scripts")
    with session_scope(engine) as session:
        for project in session.scalars(select(Project).order_by(Project.id)).all():
            count = len(
                list(session.scalars(select(Script).where(Script.project_id == project.id)).all())
            )
            table.add_row(
                str(project.id),
                project.name,
                project.model_id,
                project.voice_id or "[red]unset[/red]",
                project.prefix_tags or "",
                str(count),
            )
    console.print(table)


@project_app.command("set")
def project_set(
    project: str,
    voice: str = typer.Option(None, "--voice"),
    model: str = typer.Option(None, "--model"),
    tags: str = typer.Option(None, "--tags"),
    stability: float = typer.Option(None, "--stability"),
    similarity: float = typer.Option(None, "--similarity"),
    style: float = typer.Option(None, "--style"),
    speed: float = typer.Option(None, "--speed"),
    cap: float = typer.Option(None, "--monthly-cap"),
) -> None:
    """Update a project's customisation profile (F4)."""
    registry = _registry()
    engine = _engine()
    with session_scope(engine) as session:
        row = _lookup_project(session, project)
        if model:
            if model not in registry:
                _die(f"Unknown model {model!r}.")
            row.model_id = model
        if voice:
            row.voice_id = voices_mod.resolve(session, voice)
        if cap is not None:
            row.monthly_cap_micros = int(cap * 1_000_000)

        spec = registry.get(row.model_id)
        if tags is not None:
            if tags and not spec.audio_tags:
                _die(f"{spec.label} does not support audio tags.")
            row.prefix_tags = tags

        settings_dict: dict[str, Any] = json.loads(row.settings_json or "{}")
        for key, value in (
            ("stability", stability),
            ("similarity_boost", similarity),
            ("style", style),
            ("speed", speed),
        ):
            if value is not None:
                settings_dict[key] = value

        rejected = spec.rejected_settings(settings_dict)
        if rejected:
            console.print(
                f"[yellow]{spec.label} does not honour {', '.join(rejected)} — "
                f"these will not be sent.[/yellow]"
            )
            if spec.audio_tags:
                console.print(
                    "[dim]On this model, pacing and delivery are directed with audio tags.[/dim]"
                )
        row.settings_json = json.dumps(settings_dict, sort_keys=True)
        console.print(f"[green]Updated project {row.name}.[/green]")


def _lookup_project(session: Session, key: str) -> Project:
    row: Project | None = None
    if key.isdigit():
        row = session.get(Project, int(key))
    if row is None:
        row = session.scalar(select(Project).where(Project.name == key))
    if row is None:
        _die(f"No project matching {key!r}.")
    return row


# ---------------------------------------------------------------------------
# voice
# ---------------------------------------------------------------------------

voice_app = typer.Typer(
    no_args_is_help=True, help="Voices on the account, including clones you create."
)
app.add_typer(voice_app, name="voice")

# Instant cloning wants under two minutes of audio in total. More than this is
# not better — it is a longer upload for the same result, and the docs are
# explicit that IVC uses short samples.
CLONE_SECONDS_MAX = 150.0
CLONE_SECONDS_MIN = 10.0


async def _clone_capability(settings: Settings) -> dict[str, Any]:
    provider = ElevenLabsProvider(settings)
    try:
        return await provider.clone_capability()
    finally:
        await provider.aclose()


def _require_cloning(settings: Settings) -> dict[str, Any]:
    """Check the plan before sending anything, and explain a refusal.

    `POST /v1/voices/add` on a plan without the permission fails with little to
    go on, and a clone also occupies one of a limited number of voice slots. The
    subscription endpoint costs nothing and answers both questions exactly, so
    it is consulted first rather than after a confusing error.
    """
    try:
        capability = asyncio.run(_clone_capability(settings))
    except (MissingAPIKey, ProviderError) as exc:
        _die(str(exc))

    if not capability["instant"]:
        yes_no = {True: "yes", False: "not permitted"}
        console.print(
            Panel(
                "This account cannot create voice clones.\n\n"
                f"  tier                        {capability['tier']}\n"
                f"  instant voice cloning       {yes_no[capability['instant']]}\n"
                f"  professional voice cloning  {yes_no[capability['professional']]}\n"
                f"  custom voice slots          "
                f"{capability['slots_used']} of {capability['slot_limit']} used\n\n"
                "Instant cloning needs a plan that permits it. Until then, clone at "
                "elevenlabs.io and use the voice_id here — a clone is an ordinary "
                "voice_id once it exists, so everything else in this tool already "
                "works with one.\n\n"
                "[dim]Nothing was sent.[/dim]",
                title="Cloning not available",
                expand=False,
            )
        )
        raise typer.Exit(code=1)

    if capability["slots_free"] <= 0:
        _die(
            f"All {capability['slot_limit']} custom voice slots are in use. "
            "Remove one with `narrate voice remove <voice_id>` first."
        )
    return capability


@voice_app.command("list")
def voice_list(
    search: str = typer.Option(None, "--search", "-s"),
    mine: bool = typer.Option(False, "--mine", help="Only voices this account created."),
) -> None:
    """Voices available to this account. Costs zero characters."""
    settings = get_settings()
    try:
        found = asyncio.run(
            _fetch_voices(settings, search, voice_type="personal" if mine else None)
        )
    except (MissingAPIKey, ProviderError) as exc:
        _die(str(exc))

    if not found:
        console.print("[dim]No voices matched.[/dim]")
        return

    with session_scope(_engine()) as session:
        registered = {r.voice_id: r.slug for r in voices_mod.all_voices(session)}

    table = Table("voice_id", "name", "category", "registered as", "labels")
    for voice in found:
        labels = voice.get("labels") or {}
        vid = voice.get("voice_id", "")
        table.add_row(
            vid,
            voice.get("name", ""),
            # A clone reports `cloned`; this is how you tell yours apart.
            voice.get("category", ""),
            f"[bold]{registered[vid]}[/bold]" if vid in registered else "",
            ", ".join(f"{k}={v}" for k, v in list(labels.items())[:3]),
        )
    console.print(table)
    console.print(
        f"[dim]{len(found)} voice(s). `narrate voice register <id> --name x` gives one a "
        "name you can use instead of its id.[/dim]"
    )


@voice_app.command("register")
def voice_register(
    voice_id: str = typer.Argument(..., help="The provider's voice id."),
    name: str = typer.Option(
        None, "--name", "-n", help="What to call it. Defaults to the provider's own name."
    ),
    note: str = typer.Option(None, "--note", help="A reminder of what this voice is for."),
    replace: bool = typer.Option(
        False, "--replace", help="Repoint an existing name at this voice."
    ),
    offline: bool = typer.Option(
        False, "--offline", help="Register without checking the voice exists."
    ),
) -> None:
    """Give a voice a name you can use instead of its id.

    Registering is **not** creating. A voice cloned in ElevenLabs' own
    interface, a professional voice, a premade one you keep returning to — all
    can be registered, and the tool never needs to have made a voice to name it.
    That matters when an account may clone in the web app but not through the
    API.

    Costs nothing. The voice is confirmed against the account first, so a typo
    in an id is caught here rather than at the point of a billed generation.
    """
    settings = get_settings()
    label: str | None = None
    category = ""

    if not offline:

        async def lookup() -> dict[str, Any] | None:
            provider = ElevenLabsProvider(settings)
            try:
                return await provider.get_voice(voice_id)
            finally:
                await provider.aclose()

        try:
            found = asyncio.run(lookup())
        except MissingAPIKey:
            _die(
                "No API key, so the voice cannot be confirmed. Pass --offline to register "
                "it anyway."
            )
        except ProviderError as exc:
            _die(f"{exc.message}\n\nPass --offline to register it without checking.")

        if found is None:
            _die(
                f"No voice {voice_id!r} on this account. Check `narrate voice list`, or "
                "pass --offline if you are registering one you have not added yet."
            )
        label = found.get("name")
        category = str(found.get("category") or "")

    with session_scope(_engine()) as session:
        try:
            row = voices_mod.register(
                session,
                voice_id,
                name=name,
                label=label,
                category=category,
                note=note or "",
                verified=not offline,
                replace=replace,
            )
        except (
            voices_mod.BadVoiceName,
            voices_mod.VoiceNameTaken,
            voices_mod.VoiceAlreadyRegistered,
        ) as exc:
            _die(str(exc))
        slug, shown, kind = row.slug, row.label, row.category

    console.print(
        f"[green]Registered[/green] [bold]{slug}[/bold] → {shown or voice_id}"
        + (f" [dim]({kind})[/dim]" if kind else " [dim](unverified)[/dim]")
    )
    console.print(f"[dim]Use it anywhere a voice is asked for:  --voice {slug}[/dim]")


@voice_app.command("forget")
def voice_forget(
    name: str = typer.Argument(..., help="The registered name to drop."),
) -> None:
    """Drop a local name. The voice itself is untouched on the provider."""
    with session_scope(_engine()) as session:
        try:
            row = voices_mod.forget(session, name)
        except voices_mod.UnknownVoice:
            _die(f"No registered voice called {name!r}. See `narrate voice registered`.")
        voice_id = row.voice_id
    console.print(
        f"[green]Forgot[/green] {name}. [dim]{voice_id} is still on the account, and every "
        "take made with it is unaffected.[/dim]"
    )


@voice_app.command("registered")
def voice_registered() -> None:
    """Voices you have named locally. Costs nothing, works offline."""
    with session_scope(_engine()) as session:
        records = voices_mod.all_voices(session)

    if not records:
        console.print(
            "[dim]No voices registered. Give one a name:  "
            "narrate voice register <voice_id> --name my-voice[/dim]"
        )
        return

    table = Table("name", "voice", "voice_id", "kind", "note")
    for record in records:
        table.add_row(
            f"[bold]{record.slug}[/bold]",
            record.label,
            _short_id(record.voice_id, 20),
            record.category or "[dim]unverified[/dim]",
            record.note,
        )
    console.print(table)
    console.print("[dim]Use a name anywhere a voice id is accepted.[/dim]")


@voice_app.command("capability")
def voice_capability() -> None:
    """Whether this account may clone, and how many slots are left."""
    settings = get_settings()
    try:
        capability = asyncio.run(_clone_capability(settings))
    except (MissingAPIKey, ProviderError) as exc:
        _die(str(exc))

    table = Table("check", "value")
    table.add_row("tier", str(capability["tier"]))
    table.add_row(
        "instant voice cloning",
        "[green]yes[/green]" if capability["instant"] else "[yellow]not permitted[/yellow]",
    )
    table.add_row(
        "professional voice cloning",
        "[green]yes[/green]" if capability["professional"] else "[yellow]not permitted[/yellow]",
    )
    table.add_row(
        "custom voice slots",
        f"{capability['slots_used']} of {capability['slot_limit']} used",
    )
    console.print(table)


@voice_app.command("clone")
def voice_clone(
    name: str = typer.Argument(..., help="What to call the voice."),
    samples: list[Path] = typer.Argument(..., help="Audio files to clone from."),
    description: str = typer.Option(None, "--description"),
    accent: str = typer.Option(None, "--accent", help="Recorded as a label."),
    denoise: bool = typer.Option(
        False, "--denoise", help="Have the provider strip background noise from the samples."
    ),
    assign: str = typer.Option(
        None, "--assign", help="Set the new voice on this project once created."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
) -> None:
    """Clone a voice from audio samples, and optionally cast it on a project.

    Costs no characters — a clone consumes one of the account's voice *slots*
    rather than character quota. The plan permission and the free slot count are
    both checked before anything is uploaded.
    """
    settings = get_settings()
    capability = _require_cloning(settings)

    missing = [p for p in samples if not p.is_file()]
    if missing:
        _die("No such file: " + ", ".join(str(p) for p in missing))

    total = 0.0
    unmeasured: list[Path] = []
    for path in samples:
        try:
            total += audio.duration_seconds(path)
        except (audio.FFmpegMissing, audio.FFmpegFailed):
            unmeasured.append(path)

    console.print(f"[bold]{name}[/bold] from {len(samples)} sample(s):")
    for path in samples:
        console.print(f"  {path}")
    if unmeasured:
        console.print(
            f"[yellow]Could not measure {len(unmeasured)} file(s)[/yellow] — "
            "ffmpeg is needed for that, and the upload will proceed regardless."
        )
    elif total:
        console.print(f"[dim]{total:.0f}s of audio in total.[/dim]")
        if total > CLONE_SECONDS_MAX:
            console.print(
                f"[yellow]That is more than the ~{CLONE_SECONDS_MAX:.0f}s instant cloning "
                "uses. Extra audio is a longer upload for the same result.[/yellow]"
            )
        elif total < CLONE_SECONDS_MIN:
            console.print(
                f"[yellow]Under {CLONE_SECONDS_MIN:.0f}s is very little to clone from; "
                "expect a rough result.[/yellow]"
            )
    console.print(
        f"[dim]Uses 1 of {capability['slots_free']} free voice slot(s). "
        "No characters are spent.[/dim]"
    )

    if not yes and not typer.confirm("Create it?"):
        console.print("[dim]Nothing was sent.[/dim]")
        raise typer.Exit(code=1)

    async def run() -> dict[str, Any]:
        provider = ElevenLabsProvider(settings)
        try:
            return await provider.create_voice(
                name,
                list(samples),
                description=description,
                labels={"accent": accent} if accent else None,
                remove_background_noise=denoise,
            )
        finally:
            await provider.aclose()

    try:
        created = asyncio.run(run())
    except ProviderError as exc:
        _die(exc.message)

    voice_id = created.get("voice_id", "")
    console.print(f"[green]Created[/green] {name} — [bold]{voice_id}[/bold]")
    if created.get("requires_verification"):
        console.print(
            "[yellow]The provider wants verification before this voice can be used.[/yellow] "
            "Complete it at elevenlabs.io."
        )

    if assign:
        with session_scope(_engine()) as session:
            project = _lookup_project(session, assign)
            project.voice_id = voice_id
        console.print(f"[green]Cast on {assign}.[/green]")
    else:
        console.print(f"[dim]Cast it:  narrate project set <project> --voice {voice_id}[/dim]")


@voice_app.command("remove")
def voice_remove(
    voice_id: str = typer.Argument(..., help="The voice to delete."),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete a voice from the account, freeing its slot.

    Irreversible on the provider's side, and takes already generated with it
    keep working — the audio is on disk here and the ledger rows stay. What
    breaks is generating anything *new* in that voice.
    """
    settings = get_settings()
    if not yes and not typer.confirm(f"Delete voice {voice_id} from the account?"):
        console.print("[dim]Nothing was sent.[/dim]")
        raise typer.Exit(code=1)

    async def run() -> None:
        provider = ElevenLabsProvider(settings)
        try:
            await provider.delete_voice(voice_id)
        finally:
            await provider.aclose()

    try:
        asyncio.run(run())
    except (MissingAPIKey, ProviderError) as exc:
        _die(str(exc))
    console.print(f"[green]Deleted[/green] {voice_id}. Existing takes are unaffected.")


# ---------------------------------------------------------------------------
# db
# ---------------------------------------------------------------------------

db_app = typer.Typer(no_args_is_help=True, help="Where the ledger lives, and which copies exist.")
app.add_typer(db_app, name="db")


def _describe(info: locate.DatabaseInfo) -> str:
    if not info.readable:
        return f"[red]unreadable[/red] — {info.problem}"
    if not info.counts:
        return "[dim]empty — no tables[/dim]"
    parts = [
        f"{info.counts.get('project', 0)} project(s)",
        f"{info.counts.get('script', 0)} script(s)",
        f"{info.counts.get('ledger_entry', 0)} ledger row(s)",
    ]
    return " · ".join(parts)


@db_app.command("status")
def db_status() -> None:
    """Which database is in use, and whether it is somewhere sensible."""
    settings = get_settings()
    active = settings.db_path
    info = locate.inspect(active) if active.exists() else None

    console.print(
        Panel(
            f"[bold]{active}[/bold]\n"
            + (
                f"{_describe(info)} · {fmt_usd(info.ledger_micros)} recorded"
                f"{' · revision ' + (info.revision or 'unmigrated') if info.revision else ''}"
                if info
                else "[yellow]does not exist yet — it is created on first use[/yellow]"
            ),
            title="Database",
        )
    )

    if locate.is_inside_project(active):
        console.print(
            "[yellow]This database is inside the project directory.[/yellow] A project tree "
            "gets synced, backed up, duplicated and watched by editors, and a WAL-mode "
            "SQLite file is not safe under any of those. The ledger is append-only and "
            "reconciliation replays it, so a shadowed copy is the worst failure here."
        )
        console.print("[dim]Move it with:  narrate db adopt[/dim]")

    extra = locate.strays()
    if extra:
        console.print(
            f"\n[yellow]{len(extra)} other database file(s)[/yellow] in the project root. "
            "[dim]See `narrate db list`.[/dim]"
        )


@db_app.command("list")
def db_list() -> None:
    """Every narrate database this machine has, and what is in each.

    The question this answers is "why do I have three of these" — and, more to
    the point, which of them can go. A file whose charges all came from the mock
    provider cost nothing; one with real request ids is money, and money is
    never deleted on a hunch.
    """
    infos = locate.discover()
    if not infos:
        console.print("[dim]No database yet. One is created on first use.[/dim]")
        return

    active = get_settings().db_path.resolve()
    # Deliberately few columns. The question this table answers is "which of
    # these can go", and that needs the file, whether it is live, how much
    # history it holds and whether any of it was real money. Size and schema
    # revision are context for one database, which is what `db status` is for.
    table = Table(title="Databases")
    table.add_column("file")
    table.add_column("role")
    table.add_column("ledger rows", justify="right")
    table.add_column("recorded", justify="right")
    table.add_column("real money")
    table.add_column("revision", style="dim")

    for info in infos:
        if info.path.resolve() == active:
            role = "[green]in use[/green]"
        elif info.is_managed:
            role = "managed"
        else:
            role = "[yellow]stray[/yellow]"
        table.add_row(
            info.path.name,
            role,
            str(info.counts.get("ledger_entry", 0)),
            fmt_usd(info.ledger_micros),
            f"[red]yes ({info.real_charges})[/red]" if info.has_money else "[dim]no[/dim]",
            (info.revision or "—")[:12],
        )
    console.print(table)

    disposable = [i for i in locate.strays(infos) if not i.has_money]
    keepers = [i for i in locate.strays(infos) if i.has_money]
    if disposable:
        console.print(
            f"\n[dim]{len(disposable)} stray file(s) contain no real charges: "
            + ", ".join(f"{i.path.name!r}" for i in disposable)
            + ". Remove them yourself once you are satisfied — this tool will not "
            "delete a database.[/dim]"
        )
    if keepers:
        console.print(
            f"\n[yellow]{len(keepers)} stray file(s) contain real charges[/yellow] and are "
            "history worth keeping: " + ", ".join(f"{i.path.name!r}" for i in keepers)
        )


@db_app.command("adopt")
def db_adopt(
    source: Path = typer.Argument(
        None, help="Which database to adopt. Defaults to the one in use."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite a managed database that exists."),
) -> None:
    """Move a database into the managed location, keeping the original.

    Copied rather than moved, and verified by comparing row counts before the
    original is left alone. Nothing is deleted: the point of the exercise is to
    stop a ledger living somewhere fragile, not to practise deleting ledgers.

    The write-ahead log is checkpointed first. Copying a WAL-mode database
    without folding in its `-wal` silently drops every transaction still in the
    log, which for this file means dropping charges.
    """
    target = locate.managed_db()
    origin = (source or get_settings().db_path).expanduser()

    if not origin.exists():
        _die(f"{origin} does not exist.")
    if origin.resolve() == target.resolve():
        console.print(f"[green]Already managed:[/green] {target}")
        return
    if target.exists() and not force:
        existing = locate.inspect(target)
        _die(
            f"{target} already exists ({_describe(existing)}). "
            "Pass --force to replace it, or point --database at it instead."
        )

    before = locate.inspect(origin)

    # Fold the write-ahead log into the main file so a plain copy is complete.
    connection = sqlite3.connect(origin)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(origin, target)

    after = locate.inspect(target)
    if after.counts != before.counts or after.ledger_micros != before.ledger_micros:
        target.unlink(missing_ok=True)
        _die(
            "The copy did not match the original, so it was removed and nothing changed. "
            f"Original: {before.counts}, copy: {after.counts}."
        )

    console.print(f"[green]Adopted[/green] {origin.name} → {target}")
    console.print(
        f"[dim]{_describe(after)} · {fmt_usd(after.ledger_micros)} recorded — verified.[/dim]"
    )
    console.print(
        f"[dim]The original is untouched at {origin}. Remove it when you are satisfied; "
        "everything will use the managed copy from now on.[/dim]"
    )


# ---------------------------------------------------------------------------
# cast
# ---------------------------------------------------------------------------

cast_app = typer.Typer(no_args_is_help=True, help="Who speaks in a project, and in which voice.")
app.add_typer(cast_app, name="cast")


@cast_app.command("list")
def cast_list(project: str) -> None:
    """Who is cast in this project. Costs nothing."""
    with session_scope(_engine()) as session:
        row = _lookup_project(session, project)
        members = session.scalars(
            select(CastMember).where(CastMember.project_id == row.id).order_by(CastMember.name)
        ).all()
        if not members:
            console.print(
                f"[dim]Nobody is cast in {row.name}. Add someone with "
                "`narrate cast set <project> <name> --voice <voice_id>`.[/dim]"
            )
            return
        table = Table(title=f"Cast — {row.name}")
        table.add_column("name")
        table.add_column("voice")
        table.add_column("delivery", style="dim")
        table.add_column("note", style="dim")
        for member in members:
            table.add_row(
                member.name,
                member.voice_id,
                member.settings_json or "project default",
                member.note,
            )
        console.print(table)


@cast_app.command("set")
def cast_set(
    project: str,
    name: str,
    voice: str = typer.Option(
        ..., "--voice", help="Voice id, or a name from `narrate voice register`."
    ),
    note: str = typer.Option(None, "--note"),
) -> None:
    """Cast a speaker, or change who plays them.

    The name is what a script writes before a colon — `Morag: the line she
    says`. A prefix only counts as a speaker when the name is cast, which is
    what stops `Note:` and `12:30` being mistaken for dialogue.
    """
    with session_scope(_engine()) as session:
        row = _lookup_project(session, project)
        member = session.scalars(
            select(CastMember).where(CastMember.project_id == row.id, CastMember.name == name)
        ).first()
        if member is None:
            member = CastMember(
                project_id=row.id, name=name, voice_id=voices_mod.resolve_required(session, voice)
            )
            session.add(member)
            verb = "Cast"
        else:
            member.voice_id = voices_mod.resolve_required(session, voice)
            verb = "Recast"
        if note is not None:
            member.note = note
        console.print(f"[green]{verb} {name} as {voice} in {row.name}.[/green]")
        console.print(
            "[dim]Existing chunks keep the voice they were ingested with — "
            "re-run `narrate chunk rechunk` to apply this.[/dim]"
        )


@cast_app.command("remove")
def cast_remove(project: str, name: str) -> None:
    """Uncast a speaker. Their lines become ordinary narration."""
    with session_scope(_engine()) as session:
        row = _lookup_project(session, project)
        member = session.scalars(
            select(CastMember).where(CastMember.project_id == row.id, CastMember.name == name)
        ).first()
        if member is None:
            _die(f"{name!r} is not cast in {row.name}.")
        session.delete(member)
        console.print(f"[green]Removed {name} from {row.name}'s cast.[/green]")


# ---------------------------------------------------------------------------
# script / chunk
# ---------------------------------------------------------------------------

script_app = typer.Typer(no_args_is_help=True, help="Scripts (episodes).")
app.add_typer(script_app, name="script")


@script_app.command("template")
def script_template(
    kind: str = typer.Argument("single-voice", help="single-voice or multi-voice."),
    out: Path = typer.Option(None, "--out", "-o", help="Where to write it."),
    show: bool = typer.Option(False, "--show", help="Print it instead of writing a file."),
) -> None:
    """Write a starter script you can edit and generate from.

    The template is annotated in HTML comments, which are stripped before
    anything is sent — so it explains itself *and* generates correctly with
    every comment left in place. Nothing has to be deleted first.
    """
    try:
        template = templates.get(kind)
    except KeyError as exc:
        _die(str(exc).strip('"'))

    body = template.read()

    if show:
        console.print(body)
        return

    target = out or Path(template.filename)
    if target.is_dir():
        target = target / template.filename
    if target.exists():
        _die(f"{target} already exists. Pass --out to write somewhere else.")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")

    # What it produces, so the numbers are visible before anything is spent.
    parsed = parse_script(body)
    console.print(f"[green]Wrote[/green] {target}  [dim]({template.title})[/dim]")
    console.print(
        f"[dim]As-is it narrates {len(parsed.text):,} characters"
        + (f", with {len(parsed.slots)} effect cue(s)" if parsed.slots else "")
        + (f" and {len(parsed.speakers)} speaker(s)" if parsed.speakers else "")
        + ". Every comment is stripped before sending.[/dim]"
    )
    console.print(f"[dim]Next:  narrate script add {target} --project <name>[/dim]")


@script_app.command("templates")
def script_templates() -> None:
    """List the starter scripts. Costs nothing."""
    table = Table("template", "what it covers", show_header=True, header_style="bold")
    for template in templates.all_templates():
        table.add_row(f"[bold]{template.slug}[/bold]", template.summary)
    console.print(table)
    console.print("[dim]narrate script template <name> --out my-episode.md[/dim]")


@script_app.command("add")
def script_add(
    path: Path = typer.Argument(..., exists=True, readable=True),
    project: str = typer.Option(..., "--project", "-p"),
    title: str = typer.Option(None, "--title"),
    model: str = typer.Option(None, "--model", help="Override the project's model."),
    dialogue: bool = typer.Option(
        False,
        "--dialogue",
        help="Send each group of speaker turns as one conversation (eleven_v3 only).",
    ),
) -> None:
    """Import a script and chunk it (F1)."""
    text = path.read_text(encoding="utf-8")
    registry = _registry()
    engine = _engine()

    with session_scope(engine) as session:
        row = _lookup_project(session, project)
        model_id = model or row.model_id
        if model_id not in registry:
            _die(f"Unknown model {model_id!r}.")

        script = Script(
            project_id=row.id,
            title=title or path.stem,
            source_path=str(path.resolve()),
            source_text=text,
            source_sha256=hashlib.sha256(text.encode()).hexdigest(),
            model_id=model if model else None,
        )
        session.add(script)
        session.flush()
        script_id = script.id
        _rechunk(
            session,
            script,
            registry.get(model_id),
            row.prefix_tags,
            cast=_cast_of(session, row.id),
            dialogue=dialogue,
        )

    console.print(f"[green]Added script {script_id}: {title or path.stem}[/green]")
    _render_chunks(script_id)


def _rechunk(
    session: Session,
    script: Script,
    spec: ModelSpec,
    prefix_tags: str,
    cast: dict[str, str] | None = None,
    dialogue: bool = False,
) -> list[ChunkObj]:
    """Re-chunk a script, reporting what the parser found.

    The work lives in `narrate.ingest` so the API and the tests take the same
    path — in particular the marker stripping, which is what keeps `[SFX: ...]`
    out of a billed request.
    """
    try:
        result = ingest_script(session, script, spec, prefix_tags, cast=cast, dialogue=dialogue)
    except ScriptHasTakes as exc:
        _die(str(exc))

    for warning in result.warnings:
        console.print(f"[yellow]{warning}[/yellow]")
    if result.slots_created:
        console.print(
            f"[green]{result.slots_created} effect slot(s)[/green] from [SFX:] markers. "
            "[dim]Nothing generated — see `narrate effects list`.[/dim]"
        )
    if result.speakers:
        how = "as one dialogue per group" if result.dialogue else "one chunk per turn"
        console.print(
            f"[green]{len(result.speakers)} speaker(s)[/green]: "
            f"{', '.join(result.speakers)} — {result.turns_assigned} chunk(s) cast, {how}."
        )
    return result.chunks


def _cast_of(session: Session, project_id: int) -> dict[str, str]:
    """A project's standing name→voice map."""
    rows = session.scalars(
        select(CastMember).where(CastMember.project_id == project_id).order_by(CastMember.name)
    ).all()
    return {row.name: row.voice_id for row in rows}


@script_app.command("list")
def script_list() -> None:
    """List scripts."""
    engine = _engine()
    table = Table("id", "title", "project", "chunks", "chars")
    with session_scope(engine) as session:
        for script in session.scalars(select(Script).order_by(Script.id)).all():
            project = session.get(Project, script.project_id)
            chunks = list(session.scalars(select(Chunk).where(Chunk.script_id == script.id)).all())
            table.add_row(
                str(script.id),
                script.title,
                project.name if project else "?",
                str(len(chunks)),
                f"{sum(len(c.text) for c in chunks):,}",
            )
    console.print(table)


chunk_app = typer.Typer(
    no_args_is_help=True, help="Review and adjust a script's chunks before generating."
)
app.add_typer(chunk_app, name="chunk")


@chunk_app.command("review")
def chunk_review(script_id: int) -> None:
    """Show every chunk with its character count and cost (F2). Spends nothing."""
    _render_chunks(script_id)


@chunk_app.command("rechunk")
def chunk_rechunk(script_id: int) -> None:
    """Discard the current chunks and re-split from the source text."""
    registry = _registry()
    engine = _engine()
    with session_scope(engine) as session:
        script = _require_script(session, script_id)
        project = session.get(Project, script.project_id)
        spec = registry.get(script.model_id or (project.model_id if project else ""))
        _rechunk(session, script, spec, project.prefix_tags if project else "")
    _render_chunks(script_id)


@chunk_app.command("merge")
def chunk_merge(
    script_id: int,
    chunks: str = typer.Argument(..., help="Two adjacent chunk numbers, e.g. 3,4"),
) -> None:
    """Merge two adjacent chunks into one."""
    try:
        first, second = (int(x) for x in chunks.split(","))
    except ValueError:
        _die(f"Expected two chunk numbers like `3,4`, got {chunks!r}.")
    _edit_chunks(script_id, lambda current: merge_chunks(current, first, second))


@chunk_app.command("split")
def chunk_split(
    script_id: int,
    at: str = typer.Argument(..., help="Chunk and character offset, e.g. 5@1200"),
) -> None:
    """Split one chunk at a character offset."""
    try:
        ordinal, offset = at.split("@")
        target, position = int(ordinal), int(offset)
    except ValueError:
        _die(f"Expected `<chunk>@<offset>` like `5@1200`, got {at!r}.")
    _edit_chunks(script_id, lambda current: split_chunk(current, target, position))


@chunk_app.command("set")
def chunk_set(
    script_id: int,
    ordinal: int,
    voice: str = typer.Option(
        None, "--voice", help="A different voice for this chunk. Id or registered name."
    ),
    model: str = typer.Option(None, "--model", help="Use a different model for this chunk."),
    tags: str = typer.Option(None, "--tags", help="Prefix tags for this chunk only."),
    stability: float = typer.Option(None, "--stability"),
    similarity: float = typer.Option(None, "--similarity"),
    style: float = typer.Option(None, "--style"),
    speed: float = typer.Option(None, "--speed"),
    clear: bool = typer.Option(False, "--clear", help="Drop all overrides on this chunk."),
) -> None:
    """Override the project profile for one chunk (F3, F4).

    PRD F4's "some sections want different direction" — a chunk that needs a
    different voice, model, or delivery than the rest of the episode.
    """
    registry = _registry()
    engine = _engine()

    with session_scope(engine) as session:
        script = _require_script(session, script_id)
        project = session.get(Project, script.project_id)
        row = session.scalar(
            select(Chunk).where(Chunk.script_id == script_id, Chunk.ordinal == ordinal)
        )
        if row is None:
            _die(f"Script {script_id} has no chunk {ordinal}.")

        if clear:
            row.voice_id = row.model_id = row.settings_json = row.prefix_tags = None
            console.print(f"[green]Chunk {ordinal} now follows the project profile.[/green]")
            return

        if model:
            if model not in registry:
                _die(f"Unknown model {model!r}. Run `narrate models` to see the options.")
            row.model_id = model
        if voice:
            row.voice_id = voices_mod.resolve(session, voice)

        effective_model = row.model_id or script.model_id or (project.model_id if project else "")
        spec = registry.get(effective_model)

        if tags is not None:
            if tags and not spec.audio_tags:
                _die(
                    f"{spec.label} does not support audio tags. Set --model to one that does, "
                    "or drop --tags."
                )
            row.prefix_tags = tags

        overrides: dict[str, Any] = json.loads(row.settings_json or "{}")
        for key, value in (
            ("stability", stability),
            ("similarity_boost", similarity),
            ("style", style),
            ("speed", speed),
        ):
            if value is not None:
                overrides[key] = value

        if overrides:
            rejected = spec.rejected_settings(overrides)
            if rejected:
                console.print(
                    f"[yellow]{spec.label} does not honour {', '.join(rejected)} — "
                    "these will not be sent.[/yellow]"
                )
            row.settings_json = json.dumps(overrides, sort_keys=True)

        # A chunk that diverges from the project profile is the most likely
        # source of an audible mismatch, so PRD §11 asks for it to be flagged.
        console.print(
            f"[green]Chunk {ordinal} overridden.[/green] "
            f"[dim]It now differs from the project profile — check it against its "
            f"neighbours before exporting.[/dim]"
        )

    _render_chunks(script_id)


def _edit_chunks(script_id: int, transform: Callable[[list[ChunkObj]], list[ChunkObj]]) -> None:
    """Apply a structural edit, then rewrite the chunk rows.

    Refuses once takes exist: renumbering chunks under generated audio would
    silently reattribute cost to the wrong segment.
    """
    registry = _registry()
    engine = _engine()
    with session_scope(engine) as session:
        script = _require_script(session, script_id)
        project = session.get(Project, script.project_id)
        registry.get(script.model_id or (project.model_id if project else ""))
        prefix = project.prefix_tags if project else ""

        rows = list(
            session.scalars(
                select(Chunk).where(Chunk.script_id == script_id).order_by(Chunk.ordinal)
            ).all()
        )
        if session.scalar(select(Take.id).where(Take.chunk_id.in_([r.id for r in rows])).limit(1)):
            _die(
                "This script already has takes. Re-shaping chunks now would reattribute their "
                "cost to the wrong segment. Create a new script instead."
            )

        try:
            current = transform([_to_chunk_obj(r, prefix) for r in rows])
        except ValueError as exc:
            _die(str(exc))

        for row in rows:
            session.delete(row)
        session.flush()
        for c in current:
            session.add(Chunk(script_id=script_id, ordinal=c.ordinal, text=c.text, source=c.source))

    _render_chunks(script_id)


def _render_chunks(script_id: int) -> None:
    """The chunk review table (F2).

    Separate from the `chunk` command so `script add` can show it too —
    calling a typer command as a plain function passes its `OptionInfo`
    defaults rather than real values.
    """
    registry = _registry()
    engine = _engine()
    with session_scope(engine) as session:
        script = _require_script(session, script_id)
        project = session.get(Project, script.project_id)
        spec = registry.get(script.model_id or (project.model_id if project else ""))
        prefix = project.prefix_tags if project else ""
        rows = list(
            session.scalars(
                select(Chunk).where(Chunk.script_id == script_id).order_by(Chunk.ordinal)
            ).all()
        )
        title = script.title

    # Each row is measured against *its own* model. A chunk overridden to v3
    # has a 5,000-character ceiling even when the project default is v2's
    # 10,000, and checking it against the project's limit would pass a chunk
    # the API will reject.
    per_row = [
        (row, _to_chunk_obj(row, prefix), registry.get(row.model_id) if row.model_id else spec)
        for row in rows
    ]
    models_used = {rs.model_id for _, _, rs in per_row}
    only_model = next(iter(models_used)) if len(models_used) == 1 else None
    heading = only_model or f"{len(models_used)} models"

    table = Table(title=f"{title} — {len(rows)} chunks on {heading}")
    table.add_column("#", justify="right")
    table.add_column("chars", justify="right")
    table.add_column("est. cost", justify="right")
    table.add_column("split at")
    table.add_column("overrides")
    table.add_column("opening")

    total_chars = 0
    total_micros = 0
    oversized: list[int] = []
    for row, obj, row_spec in per_row:
        total_chars += obj.char_count
        total_micros += row_spec.cost_micros(obj.char_count)
        over = obj.char_count > row_spec.chunk_ceiling
        if over:
            oversized.append(row.ordinal)
        table.add_row(
            str(row.ordinal),
            f"[red]{obj.char_count:,}[/red]" if over else f"{obj.char_count:,}",
            fmt_usd(row_spec.cost_micros(obj.char_count)),
            "[yellow]hard[/yellow]" if row.source == "hard" else row.source,
            _override_summary(row),
            obj.text[:44].replace("\n", " ") + ("…" if len(obj.text) > 44 else ""),
        )

    console.print(table)
    rate = f"at ${spec.usd_per_1k:.2f}/1k" if len(models_used) == 1 else "at mixed per-model rates"
    console.print(
        f"Total [bold]{total_chars:,}[/bold] characters — "
        f"estimated [bold]{fmt_usd(total_micros, 2)}[/bold] {rate}."
    )
    if oversized:
        console.print(
            f"[red]Chunk(s) {', '.join(str(o) for o in oversized)} exceed their model's "
            "per-request limit and will be rejected.[/red] Split them with "
            "`narrate chunk split`, or move them to a model with a larger ceiling."
        )
    if any(r.source == "hard" for r in rows):
        console.print(
            "[yellow]Some chunks were split mid-sentence. Adjust them with "
            "`--merge` or `--split` before generating.[/yellow]"
        )


def _override_summary(row: Chunk) -> str:
    """Which fields this chunk overrides — PRD §11 asks for divergence to be flagged."""
    parts: list[str] = []
    if row.model_id:
        parts.append(row.model_id.removeprefix("eleven_"))
    if row.voice_id:
        parts.append("voice")
    if row.prefix_tags:
        parts.append("tags")
    if row.settings_json and row.settings_json != "{}":
        parts.append("settings")
    return f"[yellow]{', '.join(parts)}[/yellow]" if parts else ""


def _to_chunk_obj(row: Chunk, prefix: str) -> ChunkObj:
    """ORM row -> the chunking dataclass, so char counts include prefix tags."""
    return ChunkObj(
        ordinal=row.ordinal,
        text=row.text,
        source=row.source,
        prefix_tags=row.prefix_tags if row.prefix_tags is not None else prefix,
    )


# ---------------------------------------------------------------------------
# estimate / generate
# ---------------------------------------------------------------------------


@app.command()
def estimate(script_id: int) -> None:
    """Pre-flight cost, before any generation (C1). Costs nothing to run."""
    registry = _registry()
    engine = _engine()
    with session_scope(engine) as session:
        script = _require_script(session, script_id)
        project = session.get(Project, script.project_id)
        spec = registry.get(script.model_id or (project.model_id if project else ""))
        prefix = project.prefix_tags if project else ""
        rows = list(
            session.scalars(
                select(Chunk).where(Chunk.script_id == script_id).order_by(Chunk.ordinal)
            ).all()
        )
        title = script.title
        ratio = ledger.billing_ratio(session, spec.model_id)

    objs = [_to_chunk_obj(r, prefix) for r in rows]
    est = ledger.estimate_run(
        spec, [o.char_count for o in objs], sum(o.tag_chars for o in objs), ratio
    )

    body = [
        f"Script       {title}",
        f"Model        {spec.model_id} at ${spec.usd_per_1k:.2f} per 1,000 characters",
        f"Chunks       {est.chunks}",
        f"Text         {est.text_chars:,} characters",
        f"Prefix tags  {est.tag_chars:,} characters (billable)",
        f"Total        {est.total_chars:,} characters",
        "",
        f"At list rate  {fmt_usd(est.cost_micros, 2)}   "
        f"({est.credits:,.0f} credits on a subscription)",
    ]
    if est.is_calibrated:
        body += [
            "",
            f"Projected     {fmt_usd(est.calibrated_micros, 4)}",
            f"  calibrated against {est.ratio_samples} past take(s) on this model, which "
            f"billed {est.ratio:.2f} characters per character submitted.",
        ]
    else:
        body += [
            "",
            "The list rate is an upper bound. API generations are discounted by an "
            "amount the provider does not publish, so this figure calibrates itself "
            "once a few takes have been billed.",
        ]
    console.print(Panel("\n".join(body), title="Pre-flight estimate", expand=False))

    mode = spec.continuity_mode
    if mode == "none":
        console.print(
            f"[yellow]{spec.label} has no cross-chunk continuity.[/yellow] It supports "
            "neither request stitching nor text conditioning, so every chunk is generated "
            "cold and seams between them are unavoidable.\n"
            "[dim]For a long single-narrator piece, a model with stitching will sound more "
            "continuous — see `narrate models`.[/dim]"
        )
    elif mode == "text":
        console.print(
            f"[yellow]{spec.label} has no request stitching[/yellow] — chunk boundaries fall "
            "back to text context, which is weaker than conditioning on the audio."
        )


@app.command()
def generate(
    script_id: int,
    go: bool = typer.Option(False, "--go", help="Actually spend. Without this it is a dry run."),
    no_effects: bool = typer.Option(
        False, "--no-effects", help="Speech only; leave the effect cues alone."
    ),
    only: str = typer.Option(None, "--only", help="Chunks to generate, e.g. 7 or 3,5,9."),
    force: bool = typer.Option(False, "--force", help="Re-roll even if an identical take exists."),
    max_spend: float = typer.Option(None, "--max-spend", help="Hard stop for this run, in USD."),
    override_cap: bool = typer.Option(
        False, "--override-cap", help="Proceed past the project's monthly cap."
    ),
    concurrency: int = typer.Option(None, "--concurrency"),
    provider_name: str = typer.Option(None, "--provider", help="elevenlabs | mock"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Produce an episode: narration and its accepted effect cues (F5).

    Dry-runs unless `--go`. One confirmation covers both phases, and the
    monthly cap is checked against the whole run rather than the speech alone.
    """
    settings = get_settings()
    registry = _registry()
    engine = _engine()
    chunks = [int(x) for x in only.split(",")] if only else None
    with_effects = not no_effects
    dry = not go

    projection = produce_mod.project(engine, script_id, registry, with_effects=with_effects)

    if go and not yes:
        if projection.is_empty:
            console.print(
                "[green]Nothing to generate — every chunk has a take and every cue "
                "has audio.[/green]"
            )
            raise typer.Exit(0)

        body = produce_mod.describe(projection)
        body += [
            "  " + "─" * 46,
            f"  {'total':<12}{fmt_usd(projection.total_micros, 4):>34}",
        ]
        console.print(Panel("\n".join(body), title="This run", expand=False))
        if not typer.confirm("Spend it?"):
            console.print("Nothing was sent.")
            raise typer.Exit(0)

    report = asyncio.run(
        _produce(
            engine,
            script_id,
            settings,
            registry,
            chunks,
            dry,
            force,
            int(max_spend * 1_000_000) if max_spend else None,
            concurrency,
            provider_name,
            override_cap,
            with_effects,
        )
    )

    if report.blocked:
        _report_cap_block(report.speech)
        raise typer.Exit(1)

    speech = report.speech
    if report.dry_run:
        table = Table(title="Dry run — nothing was sent")
        table.add_column("what")
        table.add_column("amount", justify="right")
        table.add_column("est. cost", justify="right")
        if speech is not None:
            for chunk_outcome in speech.outcomes:
                if chunk_outcome.status == "would_generate":
                    table.add_row(
                        f"chunk {chunk_outcome.ordinal}",
                        f"{chunk_outcome.billed_chars:,} chars",
                        fmt_usd(chunk_outcome.cost_micros),
                    )
        for cue in report.effects:
            if cue.status == "would_generate":
                table.add_row(
                    f"effect · {cue.description[:28]}",
                    f"{cue.duration_s:g}s" if cue.duration_s else "auto",
                    fmt_usd(cue.cost_micros),
                )
        console.print(table)
        skipped = len(speech.skipped) if speech else 0
        reused = report.effects_reused
        console.print(
            f"Would spend [bold]{fmt_usd(projection.total_micros, 4)}[/bold]. "
            f"Skipping {skipped} chunk(s) already done"
            + (f", reusing {reused} cue(s)" if reused else "")
            + ".\n[dim]Add --go to generate.[/dim]"
        )
        return

    parts = []
    if speech is not None:
        parts.append(
            f"[green]{len(speech.succeeded)} chunk(s)[/green]"
            + (f", [red]{len(speech.failed)} failed[/red]" if speech.failed else "")
        )
    if with_effects:
        parts.append(
            f"[green]{report.effects_generated} cue(s)[/green]"
            + (f", {report.effects_reused} reused" if report.effects_reused else "")
            + (f", [red]{len(report.effects_failed)} failed[/red]" if report.effects_failed else "")
        )
    console.print(f"\n{' · '.join(parts)} — spent [bold]{fmt_usd(report.spent_micros, 4)}[/bold]")

    if speech is not None:
        for failure in speech.failed:
            marker = "[red]UNKNOWN[/red]" if failure.status == "unknown" else "[red]failed[/red]"
            console.print(f"  chunk {failure.ordinal}: {marker} — {failure.error}")
        if any(o.status == "unknown" for o in speech.failed):
            console.print(
                "\n[yellow]One or more requests were sent but never confirmed. They may have "
                "been billed. Check `narrate cost reconcile` before regenerating them.[/yellow]"
            )
    for cue in report.effects_failed:
        console.print(f"  effect {cue.description}: [red]{cue.error}[/red]")

    if report.stopped_reason == "budget":
        console.print("[yellow]Run stopped at this run's spending cap.[/yellow]")
    elif report.stopped_reason == "monthly_cap":
        console.print(
            "[yellow]Run stopped at the project's monthly cap.[/yellow] "
            "[dim]Raise it with `narrate project set --monthly-cap`, or re-run with "
            "--override-cap.[/dim]"
        )


def _report_cap_block(report: Any) -> None:
    """Explain a monthly-cap block with the numbers that caused it (F10).

    The projection includes the effect cues, so the figures here are for the
    whole run rather than the speech alone.
    """
    budget = report.budget if report is not None else None
    if budget is None or budget.cap_micros is None:
        console.print("[red]Blocked by the project's monthly cap.[/red]")
        return
    console.print(
        Panel(
            f"Monthly cap    {fmt_usd(budget.cap_micros, 2)}\n"
            f"Spent so far   {fmt_usd(budget.spent_micros, 4)}   ({budget.used_pct}%)\n"
            f"This run       {fmt_usd(budget.projected_micros, 4)}   "
            "[dim]narration + cues[/dim]\n"
            f"Would reach    {fmt_usd(budget.spent_micros + budget.projected_micros, 4)}   "
            f"({budget.projected_pct}%)\n\n"
            "Nothing was sent. Raise the cap with `narrate project set --monthly-cap`, "
            "trim the run with --only or --no-effects, or re-run with --override-cap.",
            title="[red]Blocked by the monthly cap[/red]",
            expand=False,
        )
    )


async def _produce(
    engine: Engine,
    script_id: int,
    settings: Settings,
    registry: Registry,
    only: list[int] | None,
    dry_run: bool,
    force: bool,
    max_spend_micros: int | None,
    concurrency: int | None,
    provider_name: str | None,
    override_cap: bool,
    with_effects: bool,
) -> produce_mod.ProductionReport:
    tts = _provider(settings, provider_name) if not dry_run else MockProvider()
    sfx = _sfx_provider(settings, provider_name) if not dry_run else MockSFXProvider()
    try:
        return await produce_mod.produce(
            engine,
            script_id,
            tts,
            sfx,
            registry,
            settings,
            with_effects=with_effects,
            only=only,
            dry_run=dry_run,
            force=force,
            max_spend_micros=max_spend_micros,
            concurrency=concurrency,
            override_cap=override_cap,
            on_event=lambda m: console.print(f"[dim]{m}[/dim]"),
        )
    finally:
        await tts.aclose()
        await sfx.aclose()


async def _generate(
    engine: Engine,
    script_id: int,
    settings: Settings,
    registry: Registry,
    only: list[int] | None,
    dry_run: bool,
    force: bool,
    max_spend_micros: int | None,
    concurrency: int | None,
    provider_name: str | None,
    override_cap: bool = False,
) -> Any:
    provider = _provider(settings, provider_name) if not dry_run else MockProvider()
    try:
        return await run_generate(
            engine,
            script_id,
            provider,
            registry,
            settings,
            only=only,
            dry_run=dry_run,
            force=force,
            max_spend_micros=max_spend_micros,
            concurrency=concurrency,
            override_cap=override_cap,
            on_event=lambda m: console.print(f"[dim]{m}[/dim]"),
        )
    finally:
        await provider.aclose()


# ---------------------------------------------------------------------------
# takes / cut / export
# ---------------------------------------------------------------------------


@app.command()
def takes(
    script_id: int,
    chunk_ordinal: int = typer.Option(None, "--chunk", "-c"),
) -> None:
    """List every take, with its cost and whether it is in the cut (F6)."""
    engine = _engine()
    # `voice` matters as soon as a script has been generated in more than one:
    # two takes of the same chunk are otherwise indistinguishable in this table,
    # which is exactly the situation a variant export creates.
    table = Table("chunk", "take", "status", "chars", "cost", "in cut", "voice", "model", "file")
    with session_scope(engine) as session:
        _require_script(session, script_id)
        rows = session.scalars(
            select(Chunk).where(Chunk.script_id == script_id).order_by(Chunk.ordinal)
        ).all()
        selected = {
            c.chunk_id: c.take_id
            for c in session.scalars(select(Cut).where(Cut.script_id == script_id)).all()
        }

        for row in rows:
            if chunk_ordinal and row.ordinal != chunk_ordinal:
                continue
            for take in session.scalars(
                select(Take).where(Take.chunk_id == row.id).order_by(Take.ordinal)
            ).all():
                is_cut = selected.get(row.id) == take.id
                status = {
                    "succeeded": "[green]ok[/green]",
                    "failed": "[red]failed[/red]",
                    "unknown": "[yellow]unknown[/yellow]",
                }.get(take.status, take.status)
                table.add_row(
                    str(row.ordinal),
                    str(take.ordinal),
                    status,
                    f"{take.billed_chars:,}",
                    fmt_usd(take.cost_micros),
                    "[green]✓[/green]" if is_cut else "",
                    _short_id(take.voice_id),
                    take.model_id,
                    Path(take.asset_path).name if take.asset_path else "",
                )
    console.print(table)


@cut_app.command("set")
def cut_set(
    script_id: int, chunk_ordinal: int, take: int = typer.Option(..., "--take", "-t")
) -> None:
    """Promote a take into the cut."""
    engine = _engine()
    with session_scope(engine) as session:
        row = session.scalar(
            select(Chunk).where(Chunk.script_id == script_id, Chunk.ordinal == chunk_ordinal)
        )
        if row is None:
            _die(f"Script {script_id} has no chunk {chunk_ordinal}.")
        chosen = session.scalar(select(Take).where(Take.chunk_id == row.id, Take.ordinal == take))
        if chosen is None:
            _die(f"Chunk {chunk_ordinal} has no take {take}.")
        if chosen.status != "succeeded":
            _die(f"Take {take} has status {chosen.status!r} and cannot be selected.")

        existing = session.get(Cut, row.id)
        if existing:
            existing.take_id = chosen.id
        else:
            session.add(Cut(chunk_id=row.id, script_id=script_id, take_id=chosen.id))
    console.print(f"[green]Chunk {chunk_ordinal} now uses take {take}.[/green]")


@app.command()
def export(
    script_id: int,
    gap: float = typer.Option(None, "--gap", help="Silence between chunks, in seconds."),
    fmt: str = typer.Option(
        None, "--format", "-f", help="Masters to write, e.g. wav,m4a,mp3. See `narrate formats`."
    ),
    piece_fmt: str = typer.Option(
        None, "--piece-format", help="Format for the timeline-named pieces."
    ),
    no_mp3: bool = typer.Option(False, "--no-mp3"),
    out: Path = typer.Option(None, "--out", help="Output directory."),
    voice: str = typer.Option(
        None,
        "--voice",
        help="Export this voice's performance instead of the cut. `narrate takes` lists them.",
    ),
) -> None:
    """Stitch the cut, name every piece by timeline position, and write the plan.

    `--voice` exports a **variant**: the episode as one voice performed it,
    assembled from the takes already on record. Generate a script in two voices
    and you can export both from one generation history, into separate folders
    so neither overwrites the other.
    """
    settings = get_settings()
    engine = _engine()

    label = None
    if voice:
        # A registered name wins: it is what was typed, it needs no network, and
        # it makes the exported filename predictable rather than dependent on
        # whatever the voice happens to be called on the account.
        with session_scope(engine) as session:
            resolved = voices_mod.resolve(session, voice)
            label = voices_mod.label_for(session, resolved or voice)
        voice = resolved or voice

        if label is None:
            try:
                found = asyncio.run(_fetch_voices(settings, None))
                label = next((v.get("name") for v in found if v.get("voice_id") == voice), None)
            except (MissingAPIKey, ProviderError):
                label = None

    try:
        result = export_script(
            engine,
            script_id,
            settings,
            gap_seconds=gap,
            want_mp3=not no_mp3,
            out_dir=out,
            formats=fmt,
            piece_format=piece_fmt,
            voice_id=voice or None,
            variant_label=label,
        )
    except audio.UnknownFormat as exc:
        _die(str(exc))
    except NothingToExport as exc:
        _die(str(exc))
    except (audio.FFmpegMissing, audio.FFmpegFailed) as exc:
        _die(str(exc))

    minutes, seconds = divmod(int(result.duration_s), 60)
    masters = "  ".join(f"{key}: {path.name}" for key, path in result.masters.items())
    body = [
        f"Folder    {result.out_dir}",
        f"Masters   {masters}",
        f"Plan      {result.plan.name}",
        f"Runtime   {minutes}m {seconds:02d}s from {result.chunks} chunk(s), "
        f"{result.gap_seconds}s gaps",
        f"Pieces    {len(result.names)} timeline-named file(s)",
    ]
    if result.effects:
        body.append(f"Effects   {result.effects} placed on the timeline (not mixed in)")
    console.print(Panel("\n".join(body), title="[green]Exported[/green]", expand=False))

    if result.planned:
        console.print(
            f"[yellow]{result.planned} effect slot(s) have no audio.[/yellow] "
            f"They are marked outstanding in {result.plan.name}."
        )

    with session_scope(engine) as session:
        per_minute = ledger.cost_per_minute_micros(session, script_id)
    if per_minute:
        console.print(f"Cost per finished minute: [bold]{fmt_usd(per_minute, 4)}[/bold]")


@app.command()
def formats() -> None:
    """Delivery formats export can write. Costs nothing."""
    table = Table("format", "codec", "kind", "what it's for")
    for fmt in audio.FORMATS.values():
        table.add_row(
            fmt.suffix.lstrip("."),
            fmt.codec,
            "lossless" if fmt.lossless else "lossy",
            fmt.note,
        )
    console.print(table)
    settings = get_settings()
    console.print(
        f"Masters default to [bold]{','.join(settings.export_formats)}[/bold]; "
        f"timeline pieces to [bold]{settings.piece_format}[/bold]. "
        "Override with `narrate export --format` / `--piece-format`."
    )


@app.command()
def media(
    project: str = typer.Option(None, "--project", "-p", help="Limit to one project."),
) -> None:
    """Where the generated audio lives, and how much of it there is."""
    settings = get_settings()
    engine = _engine()

    with session_scope(engine) as session:
        projects = (
            [_lookup_project(session, project)]
            if project
            else list(session.scalars(select(Project).order_by(Project.id)).all())
        )
        listings = [media_mod.project_media(session, p.id, settings) for p in projects]

    table = Table("project", "exports", "effects", "takes", "files", "size")
    for listing in listings:
        table.add_row(
            listing.project_name,
            str(sum(len(g.files) for g in listing.exports)),
            str(len(listing.effects.files)),
            str(len(listing.takes.files)),
            str(listing.file_count),
            media_mod.human_bytes(listing.total_bytes),
        )
    console.print(table)

    files, size = media_mod.disk_usage(settings)
    console.print(
        f"Root [bold]{settings.assets_dir}[/bold] — "
        f"{files} file(s), {media_mod.human_bytes(size)} total."
    )
    if len(listings) == 1:
        for group in [*listings[0].exports, listings[0].effects, listings[0].takes]:
            if not group.files:
                continue
            console.print(f"\n[bold]{group.title}[/bold]")
            for file in group.files:
                detail = f"  {file.label}" if file.label else ""
                console.print(
                    f"  {file.name}  [dim]{media_mod.human_bytes(file.bytes)}{detail}[/dim]"
                )


@app.command()
def timeline(script_id: int) -> None:
    """Show the running order — what plays when. Costs nothing."""
    settings = get_settings()
    engine = _engine()
    with session_scope(engine) as session:
        tl = build_timeline(session, script_id, gap_seconds=settings.gap_seconds)

    table = Table(title=f"{tl.script_title} — {format_time(tl.runtime_s)}")
    table.add_column("#", justify="right")
    table.add_column("start")
    table.add_column("end")
    table.add_column("len", justify="right")
    table.add_column("track")
    table.add_column("content")

    for entry in tl.entries:
        style = {
            "narration": "",
            "effect": "cyan",
            "planned": "yellow",
        }.get(entry.kind, "")
        track = entry.kind if entry.generated else f"{entry.kind} (todo)"
        table.add_row(
            str(entry.index),
            entry.start_stamp,
            entry.end_stamp,
            f"{entry.duration_s:.2f}s",
            f"[{style}]{track}[/{style}]" if style else track,
            entry.label,
        )
    console.print(table)

    drifted = [e for e in tl.drifts if e.drift_s]
    for entry in drifted:
        console.print(
            f"[yellow]Chunk {entry.chunk_ordinal}[/yellow] targeted "
            f"{format_time(entry.target_s or 0)}, landed {entry.start_stamp} "
            f"({entry.drift_s:+.1f}s)"
        )
    if not tl.is_complete:
        console.print("[dim]Timings after the first ungenerated chunk are provisional.[/dim]")


@app.command("plan")
def write_plan(
    script_id: int,
    out: Path = typer.Option(None, "--out", help="Where to write plan.md."),
) -> None:
    """Write the editing plan without exporting audio.

    Before anything is generated this doubles as a shot list: the running
    order, with every effect slot already marked.
    """
    settings = get_settings()
    path = write_plan_only(_engine(), script_id, settings, out)
    console.print(f"[green]Wrote {path}[/green]")


# ---------------------------------------------------------------------------
# effects
# ---------------------------------------------------------------------------


@effects_app.command("list")
def effects_list(script_id: int) -> None:
    """Effect slots on a script, and which have audio."""
    engine = _engine()
    table = Table("slot", "at chunk", "description", "length", "loop", "source", "status")
    with session_scope(engine) as session:
        _require_script(session, script_id)
        slots = list(
            session.scalars(
                select(EffectSlot)
                .where(EffectSlot.script_id == script_id)
                .order_by(EffectSlot.at_chunk_ordinal, EffectSlot.ordinal)
            ).all()
        )
        for slot in slots:
            effect = session.get(Effect, slot.effect_id) if slot.effect_id else None
            if not slot.accepted:
                status = "[dim]not accepted[/dim]"
            elif effect is not None:
                status = f"[green]{effect.slug}[/green]"
            else:
                status = "[yellow]planned[/yellow]"
            table.add_row(
                str(slot.id),
                str(slot.at_chunk_ordinal),
                slot.description,
                f"{slot.duration_s:g}s" if slot.duration_s else "auto",
                "yes" if slot.loop else "",
                slot.source,
                status,
            )
    console.print(table)
    if not slots:
        console.print(
            "[dim]No slots. Add [SFX: description, 4s] markers to the script and "
            "re-chunk, or use `narrate effects add`.[/dim]"
        )


@effects_app.command("add")
def effects_add(
    script_id: int,
    description: str,
    at_chunk: int = typer.Option(1, "--at-chunk", help="Chunk whose start it sits at."),
    duration: float = typer.Option(None, "--duration", help="Seconds (0.5-30)."),
    loop: bool = typer.Option(False, "--loop", help="Ambience bed rather than a one-shot."),
) -> None:
    """Add an effect slot by hand."""
    engine = _engine()
    with session_scope(engine) as session:
        _require_script(session, script_id)
        slot = add_slot(session, script_id, at_chunk, description, duration, loop, source="manual")
        console.print(
            f"[green]Slot {slot.id}[/green] at chunk {at_chunk}: {description}. "
            "[dim]Nothing generated yet.[/dim]"
        )


@effects_app.command("library")
def effects_library(project: str) -> None:
    """Every effect generated for a project, and where each is used."""
    engine = _engine()
    table = Table("slug", "length", "cost", "placements", "prompt")
    with session_scope(engine) as session:
        row = _lookup_project(session, project)
        for effect in library(session, row.id):
            used = placements(session, effect.id)
            table.add_row(
                effect.slug,
                f"{effect.actual_duration_s:.2f}s" if effect.actual_duration_s else "—",
                fmt_usd(effect.cost_micros),
                str(len(used)),
                effect.prompt[:44],
            )
    console.print(table)
    console.print(
        "[dim]An effect is generated once and placed as often as you like — "
        "reuse costs nothing.[/dim]"
    )


@effects_app.command("suggest")
def effects_suggest(
    script_id: int,
    go: bool = typer.Option(False, "--go", help="Actually call Groq. Costs a fraction of a cent."),
    model: str = typer.Option(None, "--model", help="openai/gpt-oss-120b | openai/gpt-oss-20b"),
    max_per_chunk: int = typer.Option(2, "--max-per-chunk"),
    accept: bool = typer.Option(False, "--accept", help="Accept every suggestion immediately."),
) -> None:
    """Ask an LLM where effects might belong. Suggestions never generate on their own."""
    settings = get_settings()
    engine = _engine()

    if not settings.has_groq_key:
        _die("No Groq key found. Set GROQ_API_KEY in .env, or add slots by hand.")

    with session_scope(engine) as session:
        _require_script(session, script_id)
        chunks = [
            (c.ordinal, c.text)
            for c in session.scalars(
                select(Chunk).where(Chunk.script_id == script_id).order_by(Chunk.ordinal)
            ).all()
        ]
    if not chunks:
        _die("This script has no chunks yet.")

    chosen = model or settings.groq_model
    tokens = estimate_tokens(chunks)
    rate_in = MODEL_RATES.get(chosen, (0.0, 0.0))[0]
    rough = int(tokens * rate_in / 1_000_000 * 1_000_000)

    if not go:
        console.print(
            Panel(
                f"Would send {len(chunks)} chunk(s) to {chosen} — about {tokens:,} input "
                f"tokens, roughly {fmt_usd(rough, 4)}.\n\n"
                "Every proposal is stored **unaccepted**, so nothing can be generated "
                "until you say so.\n\n"
                "[bold]Re-run with --go.[/bold]",
                title="Suggestions (nothing sent)",
                expand=False,
            )
        )
        return

    run = asyncio.run(_suggest(chunks, settings, chosen, max_per_chunk))

    # Tokens were spent whether or not the model proposed anything, so the
    # charge is recorded before the results are even looked at.
    if run.prompt_tokens or run.completion_tokens:
        with session_scope(engine) as session:
            script = _require_script(session, script_id)
            ledger.record_operation(
                session,
                kind="suggestion",
                provider=ledger.GROQ,
                units=float(run.prompt_tokens + run.completion_tokens),
                unit_kind=ledger.TOKENS,
                cost_micros=run.cost_micros,
                model_id=run.model,
                project_id=script.project_id,
                script_id=script_id,
                cost_source="usage",
                note=(
                    f"{len(run.suggestions)} proposed from {len(chunks)} chunk(s) "
                    f"({run.prompt_tokens} in, {run.completion_tokens} out)"
                ),
            )

    for error in run.errors:
        console.print(f"[red]{error}[/red]")
    # Tokens were spent whether or not anything came back, so say so either way.
    usage = (
        f"Used {run.prompt_tokens:,} + {run.completion_tokens:,} tokens "
        f"({fmt_usd(run.cost_micros, 4)}) on {run.model}."
    )
    if not run.suggestions:
        console.print(
            "[green]No effects proposed.[/green] "
            "[dim]An empty answer is a valid one — the model found nothing the text "
            "plainly calls for.[/dim]"
        )
        console.print(f"[dim]{usage}[/dim]")
        return

    table = Table(title=f"{len(run.suggestions)} proposed")
    table.add_column("at chunk", justify="right")
    table.add_column("description")
    table.add_column("length", justify="right")
    table.add_column("why")
    with session_scope(engine) as session:
        for item in run.suggestions:
            add_slot(
                session,
                script_id,
                item.chunk_ordinal,
                item.description,
                item.duration_s,
                item.loop,
                source="suggested",
                accepted=accept,
                note=item.reason,
            )
            table.add_row(
                str(item.chunk_ordinal),
                item.description,
                f"{item.duration_s:g}s",
                item.reason[:52],
            )
    console.print(table)
    console.print(usage)
    if accept:
        console.print("[yellow]Accepted. Generate with `narrate effects generate`.[/yellow]")
    else:
        console.print(
            "[dim]Stored unaccepted — nothing will be generated. Accept with "
            "`narrate effects accept <slot>`.[/dim]"
        )


async def _suggest(
    chunks: list[tuple[int, str]], settings: Settings, model: str, max_per_chunk: int
) -> SuggestionRun:
    return await suggest_for_chunks(chunks, settings, model=model, max_per_chunk=max_per_chunk)


@effects_app.command("accept")
def effects_accept(
    slot_ids: list[int],
    reject: bool = typer.Option(False, "--reject", help="Mark them unaccepted instead."),
) -> None:
    """Accept (or reject) proposed effect slots."""
    engine = _engine()
    with session_scope(engine) as session:
        for slot_id in slot_ids:
            slot = session.get(EffectSlot, slot_id)
            if slot is None:
                console.print(f"[red]No slot {slot_id}.[/red]")
                continue
            slot.accepted = not reject
    verb = "rejected" if reject else "accepted"
    console.print(f"[green]{len(slot_ids)} slot(s) {verb}.[/green]")


@effects_app.command("remove")
def effects_remove(slot_ids: list[int]) -> None:
    """Delete effect slots. The generated audio stays in the library."""
    engine = _engine()
    with session_scope(engine) as session:
        for slot_id in slot_ids:
            slot = session.get(EffectSlot, slot_id)
            if slot is not None:
                session.delete(slot)
    console.print(f"[green]Removed {len(slot_ids)} slot(s).[/green]")


@effects_app.command("generate")
def effects_generate(
    script_id: int,
    go: bool = typer.Option(False, "--go", help="Actually spend. Without this it is a dry run."),
    slot: list[int] = typer.Option(None, "--slot", help="Only these slot ids."),
    provider_name: str = typer.Option(None, "--provider", help="elevenlabs | mock"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Generate audio for the accepted effect slots, reusing whatever exists."""
    settings = get_settings()
    engine = _engine()
    rates = EffectRates.load()

    preview = asyncio.run(
        _generate_effects(engine, script_id, settings, rates, slot, True, provider_name)
    )
    pending = [o for o in preview if o.status == "would_generate"]
    reused = [o for o in preview if o.status == "reused"]

    if reused:
        console.print(
            f"[green]{len(reused)} slot(s) reuse an effect already generated[/green] — no charge."
        )
    if not pending:
        console.print("[green]Nothing to generate.[/green]")
        return

    projected = sum(o.cost_micros for o in pending)
    if not go:
        table = Table(title="Dry run — nothing was sent")
        table.add_column("slot", justify="right")
        table.add_column("description")
        table.add_column("length", justify="right")
        table.add_column("est. cost", justify="right")
        for outcome in pending:
            table.add_row(
                str(outcome.slot_id),
                outcome.description,
                f"{outcome.duration_s:g}s" if outcome.duration_s else "auto",
                fmt_usd(outcome.cost_micros),
            )
        console.print(table)
        console.print(
            f"Would spend [bold]{fmt_usd(projected, 4)}[/bold] at "
            f"${rates.usd_per_minute:.2f}/minute. [dim]Add --go to generate.[/dim]"
        )
        return

    if not yes:
        console.print(
            f"About to generate [bold]{len(pending)}[/bold] effect(s) for about "
            f"[bold]{fmt_usd(projected, 4)}[/bold]."
        )
        if not typer.confirm("Spend it?"):
            console.print("Nothing was sent.")
            raise typer.Exit(0)

    outcomes = asyncio.run(
        _generate_effects(engine, script_id, settings, rates, slot, False, provider_name)
    )
    made = [o for o in outcomes if o.status == "generated"]
    failed = [o for o in outcomes if o.status == "failed"]
    spent = sum(o.cost_micros for o in outcomes)
    console.print(
        f"\n[green]{len(made)} generated[/green], {len(reused)} reused, "
        f"[red]{len(failed)} failed[/red] — spent [bold]{fmt_usd(spent, 4)}[/bold]"
    )
    for outcome in failed:
        console.print(f"  {outcome.description}: [red]{outcome.error}[/red]")


async def _generate_effects(
    engine: Engine,
    script_id: int,
    settings: Settings,
    rates: EffectRates,
    slot_ids: list[int] | None,
    dry_run: bool,
    provider_name: str | None,
) -> list[EffectOutcome]:
    provider = _sfx_provider(settings, provider_name) if not dry_run else MockSFXProvider()
    try:
        return await generate_slots(
            engine,
            script_id,
            provider,
            settings,
            slot_ids=slot_ids or None,
            dry_run=dry_run,
            rates=rates,
            on_event=lambda m: console.print(f"[dim]{m}[/dim]"),
        )
    finally:
        await provider.aclose()


def _sfx_provider(settings: Settings, override: str | None = None) -> SFXProvider:
    if resolve_provider(override) == "mock":
        console.print("[dim]Using the mock provider — nothing will be spent.[/dim]")
        return MockSFXProvider()
    return ElevenLabsProvider(settings)


# ---------------------------------------------------------------------------
# cost
# ---------------------------------------------------------------------------
# cost
# ---------------------------------------------------------------------------


KIND_LABEL = {
    "generation": "narration",
    "effect": "effects",
    "suggestion": "suggestions",
    "probe": "probes",
    "correction": "corrections",
}


@cost_app.command("report")
def cost_report(
    script_id: int = typer.Option(None, "--script", "-s"),
    project: str = typer.Option(None, "--project", "-p"),
    days: int = typer.Option(None, "--days", help="Limit to the last N days."),
) -> None:
    """What has been spent, broken down by operation (C3, C4, C7)."""
    engine = _engine()
    since = datetime.now(tz=UTC) - timedelta(days=days) if days else None
    todo: ledger.Outstanding | None = None

    with session_scope(engine) as session:
        if script_id:
            script = _require_script(session, script_id)
            roll = ledger.script_rollup(session, script_id)
            per_chunk = ledger.chunk_costs(session, script_id)
            per_minute = ledger.cost_per_minute_micros(session, script_id)
            pending = ledger.unknown_takes(session, script_id)
            budget = ledger.project_budget(session, script.project_id)
            todo = ledger.outstanding(session, script_id, _registry())
        elif project:
            row = _lookup_project(session, project)
            roll = ledger.project_totals(session, row.id, since=since)
            per_chunk, per_minute = [], None
            pending = ledger.unknown_takes(session)
            budget = ledger.project_budget(session, row.id)
        else:
            _account_report(session, since)
            return

    body = [_kind_line(k) for k in roll.by_kind]
    if body:
        body.append("─" * 58)
    body += [
        f"  {'spent':<14}{fmt_usd(roll.cost_micros, 4):>12}{roll.credits:>14,.0f} credits",
    ]
    if todo is not None and not todo.is_empty:
        parts = []
        if todo.chunks:
            parts.append(f"{todo.chunks} chunk(s), {todo.chars:,} chars")
        if todo.effects:
            parts.append(f"{todo.effects} effect(s), {todo.effect_seconds:g}s")
        body += [
            f"  {'outstanding':<14}{'~' + fmt_usd(todo.cost_micros, 4):>12}   {' · '.join(parts)}",
            f"  {'projected':<14}{fmt_usd(roll.cost_micros + todo.cost_micros, 4):>12}",
        ]

    body += [
        "",
        f"  {'in the cut':<14}{fmt_usd(roll.selected_micros, 4):>12}",
        f"  {'re-rolls':<14}{fmt_usd(roll.wasted_micros, 4):>12}"
        f"   {roll.waste_pct}%  [target: under 25%]",
    ]
    if per_minute:
        body.append(f"  {'cost / minute':<14}{fmt_usd(per_minute, 4):>12}")
    if budget is not None and budget.cap_micros is not None:
        body += [
            "",
            f"  {'monthly cap':<14}{fmt_usd(budget.cap_micros, 2):>12}"
            f"   {budget.used_pct}% used, "
            f"{fmt_usd(budget.remaining_micros or 0, 4)} left",
        ]
    if roll.estimated_entries:
        body.append(
            f"\n  {roll.estimated_entries} entr(ies) had no character-cost header "
            "and were estimated from text length."
        )
    console.print(Panel("\n".join(body), title=f"Cost — {roll.label}", expand=False))

    if per_chunk:
        table = Table("chunk", "takes", "spend", "in cut", "wasted")
        for ordinal, count, total, picked in per_chunk:
            table.add_row(
                str(ordinal), str(count), fmt_usd(total), fmt_usd(picked), fmt_usd(total - picked)
            )
        console.print(table)

    if pending:
        console.print(
            f"[yellow]{len(pending)} take(s) have an unknown billing outcome. "
            "Reconcile before assuming these totals are complete.[/yellow]"
        )


def _kind_line(kind: ledger.KindTotal) -> str:
    label = KIND_LABEL.get(kind.kind, kind.kind)
    credits = f"{kind.credits:,.0f} credits" if kind.credits else "—"
    return f"  {label:<14}{kind.units_display:>16}{fmt_usd(kind.cost_micros, 4):>12}{credits:>16}"


def _account_report(session: Session, since: datetime | None) -> None:
    """Every project, plus the spend that belongs to none of them."""
    table = Table("id", "project", "ops", "spend", "credits", "waste")
    for row in session.scalars(select(Project).order_by(Project.id)).all():
        roll = ledger.project_totals(session, row.id, since=since)
        table.add_row(
            str(row.id),
            row.name,
            str(roll.operations),
            fmt_usd(roll.cost_micros, 4),
            f"{roll.credits:,.0f}",
            f"{roll.waste_pct}%",
        )

    loose = ledger.unattributed(session, since=since)
    if loose.operations:
        # Diagnostics are real money with no project to bill. Showing them
        # here keeps the account total honest without polluting any episode.
        table.add_row(
            "[dim]—[/dim]",
            "[dim]unattributed[/dim]",
            str(loose.operations),
            fmt_usd(loose.cost_micros, 4),
            f"{loose.credits:,.0f}",
            "[dim]—[/dim]",
        )
    console.print(table)

    total = ledger.account_totals(session, since=since)
    console.print(
        f"Account total [bold]{fmt_usd(total.cost_micros, 4)}[/bold] "
        f"across {total.operations} operation(s)."
    )
    if loose.operations:
        console.print(
            "[dim]Unattributed spend is diagnostics — `narrate probe` and the like. "
            "It is deliberately excluded from every project.[/dim]"
        )


@cost_app.command("log")
def cost_log(
    project: str = typer.Option(None, "--project", "-p"),
    script_id: int = typer.Option(None, "--script", "-s"),
    kind: str = typer.Option(None, "--kind", help="generation | effect | suggestion | probe"),
    unattributed: bool = typer.Option(False, "--unattributed", help="Only account-level spend."),
    limit: int = typer.Option(40, "--limit", "-n"),
) -> None:
    """Every individual charge, newest first."""
    engine = _engine()
    with session_scope(engine) as session:
        project_id = _lookup_project(session, project).id if project else None
        rows = ledger.cost_log(
            session,
            project_id=project_id,
            script_id=script_id,
            kind=kind,
            limit=limit,
            unattributed_only=unattributed,
        )
        names = {p.id: p.name for p in session.scalars(select(Project)).all()}

    if not rows:
        console.print("[dim]No charges recorded for that filter.[/dim]")
        return

    table = Table("when", "operation", "project", "model", "amount", "cost", "note")
    for entry in rows:
        units = ledger.KindTotal(
            entry.kind, entry.provider, entry.unit_kind, 1, entry.units, 0, 0
        ).units_display
        table.add_row(
            entry.ts.strftime("%m-%d %H:%M"),
            KIND_LABEL.get(entry.kind, entry.kind),
            names.get(entry.project_id or -1, "[dim]—[/dim]"),
            entry.model_id.removeprefix("eleven_") or "—",
            units,
            fmt_usd(entry.cost_micros),
            entry.note[:38],
        )
    console.print(table)
    console.print(
        f"[dim]{len(rows)} charge(s) totalling "
        f"{fmt_usd(sum(e.cost_micros for e in rows), 4)}.[/dim]"
    )


@cost_app.command("reconcile")
def cost_reconcile(days: int = typer.Option(7, "--days")) -> None:
    """Compare the local ledger against the provider's own counter (C5)."""
    settings = get_settings()
    engine = _engine()
    end = datetime.now(tz=UTC)
    start = end - timedelta(days=days)

    try:
        usage = asyncio.run(_fetch_usage(settings, start, end))
    except MissingAPIKey as exc:
        _die(str(exc))
    except ProviderError as exc:
        _die(f"Could not fetch usage: {exc.message}")

    try:
        result = reconcile_mod.reconcile(engine, usage, start, end)
    except reconcile_mod.UnreadableUsageResponse as exc:
        _die(str(exc))

    colour = "green" if result.within_threshold else "red"
    console.print(
        Panel(
            f"Window          {start.date()} to {end.date()}\n"
            f"Local ledger    {result.local_chars:,} characters "
            f"({fmt_usd(result.local_micros, 4)})\n"
            f"Provider says   {result.provider_chars:,} characters\n"
            f"Drift           {result.drift_pct:+.2f}%   "
            f"(ledger covers {result.coverage_pct}%)\n\n"
            f"[{colour}]{result.verdict}[/{colour}]"
            + (f"\n\n{result.note}" if result.note else ""),
            title="Reconciliation",
            expand=False,
        )
    )


async def _fetch_usage(settings: Settings, start: datetime, end: datetime) -> dict[str, Any]:
    provider = ElevenLabsProvider(settings)
    try:
        return await provider.usage_by_product(start, end, group_by=["model"])
    finally:
        await provider.aclose()


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8420, "--port"),
) -> None:
    """Run the web UI over the same pipeline the CLI drives."""
    from narrate.api import FRONTEND_DIST
    from narrate.api import serve as run_server

    if not FRONTEND_DIST.is_dir():
        console.print(
            "[yellow]The frontend is not built.[/yellow] The API will still answer at "
            f"http://{host}:{port}/api/docs.\n"
            "[dim]Build it with `just ui-build`, or run `just dev` for the Vite "
            "dev server against this API.[/dim]"
        )
    console.print(f"[green]narrate[/green] on http://{host}:{port}")
    run_server(host=host, port=port)


@app.command()
def probe(
    live: bool = typer.Option(False, "--live", help="Required. This command spends characters."),
    model: str = typer.Option(None, "--model"),
    voice: str = typer.Option(None, "--voice"),
    effects: bool = typer.Option(
        False, "--effects", help="Probe sound-effect billing instead of speech."
    ),
    dialogue: bool = typer.Option(
        False, "--dialogue", help="Probe multi-speaker dialogue billing, which is undocumented."
    ),
) -> None:
    """Settle the questions the docs do not answer. Spends about two cents."""
    settings = get_settings()
    registry = _registry()

    if effects:
        _probe_effects(settings, live)
        return
    if dialogue:
        _probe_dialogue(settings, registry, live, voice, model)
        return
    spec = registry.get(model) if model else registry.get("eleven_v3")
    chars = probe_mod.estimate_probe_chars(spec.audio_tags)
    cost = spec.cost_micros(chars)

    if not live:
        console.print(
            Panel(
                f"Would submit about {chars} characters on {spec.model_id} — "
                f"roughly {fmt_usd(cost, 4)}.\n\n"
                "It answers: are audio-tag characters billed, is context text billed, "
                "does this model accept `speed`, and what headers actually come back.\n\n"
                "[bold]Re-run with --live to spend it.[/bold]",
                title="Probe (nothing sent)",
                expand=False,
            )
        )
        return

    if not voice:
        try:
            found = asyncio.run(_fetch_voices(settings, None))
        except (MissingAPIKey, ProviderError) as exc:
            _die(str(exc))
        if not found:
            _die("No voices on this account. Pass --voice explicitly.")
        voice = found[0]["voice_id"]
        console.print(f"[dim]Using voice {found[0].get('name')} ({voice}).[/dim]")

    report = asyncio.run(_run_probe(settings, registry, voice, spec.model_id))
    path = probe_mod.write_report(report)
    _record_probe(
        units=float(report.total_billed or report.total_submitted),
        unit_kind=ledger.CHARACTERS,
        billed_chars=report.total_billed,
        cost_micros=spec.cost_micros(report.total_billed or report.total_submitted),
        model_id=spec.model_id,
        credits=spec.credits(report.total_billed or report.total_submitted),
        note=f"speech probe on {spec.model_id}",
    )

    console.print(
        Panel("\n".join(f"• {f}" for f in report.findings), title="Findings", expand=False)
    )
    console.print(
        f"Submitted {report.total_submitted} characters, billed {report.total_billed}. "
        f"Written to {path}"
    )


def _probe_effects(settings: Settings, live: bool) -> None:
    """Find out what `character-cost` means for a per-second-billed product."""
    rates = EffectRates.load()
    seconds = sum(probe_mod.EFFECT_DURATIONS)
    cost = rates.cost_micros(seconds)

    if not live:
        console.print(
            Panel(
                f"Would generate {len(probe_mod.EFFECT_DURATIONS)} short effects "
                f"({seconds:g}s total) — roughly {fmt_usd(cost, 4)}.\n\n"
                "It answers what the `character-cost` header contains for sound "
                "effects, which no documentation states, and whether partial seconds "
                "are rounded up.\n\n"
                "[bold]Re-run with --live --effects to spend it.[/bold]",
                title="Effect probe (nothing sent)",
                expand=False,
            )
        )
        return

    report = asyncio.run(_run_effect_probe(settings))
    path = probe_mod.write_effect_report(report)
    _record_probe(
        units=report.total_seconds,
        unit_kind=ledger.SECONDS,
        cost_micros=rates.cost_micros(report.total_seconds),
        model_id=rates.model_id,
        credits=rates.credits(report.total_seconds),
        note="sound-effect billing probe",
    )
    console.print(
        Panel("\n".join(f"• {f}" for f in report.findings), title="Findings", expand=False)
    )
    console.print(f"Generated {report.total_seconds:g}s of audio. Written to {path}")


def _probe_dialogue(
    settings: Settings,
    registry: Registry,
    live: bool,
    voice: str | None,
    model: str | None,
) -> None:
    """Find out what a dialogue request is actually billed.

    The rate card has promised this command since dialogue was added: no
    ElevenLabs page states the price, third-party sources claim roughly 1.84x
    plain speech, and `dialogue_cost_multiplier` sits at 1.0 flagged unverified
    until something measures it. A declared-but-unverifiable number is a
    liability in a tool whose claim is that it can tell you what an episode
    cost.
    """
    spec = registry.get(model) if model else registry.get("eleven_v3")
    if not spec.dialogue:
        _die(
            f"{spec.label} has no dialogue endpoint. Only models with "
            "`dialogue = true` in the rate card can be probed this way."
        )

    chars = sum(len(text) for text, _ in probe_mod.DIALOGUE_TURNS)
    cost = spec.dialogue_cost_micros(chars)

    if not live:
        console.print(
            Panel(
                f"Would send {len(probe_mod.DIALOGUE_TURNS)} turns "
                f"({chars} characters) to /v1/text-to-dialogue on {spec.model_id} — "
                f"roughly {fmt_usd(cost, 4)} at the declared rate.\n\n"
                "It answers what `character-cost` comes back for a dialogue request, "
                "which no documentation states. Until it is run, "
                "`dialogue_cost_multiplier` stays 1.0 and unverified.\n\n"
                "[bold]Re-run with --live --dialogue to spend it.[/bold]",
                title="Dialogue probe (nothing sent)",
                expand=False,
            )
        )
        return

    voices = [voice] if voice else []
    if not voices:
        try:
            found = asyncio.run(_fetch_voices(settings, None))
        except (MissingAPIKey, ProviderError) as exc:
            _die(str(exc))
        if len(found) < 2:
            _die("Two voices are needed to probe a dialogue. Pass --voice, or add one.")
        # Two distinct voices, because a "dialogue" in one voice may well be
        # priced as ordinary speech and would not answer the question.
        voices = [found[0]["voice_id"], found[1]["voice_id"]]
        console.print(f"[dim]Using {found[0].get('name')} and {found[1].get('name')}.[/dim]")

    report = asyncio.run(_run_dialogue_probe(settings, spec.model_id, voices))
    path = probe_mod.write_dialogue_report(report)

    if report.billed_chars is not None:
        _record_probe(
            units=float(report.billed_chars),
            unit_kind=ledger.CHARACTERS,
            cost_micros=spec.dialogue_cost_micros(report.billed_chars),
            model_id=spec.model_id,
            credits=spec.credits(report.billed_chars),
            note="dialogue billing probe",
            billed_chars=report.billed_chars,
        )

    console.print(
        Panel("\n".join(f"• {f}" for f in report.findings), title="Findings", expand=False)
    )
    console.print(f"Written to {path}")
    if report.multiplier is not None:
        console.print(
            "[dim]Compare this ratio with the plain-speech one in "
            "docs/probe-results.md before changing `dialogue_cost_multiplier`.[/dim]"
        )


async def _run_dialogue_probe(
    settings: Settings, model_id: str, voices: list[str]
) -> probe_mod.DialogueProbeReport:
    provider = ElevenLabsProvider(settings)
    try:
        return await probe_mod.run_dialogue_probe(
            provider, model_id, voices, settings.output_format
        )
    finally:
        await provider.aclose()


def _record_probe(
    *,
    units: float,
    unit_kind: str,
    cost_micros: int,
    model_id: str,
    credits: float,
    note: str,
    billed_chars: int = 0,
) -> None:
    """Record diagnostic spend against no project.

    A probe is real money and belongs on the ledger, but it is not part of any
    episode — billing it to one would misstate what that episode cost. It
    shows up in the account total and in `cost report`'s unattributed row.
    """
    with session_scope(_engine()) as session:
        ledger.record_operation(
            session,
            kind="probe",
            provider=ledger.ELEVENLABS,
            units=units,
            unit_kind=unit_kind,
            billed_chars=billed_chars,
            cost_micros=cost_micros,
            credits=credits,
            model_id=model_id,
            project_id=None,
            cost_source="header" if billed_chars else "duration",
            note=note,
        )


async def _run_effect_probe(settings: Settings) -> Any:
    provider = ElevenLabsProvider(settings)
    try:
        return await probe_mod.run_effect_probe(provider, settings.output_format)
    finally:
        await provider.aclose()


async def _run_probe(settings: Settings, registry: Registry, voice_id: str, model_id: str) -> Any:
    provider = ElevenLabsProvider(settings)
    try:
        return await probe_mod.run_probe(provider, registry, voice_id, model_id)
    finally:
        await provider.aclose()


if __name__ == "__main__":
    app()
