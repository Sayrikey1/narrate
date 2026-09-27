"""The verify and regenerate endpoints behind the Regenerate button.

The rule the button depends on is that asking for a price sends nothing: only a
request carrying `confirm` spends, and the UI makes that a second, separate
click that names the amount.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from narrate.api import RunTracker, create_app
from narrate.db.models import Chunk, Take
from narrate.db.session import session_scope
from narrate.settings import Settings

from .conftest import needs_ffmpeg
from .verify_support import HearsTheScript

pytestmark = needs_ffmpeg

TEXT = (
    "The keeper wrote his last entry at dawn.\n\n[CHUNK 2]\n\n"
    "Nobody answered the radio that night, or the next.\n\n[CHUNK 3]\n\n"
    "The light kept turning anyway."
)


@pytest.fixture
def hears(engine: Engine) -> HearsTheScript:
    return HearsTheScript(engine)


@pytest.fixture
def client(
    engine: Engine, settings: Settings, hears: HearsTheScript, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("NARRATE_PROVIDER", "mock")
    with TestClient(create_app(engine, settings, transcriber=hears)) as c:
        yield c


@pytest.fixture
def script_id(client: TestClient) -> int:
    # Named, not the default: several tests here depend on a model that carries
    # neighbouring text into each request.
    project = client.post(
        "/api/projects",
        json={"name": "UI", "voice_id": "v1", "model_id": "eleven_multilingual_v2"},
    ).json()
    created = client.post(
        "/api/scripts", json={"project_id": project["id"], "title": "Ep", "text": TEXT}
    ).json()
    client.post(f"/api/scripts/{created['id']}/generate", json={"dry_run": False})
    _drain(client, f"script-{created['id']}")
    return int(created["id"])


def _drain(client: TestClient, run_key: str) -> dict[str, Any]:
    """Wait for a background run and return its final payload."""
    with client.stream("GET", f"/api/runs/{run_key}/events") as stream:
        for line in stream.iter_lines():
            if line.startswith("data:") and "message" not in line:
                payload: dict[str, Any] = json.loads(line[5:])
                return payload
    return {}


def _takes(engine: Engine) -> int:
    with session_scope(engine) as session:
        return len(session.scalars(select(Take)).all())


def _take_file(engine: Engine, ordinal: int, take: int = 1) -> str:
    return f"chunk-{ordinal:03d}-take-{take:02d}.mp3"


def test_the_ui_can_ask_whether_checking_is_possible(client: TestClient) -> None:
    payload = client.get("/api/verify").json()
    assert payload["available"] is True
    assert payload["model"] == "small.en"
    assert "download" in payload["download_hint"]


def test_a_fresh_take_is_unverified_until_checked(client: TestClient, script_id: int) -> None:
    take = client.get(f"/api/scripts/{script_id}/chunks").json()[0]["takes"][0]
    assert take["verify_status"] == "unverified"
    assert take["findings"] == []


def test_checking_records_each_take_with_a_readable_finding(
    client: TestClient, script_id: int, engine: Engine, hears: HearsTheScript
) -> None:
    hears.drops[_take_file(engine, 2)] = "radio"

    started = client.post(f"/api/scripts/{script_id}/verify", json={}).json()
    result = _drain(client, started["run_key"])

    assert result["checked"] == 3
    chunks = client.get(f"/api/scripts/{script_id}/chunks").json()
    statuses = [c["takes"][0]["verify_status"] for c in chunks]
    assert statuses == ["clear", "suspect", "clear"]
    [finding] = chunks[1]["takes"][0]["findings"]
    assert finding["summary"] == '"radio" not spoken (0.93s gap)'


def test_asking_for_a_price_sends_nothing(
    client: TestClient, script_id: int, engine: Engine
) -> None:
    before = _takes(engine)
    quote = client.post(f"/api/scripts/{script_id}/regenerate", json={"chunks": [2]}).json()

    assert quote["dry_run"] is True
    assert quote["worst_micros"] > 0
    assert quote["plans"][0]["cut_take"] == 1
    assert quote["mock"] is True
    assert _takes(engine) == before


def test_confirming_regenerates_and_replaces_a_flagged_take(
    client: TestClient, script_id: int, engine: Engine, hears: HearsTheScript
) -> None:
    hears.drops[_take_file(engine, 2)] = "radio"
    client.post(f"/api/scripts/{script_id}/verify", json={})
    _drain(client, f"verify-{script_id}")

    started = client.post(
        f"/api/scripts/{script_id}/regenerate", json={"chunks": [2], "confirm": True}
    ).json()
    result = _drain(client, started["run_key"])

    [done] = result["regenerated"]
    assert done["statuses"] == ["clear"]
    assert done["moved"] is True
    takes = client.get(f"/api/scripts/{script_id}/chunks").json()[1]["takes"]
    assert [(t["ordinal"], t["in_cut"]) for t in takes] == [(1, False), (2, True)]


def test_rewording_is_refused_with_the_price_not_after_it(
    client: TestClient, script_id: int, engine: Engine
) -> None:
    """The project's model stitches neighbouring text into each request, so a
    reworded chunk would re-bill its neighbours. The refusal arrives with the
    quote, before any confirmation."""
    before = _takes(engine)
    response = client.post(
        f"/api/scripts/{script_id}/regenerate",
        json={"chunks": [2], "text": "Entirely new words.", "confirm": True},
    )
    assert response.status_code == 422
    assert "would change what chunk(s) 3" in response.json()["detail"]
    assert _takes(engine) == before
    with session_scope(engine) as session:
        chunk = session.scalar(select(Chunk).where(Chunk.ordinal == 2))
        assert chunk is not None and "radio" in chunk.text


def test_a_nonsense_cut_rule_is_rejected(client: TestClient, script_id: int) -> None:
    response = client.post(
        f"/api/scripts/{script_id}/regenerate", json={"chunks": [2], "move": "sometimes"}
    )
    assert response.status_code == 422


def test_a_run_is_busy_until_it_finishes_and_a_new_one_forgets_the_old_result() -> None:
    tracker = RunTracker()
    tracker.start("script-1")
    assert tracker.busy("script-1")
    tracker.finish("script-1", {"done": True})
    assert not tracker.busy("script-1")
    tracker.start("script-1")
    assert tracker.busy("script-1")
    assert tracker.result("script-1") is None


def test_no_path_leaks_into_the_finding(client: TestClient, script_id: int) -> None:
    """Findings are shown to the user; they describe words, not files."""
    started = client.post(f"/api/scripts/{script_id}/verify", json={}).json()
    result = _drain(client, started["run_key"])
    for row in result["results"]:
        for finding in row["findings"]:
            assert str(Path.home()) not in str(finding)


def test_an_empty_chunk_list_is_refused_not_read_as_everything(
    client: TestClient, script_id: int
) -> None:
    """An empty selection reaching the server is a mistake, and reading it as
    "all chunks" would make it an expensive one."""
    response = client.post(f"/api/scripts/{script_id}/regenerate", json={"chunks": []})
    assert response.status_code == 422


def test_a_missing_script_is_a_404(client: TestClient) -> None:
    response = client.post("/api/scripts/999/regenerate", json={"chunks": [1]})
    assert response.status_code == 404


def test_a_zero_spending_limit_sends_nothing(
    client: TestClient, script_id: int, engine: Engine
) -> None:
    """Zero is a limit, not "no limit" — and one below a single try is refused
    up front, with the reason, rather than started and left to send nothing."""
    before = _takes(engine)
    response = client.post(
        f"/api/scripts/{script_id}/regenerate",
        json={"chunks": [2], "confirm": True, "max_spend_usd": 0},
    )
    assert response.status_code == 422
    assert "below the price of one try" in response.json()["detail"]
    assert _takes(engine) == before


# --------------------------------------------------------------------------
# Found checking the review fixes
# --------------------------------------------------------------------------


def test_a_generate_that_cannot_be_priced_does_not_lock_the_script(
    client: TestClient, script_id: int, engine: Engine
) -> None:
    """A chunk naming a model the rate card no longer has: the answer is the
    reason, and the script is not left "busy" until the server restarts."""
    with session_scope(engine) as session:
        chunk = session.scalar(select(Chunk).where(Chunk.ordinal == 2))
        assert chunk is not None
        chunk.model_id = "eleven_gone"

    refused = client.post(f"/api/scripts/{script_id}/generate", json={"dry_run": True})
    assert refused.status_code == 422
    assert "eleven_gone" in refused.json()["detail"]

    with session_scope(engine) as session:
        chunk = session.scalar(select(Chunk).where(Chunk.ordinal == 2))
        assert chunk is not None
        chunk.model_id = None
    again = client.post(f"/api/scripts/{script_id}/generate", json={"dry_run": True})
    assert again.status_code == 200
    _drain(client, again.json()["run_key"])


def test_a_provider_that_cannot_be_built_still_ends_the_run(
    client: TestClient, script_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No API key, say. The run must finish with the error, or every later
    generate and regenerate for the script answers 409 until a restart."""

    class NoKey:
        def __init__(self, *_: object) -> None:
            raise RuntimeError("No ElevenLabs API key is set.")

    monkeypatch.setattr("narrate.api.resolve_provider", lambda *_: "elevenlabs")
    monkeypatch.setattr("narrate.api.ElevenLabsProvider", NoKey)

    started = client.post(
        f"/api/scripts/{script_id}/generate", json={"dry_run": False, "force": True}
    )
    assert started.status_code == 200
    result = _drain(client, started.json()["run_key"])
    assert "API key" in result["error"]

    monkeypatch.setattr("narrate.api.resolve_provider", lambda *_: "mock")
    again = client.post(f"/api/scripts/{script_id}/generate", json={"dry_run": True})
    assert again.status_code == 200
    _drain(client, again.json()["run_key"])


