"""Process configuration.

The API key is read from the environment only. It is never written to the
database, never logged, and never echoed back to a caller — PRD §7's secrets
rule. `SecretStr` makes an accidental `print(settings)` safe by construction.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["elevenlabs", "mock"]

# Anchored on this file so the CLI behaves the same from any working directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_prefix="NARRATE_",
        extra="ignore",
    )

    # The existing .env uses ELEVEN_API; ELEVENLABS_API_KEY is the SDK's own
    # convention and is accepted as a fallback so either name works.
    api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("ELEVEN_API", "ELEVENLABS_API_KEY", "NARRATE_API_KEY"),
    )

    base_url: str = "https://api.elevenlabs.io"
    provider: ProviderName = "elevenlabs"

    # Groq, for proposing where effects belong. Entirely optional — the tool
    # works without it, and nothing it suggests is ever generated unaccepted.
    groq_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("GROQ_API_KEY", "NARRATE_GROQ_API_KEY")
    )
    groq_base_url: str = "https://api.groq.com/openai/v1"
    # `openai/gpt-oss-120b` and `-20b` are the only Groq production text models
    # that support strict json_schema output, which is what keeps a malformed
    # response from becoming a parsing problem. The larger one reads a scene
    # better; the smaller is half the price.
    groq_model: str = "openai/gpt-oss-120b"

    # Where the speech-to-text model for `narrate verify` lives, when it is not
    # in the default place beside the database — an offline machine, a model
    # copied from elsewhere. NARRATE_STT_MODEL_DIR, in the environment or .env.
    stt_model_dir: Path | None = None

    @field_validator("stt_model_dir", mode="before")
    @classmethod
    def _unset_when_empty(cls, value: object) -> object:
        # `NARRATE_STT_MODEL_DIR=` would otherwise parse as Path("."), and a
        # model download would land in whichever directory the command ran in.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    # Defaults to a managed location outside the project — see
    # [db/locate.py](db/locate.py) for why a project directory is the wrong
    # place for a WAL-mode database holding the money record. An existing
    # database in the old location is still used, and reported, rather than
    # being silently replaced by an empty one.
    db_path: Path = Field(default_factory=lambda: _default_db_path())

    # A full SQLAlchemy URL, overriding `db_path` when set. The seam exists so
    # that moving to Postgres later is configuration rather than a rewrite; no
    # dialect other than SQLite is supported or tested today.
    database_url: str | None = None
    assets_dir: Path = PROJECT_ROOT / "assets"

    # Conservative by default, as PRD §7 requires. Raised only after
    # `narrate models --sync` reports the account's real ceiling, since
    # exceeding the concurrency limit produces rejections rather than queuing.
    concurrency: int = 2

    # The SDK defaults to 240s, which is far too long to sit in a pipeline.
    connect_timeout_s: float = 10.0
    read_timeout_s: float = 120.0

    output_format: str = "mp3_44100_128"

    # Export defaults (F7).
    gap_seconds: float = 0.4
    mp3_bitrate: str = "192k"
    sample_rate: int = 44100

    # Masters to produce. WAV is the lossless one and the correct input for
    # later loudness work; MP3 plays everywhere. Add "m4a" for an NLE.
    export_formats: list[str] = ["wav", "mp3"]

    # The per-piece timeline-named files. These are what get dropped onto a
    # track, so the default is the widely-compatible one.
    piece_format: str = "mp3"

    def require_api_key(self) -> str:
        if self.api_key is None:
            raise MissingAPIKey(
                "No API key found. Set ELEVEN_API in .env (or export ELEVENLABS_API_KEY)."
            )
        return self.api_key.get_secret_value()

    @property
    def sqlalchemy_url(self) -> str:
        return self.database_url or f"sqlite:///{self.db_path}"

    @property
    def has_api_key(self) -> bool:
        return self.api_key is not None

    def require_groq_key(self) -> str:
        if self.groq_api_key is None:
            raise MissingAPIKey("No Groq key found. Set GROQ_API_KEY in .env.")
        return self.groq_api_key.get_secret_value()

    @property
    def has_groq_key(self) -> bool:
        return self.groq_api_key is not None


class MissingAPIKey(RuntimeError):
    """Raised when a command that needs the network has no key to use."""


def _default_db_path() -> Path:
    """Deferred import — `db.locate` reads `PROJECT_ROOT` from this module."""
    from narrate.db.locate import default_db_path

    return default_db_path()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def resolve_provider(override: str | None = None) -> ProviderName:
    """Pick the provider, honouring an explicit override then the environment.

    `NARRATE_PROVIDER=mock` is how the whole test suite and `just demo` run the
    real pipeline end to end without spending a character.
    """
    name = override or os.environ.get("NARRATE_PROVIDER") or get_settings().provider
    if name not in ("elevenlabs", "mock"):
        raise ValueError(f"Unknown provider {name!r}; expected 'elevenlabs' or 'mock'.")
    return name  # type: ignore[return-value]
