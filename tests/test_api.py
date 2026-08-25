"""The HTTP layer. It must agree with the CLI, because it calls the same code."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from narrate.api import create_app
from narrate.settings import Settings

from .conftest import needs_ffmpeg

SCRIPT = """First paragraph of the episode, which runs for a sentence or two.

[SFX: wind howling, 4s]

Second paragraph picks the story up again after the wind.

[SFX: wind howling, 4s]

Third paragraph is a short coda.
"""


@pytest.fixture
def client(
    engine: Engine, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("NARRATE_PROVIDER", "mock")
    with TestClient(create_app(engine, settings)) as c:
        yield c


@pytest.fixture
def script(client: TestClient) -> dict[str, int]:
    project = client.post("/api/projects", json={"name": "UI", "voice_id": "v1"}).json()
    created = client.post(
        "/api/scripts",
        json={"project_id": project["id"], "title": "Episode", "text": SCRIPT},
    ).json()
    return {"project_id": project["id"], "script_id": created["id"]}


# -- reference data ---------------------------------------------------------


def test_models_expose_the_continuity_capability(client: TestClient) -> None:
    """The UI has to be able to warn about v3's lack of continuity."""
    models = {m["model_id"]: m for m in client.get("/api/models").json()}
    assert models["eleven_v3"]["continuity_mode"] == "none"
    assert models["eleven_multilingual_v2"]["continuity_mode"] == "request_ids"


def test_voices_never_fail_without_a_key(client: TestClient) -> None:
    """Missing credentials must not break the picker — see the stand-in test below."""
    response = client.get("/api/voices")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


# -- ingestion --------------------------------------------------------------


def test_creating_a_script_chunks_it_and_records_its_slots(client: TestClient) -> None:
    project = client.post("/api/projects", json={"name": "P", "voice_id": "v1"}).json()
    created = client.post(
        "/api/scripts", json={"project_id": project["id"], "title": "E", "text": SCRIPT}
    ).json()
    assert created["chunks"] == 3
    assert created["slots"] == 2


def test_the_api_strips_markers_exactly_as_the_cli_does(
    client: TestClient, script: dict[str, int]
) -> None:
    """Two entry points that disagreed about this would be a billing bug."""
    for chunk in client.get(f"/api/scripts/{script['script_id']}/chunks").json():
        assert "[SFX" not in chunk["text"]
        assert "[@" not in chunk["text"]


def test_creating_a_script_for_a_missing_project_is_a_404(client: TestClient) -> None:
    response = client.post("/api/scripts", json={"project_id": 999, "title": "E", "text": "text"})
    assert response.status_code == 404


def test_duplicate_project_names_are_rejected(client: TestClient) -> None:
    client.post("/api/projects", json={"name": "dup"})
    assert client.post("/api/projects", json={"name": "dup"}).status_code == 409


# -- timeline and plan ------------------------------------------------------


def test_timeline_is_available_before_anything_is_generated(
    client: TestClient, script: dict[str, int]
) -> None:
    payload = client.get(f"/api/scripts/{script['script_id']}/timeline").json()
    assert payload["complete"] is False
    assert [e["kind"] for e in payload["entries"]].count("planned") == 2
    assert all("start" in e and "end" in e for e in payload["entries"])


def test_plan_endpoint_returns_markdown(client: TestClient, script: dict[str, int]) -> None:
    body = client.get(f"/api/scripts/{script['script_id']}/plan").json()["markdown"]
    assert body.startswith("# Episode")
    assert "Running order" in body


def test_unknown_script_is_a_404(client: TestClient) -> None:
    assert client.get("/api/scripts/999/timeline").status_code == 404
    assert client.get("/api/scripts/999/plan").status_code == 404


# -- generation -------------------------------------------------------------


@needs_ffmpeg
def test_generate_streams_progress_and_reports_a_result(
    client: TestClient, script: dict[str, int]
) -> None:
    """PRD §9: the UI must never block, and progress is streamed per chunk."""
    script_id = script["script_id"]
    started = client.post(f"/api/scripts/{script_id}/generate", json={"dry_run": False}).json()
    assert started["streaming"] is True

    messages: list[str] = []
    done: dict[str, object] = {}
    with client.stream("GET", f"/api/runs/{started['run_key']}/events") as stream:
        for line in stream.iter_lines():
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                if "message" in payload:
                    messages.append(payload["message"])
                else:
                    done = payload
            if done:
                break

    assert any("chunk" in m for m in messages)
    assert done["succeeded"] == 3
    assert done["failed"] == 0
    assert int(done["spent_micros"]) > 0  # type: ignore[call-overload]


