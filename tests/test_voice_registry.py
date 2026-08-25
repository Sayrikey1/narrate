"""Local names for voices, so a cloned one can be reused without its id.

The registry stores no audio and grants no access — it maps a handle to a
provider voice id. Two properties carry the weight:

* **Resolution is forgiving.** Anything that is not a known handle passes
  through untouched, so every `--voice <id>` written before handles existed
  keeps working and no id can be shadowed by a name.
* **Repointing a name is never implicit.** A name silently moving to a different
  voice would change what the next generation produces, and the charge would
  land before anybody noticed.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine

from narrate import voices
from narrate.db.session import session_scope


def test_a_voice_can_be_named(engine: Engine) -> None:
    with session_scope(engine) as session:
        row = voices.register(session, "21m00Tcm4TlvDq8ikWAM", name="my-voice")
        assert row.slug == "my-voice"
        assert row.voice_id == "21m00Tcm4TlvDq8ikWAM"


def test_the_name_defaults_to_the_providers_own(engine: Engine) -> None:
    """Which is what makes registration a one-argument command most of the time."""
    with session_scope(engine) as session:
        row = voices.register(session, "abc123", label="Brian - Relatable Everyman")
        assert row.slug == "brian-relatable-everyman"
        assert row.label == "Brian - Relatable Everyman"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("My Voice", "my-voice"),
        ("Brian — Relatable", "brian-relatable"),
        ("  spaced  out  ", "spaced-out"),
        ("Ünïcodé", "n-cod"),
        ("!!!", "voice"),
    ],
)
def test_slugify(raw: str, expected: str) -> None:
    assert voices.slugify(raw) == expected


# ---------------------------------------------------------------------------
# Resolution — the property every existing command depends on
# ---------------------------------------------------------------------------


def test_a_name_resolves_to_its_id(engine: Engine) -> None:
    with session_scope(engine) as session:
        voices.register(session, "21m00Tcm4TlvDq8ikWAM", name="my-voice")
        assert voices.resolve(session, "my-voice") == "21m00Tcm4TlvDq8ikWAM"


def test_an_unknown_value_passes_through_untouched(engine: Engine) -> None:
    """Every `--voice <id>` written before names existed has to keep working."""
    with session_scope(engine) as session:
        assert voices.resolve(session, "21m00Tcm4TlvDq8ikWAM") == "21m00Tcm4TlvDq8ikWAM"
        assert voices.resolve(session, "not-registered") == "not-registered"


def test_resolution_is_case_insensitive(engine: Engine) -> None:
    with session_scope(engine) as session:
        voices.register(session, "abc123", name="my-voice")
        assert voices.resolve(session, "My-Voice") == "abc123"
        assert voices.resolve(session, "  MY-VOICE  ") == "abc123"


def test_nothing_resolves_to_nothing(engine: Engine) -> None:
    with session_scope(engine) as session:
        assert voices.resolve(session, None) is None
        assert voices.resolve(session, "") == ""


def test_a_registered_id_can_be_looked_up_by_id(engine: Engine) -> None:
    with session_scope(engine) as session:
        voices.register(session, "abc123", name="my-voice")
        assert voices.label_for(session, "abc123") == "my-voice"
        assert voices.label_for(session, "unknown") is None


# ---------------------------------------------------------------------------
# The guards
# ---------------------------------------------------------------------------


def test_a_name_that_looks_like_an_id_is_refused(engine: Engine) -> None:
    """A handle exists so the id does not have to be typed. One shaped like an
    id would also make `--voice` ambiguous about which it was handed."""
    with session_scope(engine) as session:
        with pytest.raises(voices.BadVoiceName, match="looks like a voice id"):
            voices.register(session, "abc123", name="AbCdEfGhIjKlMnOpQrSt")


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("-leading", "leading"),
        ("has space", "has-space"),
        ("UPPER!", "upper"),
        ("with/slash", "with-slash"),
        ("", "abc123"),  # falls back to naming it after the id
    ],
)
def test_awkward_input_is_normalised_rather_than_refused(
    engine: Engine, typed: str, stored: str
) -> None:
    """`--name "My Voice"` should work, so the handle is cleaned rather than
    rejected. Refusing would mean explaining slug rules to somebody who typed
    something perfectly reasonable."""
    with session_scope(engine) as session:
        assert voices.register(session, "abc123", name=typed).slug == stored


def test_a_name_is_never_silently_repointed(engine: Engine) -> None:
    """The money-adjacent one. A name moving to a different voice changes what
    the next generation produces, and the charge lands before anybody looks."""
    with session_scope(engine) as session:
        voices.register(session, "first-voice", name="my-voice")
        with pytest.raises(voices.VoiceNameTaken, match="already points at"):
            voices.register(session, "second-voice", name="my-voice")


def test_replace_repoints_a_name_deliberately(engine: Engine) -> None:
    with session_scope(engine) as session:
        voices.register(session, "first-voice", name="my-voice")
        voices.register(session, "second-voice", name="my-voice", replace=True)
        assert voices.resolve(session, "my-voice") == "second-voice"


def test_the_same_voice_is_not_registered_twice_by_accident(engine: Engine) -> None:
    with session_scope(engine) as session:
        voices.register(session, "abc123", name="first-name")
        with pytest.raises(voices.VoiceAlreadyRegistered, match="already registered"):
            voices.register(session, "abc123", name="second-name")


def test_registering_a_name_again_for_the_same_voice_is_idempotent(engine: Engine) -> None:
    with session_scope(engine) as session:
        voices.register(session, "abc123", name="my-voice", note="first")
        voices.register(session, "abc123", name="my-voice", note="second")
        records = voices.all_voices(session)
        assert len(records) == 1
        assert records[0].note == "second"


# ---------------------------------------------------------------------------
# Forgetting, and what it deliberately does not do
# ---------------------------------------------------------------------------


def test_forgetting_drops_the_name_only(engine: Engine) -> None:
    with session_scope(engine) as session:
        voices.register(session, "abc123", name="my-voice")
        voices.forget(session, "my-voice")
        assert voices.all_voices(session) == []
        # The id still works, because the voice itself was never ours.
        assert voices.resolve(session, "abc123") == "abc123"


def test_forgetting_something_unregistered_says_so(engine: Engine) -> None:
    with session_scope(engine) as session:
        with pytest.raises(voices.UnknownVoice):
            voices.forget(session, "never-registered")


def test_a_clone_is_marked_as_one(engine: Engine) -> None:
    with session_scope(engine) as session:
        voices.register(session, "abc123", name="mine", category="cloned")
        voices.register(session, "def456", name="stock", category="premade")
        by_slug = {v.slug: v for v in voices.all_voices(session)}
        assert by_slug["mine"].is_clone
        assert not by_slug["stock"].is_clone


def test_offline_registration_is_marked_unverified(engine: Engine) -> None:
    """A voice can be named before the provider has confirmed it — for one added
    to the account since the last list, or when there is no key."""
    with session_scope(engine) as session:
        voices.register(session, "abc123", name="mine", verified=False)
        assert voices.all_voices(session)[0].verified is False
        voices.register(session, "def456", name="checked", verified=True)
        assert {v.slug: v.verified for v in voices.all_voices(session)}["checked"] is True
