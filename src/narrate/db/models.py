"""The schema.

Shape follows PRD §4 — Project → Script → Chunk → Take → Asset — with the
addition of `Run` (one batch generation), `Cut` (the selected take per chunk),
and `LedgerEntry` (the append-only cost record).

The separation the PRD calls out is load-bearing here: **cost accrues at the
take level, value is measured at the script level.** That is what makes
"episode 14 cost $3.40, of which $1.10 was re-rolls" a query rather than an
archaeology project.

Money is stored as integer micro-USD throughout — see `narrate.money`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


class Base(DeclarativeBase):
    pass


class Project(Base):
    """A channel, client, or series. Carries the customisation profile (F4)."""

    __tablename__ = "project"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    voice_id: Mapped[str | None] = mapped_column(String(64), default=None)
    model_id: Mapped[str] = mapped_column(String(64))

    # Voice settings as JSON. Stored whole rather than as columns because which
    # fields are meaningful depends on the model, and the registry decides that.
    settings_json: Mapped[str] = mapped_column(Text, default="{}")

    # Prepended to every chunk (F4). Billable characters — the estimate counts them.
    prefix_tags: Mapped[str] = mapped_column(Text, default="")

    monthly_cap_micros: Mapped[int | None] = mapped_column(Integer, default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    scripts: Mapped[list[Script]] = relationship(back_populates="project")


class Script(Base):
    """One episode."""

    __tablename__ = "script"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(256))
    source_path: Mapped[str | None] = mapped_column(Text, default=None)
    source_text: Mapped[str] = mapped_column(Text)

    # Lets a re-import detect that nothing changed (the groundwork for F11).
    source_sha256: Mapped[str] = mapped_column(String(64))

    model_id: Mapped[str | None] = mapped_column(String(64), default=None)
    voice_id: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    project: Mapped[Project] = relationship(back_populates="scripts")
    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="script", order_by="Chunk.ordinal", cascade="all, delete-orphan"
    )


class Chunk(Base):
    """An ordered segment of a script, sized to fit the model's request limit."""

    __tablename__ = "chunk"
    __table_args__ = (UniqueConstraint("script_id", "ordinal", name="uq_chunk_order"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    script_id: Mapped[int] = mapped_column(ForeignKey("script.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)

    # How the boundary was chosen: marker / paragraph / sentence / clause / hard.
    # Surfaced in review so a "hard" split can be fixed before it is heard.
    source: Mapped[str] = mapped_column(String(16), default="paragraph")

    # Offset into the parsed script this chunk starts at, where exactly known.
    # Effect slots are matched to chunks through this.
    start_offset: Mapped[int | None] = mapped_column(Integer, default=None)

    # From an `[@ MM:SS]` anchor: where the writer intended this to begin.
    # A target, never enforced — TTS duration cannot be dialled to a mark, so
    # the plan reports drift instead of pretending to hit it.
    target_start_s: Mapped[float | None] = mapped_column(default=None)

    # Per-chunk overrides (F4: "some sections want different direction").
    prefix_tags: Mapped[str | None] = mapped_column(Text, default=None)
    voice_id: Mapped[str | None] = mapped_column(String(64), default=None)
    model_id: Mapped[str | None] = mapped_column(String(64), default=None)
    settings_json: Mapped[str | None] = mapped_column(Text, default=None)

    # Speaker turns inside this chunk, as `[{"speaker", "voice_id", "text"}]`.
    #
    # Set only for a chunk generated as a *dialogue*, where one request carries
    # several turns in several voices. A chunk with one voice leaves this null
    # and takes the ordinary path — `voice_id` above is enough for it, and the
    # runner already prefers it over the script's and the project's.
    turns_json: Mapped[str | None] = mapped_column(Text, default=None)

    script: Mapped[Script] = relationship(back_populates="chunks")
    takes: Mapped[list[Take]] = relationship(
        back_populates="chunk", order_by="Take.ordinal", cascade="all, delete-orphan"
    )


class Run(Base):
    """One batch generation. Resumable: state lives here, not in a worker."""

    __tablename__ = "run"

    id: Mapped[int] = mapped_column(primary_key=True)
    script_id: Mapped[int] = mapped_column(ForeignKey("script.id", ondelete="CASCADE"))
    model_id: Mapped[str] = mapped_column(String(64))

    # pending | running | completed | failed | stopped_by_budget
    status: Mapped[str] = mapped_column(String(24), default="pending")

    estimate_micros: Mapped[int] = mapped_column(Integer, default=0)
    spent_micros: Mapped[int] = mapped_column(Integer, default=0)
    max_spend_micros: Mapped[int | None] = mapped_column(Integer, default=None)
    concurrency: Mapped[int] = mapped_column(Integer, default=2)
    dry_run: Mapped[bool] = mapped_column(default=False)

    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)


class Take(Base):
    """One generation attempt. This is where cost is attributed (C2).

    Every field needed to answer "what was this, what did it cost, and was it
    used?" is recorded at submit time. `rate_usd_per_1k` is frozen here rather
    than looked up later, so a repricing never rewrites history (C6).
    """

    __tablename__ = "take"
    __table_args__ = (
        Index("ix_take_idempotency", "idempotency_key"),
        Index("ix_take_chunk", "chunk_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chunk_id: Mapped[int] = mapped_column(ForeignKey("chunk.id", ondelete="CASCADE"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("run.id"), default=None)
    ordinal: Mapped[int] = mapped_column(Integer, default=1)

    # sha256 over the canonical request. Prevents a retry double-charging.
    idempotency_key: Mapped[str] = mapped_column(String(64))

    # The provider's own id for this generation. Feeds `previous_request_ids`
    # on models where request stitching works, and identifies the charge for
    # reconciliation on every model.
    request_id: Mapped[str | None] = mapped_column(String(128), default=None)

    model_id: Mapped[str] = mapped_column(String(64))
    # For a dialogue take this is the *lead* speaker's voice — the column is
    # non-null and every existing query reads it, so it keeps meaning "a voice
    # this take used". `voices_json` below carries the full ordered list rather
    # than letting a four-voice exchange claim it had one.
    voice_id: Mapped[str] = mapped_column(String(64))
    voices_json: Mapped[str | None] = mapped_column(Text, default=None)
    settings_json: Mapped[str] = mapped_column(Text, default="{}")
    output_format: Mapped[str] = mapped_column(String(32), default="mp3_44100_128")

    submitted_text: Mapped[str] = mapped_column(Text)
    submitted_chars: Mapped[int] = mapped_column(Integer)

    # What the provider says it billed, from the `character-cost` header.
    # Falls back to `submitted_chars` only when the header is absent, and
    # `cost_source` records which of the two happened.
    billed_chars: Mapped[int] = mapped_column(Integer, default=0)
    cost_source: Mapped[str] = mapped_column(String(16), default="header")

    rate_usd_per_1k: Mapped[float] = mapped_column(default=0.0)
    rate_card_version: Mapped[str] = mapped_column(String(32), default="")
    cost_micros: Mapped[int] = mapped_column(Integer, default=0)
    credits: Mapped[float] = mapped_column(default=0.0)

    asset_path: Mapped[str | None] = mapped_column(Text, default=None)
    duration_s: Mapped[float | None] = mapped_column(default=None)

    # succeeded | failed | unknown | skipped_duplicate
    #
    # `unknown` is the important one: the request was sent but the outcome was
    # never observed (a read timeout). It may or may not have been billed, so
    # it is never auto-retried and never silently counted as free.
    status: Mapped[str] = mapped_column(String(24), default="succeeded")
    error_json: Mapped[str | None] = mapped_column(Text, default=None)
    attempts: Mapped[int] = mapped_column(Integer, default=1)

    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    chunk: Mapped[Chunk] = relationship(back_populates="takes")


class Cut(Base):
    """The selected take for a chunk. One row per chunk defines the cut."""

    __tablename__ = "cut"

    chunk_id: Mapped[int] = mapped_column(
        ForeignKey("chunk.id", ondelete="CASCADE"), primary_key=True
    )
    script_id: Mapped[int] = mapped_column(ForeignKey("script.id", ondelete="CASCADE"))
    take_id: Mapped[int] = mapped_column(ForeignKey("take.id", ondelete="CASCADE"))
    selected_at: Mapped[datetime] = mapped_column(default=utcnow)


class LedgerEntry(Base):
    """Append-only cost record (PRD §7).

    Never updated, never deleted — the triggers in `session.py` enforce that at
    the database level rather than trusting every caller to remember. A
    correction is a new compensating entry with a negative amount, which is
    what keeps reconciliation auditable.
    """

    __tablename__ = "ledger_entry"
    __table_args__ = (Index("ix_ledger_ts", "ts"), Index("ix_ledger_project", "project_id"))

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)

    # Null for an account-level charge — a diagnostic probe genuinely belongs
    # to no project, and billing it to one would corrupt exactly the figure
    # this table exists to produce.
    project_id: Mapped[int | None] = mapped_column(Integer, default=None)
    script_id: Mapped[int | None] = mapped_column(Integer, default=None)
    chunk_id: Mapped[int | None] = mapped_column(Integer, default=None)
    take_id: Mapped[int | None] = mapped_column(Integer, default=None)
    run_id: Mapped[int | None] = mapped_column(Integer, default=None)

    # generation | effect | suggestion | probe | correction |
    # reconciliation_adjustment
    kind: Mapped[str] = mapped_column(String(32), default="generation")

    # Which service was billed. Without this, Groq tokens would pool with
    # ElevenLabs characters and quietly skew every reconciliation.
    provider: Mapped[str] = mapped_column(String(24), default="elevenlabs")

    model_id: Mapped[str] = mapped_column(String(64), default="")

    # What the operation consumed, in its own unit. Speech is billed per
    # character, effects per second, and an LLM per token — recording only
    # characters meant an effect's quantity was simply lost, and a ledger that
    # records a cost but not what it bought cannot be checked.
    units: Mapped[float] = mapped_column(default=0.0)
    unit_kind: Mapped[str] = mapped_column(String(16), default="characters")

    # Kept deliberately alongside `units`, not replaced by it: reconciliation
    # sums this against the provider's *character* usage, and a 4-second effect
    # is not 4 characters. Zero on anything not billed per character.
    billed_chars: Mapped[int] = mapped_column(Integer, default=0)

    rate_usd_per_1k: Mapped[float] = mapped_column(default=0.0)
    rate_card_version: Mapped[str] = mapped_column(String(32), default="")
    cost_micros: Mapped[int] = mapped_column(Integer, default=0)
    credits: Mapped[float] = mapped_column(default=0.0)
    cost_source: Mapped[str] = mapped_column(String(16), default="header")

    request_id: Mapped[str | None] = mapped_column(String(128), default=None)
    note: Mapped[str] = mapped_column(Text, default="")


class Export(Base):
    """A stitched deliverable, with the runtime that cost-per-minute divides by."""

    __tablename__ = "export"

    id: Mapped[int] = mapped_column(primary_key=True)
    script_id: Mapped[int] = mapped_column(ForeignKey("script.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(Text)
    fmt: Mapped[str] = mapped_column(String(16))
    duration_s: Mapped[float] = mapped_column(default=0.0)

    # Hash of the take ids in order. Two exports with the same fingerprint are
    # the same audio, which is what makes "is this export stale?" answerable.
    cut_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    gap_seconds: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Reconciliation(Base):
    """One comparison of the local ledger against the provider's counter (C5)."""

    __tablename__ = "reconciliation"

    id: Mapped[int] = mapped_column(primary_key=True)
    window_start: Mapped[datetime] = mapped_column()
    window_end: Mapped[datetime] = mapped_column()
    local_chars: Mapped[int] = mapped_column(Integer, default=0)
    provider_chars: Mapped[int] = mapped_column(Integer, default=0)
    local_micros: Mapped[int] = mapped_column(Integer, default=0)
    drift_pct: Mapped[float] = mapped_column(default=0.0)
    within_threshold: Mapped[bool] = mapped_column(default=True)
    note: Mapped[str] = mapped_column(Text, default="")
    fetched_at: Mapped[datetime] = mapped_column(default=utcnow)


class RegisteredVoice(Base):
    """A voice given a local name, so it can be reused without its id.

    Voices live on the provider, not here — this table stores no audio and
    grants no access. What it adds is a **handle**: `--voice my-voice` instead
    of `--voice 21m00Tcm4TlvDq8ikWAM`, which matters most for cloned voices,
    because those are the ones you create, name, and then use across several
    projects from a terminal.

    Account-wide on purpose, unlike [`CastMember`](models.py) which is
    project-scoped. A cast is who speaks in *this* series; a registered voice is
    one you own and reach for anywhere.

    `category` and `verified_at` record what the provider said about it when it
    was registered. They are a snapshot, not a lease: a voice deleted from the
    account leaves this row behind, and that is deliberate — takes generated
    with it are still on disk and still in the ledger, so the name should keep
    resolving well enough to explain where an old master came from.
    """

    __tablename__ = "registered_voice"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_registered_voice_slug"),
        UniqueConstraint("voice_id", name="uq_registered_voice_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # The handle typed at the CLI: lowercase, hyphenated, unique.
    slug: Mapped[str] = mapped_column(String(64))
    # What to show a person. Defaults to the provider's own name.
    label: Mapped[str] = mapped_column(String(128))
    voice_id: Mapped[str] = mapped_column(String(64))
    # `cloned`, `premade`, `professional`, `generated` — or empty when it was
    # registered without the provider being reachable.
    category: Mapped[str] = mapped_column(String(32), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    # When the provider last confirmed this voice exists on the account.
    verified_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class CastMember(Base):
    """A named speaker in a project, and the voice that plays them.

    Project-scoped rather than script-scoped, because a series has recurring
    characters: casting Morag once should cover every episode she is in. A
    script can still override with its own `[CAST]` block for a one-off.

    **This table is what makes a speaker prefix safe.** `Morag:` becomes a
    speaker change only when Morag is cast here or in the script; otherwise it
    stays ordinary prose. Recognising speakers by capitalisation instead would
    eventually delete words from somebody's narration, silently.

    `settings_json` is per-character delivery — a narrator and a shouted line
    do not want the same stability — and falls back to the project's profile
    when null.
    """

    __tablename__ = "cast_member"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_cast_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    # Matched against script prefixes case-insensitively; stored as declared,
    # because that is the spelling worth showing back to a person.
    name: Mapped[str] = mapped_column(String(64))
    voice_id: Mapped[str] = mapped_column(String(64))
    settings_json: Mapped[str | None] = mapped_column(Text, default=None)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Effect(Base):
    """A generated sound effect. The library — generated once, placed many times.

    Separating this from `EffectSlot` is the whole point of the feature: the
    same "wind howling" cue used at four points in an episode is one
    generation and one charge, not four.

    Costing differs from narration in a way that matters. The endpoint returns
    a `character-cost` header, but nothing documents what that number means for
    a product billed per second, and the docs state the cost "is not influenced
    by the text input". So USD is computed from duration at the published
    $0.12/minute, and the header is recorded in `observed_cost` as an
    observation to be reconciled later — never as the basis of a charge.
    """

    __tablename__ = "effect"
    __table_args__ = (Index("ix_effect_idempotency", "idempotency_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    slug: Mapped[str] = mapped_column(String(96))

    prompt: Mapped[str] = mapped_column(Text)
    duration_s: Mapped[float | None] = mapped_column(default=None)
    loop: Mapped[bool] = mapped_column(default=False)
    prompt_influence: Mapped[float] = mapped_column(default=0.3)
    model_id: Mapped[str] = mapped_column(String(64), default="eleven_text_to_sound_v2")
    output_format: Mapped[str] = mapped_column(String(32), default="mp3_44100_128")

    idempotency_key: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(128), default=None)

    asset_path: Mapped[str | None] = mapped_column(Text, default=None)
    # What the file actually turned out to be, which is what the timeline uses.
    actual_duration_s: Mapped[float | None] = mapped_column(default=None)

    # The `character-cost` header. Unit undocumented for per-second billing —
    # kept for reconciliation, never used to compute a charge.
    observed_cost: Mapped[int | None] = mapped_column(Integer, default=None)

    rate_usd_per_min: Mapped[float] = mapped_column(default=0.0)
    rate_card_version: Mapped[str] = mapped_column(String(32), default="")
    cost_micros: Mapped[int] = mapped_column(Integer, default=0)
    credits: Mapped[float] = mapped_column(default=0.0)
    cost_source: Mapped[str] = mapped_column(String(16), default="duration")

    status: Mapped[str] = mapped_column(String(24), default="succeeded")
    error_json: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class EffectSlot(Base):
    """A position on a script's timeline where an effect belongs.

    A slot with `effect_id IS NULL` is *planned*: the editing plan marks the
    spot and says what belongs there without anything having been generated.
    That is what makes the plan useful before any money is spent.
    """

    __tablename__ = "effect_slot"
    __table_args__ = (Index("ix_effect_slot_script", "script_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    script_id: Mapped[int] = mapped_column(ForeignKey("script.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer, default=1)

    # The slot sits at the start of this chunk, which is a boundary whose time
    # is exactly known. Chunk 1 means "at 00:00, before the narration starts".
    at_chunk_ordinal: Mapped[int] = mapped_column(Integer, default=1)

    description: Mapped[str] = mapped_column(Text)
    duration_s: Mapped[float | None] = mapped_column(default=None)
    loop: Mapped[bool] = mapped_column(default=False)

    # marker | suggested | manual
    #
    # `suggested` slots come from the LLM and start unaccepted, so a suggestion
    # can never cause a generation on its own.
    source: Mapped[str] = mapped_column(String(16), default="marker")
    accepted: Mapped[bool] = mapped_column(default=True)

    effect_id: Mapped[int | None] = mapped_column(
        ForeignKey("effect.id", ondelete="SET NULL"), default=None
    )
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
