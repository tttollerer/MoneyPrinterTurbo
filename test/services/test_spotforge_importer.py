"""Offline migration boundary tests using generated media, never real projects."""
import hashlib
import io
import json
import subprocess
import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from spotforge.importer import create_router
from spotforge.store import Store


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory):
    target = tmp_path_factory.mktemp("import-media") / "video.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=0.2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(target)], check=True, capture_output=True)
    return target.read_bytes()


def png(color="red"):
    output = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(output, format="PNG")
    return output.getvalue()


def test_disguised_concat_cannot_read_media_outside_source(tiny_video):
    from spotforge.importer import _verify_media
    with tempfile.TemporaryDirectory(prefix="spotforge-outside-") as directory:
        outside = Path(directory) / "external.mp4"
        outside.write_bytes(tiny_video)
        # Both the external folder and the probe's temporary input live below
        # the system temp directory; concat autodetection would follow this path.
        playlist = f"ffconcat version 1.0\nfile '{Path(directory).name}/external.mp4'\n".encode()
        with pytest.raises(subprocess.CalledProcessError):
            _verify_media(playlist, "disguised.mp4", "video")


@pytest.fixture
def setup(tmp_path, tiny_video):
    source = tmp_path / "Original project with spaces"
    source.mkdir()
    (source / "start.png").write_bytes(png())
    (source / "end.png").write_bytes(png("green"))
    for name in ("first.mp4", "second.mp4", "old-export.mp4"):
        (source / name).write_bytes(tiny_video)
    data = {"schema_version": 3, "title": "Synthetic import", "recipe": "spot", "brand": "fixture",
            "format": {"w": 1080, "h": 1920, "fps": 30}, "spot": {"brief": {"product": "Fixture", "goal": "Demo", "api_key": "fixture-secret-do-not-copy"}},
            "segments": [{"id": "seg_1", "mode": "start_end", "prompt": "Camera movement", "duration_s": 5,
                          "video_model": "fal:minimax/h3/image-to-video", "vo_line": "Spoken first line",
                          "start_frame": {"file": "start.png", "prompt": "Image prompt", "takes": [{"file": "end.png", "model": "old-image-model"}]},
                          "end_frame": {"file": "end.png"}, "take_chosen": "first.mp4",
                          "takes": [{"file": "first.mp4", "model": "old-video-model", "prompt": "Original first prompt", "seed": 123,
                                     "params": {"guidance_scale": 2, "api_key": "fixture-secret-do-not-copy"}, "cost_eur": 1.2, "measured": True},
                                    {"file": "second.mp4", "model": "different-model", "prompt": "Original second prompt"}]}],
            "render": {"file": "old-export.mp4"}, "gates": {"final": {"status": "approved"}},
            "credentials": {"token": "fixture-secret-do-not-copy"}}
    (source / "brand.json").write_text(json.dumps({"name": "Fixture Brand", "font": "Arial", "colors": {"primary": "#123456", "bg": "#222222", "text": "#ffffff"}, "tone": "clear", "logo_file": "start.png", "api_key": "fixture-secret-do-not-copy"}))
    (source / "project.json").write_text(json.dumps(data))
    store = Store(tmp_path / "destination")
    app = FastAPI()
    app.include_router(create_router(store), prefix="/api")
    return source, data, store, TestClient(app)


def preview(client, source, **extra):
    response = client.post("/api/imports/preview", json={"source_dir": str(source), **extra})
    assert response.status_code == 200
    return response.json()


def commit(client, source, shown, **extra):
    return client.post("/api/imports/commit", json={"source_dir": str(source), "token": shown["token"], "confirmed": True, **extra})


def test_complete_import_preserves_takes_selection_assets_and_originals(setup):
    source, _, store, client = setup
    before = {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in source.iterdir()}
    shown = preview(client, source)
    assert shown["can_import"], shown
    assert shown["summary"]["takes"] == 2
    assert shown["summary"]["brand"] == "Fixture Brand"
    assert store.list("projects") == []  # Preview does not create assets or projects.
    assert store.list("assets") == []
    response = commit(client, source, shown)
    assert response.status_code == 201, response.text
    project = response.json()
    assert project["id"] != "seg_1"
    assert project["script"] == "Spoken first line"
    assert project["brief"] == "Produkt/Thema: Fixture\nZiel: Demo"
    scene = project["scenes"][0]
    assert scene["selected_take_id"] == scene["takes"][0]["id"]
    assert [take["prompt"] for take in scene["takes"]] == ["Original first prompt", "Original second prompt"]
    assert scene["takes"][0]["parameters"] == {"guidance_scale": 2, "seed": 123}
    assert project["brand_snapshot"]["colors"]["background"] == "#222222"
    assert project["render"] == {} and set(project["gates"].values()) == {"todo"}
    report = client.get(f"/api/imports/{project['id']}/report").json()
    assert len(report["frame_takes"]) == 1 and len(report["archived_exports"]) == 1
    assert store.asset_path(report["frame_takes"][0]["asset_id"]).read_bytes() == (source / "end.png").read_bytes()
    assert {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in source.iterdir()} == before
    persisted = json.dumps([store.list(collection) for collection in ("projects", "brands", "brand_versions", "imports", "assets")])
    assert "fixture-secret-do-not-copy" not in persisted
    assert "fixture-secret-do-not-copy" not in json.dumps(shown)


