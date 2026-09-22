"""Isolated workflow contracts; no real stock API or paid provider calls."""
import io
import os
import shutil
import subprocess
import wave

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from spotforge import workflow
from spotforge.models import Job, Project, Scene, Take
from spotforge.store import Store


def wave_bytes(seconds=1):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        audio.writeframes(b"\0\0" * round(48000 * seconds))
    return output.getvalue()


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(workflow, "capabilities", lambda: {"speech": {"available": True, "voices": [{"id": "Anna"}]}})
    store = Store(tmp_path)
    project = Project(script="Erster Satz. Zweiter Satz.", scenes=[Scene(duration_s=1)])
    store.write("projects", project.id, project)
    return store, project


def fake_speech(script, voice, rate, progress):
    progress(0.9)
    return wave_bytes(2), [{"text": script, "start_ms": 0, "end_ms": 2000, "words": []}], 2


def selection():
    return {"selection_id": "selection1", "provider": "pexels", "title": "Natur", "provider_asset_id": "42",
            "preview_url": "https://images.pexels.com/example.jpg", "source_url": "https://www.pexels.com/video/42/",
            "download_url": "https://videos.pexels.com/video-files/42.mp4", "duration_s": 1, "width": 160, "height": 90,
            "query": "Natur", "created_at": "2026-09-22"}


class FakeStock:
    def __init__(self, content=b""):
        self.content = content
        self.searches = 0
        self.downloads = 0

    def search(self, *args):
        self.searches += 1
        return [selection()]

    def download(self, chosen):
        self.downloads += 1
        return self.content