def test_on_the_default_model_rewording_is_quoted_on_the_new_words(
    client: TestClient, engine: Engine
) -> None:
    """eleven_v3 carries nothing between chunks, so a chunk can be reworded —
    the "cannot scale: them." fix — and the quote is for the words to be sent."""
    project = client.post("/api/projects", json={"name": "V3", "voice_id": "v1"}).json()
    created = client.post(
        "/api/scripts", json={"project_id": project["id"], "title": "Ep", "text": TEXT}
    ).json()
    client.post(f"/api/scripts/{created['id']}/generate", json={"dry_run": False})
    _drain(client, f"script-{created['id']}")
    before = _takes(engine)

    new = "The one thing that cannot scale: them."
    quote = client.post(
        f"/api/scripts/{created['id']}/regenerate", json={"chunks": [1], "text": new}
    ).json()

    assert quote["dry_run"] is True
    assert quote["plans"][0]["model_id"] == "eleven_v3"
    assert quote["plans"][0]["chars"] == len(new)
    assert quote["plans"][0]["blocker"] is None
    assert _takes(engine) == before


def test_the_cost_report_survives_a_chunk_on_an_unknown_model(
    client: TestClient, script_id: int, engine: Engine
) -> None:
    """The report falls back to an estimate; only a run refuses. Otherwise the
    whole Script page fails to load, and the chunk cannot be reached to fix."""
    with session_scope(engine) as session:
        chunk = session.scalar(select(Chunk).where(Chunk.ordinal == 2))
        assert chunk is not None
        chunk.model_id = "eleven_gone"
    assert client.get(f"/api/scripts/{script_id}/cost").status_code == 200


