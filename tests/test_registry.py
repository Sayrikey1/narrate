"""Capability-driven behaviour — the thing that makes model choice a data question."""

from __future__ import annotations

import pytest

from narrate.registry import Registry, UnknownModel, detect_drift


def test_v3_has_no_cross_chunk_continuity_at_all(registry: Registry) -> None:
    """Worse than the docs admit, and verified live.

    The docs only say request stitching is unavailable on v3. The API also
    rejects text conditioning:

        400 unsupported_model — "Providing previous_text or next_text is not
        yet supported with the 'eleven_v3' model."

    Sending either would hard-fail every chunk after the first.
    """
    spec = registry.get("eleven_v3")
    assert spec.request_stitching is False
    assert spec.text_conditioning is False
    assert spec.continuity_mode == "none"


def test_text_conditioning_is_the_middle_continuity_mode(registry: Registry) -> None:
    """A model that lacks stitching but accepts context text still gets context."""
    base = registry.get("eleven_v3")
    spec = type(base)(**{**base.__dict__, "request_stitching": False, "text_conditioning": True})
    assert spec.continuity_mode == "text"


def test_v2_supports_request_stitching(registry: Registry) -> None:
    spec = registry.get("eleven_multilingual_v2")
    assert spec.request_stitching is True
    assert spec.continuity_mode == "request_ids"


def test_flash_models_do_not_honour_style_or_speaker_boost(registry: Registry) -> None:
    """Reported by GET /v1/models; the prose docs do not mention it."""
    spec = registry.get("eleven_flash_v2_5")
    assert spec.filter_settings({"style": 0.3, "use_speaker_boost": True, "speed": 1.1}) == {
        "speed": 1.1
    }


def test_v3_honours_only_stability(registry: Registry) -> None:
    """Speed, similarity and speaker boost are all unavailable on v3."""
    spec = registry.get("eleven_v3")
    settings = {"stability": 0.5, "speed": 0.8, "similarity_boost": 0.7, "use_speaker_boost": True}
    assert spec.filter_settings(settings) == {"stability": 0.5}
    assert spec.rejected_settings(settings) == ["similarity_boost", "speed", "use_speaker_boost"]


def test_v2_honours_the_full_settings_set(registry: Registry) -> None:
    spec = registry.get("eleven_multilingual_v2")
    settings = {"stability": 0.5, "speed": 0.8, "similarity_boost": 0.7}
    assert spec.filter_settings(settings) == settings
    assert spec.rejected_settings(settings) == []


def test_none_valued_settings_are_dropped(registry: Registry) -> None:
    spec = registry.get("eleven_multilingual_v2")
    assert spec.filter_settings({"stability": 0.5, "style": None}) == {"stability": 0.5}


def test_only_v3_family_supports_audio_tags(registry: Registry) -> None:
    assert registry.get("eleven_v3").audio_tags is True
    assert registry.get("eleven_multilingual_v2").audio_tags is False
    assert registry.get("eleven_flash_v2_5").audio_tags is False


def test_chunk_ceiling_sits_below_the_published_limit(registry: Registry) -> None:
    """A safety margin: text normalisation can expand what is actually sent."""
    spec = registry.get("eleven_v3")
    assert spec.max_chars == 5000
    assert spec.chunk_ceiling < spec.max_chars


def test_observed_limits_narrow_but_never_widen(registry: Registry) -> None:
    spec = registry.get("eleven_v3")
    tighter = type(spec)(
        **{**spec.__dict__, "observed": {"max_characters_request_subscribed_user": 2500}}
    )
    assert tighter.effective_max_chars == 2500

    wider = type(spec)(
        **{**spec.__dict__, "observed": {"max_characters_request_subscribed_user": 99_000}}
    )
    assert wider.effective_max_chars == 5000


def test_sanity_bound_values_are_ignored(registry: Registry) -> None:
    """The API reports 1,000,000 as an outer bound; it is not a real limit."""
    spec = registry.get("eleven_v3")
    bounded = type(spec)(
        **{**spec.__dict__, "observed": {"maximum_text_length_per_request": 1_000_000}}
    )
    assert bounded.effective_max_chars == 5000


def test_flash_is_half_the_price(registry: Registry) -> None:
    assert registry.get("eleven_flash_v2_5").usd_per_1k == 0.05
    assert registry.get("eleven_v3").usd_per_1k == 0.10


def test_recommended_model_supports_stitching(registry: Registry) -> None:
    """Long-form default must be one where prosody carries across chunks."""
    assert registry.recommended().request_stitching is True


def test_deprecated_models_are_hidden_by_default(registry: Registry) -> None:
    visible = {s.model_id for s in registry.all()}
    everything = {s.model_id for s in registry.all(include_deprecated=True)}
    assert "eleven_turbo_v2_5" in everything
    assert "eleven_turbo_v2_5" not in visible


def test_deprecated_models_name_their_replacement(registry: Registry) -> None:
    assert registry.get("eleven_turbo_v2_5").replacement == "eleven_flash_v2_5"


def test_unknown_model_says_what_is_available(registry: Registry) -> None:
    with pytest.raises(UnknownModel, match="eleven_multilingual_v2"):
        registry.get("eleven_v9")


def test_drift_detects_a_tighter_api_limit(registry: Registry) -> None:
    drift = detect_drift(
        registry,
        [{"model_id": "eleven_v3", "max_characters_request_subscribed_user": 3000}],
    )
    assert any(d.field == "max_chars" and d.observed == 3000 for d in drift)


def test_drift_detects_alpha_gating(registry: Registry) -> None:
    drift = detect_drift(registry, [{"model_id": "eleven_v3", "requires_alpha_access": True}])
    assert any(d.field == "requires_alpha_access" for d in drift)


def test_drift_reports_models_missing_from_the_api(registry: Registry) -> None:
    drift = detect_drift(registry, [])
    assert any(d.field == "presence" for d in drift)