@pytest.fixture
def video(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg required for real local video fixture")
    target = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x90:d=1:r=10",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(target)], check=True)
    return target.read_bytes()


def test_search_requires_explicit_action_and_keeps_download_url_server_side(fixture):
    store, _ = fixture
    provider = FakeStock()
    service = workflow.WorkflowService(store, provider)
    with pytest.raises(workflow.WorkflowError, match="externe Suche"):
        service.search("pexels", "Natur", "landscape", False)
    assert provider.searches == 0
    results = service.search("pexels", "Natur", "landscape", True)
    assert "download_url" not in results[0]
    assert results[0]["source_url"].startswith("https://www.pexels.com")
    assert store.read("stock_selections", "selection1")["download_url"]


def test_no_secret_discovery_when_provider_env_missing(monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.setattr(workflow.requests, "Session", lambda: pytest.fail("must not send request without explicit key"))
    with pytest.raises(workflow.WorkflowError, match="PEXELS_API_KEY"):
        workflow.StockProvider().search("pexels", "Natur", "landscape")


def test_candidate_parser_filters_orientation_and_untrusted_download_hosts():
    videos = [{"id": 2, "duration": 8, "url": "https://www.pexels.com/video/2/?secret=drop",
               "image": "https://images.pexels.com/2.jpg?auto=compress", "video_files": [
                   {"width": 1080, "height": 1920, "link": "https://videos.pexels.com/2.mp4"},
                   {"width": 1920, "height": 1080, "link": "http://127.0.0.1/private"}]}]
    assert workflow.StockProvider.parse("pexels", {"videos": videos}, "rain", "landscape") == []
    found = workflow.StockProvider.parse("pexels", {"videos": videos}, "rain", "portrait")
    assert len(found) == 1
    assert found[0]["source_url"] == "https://www.pexels.com/video/2/"
    assert "?" not in found[0]["preview_url"]
    for url in ["https://evil.example/x", "https://pexels.com.evil.example/x", "https://videos.pexels.com/x?key=secret"]:
        with pytest.raises(workflow.WorkflowError):
            workflow._media_url(url, "pexels")


def test_speech_checks_saved_script_revision_gates_and_voice(fixture):
    store, project = fixture
    service = workflow.WorkflowService(store, speech_engine=fake_speech)
    with pytest.raises(workflow.WorkflowError, match="bestätigen"):
        service.start(project.id, "speech", 1, False, voice="Anna", rate=145)
    with pytest.raises(workflow.WorkflowError, match="geändert"):
        service.start(project.id, "speech", 2, True, voice="Anna", rate=145)
    with pytest.raises(workflow.WorkflowError, match="installiert"):
        service.start(project.id, "speech", 1, True, voice="unknown", rate=145)
    project.recipe = "spot"
    store.write("projects", project.id, project)
    with pytest.raises(workflow.WorkflowError, match="freigeben"):
        service.start(project.id, "speech", 1, True, voice="Anna", rate=145)
    assert not store.list("jobs")


def test_speech_apply_preserves_script_gate_and_warns_about_short_timeline(fixture):
    store, project = fixture
    project.recipe = "spot"
    project.gates = dict.fromkeys(["concept", "script", "storyboard", "clips", "final"], "approved")
    store.write("projects", project.id, project)
    service = workflow.WorkflowService(store, speech_engine=fake_speech)
    job = service.start(project.id, "speech", 1, True, voice="Anna", rate=145)
    assert store.read("jobs", job.id)["input_snapshot"]["script"] == project.script
    service.run(job.id)
    result = store.read("jobs", job.id)
    updated = store.read("projects", project.id)
    assert result["state"] == "complete" and result["result"]["applied"]
    assert "länger" in result["result"]["warning"]
    assert updated["audio"]["narration_asset_id"] == result["result"]["asset_id"]
    assert updated["captions"][0]["end_ms"] == 2000
    assert updated["scenes"][0]["duration_s"] == 1  # No implicit timeline edit.
    assert updated["gates"]["script"] == "approved"
    assert updated["gates"]["storyboard"] == "todo"


def test_edit_while_speech_runs_retains_results_without_overwriting_project(fixture):
    store, project = fixture
    def change(*args):
        project.script = "Neuer Text."
        project.revision += 1
        store.write("projects", project.id, project)
        return fake_speech(*args)
    service = workflow.WorkflowService(store, speech_engine=change)
    job = service.start(project.id, "speech", 1, True, voice="Anna", rate=145)
    service.run(job.id)
    result = store.read("jobs", job.id)
    assert result["state"] == "complete"
    assert not result["result"]["applied"]
    assert result["result"]["captions"]
    assert store.asset_path(result["result"]["asset_id"]).exists()
    assert store.read("projects", project.id)["audio"]["narration_asset_id"] is None


def test_restart_marks_unfinished_local_jobs_and_never_runs_provider(fixture):
    store, project = fixture
    job = Job(project_id=project.id, kind="stock_import", state="running")
    store.write("jobs", job.id, job)
    provider = FakeStock()
    service = workflow.WorkflowService(store, provider)
    service.recover()
    service.run(job.id)
    assert store.read("jobs", job.id)["state"] == "interrupted"
    assert provider.downloads == provider.searches == 0


def test_stock_import_real_video_persists_attribution_and_preserves_take_history(fixture, video):
    store, project = fixture
    old = store.add_asset(video, "old.mp4", "video", "video/mp4")
    take = Take(asset_id=old.id)
    original = project.scenes[0]
    original.takes = [take]
    original.selected_take_id = take.id
    descendant = Scene(predecessor_scene_id=original.id)
    project.scenes.append(descendant)
    store.write("projects", project.id, project)
    provider = FakeStock(video)
    service = workflow.WorkflowService(store, provider)
    service.search("pexels", "Natur", "landscape", True)
    job = service.start(project.id, "stock_import", 1, True, selection_id="selection1", scene_id=original.id)
    service.run(job.id)
    result = store.read("jobs", job.id)
    assert result["state"] == "complete" and result["result"]["applied"]
    updated = store.read("projects", project.id)
    assert updated["scenes"][0]["takes"][0]["id"] == take.id
    assert updated["scenes"][0]["selected_take_id"] is None
    assert updated["scenes"][1]["stale"]
    assert result["result"]["duration_s"] == 1
    assert store.read("asset_provenance", result["result"]["asset_id"])["provider_asset_id"] == "42"
    assert provider.downloads == 1


def test_stock_import_creates_new_scene_without_changing_script(fixture, video):
    store, project = fixture
    service = workflow.WorkflowService(store, FakeStock(video))
    service.search("pexels", "Natur", "landscape", True)
    job = service.start(project.id, "stock_import", 1, True, selection_id="selection1", scene_id=None)
    service.run(job.id)
    updated = store.read("projects", project.id)
    assert len(updated["scenes"]) == 2
    assert updated["script"] == project.script
    assert updated["gates"] == {}


def test_failed_download_error_never_exposes_credentials(fixture):
    store, project = fixture
    class BadStock(FakeStock):
        def download(self, selection):
            raise RuntimeError("https://provider.example/?key=DO_NOT_EXPOSE")
    service = workflow.WorkflowService(store, BadStock())
    service.search("pexels", "Natur", "landscape", True)
    job = service.start(project.id, "stock_import", 1, True, selection_id="selection1", scene_id=None)
    service.run(job.id)
    result = store.read("jobs", job.id)
    assert result["state"] == "failed"
    assert "DO_NOT_EXPOSE" not in result["error"]


def test_bounded_response_stops_before_collecting_unbounded_content():
    class TooLarge:
        headers = {"Content-Length": "9000000"}
        def iter_content(self, chunk_size):
            pytest.fail("oversize content must not be read")
    with pytest.raises(workflow.WorkflowError, match="Größenlimit"):
        workflow._read_response(TooLarge(), 1024, 10)


def test_workflow_route_rejects_unconfirmed_search_and_invalid_rate(fixture):
    store, project = fixture
    app = FastAPI()
    app.include_router(workflow.create_router(store), prefix="/api")
    with TestClient(app) as client:
        response = client.post("/api/stock/search", json={"provider": "pexels", "query": "rain", "confirmed": False})
        assert response.status_code == 409
        response = client.post(f"/api/projects/{project.id}/speech", json={"confirmed": True, "expected_revision": 1, "rate": 1000})
        assert response.status_code == 422


def test_local_macos_speech_has_sample_accurate_sentence_cues():
    if os.environ.get("SPOTFORGE_TEST_LOCAL_SPEECH") != "1":
        pytest.skip("Native speech acceptance is opt-in outside restricted speech-service sandboxes")
    if not shutil.which("say") or not shutil.which("ffmpeg"):
        pytest.skip("Local macOS speech requires say and FFmpeg")
    installed = workflow.voices()
    voice = next((v["id"] for v in installed if v["id"] == "Anna"), None)
    if not voice:
        pytest.skip("Anna voice is not installed")
    content, cues, duration = workflow.synthesize("Hallo. Guten Tag.", voice, 180)
    with wave.open(io.BytesIO(content), "rb") as audio:
        assert duration == audio.getnframes() / audio.getframerate()
    assert len(cues) == 2
    assert cues[0]["start_ms"] == 0
    assert cues[0]["end_ms"] == cues[1]["start_ms"]
    assert abs(cues[-1]["end_ms"] - duration * 1000) <= 0.5
    assert all(c["words"] == [] for c in cues)
