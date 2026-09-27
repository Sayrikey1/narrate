"""The model registry: rates, limits, and capabilities.

This is the module that makes model choice a data question rather than a set of
`if model_id == "eleven_v3"` branches scattered through the pipeline. Three
behaviours are driven from the capability flags:

    chunk ceiling      -> chunking.py sizes segments to fit the model
    continuity mode    -> runner.py picks request-id stitching or text context
    settings payload   -> the provider sends only fields the model honours

Declared values come from `config/models.toml`. Observed values come from
`GET /v1/models` (which costs zero characters) and are cached in a sidecar
JSON. Observed data *narrows* declared limits but never rewrites a rate —
prices are an operator decision, and silently adopting a scraped number into a
cost ledger is exactly the sort of thing that makes historical figures wrong.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from narrate.money import micros_from_chars
from narrate.settings import PROJECT_ROOT

# The rate card. Source, committed, edited by hand.
CONFIG_PATH = Path(__file__).resolve().parent / "config" / "models.toml"

# What `GET /v1/models` reported, cached by `narrate models --sync`. Generated
# and account-specific, so it lives with the database rather than in the project
# — for the same reason: a project directory is not a data directory, and this
# is one account's view of the API, not something to commit and share.
_LEGACY_OBSERVED = PROJECT_ROOT / "narrate.observed.json"


def observed_path() -> Path:
    """Where the observed-model cache lives.

    Falls back to the old in-project location while one exists, so an existing
    checkout keeps its cache until the next `--sync` writes to the new place.
    """
    from narrate.db.locate import data_home

    managed = data_home() / "observed.json"
    if managed.exists() or not _LEGACY_OBSERVED.exists():
        return managed
    return _LEGACY_OBSERVED


# Chunk targets are expressed as a range, but the hard ceiling gets a margin on
# top. Text normalisation can expand what is sent (numerals to words, for one),
# and the docs do not say which of the three character-limit fields the TTS
# endpoint actually enforces — their own example values contradict the
# published per-model limits. A 10% cushion costs nothing and avoids a 400.
CEILING_SAFETY = 0.90


class UnknownModel(KeyError):
    """Raised when a model id is not in the registry."""


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    label: str
    usd_per_1k: float
    credits_per_char: float
    max_chars: int
    chunk_target_min: int
    chunk_target_max: int
    request_stitching: bool
    text_conditioning: bool
    audio_tags: bool
    settings_honoured: frozenset[str]
    long_form: str
    languages: int = 0
    note: str = ""

    # -- multi-speaker dialogue --------------------------------------------
    # Whether this model can render several speakers in one request through
    # `POST /v1/text-to-dialogue`. Only `eleven_v3` can, per the docs.
    dialogue: bool = False
    # The dialogue endpoint's own ceiling: 2,000 characters across all turns,
    # *tighter* than v3's 5,000-character TTS limit, so it cannot be derived
    # from `max_chars`.
    dialogue_max_chars: int = 2000
    # How dialogue characters are priced relative to `usd_per_1k`. Left at 1.0
    # and marked unverified on purpose: no ElevenLabs page states it, and the
    # only figures available are third-party. `narrate probe --live --dialogue`
    # reads the `character-cost` header and settles it, the same way
    # docs/probe-effects.md settled the per-second question for effects.
    dialogue_cost_multiplier: float = 1.0
    dialogue_cost_verified: bool = False
    deprecated: bool = False
    replacement: str | None = None
    unverified: bool = False
    # Which pool this model draws concurrency from. Models in the same group
    # share the account's limit, so a mixed run must budget across them.
    concurrency_group: str = ""
    # Populated by `narrate models --sync`; empty until then.
    observed: dict[str, Any] = field(default_factory=dict)

    # -- limits ------------------------------------------------------------

    @property
    def effective_max_chars(self) -> int:
        """The smaller of the declared limit and anything the API reported.

        `GET /v1/models` returns three different integers and the docs do not
        say which the endpoint enforces, so take the tightest credible one.
        """
        candidates = [self.max_chars]
        for key in ("max_characters_request_subscribed_user", "maximum_text_length_per_request"):
            value = self.observed.get(key)
            # 1,000,000 appears as an outer sanity bound rather than a real
            # limit, so a value that large tells us nothing useful.
            if isinstance(value, int) and 0 < value < 1_000_000:
                candidates.append(value)
        return min(candidates)

    @property
    def chunk_ceiling(self) -> int:
        """Hard upper bound the chunker must never exceed."""
        return int(self.effective_max_chars * CEILING_SAFETY)

    @property
    def chunk_target(self) -> tuple[int, int]:
        """Preferred chunk size range, clamped to fit the effective ceiling."""
        ceiling = self.chunk_ceiling
        high = min(self.chunk_target_max, ceiling)
        low = min(self.chunk_target_min, high)
        return low, high

    # -- capabilities ------------------------------------------------------

    @property
    def continuity_mode(self) -> str:
        """How this model can be told what comes before and after a chunk.

        `request_ids` conditions on the actual generated audio and is the
        strongest. `text` conditions on the surrounding words. `none` means the
        model offers no cross-chunk continuity at all — every chunk is
        generated cold, and seams are simply a fact of using it.

        v3 is the `none` case, which is worse than the docs suggest. They only
        state that request stitching is unavailable; the API additionally
        rejects text conditioning outright:

            HTTP 400 unsupported_model — "Providing previous_text or next_text
            is not yet supported with the 'eleven_v3' model."
        """
        if self.request_stitching:
            return "request_ids"
        return "text" if self.text_conditioning else "none"

    def filter_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        """Drop settings the model does not honour.

        Sending `speed` to v3 is undocumented behaviour — it may be ignored or
        it may 400. Not sending it is defined behaviour, so we do that.
        """
        return {k: v for k, v in settings.items() if k in self.settings_honoured and v is not None}

    def rejected_settings(self, settings: dict[str, Any]) -> list[str]:
        """Setting names that would be dropped — so the CLI can say why."""
        return sorted(
            k for k, v in settings.items() if v is not None and k not in self.settings_honoured
        )

    def supports(self, capability: str) -> bool:
        return bool(getattr(self, capability, False))

    # -- cost --------------------------------------------------------------

    def cost_micros(self, chars: int) -> int:
        return micros_from_chars(chars, self.usd_per_1k)

    def dialogue_cost_micros(self, chars: int) -> int:
        """Cost of dialogue characters.

        A separate method rather than a branch inside `cost_micros`, so every
        caller has to say which it means. The multiplier is 1.0 and marked
        unverified until `narrate probe --live --dialogue` measures the real
        `character-cost` header — see `dialogue_cost_multiplier`.
        """
        return micros_from_chars(chars, self.usd_per_1k * self.dialogue_cost_multiplier)

    def credits(self, chars: int) -> float:
        return round(chars * self.credits_per_char, 2)

    @property
    def rate(self) -> Decimal:
        return Decimal(str(self.usd_per_1k))


# Everything `[meta]` in models.toml may hold.
_META_KEYS = frozenset({"rate_card_version", "default_model"})


class Registry:
    """Loaded rate card, optionally overlaid with observed API metadata."""

    def __init__(self, specs: dict[str, ModelSpec], default_model: str | None = None) -> None:
        self._specs = specs
        self._default = default_model

    @classmethod
    def load(cls, config_path: Path | None = None, observed: Path | None = None) -> Registry:
        raw = tomllib.loads((config_path or CONFIG_PATH).read_text(encoding="utf-8"))
        observed_models = _load_observed(observed or observed_path())

        specs: dict[str, ModelSpec] = {}
        for model_id, entry in raw.get("models", {}).items():
            specs[model_id] = ModelSpec(
                model_id=model_id,
                label=entry.get("label", model_id),
                usd_per_1k=float(entry["usd_per_1k"]),
                credits_per_char=float(entry.get("credits_per_char", 1.0)),
                max_chars=int(entry["max_chars"]),
                chunk_target_min=int(entry["chunk_target_min"]),
                chunk_target_max=int(entry["chunk_target_max"]),
                request_stitching=bool(entry.get("request_stitching", False)),
                text_conditioning=bool(entry.get("text_conditioning", True)),
                audio_tags=bool(entry.get("audio_tags", False)),
                dialogue=bool(entry.get("dialogue", False)),
                dialogue_max_chars=int(entry.get("dialogue_max_chars", 2000)),
                dialogue_cost_multiplier=float(entry.get("dialogue_cost_multiplier", 1.0)),
                dialogue_cost_verified=bool(entry.get("dialogue_cost_verified", False)),
                settings_honoured=frozenset(entry.get("settings_honoured", [])),
                long_form=entry.get("long_form", "unknown"),
                languages=int(entry.get("languages", 0)),
                note=entry.get("note", ""),
                deprecated=bool(entry.get("deprecated", False)),
                replacement=entry.get("replacement"),
                unverified=bool(entry.get("unverified", False)),
                concurrency_group=entry.get("concurrency_group", ""),
                observed=observed_models.get(model_id, {}),
            )

        meta = raw.get("meta", {})
        # A misspelled key would read as "no default declared" and quietly fall
        # back to another model — so an unknown key is as loud as a bad value.
        unknown = sorted(set(meta) - _META_KEYS)
        if unknown:
            raise ValueError(
                f"Unknown [meta] key(s) {', '.join(unknown)} in "
                f"{(config_path or CONFIG_PATH).name}. Known: {', '.join(sorted(_META_KEYS))}."
            )
        default = meta.get("default_model")
        if default is not None:
            # A typo here would otherwise quietly hand every new project some
            # other model. Loud, at load, names the fix.
            if default not in specs:
                raise ValueError(
                    f"default_model {default!r} in {(config_path or CONFIG_PATH).name} is not "
                    f"a model in the rate card. Known models: {', '.join(sorted(specs))}."
                )
            if specs[default].deprecated:
                raise ValueError(
                    f"default_model {default!r} is deprecated; name its replacement "
                    f"({specs[default].replacement or 'see models.toml'}) instead."
                )
        return cls(specs, default)

    @property
    def rate_card_version(self) -> str:
        raw = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        version: str = raw.get("meta", {}).get("rate_card_version", "unknown")
        return version

    def get(self, model_id: str) -> ModelSpec:
        try:
            return self._specs[model_id]
        except KeyError:
            known = ", ".join(sorted(self._specs))
            raise UnknownModel(
                f"Unknown model {model_id!r}. Known models: {known}. "
                f"Add it to {CONFIG_PATH.name} if the provider has shipped a new one."
            ) from None

    def all(self, include_deprecated: bool = False) -> list[ModelSpec]:
        specs = list(self._specs.values())
        if not include_deprecated:
            specs = [s for s in specs if not s.deprecated]
        # Cheapest first, then by how much text fits in one request — the two
        # axes an operator actually chooses on.
        return sorted(specs, key=lambda s: (s.usd_per_1k, -s.max_chars))

    def recommended(self) -> ModelSpec:
        """The model a new project gets when none is chosen.

        The rate card's `default_model` — eleven_v3 — chosen for narration
        quality and audio tags, not for continuity: v3 has none, and
        eleven_multilingual_v2 remains the choice where seamless prosody across
        chunks matters more. Without a declared default, falls back to a model
        rated "best" for long form, then to the first listed.
        """
        if self._default is not None:
            return self._specs[self._default]
        for spec in self.all():
            if spec.long_form == "best":
                return spec
        return self.all()[0]

    def __contains__(self, model_id: object) -> bool:
        return model_id in self._specs


# ---------------------------------------------------------------------------
# Observed metadata from GET /v1/models
# ---------------------------------------------------------------------------

# The fields worth keeping from the API's model objects. Each closes a gap the
# prose docs leave open: real per-tier character limits, which voice settings a
# model honours, whether the key even has access, and which concurrency pool
# the model draws from.
OBSERVED_FIELDS = (
    "max_characters_request_free_user",
    "max_characters_request_subscribed_user",
    "maximum_text_length_per_request",
    "can_do_text_to_speech",
    "can_use_style",
    "can_use_speaker_boost",
    "requires_alpha_access",
    "concurrency_group",
    "token_cost_factor",
    "model_rates",
)


def _load_observed(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # A corrupt cache must not break the tool — it is only ever an overlay.
        return {}
    models = data.get("models", {})
    return models if isinstance(models, dict) else {}


def save_observed(models: list[dict[str, Any]], fetched_at: str, path: Path | None = None) -> Path:
    """Persist the useful subset of `GET /v1/models` for the registry overlay."""
    target = path or observed_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    trimmed = {
        m["model_id"]: {k: m[k] for k in OBSERVED_FIELDS if k in m}
        for m in models
        if "model_id" in m
    }
    target.write_text(
        json.dumps({"fetched_at": fetched_at, "models": trimmed}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return target


@dataclass(frozen=True)
class Drift:
    """A disagreement between the declared rate card and what the API reports."""

    model_id: str
    field: str
    declared: Any
    observed: Any

    def __str__(self) -> str:
        return f"{self.model_id}.{self.field}: config={self.declared} api={self.observed}"


def detect_drift(registry: Registry, models: list[dict[str, Any]]) -> list[Drift]:
    """Compare declared capabilities against the API's own answer.

    Deliberately does not compare rates: `GET /v1/models` reports cost
    *multipliers*, not dollars, and conflating the two is how a ledger starts
    quoting numbers nobody can reconcile against an invoice.
    """
    drift: list[Drift] = []
    by_id = {m["model_id"]: m for m in models if "model_id" in m}

    for spec in registry.all(include_deprecated=True):
        observed = by_id.get(spec.model_id)
        if observed is None:
            drift.append(Drift(spec.model_id, "presence", "in config", "absent from API"))
            continue

        api_max = observed.get("max_characters_request_subscribed_user")
        if isinstance(api_max, int) and 0 < api_max < 1_000_000 and api_max != spec.max_chars:
            drift.append(Drift(spec.model_id, "max_chars", spec.max_chars, api_max))

        for cap, api_field in (
            ("style", "can_use_style"),
            ("use_speaker_boost", "can_use_speaker_boost"),
        ):
            api_value = observed.get(api_field)
            if isinstance(api_value, bool):
                declared = cap in spec.settings_honoured
                if declared != api_value:
                    drift.append(Drift(spec.model_id, api_field, declared, api_value))

        if observed.get("requires_alpha_access"):
            drift.append(Drift(spec.model_id, "requires_alpha_access", False, True))
        if observed.get("can_do_text_to_speech") is False:
            drift.append(Drift(spec.model_id, "can_do_text_to_speech", True, False))

    for model_id in by_id:
        if model_id not in registry and by_id[model_id].get("can_do_text_to_speech"):
            drift.append(Drift(model_id, "presence", "absent from config", "in API"))

    return drift
