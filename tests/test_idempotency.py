"""Idempotency key stability — PRD's "most important correctness property"."""

from __future__ import annotations

from narrate.provider.base import TTSRequest, idempotency_key


def _req(**overrides: object) -> TTSRequest:
    base: dict[str, object] = {
        "text": "Hello world.",
        "voice_id": "voice-1",
        "model_id": "eleven_v3",
        "settings": {"stability": 0.5},
    }
    base.update(overrides)
    return TTSRequest(**base)  # type: ignore[arg-type]


def test_identical_requests_hash_identically() -> None:
    assert idempotency_key(_req()) == idempotency_key(_req())


def test_dict_key_order_does_not_change_the_key() -> None:
    a = _req(settings={"stability": 0.5, "style": 0.2})
    b = _req(settings={"style": 0.2, "stability": 0.5})
    assert idempotency_key(a) == idempotency_key(b)


def test_float_formatting_does_not_change_the_key() -> None:
    """0.5 and 0.50 are the same setting and must not look like a new generation."""
    assert idempotency_key(_req(settings={"stability": 0.5})) == idempotency_key(
        _req(settings={"stability": 0.50})
    )


def test_float_arithmetic_noise_does_not_change_the_key() -> None:
    """0.1 + 0.2 is not 0.3 in binary floating point. It must still hash the same."""
    assert idempotency_key(_req(settings={"style": 0.1 + 0.2})) == idempotency_key(
        _req(settings={"style": 0.3})
    )


def test_changing_the_text_changes_the_key() -> None:
    assert idempotency_key(_req()) != idempotency_key(_req(text="Hello world!"))


def test_changing_any_billable_parameter_changes_the_key() -> None:
    baseline = idempotency_key(_req())
    for field, value in (
        ("voice_id", "voice-2"),
        ("model_id", "eleven_multilingual_v2"),
        ("output_format", "mp3_44100_192"),
        ("seed", 7),
        ("settings", {"stability": 0.9}),
    ):
        assert idempotency_key(_req(**{field: value})) != baseline, field


def test_volatile_request_ids_do_not_affect_the_key() -> None:
    """The resume-safety property.

    Request ids differ on every run and the provider expires them after two
    hours. If they were part of the key, a second run could never match a
    first, and every chunk after the first would be silently regenerated and
    re-charged.
    """
    assert idempotency_key(_req(previous_request_ids=("req-1",))) == idempotency_key(
        _req(previous_request_ids=("req-2",))
    )
    assert idempotency_key(_req(previous_request_ids=("req-1",))) == idempotency_key(_req())


def test_continuity_fingerprint_is_part_of_the_key() -> None:
    """Conditioning on different prior audio genuinely produces different output.

    The fingerprint is derived from the preceding chunks' text, so it is stable
    across runs but changes the moment an earlier chunk is edited.
    """
    a = _req(continuity_fingerprint="abc123")
    b = _req(continuity_fingerprint="def456")
    assert idempotency_key(a) != idempotency_key(b)
    assert idempotency_key(a) != idempotency_key(_req())


def test_text_context_is_part_of_the_key() -> None:
    """On models without stitching, the surrounding text is the conditioning."""
    assert idempotency_key(_req(previous_text="Before.")) != idempotency_key(_req())


def test_key_is_a_hex_sha256() -> None:
    key = idempotency_key(_req())
    assert len(key) == 64
    assert all(c in "0123456789abcdef" for c in key)
