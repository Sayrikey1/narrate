"""Deleting, restoring and replacing episodes through the web API."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from narrate.api import create_app
from narrate.settings import Settings

from .conftest import needs_ffmpeg

pytestmark = needs_ffmpeg

TEXT = (
    "The keeper wrote his last entry at dawn.\n\n[CHUNK 2]\n\n"
    "Nobody answered the radio that night, or the next.\n\n[CHUNK 3]\n\n"
    "The light kept turning anyway."
)


@pytest.fixture
def client(
    engine: Engine, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("NARRATE_PROVIDER", "mock")
    with TestClient(create_app(engine, settings)) as c:
        yield c


def _drain(client: TestClient, run_key: str) -> dict[str, Any]:
    with client.stream("GET", f"/api/runs/{run_key}/events") as stream:
        for line in stream.iter_lines():
            if line.startswith("data:") and "message" not in line:
                payload: dict[str, Any] = json.loads(line[5:])
                return payload
    return {}


@pytest.fixture
def generated(client: TestClient) -> tuple[int, int]:
    """A v3 project with one generated three-chunk episode: (project, script)."""
    project = client.post(
        "/api/projects", json={"name": "Show", "voice_id": "v1", "model_id": "eleven_v3"}
    ).json()
    script = client.post(
        "/api/scripts", json={"project_id": project["id"], "title": "Ep", "text": TEXT}
    ).json()
    client.post(f"/api/scripts/{script['id']}/generate", json={"dry_run": False})
    _drain(client, f"script-{script['id']}")
    return project["id"], script["id"]


def test_a_delete_is_previewed_then_done_and_the_spend_still_adds_up(
    client: TestClient, generated: tuple[int, int]
) -> None:
    project_id, script_id = generated
    spend = client.get(f"/api/scripts/{script_id}/cost").json()

    preview = client.post(f"/api/scripts/{script_id}/delete", json={}).json()
    assert preview["done"] is False and preview["takes"] == 3 and preview["spend_micros"] > 0
    assert [s["id"] for s in client.get("/api/scripts").json()] == [script_id]

    done = client.post(f"/api/scripts/{script_id}/delete", json={"confirm": True}).json()
    assert done["done"] is True
    assert client.get("/api/scripts").json() == []
    deleted = client.get("/api/deleted").json()
    assert [(s["id"], s["spend_micros"]) for s in deleted["scripts"]] == [
        (script_id, preview["spend_micros"])
    ]
    # Its spend is still on the project: nothing moved out of the cap.
    projects = {p["id"]: p for p in client.get("/api/projects").json()}
    assert projects[project_id]["spend_micros"] >= preview["spend_micros"]
    assert spend  # the episode's own figures were readable before


def test_a_deleted_episode_cannot_be_generated(
    client: TestClient, generated: tuple[int, int]
) -> None:
    _, script_id = generated
    client.post(f"/api/scripts/{script_id}/delete", json={"confirm": True})

    refused = client.post(f"/api/scripts/{script_id}/generate", json={"dry_run": False})
    assert refused.status_code == 409 and "deleted" in refused.json()["detail"]

    restored = client.post(f"/api/scripts/{script_id}/restore")
    assert restored.status_code == 200
    assert [s["id"] for s in client.get("/api/scripts").json()] == [script_id]


def test_deleting_a_project_hides_it_and_frees_its_name(
    client: TestClient, generated: tuple[int, int]
) -> None:
    project_id, _ = generated

    done = client.post(f"/api/projects/{project_id}/delete", json={"confirm": True}).json()

    assert done["done"] and client.get("/api/projects").json() == []
    assert client.get("/api/scripts").json() == []
    again = client.post("/api/projects", json={"name": "Show", "voice_id": "v1"})
    assert again.status_code == 200
    assert [p["id"] for p in client.get("/api/deleted").json()["projects"]] == [project_id]


def test_a_missing_episode_is_a_404(client: TestClient) -> None:
    assert client.post("/api/scripts/99/delete", json={}).status_code == 404
    assert client.post("/api/scripts/99/replace", json={"text": "x"}).status_code == 404


def test_a_replacement_is_priced_first_and_keeps_unchanged_takes(
    client: TestClient, generated: tuple[int, int]
) -> None:
    _, script_id = generated
    edited = TEXT.replace("or the next", "or the one after")

    quote = client.post(f"/api/scripts/{script_id}/replace", json={"text": edited}).json()
    assert quote["done"] is False
    assert quote["kept"] == 2 and quote["new"] == 1 and quote["chunks_to_generate"] == 1
    assert quote["quote_usd"].startswith("$")

    done = client.post(
        f"/api/scripts/{script_id}/replace", json={"text": edited, "confirm": True}
    ).json()
    assert done["done"] is True
    chunks = client.get(f"/api/scripts/{script_id}/chunks").json()
    assert [bool(c["takes"]) for c in chunks] == [True, False, True]


def test_a_replacement_can_be_uploaded_as_a_file(
    client: TestClient, generated: tuple[int, int]
) -> None:
    _, script_id = generated
    edited = TEXT.replace("The light kept turning anyway.", "The light kept on turning.")

    quote = client.post(
        f"/api/scripts/{script_id}/replace/upload",
        data={"confirm": "false"},
        files={"file": ("ep.md", edited.encode(), "text/markdown")},
    ).json()

    assert quote["kept"] == 2 and quote["new"] == 1


def test_an_effects_run_that_cannot_start_does_not_hold_the_episode(
    client: TestClient, generated: tuple[int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider that cannot be built (no key) must still release the run, or
    every later run, delete and replace of the episode is refused for good."""
    from narrate import api as api_module
    from narrate.settings import MissingAPIKey

    class NoKey:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise MissingAPIKey("No API key found.")

    _, script_id = generated
    monkeypatch.setenv("NARRATE_PROVIDER", "elevenlabs")
    monkeypatch.setattr(api_module, "ElevenLabsProvider", NoKey)

    refused = client.post(f"/api/scripts/{script_id}/effects/generate", json={"dry_run": False})
    monkeypatch.setenv("NARRATE_PROVIDER", "mock")
    deleted = client.post(f"/api/scripts/{script_id}/delete", json={"confirm": True})

    assert refused.status_code == 400 and "API key" in refused.json()["detail"]
    assert deleted.status_code == 200