def test_repeated_import_creates_independent_copies(setup):
    source, _, store, client = setup
    shown = preview(client, source)
    first = commit(client, source, shown).json()
    second = commit(client, source, shown).json()
    assert first["id"] != second["id"]
    assert first["brand_snapshot"]["id"] != second["brand_snapshot"]["id"]
    assert first["scenes"][0]["takes"][0]["asset_id"] != second["scenes"][0]["takes"][0]["asset_id"]
    (source / "first.mp4").unlink()
    assert store.asset_path(first["scenes"][0]["takes"][0]["asset_id"]).is_file()


@pytest.mark.parametrize("reference", ["../outside.mp4", "https://example.invalid/video.mp4", ".env", "missing.mp4"])
def test_missing_unsafe_and_remote_media_block_without_writes(setup, reference):
    source, data, store, client = setup
    data["segments"][0]["takes"][0]["file"] = reference
    (source / "project.json").write_text(json.dumps(data))
    shown = preview(client, source)
    assert not shown["can_import"] and shown["errors"]
    assert store.list("projects") == [] and store.list("assets") == []


@pytest.mark.parametrize("directory", [False, True])
def test_symlink_files_and_directories_are_never_followed(setup, directory):
    source, data, _, client = setup
    if directory:
        (source / "linked").symlink_to(source, target_is_directory=True)
        reference = "linked/first.mp4"
    else:
        (source / "linked.mp4").symlink_to(source / "first.mp4")
        reference = "linked.mp4"
    data["segments"][0]["takes"][0]["file"] = reference
    (source / "project.json").write_text(json.dumps(data))
    shown = preview(client, source)
    assert not shown["can_import"]
    assert "symbolische" in shown["errors"][0]


@pytest.mark.parametrize("target", ["project.json", "start.png"])
def test_changed_source_requires_fresh_confirmation(setup, target):
    source, data, store, client = setup
    shown = preview(client, source)
    if target.endswith("json"):
        data["title"] = "Changed after preview"
        (source / target).write_text(json.dumps(data))
    else:
        (source / target).write_bytes(png("yellow"))
    assert commit(client, source, shown).status_code == 409
    assert store.list("projects") == [] and store.list("assets") == []


def test_confirmation_required_and_bad_media_rolls_back_only_new_assets(setup):
    source, _, store, client = setup
    old = store.add_asset(b"untouched", "existing.txt", "document", "text/plain")
    (source / "second.mp4").write_bytes(b"not video")
    shown = preview(client, source)
    assert client.post("/api/imports/commit", json={"source_dir": str(source), "token": shown["token"], "confirmed": False}).status_code == 409
    result = commit(client, source, shown)
    assert result.status_code == 422
    assert store.list("projects") == []
    assert [asset["id"] for asset in store.list("assets")] == [old.id]
    assert store.asset_path(old.id).read_bytes() == b"untouched"


def test_nested_project_and_explicit_brand_stay_within_selected_root(setup):
    source, data, _, client = setup
    nested = source / "projects" / "one"
    nested.mkdir(parents=True)
    data["segments"] = []
    data["render"] = {}
    (nested / "project.json").write_text(json.dumps(data))
    shown = preview(client, source, project_file="projects/one/project.json", brand_file="brand.json")
    assert shown["can_import"], shown
    imported = commit(client, source, shown, project_file="projects/one/project.json", brand_file="brand.json")
    assert imported.status_code == 201, imported.text


def test_unusable_brand_font_is_explicitly_reported(setup):
    source, _, _, client = setup
    (source / "brand.json").write_text(json.dumps({"name": "Fixture", "font": "Uninstalled Custom Font"}))
    shown = preview(client, source)
    assert shown["can_import"] and shown["summary"]["brand"] is None
    assert any("Font-Datei" in warning for warning in shown["warnings"])


def test_spot_shots_migrate_and_missing_selected_take_blocks(setup):
    source, _, _, client = setup
    data = {"schema_version": 1, "brief": {"product": "Fixture"}, "shots": [{"still_chosen": "start.png", "clip": {"file": "first.mp4", "model": "legacy-model"}, "vo_line": "Legacy spoken text"}]}
    (source / "spot.json").write_text(json.dumps(data))
    shown = preview(client, source, project_file="spot.json")
    assert shown["can_import"], shown
    project = commit(client, source, shown, project_file="spot.json").json()
    assert project["script"] == "Legacy spoken text"
    assert len(project["scenes"][0]["takes"]) == 1
    original = json.loads((source / "project.json").read_text())
    original["segments"][0]["take_chosen"] = "unknown.mp4"
    (source / "project.json").write_text(json.dumps(original))
    assert not preview(client, source)["can_import"]


def test_invalid_timing_diagnostics_do_not_echo_unknown_values(setup):
    source, data, store, client = setup
    data["audio"] = {"word_timings": "words.json"}
    (source / "project.json").write_text(json.dumps(data))
    (source / "words.json").write_text(json.dumps([{"w": "Word", "start": "fixture-secret-do-not-copy", "end": 1}]))
    shown = preview(client, source)
    assert not shown["can_import"]
    assert "fixture-secret-do-not-copy" not in json.dumps(shown)
    assert store.list("assets") == []


def test_file_limits_block_import_before_copying(setup, monkeypatch):
    from spotforge import importer
    source, _, store, client = setup
    monkeypatch.setitem(importer.LIMITS, "file_bytes", 10)
    shown = preview(client, source)
    assert not shown["can_import"]
    assert any("Größenlimit" in error for error in shown["errors"])
    assert store.list("assets") == []