def test_events_for_an_unknown_run_are_a_404(client: TestClient) -> None:
    assert client.get("/api/runs/nope/events").status_code == 404


@needs_ffmpeg
def test_a_take_can_be_promoted_into_the_cut(client: TestClient, script: dict[str, int]) -> None:
    script_id = script["script_id"]
    _run_to_completion(client, script_id)
    client.post(
        f"/api/scripts/{script_id}/generate",
        json={"dry_run": False, "only": [1], "force": True},
    )
    _drain(client, f"script-{script_id}")

    response = client.post(
        f"/api/scripts/{script_id}/cut", json={"chunk_ordinal": 1, "take_ordinal": 2}
    )
    assert response.status_code == 200

    chunk = client.get(f"/api/scripts/{script_id}/chunks").json()[0]
    assert [t["in_cut"] for t in chunk["takes"]] == [False, True]


def test_selecting_a_missing_take_is_a_404(client: TestClient, script: dict[str, int]) -> None:
    response = client.post(
        f"/api/scripts/{script['script_id']}/cut",
        json={"chunk_ordinal": 1, "take_ordinal": 99},
    )
    assert response.status_code == 404


# -- effects ----------------------------------------------------------------


def test_slots_from_markers_are_listed(client: TestClient, script: dict[str, int]) -> None:
    slots = client.get(f"/api/scripts/{script['script_id']}/effects").json()
    assert len(slots) == 2
    assert all(s["source"] == "marker" and s["accepted"] for s in slots)
    assert all(s["effect_id"] is None for s in slots)


def test_a_slot_can_be_added_and_removed(client: TestClient, script: dict[str, int]) -> None:
    script_id = script["script_id"]
    created = client.post(
        f"/api/scripts/{script_id}/effects",
        json={"at_chunk_ordinal": 2, "description": "door closing", "duration_s": 2.0},
    ).json()
    assert len(client.get(f"/api/scripts/{script_id}/effects").json()) == 3

    client.delete(f"/api/effects/slots/{created['id']}")
    assert len(client.get(f"/api/scripts/{script_id}/effects").json()) == 2


@needs_ffmpeg
def test_generating_effects_reuses_a_repeated_cue(
    client: TestClient, script: dict[str, int]
) -> None:
    """Both slots ask for the same wind; that must be one charge."""
    script_id = script["script_id"]
    payload = client.post(
        f"/api/scripts/{script_id}/effects/generate", json={"dry_run": False}
    ).json()

    statuses = [o["status"] for o in payload["outcomes"]]
    assert statuses.count("generated") == 1
    assert statuses.count("reused") == 1


def test_an_unaccepted_slot_is_not_generated(client: TestClient, script: dict[str, int]) -> None:
    script_id = script["script_id"]
    for slot in client.get(f"/api/scripts/{script_id}/effects").json():
        client.post(f"/api/effects/slots/{slot['id']}/accept", params={"accepted": False})

    payload = client.post(
        f"/api/scripts/{script_id}/effects/generate", json={"dry_run": False}
    ).json()
    assert all(o["status"] == "skipped_unaccepted" for o in payload["outcomes"])
    assert payload["cost_micros"] == 0


# -- export and audio -------------------------------------------------------


@needs_ffmpeg
def test_export_returns_the_timeline_named_files(
    client: TestClient, script: dict[str, int]
) -> None:
    script_id = script["script_id"]
    _run_to_completion(client, script_id)

    payload = client.post(f"/api/scripts/{script_id}/export").json()
    assert payload["plan"] == "plan.md"
    assert payload["chunks"] == 3
    for name in payload["names"].values():
        assert (Path(payload["out_dir"]) / name).exists()


def test_exporting_an_ungenerated_script_is_a_conflict(
    client: TestClient, script: dict[str, int]
) -> None:
    assert client.post(f"/api/scripts/{script['script_id']}/export").status_code == 409