def test_fixing_rebuilds_the_episode_when_the_cut_changed(
    client: TestClient, script_id: int, engine: Engine, hears: HearsTheScript
) -> None:
    """The point is audio with the fix in it, not one more take on disk."""
    hears.drops[_take_file(engine, 2)] = "radio"
    client.post(f"/api/scripts/{script_id}/verify", json={})
    _drain(client, f"verify-{script_id}")

    started = client.post(
        f"/api/scripts/{script_id}/regenerate",
        json={"chunks": [2], "confirm": True, "export": True},
    ).json()
    result = _drain(client, started["run_key"])

    assert result["regenerated"][0]["moved"] is True
    assert result["exported"]["chunks"] == 3


def test_a_regeneration_is_quoted_for_three_tries_unless_told_otherwise(
    client: TestClient, script_id: int
) -> None:
    one = client.post(
        f"/api/scripts/{script_id}/regenerate", json={"chunks": [2], "attempts": 1}
    ).json()
    default = client.post(f"/api/scripts/{script_id}/regenerate", json={"chunks": [2]}).json()
    assert default["attempts"] == 3
    assert default["worst_micros"] == one["worst_micros"] * 3


def test_a_script_listing_names_the_scripts_own_model(
    client: TestClient, script_id: int, engine: Engine
) -> None:
    from narrate.db.models import Script

    listed = {s["id"]: s for s in client.get("/api/scripts").json()}
    assert listed[script_id]["model_id"] == "eleven_multilingual_v2"
    with session_scope(engine) as session:
        script = session.get(Script, script_id)
        assert script is not None
        script.model_id = "eleven_v3"
    listed = {s["id"]: s for s in client.get("/api/scripts").json()}
    assert listed[script_id]["model_id"] == "eleven_v3"


def test_rebuilding_catches_up_an_export_an_earlier_run_left_stale(
    client: TestClient, script_id: int
) -> None:
    """The cut moved in an earlier regeneration; this one moves nothing. The
    audio people download must still be the current cut."""
    started = client.post(
        f"/api/scripts/{script_id}/regenerate",
        json={"chunks": [2], "confirm": True, "export": True, "move": "never"},
    ).json()
    result = _drain(client, started["run_key"])

    assert result["regenerated"][0]["moved"] is False
    assert result["exported"]["chunks"] == 3