@needs_ffmpeg
def test_take_audio_is_served_for_auditioning(client: TestClient, script: dict[str, int]) -> None:
    script_id = script["script_id"]
    _run_to_completion(client, script_id)
    take_id = client.get(f"/api/scripts/{script_id}/chunks").json()[0]["takes"][0]["id"]

    response = client.get(f"/api/audio/take/{take_id}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert len(response.content) > 0


def test_audio_for_a_missing_take_is_a_404(client: TestClient) -> None:
    assert client.get("/api/audio/take/999").status_code == 404


# -- helpers ----------------------------------------------------------------


def _run_to_completion(client: TestClient, script_id: int) -> None:
    client.post(f"/api/scripts/{script_id}/generate", json={"dry_run": False})
    _drain(client, f"script-{script_id}")


def _drain(client: TestClient, run_key: str) -> None:
    with client.stream("GET", f"/api/runs/{run_key}/events") as stream:
        for line in stream.iter_lines():
            if line.startswith("event: done"):
                return


# -- projects in detail -----------------------------------------------------


def test_project_detail_carries_its_scripts_and_cost(
    client: TestClient, script: dict[str, int]
) -> None:
    payload = client.get(f"/api/projects/{script['project_id']}").json()
    assert payload["name"] == "UI"
    assert [s["id"] for s in payload["scripts"]] == [script["script_id"]]
    assert "by_kind" in payload["cost"]


def test_unknown_project_is_a_404(client: TestClient) -> None:
    assert client.get("/api/projects/999").status_code == 404
    assert client.get("/api/projects/999/media").status_code == 404


def test_script_cost_projects_what_is_left(client: TestClient, script: dict[str, int]) -> None:
    """Nothing generated yet, so everything is outstanding."""
    cost = client.get(f"/api/scripts/{script['script_id']}/cost").json()
    assert cost["cost_micros"] == 0
    assert cost["outstanding"]["chunks"] == 3
    assert cost["outstanding"]["effects"] == 2
    assert cost["projected_micros"] > 0


# -- media: the security boundary, over HTTP --------------------------------


@pytest.mark.parametrize("attack", ["../../.env", "../.env", "/etc/passwd", "sub/../../../.env"])
def test_media_file_refuses_paths_outside_the_root(client: TestClient, attack: str) -> None:
    """`.env` holds the API key. A 403 here is the whole point of the endpoint."""
    response = client.get("/api/media/file", params={"path": attack})
    assert response.status_code == 403


def test_media_file_404s_for_something_inside_that_does_not_exist(
    client: TestClient,
) -> None:
    response = client.get("/api/media/file", params={"path": "Ep14/nope.mp3"})
    assert response.status_code == 404


# -- media: browsing, playback and download ---------------------------------


@needs_ffmpeg
def test_media_listing_covers_exports_effects_and_takes(
    client: TestClient, script: dict[str, int]
) -> None:
    script_id = script["script_id"]
    _run_to_completion(client, script_id)
    client.post(f"/api/scripts/{script_id}/effects/generate", json={"dry_run": False})
    client.post(f"/api/scripts/{script_id}/export")

    payload = client.get(f"/api/projects/{script['project_id']}/media").json()
    kinds = {group["kind"] for group in payload["groups"]}
    assert {"export", "effect", "take"} <= kinds
    assert payload["files"] > 0
    assert payload["bytes"] > 0

    export_group = next(g for g in payload["groups"] if g["kind"] == "export")
    assert any(f["name"] == "plan.md" for f in export_group["files"])
    assert any(f["name"].endswith(".wav") and f["playable"] for f in export_group["files"])


@needs_ffmpeg
def test_every_listed_file_can_be_fetched(client: TestClient, script: dict[str, int]) -> None:
    script_id = script["script_id"]
    _run_to_completion(client, script_id)
    client.post(f"/api/scripts/{script_id}/export")

    payload = client.get(f"/api/projects/{script['project_id']}/media").json()
    fetched = 0
    for group in payload["groups"]:
        for file in group["files"]:
            response = client.get("/api/media/file", params={"path": file["path"]})
            assert response.status_code == 200, file["path"]
            assert len(response.content) > 0
            fetched += 1
    assert fetched > 0


@needs_ffmpeg
def test_playback_is_inline_and_download_is_an_attachment(
    client: TestClient, script: dict[str, int]
) -> None:
    """Same file, two intents — the header is what distinguishes them."""
    script_id = script["script_id"]
    _run_to_completion(client, script_id)
    client.post(f"/api/scripts/{script_id}/export")

    payload = client.get(f"/api/projects/{script['project_id']}/media").json()
    master = next(f for g in payload["groups"] for f in g["files"] if f["name"].endswith(".wav"))

    inline = client.get("/api/media/file", params={"path": master["path"]})
    assert inline.headers["content-type"].startswith("audio/")
    assert "attachment" not in inline.headers.get("content-disposition", "")

    attached = client.get("/api/media/file", params={"path": master["path"], "download": True})
    assert "attachment" in attached.headers["content-disposition"]
    assert master["name"] in attached.headers["content-disposition"]


@needs_ffmpeg
def test_the_export_archive_contains_the_whole_export(
    client: TestClient, script: dict[str, int]
) -> None:
    import io
    import zipfile

    script_id = script["script_id"]
    _run_to_completion(client, script_id)
    exported = client.post(f"/api/scripts/{script_id}/export").json()

    response = client.get(f"/api/exports/{script_id}/archive")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
        names = set(bundle.namelist())
    assert "plan.md" in names
    assert exported["master"] in names
    assert set(exported["names"].values()) <= names
    # Working files are not deliverables.
    assert not any(n.startswith("_") for n in names)


def test_archiving_an_unexported_script_is_a_conflict(
    client: TestClient, script: dict[str, int]
) -> None:
    assert client.get(f"/api/exports/{script['script_id']}/archive").status_code == 409


# -- choosing a voice before generating -------------------------------------


def test_voices_are_offered_offline_as_labelled_stand_ins(client: TestClient) -> None:
    """The picker must be usable without a key, and honest about it."""
    voices = client.get("/api/voices").json()
    assert voices
    assert all(v["mock"] for v in voices)
    assert all("voice_id" in v and "name" in v for v in voices)


def test_models_say_which_settings_they_honour(client: TestClient) -> None:
    """A slider that silently does nothing is worse than no slider."""
    models = {m["model_id"]: m for m in client.get("/api/models").json()}
    assert models["eleven_v3"]["settings_honoured"] == ["stability"]
    assert "speed" in models["eleven_multilingual_v2"]["settings_honoured"]
    assert "speed" not in models["eleven_v3"]["settings_honoured"]


def test_a_project_voice_and_delivery_can_be_changed(
    client: TestClient, script: dict[str, int]
) -> None:
    project_id = script["project_id"]
    result = client.patch(
        f"/api/projects/{project_id}",
        json={
            "voice_id": "voice-9",
            "model_id": "eleven_multilingual_v2",
            "settings": {"stability": 0.8, "speed": 0.9},
        },
    ).json()

    assert result["voice_id"] == "voice-9"
    assert result["settings"] == {"speed": 0.9, "stability": 0.8}
    assert result["rejected_settings"] == []

    assert client.get(f"/api/projects/{project_id}").json()["voice_id"] == "voice-9"


def test_settings_a_model_ignores_are_dropped_and_reported(
    client: TestClient, script: dict[str, int]
) -> None:
    """v3 honours stability alone; keeping a speed it ignores would mislead."""
    result = client.patch(
        f"/api/projects/{script['project_id']}",
        json={"model_id": "eleven_v3", "settings": {"stability": 0.4, "speed": 0.8}},
    ).json()

    assert result["settings"] == {"stability": 0.4}
    assert result["rejected_settings"] == ["speed"]


def test_audio_tags_are_refused_on_a_model_without_them(
    client: TestClient, script: dict[str, int]
) -> None:
    response = client.patch(
        f"/api/projects/{script['project_id']}",
        json={"model_id": "eleven_multilingual_v2", "prefix_tags": "[urgent]"},
    )
    assert response.status_code == 400


def test_patching_an_unknown_model_is_rejected(client: TestClient, script: dict[str, int]) -> None:
    response = client.patch(f"/api/projects/{script['project_id']}", json={"model_id": "eleven_v9"})
    assert response.status_code == 400


# -- uploading a script -----------------------------------------------------

UPLOAD = "/api/scripts/upload"


def test_a_markdown_file_can_be_uploaded(client: TestClient) -> None:
    project = client.post("/api/projects", json={"name": "Up", "voice_id": "v1"}).json()
    response = client.post(
        UPLOAD,
        data={"project_id": str(project["id"]), "title": "Episode"},
        files={"file": ("episode-14.md", SCRIPT.encode(), "text/markdown")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["chunks"] == 3
    assert body["slots"] == 2


def test_upload_and_paste_produce_identical_chunks(client: TestClient) -> None:
    """Both go through `ingest_script`; this is what proves they did.

    Two ingestion paths that diverged would mean markers stripped in one and
    spoken aloud — and billed — in the other.
    """
    a = client.post("/api/projects", json={"name": "Pasted", "voice_id": "v1"}).json()
    b = client.post("/api/projects", json={"name": "Uploaded", "voice_id": "v1"}).json()

    pasted = client.post(
        "/api/scripts", json={"project_id": a["id"], "title": "E", "text": SCRIPT}
    ).json()
    uploaded = client.post(
        UPLOAD,
        data={"project_id": str(b["id"]), "title": "E"},
        files={"file": ("e.md", SCRIPT.encode(), "text/markdown")},
    ).json()

    left = client.get(f"/api/scripts/{pasted['id']}/chunks").json()
    right = client.get(f"/api/scripts/{uploaded['id']}/chunks").json()

    assert [c["text"] for c in left] == [c["text"] for c in right]
    assert [c["source"] for c in left] == [c["source"] for c in right]
    assert [c["target_start_s"] for c in left] == [c["target_start_s"] for c in right]

    assert [
        s["description"] for s in client.get(f"/api/scripts/{pasted['id']}/effects").json()
    ] == [s["description"] for s in client.get(f"/api/scripts/{uploaded['id']}/effects").json()]


def test_upload_strips_markers_too(client: TestClient) -> None:
    """The money-protecting property, via the upload path."""
    project = client.post("/api/projects", json={"name": "M", "voice_id": "v1"}).json()
    created = client.post(
        UPLOAD,
        data={"project_id": str(project["id"])},
        files={"file": ("e.md", SCRIPT.encode(), "text/markdown")},
    ).json()

    for chunk in client.get(f"/api/scripts/{created['id']}/chunks").json():
        assert "[SFX" not in chunk["text"]
        assert "[@" not in chunk["text"]


def test_the_filename_becomes_the_title_when_none_is_given(client: TestClient) -> None:
    project = client.post("/api/projects", json={"name": "T", "voice_id": "v1"}).json()
    created = client.post(
        UPLOAD,
        data={"project_id": str(project["id"])},
        files={"file": ("the-longest-winter.md", SCRIPT.encode(), "text/markdown")},
    ).json()
    titles = {s["id"]: s["title"] for s in client.get("/api/scripts").json()}
    assert titles[created["id"]] == "the-longest-winter"


@pytest.mark.parametrize(
    ("name", "data", "reason"),
    [
        ("payload.exe", b"MZ\x90\x00", "not a text script"),
        ("huge.txt", b"x" * 2_000_000, "MB"),
        ("utf16.txt", "Hello.".encode("utf-16"), "not UTF-8"),
        ("empty.txt", b"  \n ", "empty"),
    ],
)
def test_bad_uploads_are_refused_with_a_reason_not_a_500(
    client: TestClient, name: str, data: bytes, reason: str
) -> None:
    project = client.post("/api/projects", json={"name": f"R{name}", "voice_id": "v1"}).json()
    response = client.post(
        UPLOAD,
        data={"project_id": str(project["id"])},
        files={"file": (name, data, "application/octet-stream")},
    )
    assert response.status_code == 415
    assert reason in response.json()["detail"]


def test_uploading_to_a_missing_project_is_a_404(client: TestClient) -> None:
    response = client.post(
        UPLOAD,
        data={"project_id": "999"},
        files={"file": ("e.md", b"Some prose.", "text/markdown")},
    )
    assert response.status_code == 404


# -- downloading the script back out ----------------------------------------


@pytest.mark.parametrize(("fmt", "media"), [("md", "text/markdown"), ("txt", "text/plain")])
def test_a_script_downloads_as_text(
    client: TestClient, script: dict[str, int], fmt: str, media: str
) -> None:
    response = client.get(f"/api/scripts/{script['script_id']}/download", params={"format": fmt})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(media)
    assert "attachment" in response.headers["content-disposition"]
    assert f".{fmt}" in response.headers["content-disposition"]
    # The original text, markers and all — this is the source, not the chunks.
    assert "[SFX: wind howling, 4s]" in response.text


def test_an_unknown_download_format_is_rejected(client: TestClient, script: dict[str, int]) -> None:
    response = client.get(f"/api/scripts/{script['script_id']}/download", params={"format": "pdf"})
    assert response.status_code == 400


# -- export formats over HTTP -----------------------------------------------


def test_formats_are_listed_for_the_picker(client: TestClient) -> None:
    formats = {f["key"]: f for f in client.get("/api/formats").json()}
    assert formats["m4a"]["codec"] == "aac"
    assert formats["wav"]["lossless"] is True
    assert formats["mp3"]["lossless"] is False


@needs_ffmpeg
def test_export_honours_the_requested_formats(client: TestClient, script: dict[str, int]) -> None:
    script_id = script["script_id"]
    _run_to_completion(client, script_id)

    payload = client.post(
        f"/api/scripts/{script_id}/export", json={"formats": ["wav", "m4a"]}
    ).json()

    assert set(payload["masters"]) == {"wav", "m4a"}
    for name in payload["masters"].values():
        assert (Path(payload["out_dir"]) / name).exists()


@needs_ffmpeg
def test_an_unknown_export_format_is_a_400(client: TestClient, script: dict[str, int]) -> None:
    script_id = script["script_id"]
    _run_to_completion(client, script_id)
    response = client.post(f"/api/scripts/{script_id}/export", json={"formats": ["aiff"]})
    assert response.status_code == 400


# -- one generate covering both phases --------------------------------------


def test_generate_reports_the_combined_projection(
    client: TestClient, script: dict[str, int]
) -> None:
    """The estimate the UI shows before spending covers speech and cues."""
    payload = client.post(
        f"/api/scripts/{script['script_id']}/generate", json={"dry_run": True}
    ).json()
    projection = payload["projection"]
    assert projection["chunks"] == 3
    assert projection["effects"] == 2
    assert projection["speech_micros"] > 0
    assert projection["effect_micros"] > 0
    assert projection["total_micros"] == projection["speech_micros"] + projection["effect_micros"]


def test_with_effects_false_projects_speech_only(
    client: TestClient, script: dict[str, int]
) -> None:
    payload = client.post(
        f"/api/scripts/{script['script_id']}/generate",
        json={"dry_run": True, "with_effects": False},
    ).json()
    assert payload["projection"]["effect_micros"] == 0
    assert payload["projection"]["effects"] == 0


@needs_ffmpeg
def test_one_generate_produces_speech_and_effects(
    client: TestClient, script: dict[str, int]
) -> None:
    script_id = script["script_id"]
    started = client.post(f"/api/scripts/{script_id}/generate", json={"dry_run": False}).json()

    done: dict[str, object] = {}
    with client.stream("GET", f"/api/runs/{started['run_key']}/events") as stream:
        for line in stream.iter_lines():
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                if "message" not in payload:
                    done = payload
            if done:
                break

    assert done["succeeded"] == 3
    # Two slots naming one cue: generated once, reused once.
    assert done["effects_generated"] == 1
    assert done["effects_reused"] == 1
    assert done["blocked"] is False


# -- the SPA fallback -------------------------------------------------------


@pytest.mark.parametrize("path", ["/script/1", "/script/1/media", "/script/1/effects", "/costs"])
def test_client_routes_survive_a_hard_refresh(
    client: TestClient, path: str, tmp_path: Path
) -> None:
    """A bookmarked page must load, not 404.

    Skipped when the frontend has not been built, since the fallback only
    exists once there is a shell to return.
    """
    from narrate.api import FRONTEND_DIST

    if not FRONTEND_DIST.is_dir():
        pytest.skip("frontend/dist not built")
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_an_unknown_api_path_still_404s(client: TestClient) -> None:
    """The catch-all must not swallow API typos and return HTML."""
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert "text/html" not in response.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Voice previews. Auditioning is free — it plays a sample the provider already
# hosts — so the only thing that can go wrong is the plumbing, and the plumbing
# had four separate ways to fail silently. See previews.py.
# ---------------------------------------------------------------------------


def test_a_stub_voice_advertises_a_preview(client: TestClient) -> None:
    """Offline stand-ins used to report no preview, so every row in the picker
    offered a dead control. A play button that can never work is worse than no
    play button."""
    voices = client.get("/api/voices").json()
    assert voices
    assert all(v["preview"] for v in voices)


@needs_ffmpeg
def test_a_stub_voice_auditions_as_real_audio(client: TestClient) -> None:
    voices = client.get("/api/voices").json()
    response = client.get(f"/api/voices/{voices[0]['voice_id']}/preview")
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    # Not silence: a preview that plays nothing is indistinguishable from one
    # that is broken, which is the confusion this endpoint exists to remove.
    assert len(response.content) > 2_000


@needs_ffmpeg
def test_two_stub_voices_sound_different(client: TestClient) -> None:
    """Auditioning is pointless if every voice auditions identically."""
    voices = client.get("/api/voices").json()
    assert len(voices) >= 2
    first = client.get(f"/api/voices/{voices[0]['voice_id']}/preview").content
    second = client.get(f"/api/voices/{voices[1]['voice_id']}/preview").content
    assert first != second


@needs_ffmpeg
def test_a_preview_is_cached_rather_than_rebuilt(client: TestClient, settings: Settings) -> None:
    voice_id = client.get("/api/voices").json()[0]["voice_id"]
    client.get(f"/api/voices/{voice_id}/preview")
    cached = settings.assets_dir / "previews" / f"{voice_id}.mp3"
    assert cached.is_file()


@pytest.mark.parametrize(
    "voice_id", ["../../.env", "..%2F..%2Fsecret", "a/b", "with space", "", "x" * 200]
)
def test_no_voice_id_can_ever_yield_audio_from_outside_the_cache(
    client: TestClient, voice_id: str
) -> None:
    """Whatever the router does with the path, the response is never audio.

    Deliberately asserted on the content type rather than the status: an HTTP
    client normalises `../` away before the request is sent, so some of these
    are answered by the router or the SPA fallback and never reach the
    endpoint at all. The guarantee that matters is the same either way — none
    of them returns a file. `previews.preview_path` is where the rule itself is
    tested.
    """
    response = client.get(f"/api/voices/{voice_id}/preview")
    assert "audio" not in response.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Cast. The endpoint is small; the rule it enforces is not.
# ---------------------------------------------------------------------------


@pytest.fixture
def project(client: TestClient) -> int:
    return int(client.post("/api/projects", json={"name": "Cast", "voice_id": "v1"}).json()["id"])


def test_a_new_project_has_nobody_cast(client: TestClient, project: int) -> None:
    assert client.get(f"/api/projects/{project}/cast").json() == []


def test_casting_a_speaker_and_reading_it_back(client: TestClient, project: int) -> None:
    client.post(
        f"/api/projects/{project}/cast",
        json={"name": "Morag", "voice_id": "mock-voice-1"},
    )
    cast = client.get(f"/api/projects/{project}/cast").json()
    assert [(c["name"], c["voice_id"]) for c in cast] == [("Morag", "mock-voice-1")]


def test_recasting_replaces_rather_than_duplicates(client: TestClient, project: int) -> None:
    for voice in ("mock-voice-1", "mock-voice-2"):
        client.post(f"/api/projects/{project}/cast", json={"name": "Morag", "voice_id": voice})
    cast = client.get(f"/api/projects/{project}/cast").json()
    assert len(cast) == 1
    assert cast[0]["voice_id"] == "mock-voice-2"


def test_a_name_containing_a_colon_is_refused(client: TestClient, project: int) -> None:
    """The colon is what separates the speaker from the line, so a name holding
    one could never be matched."""
    response = client.post(
        f"/api/projects/{project}/cast", json={"name": "Bad: name", "voice_id": "v"}
    )
    assert response.status_code == 422


def test_uncasting_leaves_the_others(client: TestClient, project: int) -> None:
    client.post(f"/api/projects/{project}/cast", json={"name": "Morag", "voice_id": "v1"})
    keeper = client.post(
        f"/api/projects/{project}/cast", json={"name": "Keeper", "voice_id": "v2"}
    ).json()
    client.delete(f"/api/projects/{project}/cast/{keeper['id']}")
    assert [c["name"] for c in client.get(f"/api/projects/{project}/cast").json()] == ["Morag"]


def test_a_script_ingested_after_casting_reports_its_speakers(
    client: TestClient, project: int
) -> None:
    client.post(f"/api/projects/{project}/cast", json={"name": "Morag", "voice_id": "v-m"})
    client.post(f"/api/projects/{project}/cast", json={"name": "Keeper", "voice_id": "v-k"})

    created = client.post(
        "/api/scripts",
        json={
            "project_id": project,
            "title": "Two speakers",
            "text": "Morag: One line.\n\nKeeper: Another line.\n\nNote: not dialogue.\n",
        },
    ).json()

    assert created["speakers"] == ["Morag", "Keeper"]
    assert created["turns_assigned"] == 2
    assert created["dialogue"] is False
    # The uncast `Note:` is reported rather than silently swallowed.
    assert any("Note" in w for w in created["warnings"])


def test_a_script_ingested_before_casting_has_no_speakers(client: TestClient, project: int) -> None:
    """The load-bearing rule, over HTTP: with nobody cast, a prefix is prose."""
    created = client.post(
        "/api/scripts",
        json={
            "project_id": project,
            "title": "No cast",
            "text": "Morag: One line.\n\nKeeper: Another line.\n",
        },
    ).json()
    assert created.get("speakers") == []
    assert created["turns_assigned"] == 0


# ---------------------------------------------------------------------------
# Starter scripts, offered where somebody is about to write one
# ---------------------------------------------------------------------------


def test_the_templates_are_listed(client: TestClient) -> None:
    body = client.get("/api/templates").json()
    assert {t["slug"] for t in body} == {"single-voice", "multi-voice"}
    assert all(t["summary"] for t in body)


def test_a_template_is_served_as_markdown(client: TestClient) -> None:
    response = client.get("/api/templates/multi-voice")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "[CAST]" in response.text


def test_a_template_can_be_downloaded(client: TestClient) -> None:
    response = client.get("/api/templates/single-voice?download=1")
    assert 'filename="single-voice.md"' in response.headers["content-disposition"]


def test_an_unknown_template_names_the_valid_ones(client: TestClient) -> None:
    response = client.get("/api/templates/nope")
    assert response.status_code == 404
    assert "single-voice" in response.json()["detail"]


def test_a_template_uploads_and_generates_as_it_stands(client: TestClient) -> None:
    """The promise, end to end through the API: fetch a template, upload it
    untouched, and get chunks — no editing, no stripped comments to remember."""
    body = client.get("/api/templates/single-voice").text
    project = client.post("/api/projects", json={"name": "T", "voice_id": "v1"}).json()
    created = client.post(
        "/api/scripts",
        json={"project_id": project["id"], "title": "From template", "text": body},
    ).json()

    assert created["chunks"] > 0
    assert created["slots"] > 0

    chunks = client.get(f"/api/scripts/{created['id']}/chunks").json()
    everything = " ".join(c["text"] for c in chunks)
    for marker in ("<!--", "-->", "narrate script", "===="):
        assert marker not in everything


# ---------------------------------------------------------------------------
# Variants — two performances of one script, from the takes on record
# ---------------------------------------------------------------------------


def test_variants_are_empty_before_anything_is_generated(
    client: TestClient, script: dict[str, int]
) -> None:
    assert client.get(f"/api/scripts/{script['script_id']}/variants").json() == []


def test_a_generated_script_reports_the_voice_it_was_made_in(
    client: TestClient, script: dict[str, int]
) -> None:
    script_id = script["script_id"]
    started = client.post(f"/api/scripts/{script_id}/generate", json={"dry_run": False}).json()
    _drain(client, started["run_key"])

    variants = client.get(f"/api/scripts/{script_id}/variants").json()
    assert len(variants) == 1
    assert variants[0]["voice_id"] == "v1"
    assert variants[0]["complete"] is True
    assert variants[0]["chunks"] == variants[0]["chunks_total"]


def test_an_unknown_script_has_no_variants(client: TestClient) -> None:
    assert client.get("/api/scripts/9999/variants").status_code == 404


# ---------------------------------------------------------------------------
# Naming a voice, so a cloned one can be reused without its id
# ---------------------------------------------------------------------------


def test_no_voices_are_registered_to_begin_with(client: TestClient) -> None:
    assert client.get("/api/voices/registered").json() == []


def test_a_voice_can_be_named_and_listed(client: TestClient) -> None:
    created = client.post(
        "/api/voices/registered",
        json={"voice_id": "mock-voice-1", "name": "My Clone", "offline": True},
    )
    assert created.status_code == 200
    assert created.json()["slug"] == "my-clone"

    listed = client.get("/api/voices/registered").json()
    assert [v["slug"] for v in listed] == ["my-clone"]
    assert listed[0]["voice_id"] == "mock-voice-1"


def test_a_name_is_not_silently_repointed(client: TestClient) -> None:
    """The same guarantee the CLI gives: a name moving to a different voice
    changes what the next generation produces."""
    body = {"voice_id": "mock-voice-1", "name": "mine", "offline": True}
    assert client.post("/api/voices/registered", json=body).status_code == 200

    clash = client.post(
        "/api/voices/registered",
        json={"voice_id": "mock-voice-2", "name": "mine", "offline": True},
    )
    assert clash.status_code == 409
    assert "already points at" in clash.json()["detail"]


def test_replace_repoints_deliberately(client: TestClient) -> None:
    client.post(
        "/api/voices/registered",
        json={"voice_id": "mock-voice-1", "name": "mine", "offline": True},
    )
    moved = client.post(
        "/api/voices/registered",
        json={"voice_id": "mock-voice-2", "name": "mine", "offline": True, "replace": True},
    )
    assert moved.status_code == 200
    assert moved.json()["voice_id"] == "mock-voice-2"


def test_a_name_shaped_like_an_id_is_refused(client: TestClient) -> None:
    bad = client.post(
        "/api/voices/registered",
        json={"voice_id": "mock-voice-1", "name": "AbCdEfGhIjKlMnOpQrSt", "offline": True},
    )
    assert bad.status_code == 400
    assert "looks like a voice id" in bad.json()["detail"]


def test_a_name_can_be_forgotten(client: TestClient) -> None:
    client.post(
        "/api/voices/registered",
        json={"voice_id": "mock-voice-1", "name": "mine", "offline": True},
    )
    assert client.delete("/api/voices/registered/mine").status_code == 200
    assert client.get("/api/voices/registered").json() == []


def test_forgetting_an_unknown_name_is_a_404(client: TestClient) -> None:
    assert client.delete("/api/voices/registered/nope").status_code == 404


def test_the_registered_route_is_not_swallowed_by_the_preview_route(
    client: TestClient,
) -> None:
    """`/api/voices/registered` sits beside `/api/voices/{voice_id}/preview`, so
    route order decides whether it is reachable at all."""
    response = client.get("/api/voices/registered")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
