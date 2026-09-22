"""Network-free lifecycle tests using real image files and a small local video."""
import io
import shutil
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from spotforge.generation import (
    MODEL, FalProvider, GenerationError, GenerationService, Rejected, create_router,
)
from spotforge.models import Brand, Format, Job, Project, Scene, Take
from spotforge.store import Store


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-not-a-real-key")
    store = Store(tmp_path)
    data = io.BytesIO()
    Image.new("RGB", (360, 640), "red").save(data, format="PNG")
    image = store.add_asset(data.getvalue(), "start.png", "image", "image/png")
    data = io.BytesIO()
    Image.new("RGB", (360, 640), "blue").save(data, format="PNG")
    end = store.add_asset(data.getvalue(), "end.png", "image", "image/png")
    scene = Scene(mode="start_end", prompt="A slow pan", start_asset_id=image.id, end_asset_id=end.id)
    project = Project(scenes=[scene], brand_snapshot=Brand(name="Demo", visual_style="Clean daylight"))
    store.write("projects", project.id, project)
    return store, project, image, end


@pytest.fixture
def video(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg tools required for local media test")
    path = tmp_path / "fixture.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=360x640:r=10:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-y", str(path)], check=True)
    return path.read_bytes()


class FakeProvider:
    def __init__(self, content=b""):
        self.content = content
        self.submissions = []
        self.poll_error = None
        self.submit_error = None
        self.after_submit = None

    def submit(self, payload):
        self.submissions.append(payload)
        if self.after_submit:
            self.after_submit()
        if self.submit_error:
            raise self.submit_error
        return "request-test"

    def poll(self, request_id):
        assert request_id == "request-test"
        if self.poll_error:
            raise self.poll_error
        return "COMPLETED"

    def result(self, request_id):
        return "https://v3.fal.media/test.mp4"

    def download(self, url):
        return self.content


def service(store, provider):
    return GenerationService(store, provider_factory=lambda _: provider, poll_seconds=0)


def test_rejects_unconfirmed_stale_gates_and_unsupported_before_jobs(fixture):
    store, project, _, _ = fixture
    svc = service(store, FakeProvider())
    for confirmed, revision in [(False, 1), (True, 2)]:
        with pytest.raises(GenerationError):
            svc.start(project.id, project.scenes[0].id, confirmed, revision)
    project.recipe = "spot"
    store.write("projects", project.id, project)
    with pytest.raises(GenerationError, match="freigegeben"):
        svc.start(project.id, project.scenes[0].id, True, 1)
    project.gates = dict.fromkeys(["concept", "script", "storyboard"], "approved")
    project.scenes[0].model = "unsupported"
    store.write("projects", project.id, project)
    with pytest.raises(GenerationError, match="Modell"):
        svc.start(project.id, project.scenes[0].id, True, 1)
    assert store.list("jobs") == []


def test_end_only_explains_assistance_and_does_not_submit(fixture):
    store, project, _, _ = fixture
    project.scenes[0].mode = "end"
    project.scenes[0].start_asset_id = None
    store.write("projects", project.id, project)
    with pytest.raises(GenerationError, match="Nur-Endbild-Assistent"):
        service(store, FakeProvider()).start(project.id, project.scenes[0].id, True, 1)
    assert not store.list("jobs")


def test_reference_mismatch_or_corruption_block_before_paid_request(fixture):
    store, project, image, _ = fixture
    project.format = Format(width=1920, height=1080)
    store.write("projects", project.id, project)
    with pytest.raises(GenerationError, match="Bildformat"):
        service(store, FakeProvider()).start(project.id, project.scenes[0].id, True, 1)
    project.format = Format()
    store.write("projects", project.id, project)
    store.asset_path(image.id).write_bytes(b"corrupt")
    with pytest.raises(GenerationError, match="verändert"):
        service(store, FakeProvider()).start(project.id, project.scenes[0].id, True, 1)


def test_inputs_persisted_before_submit_and_take_snapshots(fixture, video):
    store, project, image, end = fixture
    project.brand_snapshot.rules = ["Keep packaging readable"]
    project.brand_snapshot.forbidden_claims = ["Guaranteed results"]
    store.write("projects", project.id, project)
    provider = FakeProvider(video)
    svc = service(store, provider)
    job = svc.start(project.id, project.scenes[0].id, True, 1)
    provider.after_submit = lambda: (assert_submitting(store, job.id))
    with pytest.raises(GenerationError, match="läuft"):
        svc.start(project.id, project.scenes[0].id, True, 1)
    svc.run(job.id)
    payload = provider.submissions[0]
    assert payload["image_url"].startswith("data:image/png;base64,")
    assert payload["tail_image_url"] != payload["image_url"]
    assert payload["duration"] == "5"
    assert "Clean daylight" in payload["prompt"]
    assert "Keep packaging readable" in payload["prompt"]
    assert "Do not depict or claim:\n- Guaranteed results" in payload["prompt"]
    final = store.read("jobs", job.id)
    assert final["state"] == "complete"
    take = store.read("projects", project.id)["scenes"][0]["takes"][0]
    assert take["start_asset_id"] == image.id and take["end_asset_id"] == end.id
    assert take["parameters"]["input_assets"][image.id] == image.sha256
    assert take["brand_snapshot"]["version"] == 1
    assert take["cost"]["amount"] is None
    assert take["provider_request_id"] == "request-test"
    assert take["parameters"]["actual_duration_s"] == 1
    assert take["parameters"]["width"] == 360
    assert store.read("projects", project.id)["gates"] == {}


def assert_submitting(store, job_id):
    raw = store.read("jobs", job_id)
    assert raw["state"] == "submitting"
    assert raw["input_snapshot"]["scene"]["model"] == MODEL


def test_unknown_submit_is_not_retried_and_cannot_resume(fixture):
    store, project, _, _ = fixture
    provider = FakeProvider()
    provider.submit_error = TimeoutError("secret provider error is never logged")
    svc = service(store, provider)
    job = svc.start(project.id, project.scenes[0].id, True, 1)
    svc.run(job.id)
    result = store.read("jobs", job.id)
    assert result["state"] == "unknown"
    assert "secret" not in result["error"]
    assert len(provider.submissions) == 1
    with pytest.raises(GenerationError, match="Keine Anbieter-ID"):
        svc.resume(job.id, True)


def test_poll_failure_and_restart_resume_without_duplicate_charge(fixture, video):
    store, project, _, _ = fixture
    provider = FakeProvider(video)
    provider.poll_error = TimeoutError()
    svc = service(store, provider)
    job = svc.start(project.id, project.scenes[0].id, True, 1)
    svc.run(job.id)
    assert store.read("jobs", job.id)["state"] == "interrupted"
    restarted = service(store, provider)
    restarted.recover()
    provider.poll_error = None
    restarted.resume(job.id, True)
    restarted.run(job.id, resume=True)
    assert store.read("jobs", job.id)["state"] == "complete"
    assert len(provider.submissions) == 1
    restarted.run(job.id, resume=True)
    assert len(store.read("projects", project.id)["scenes"][0]["takes"]) == 1


def test_restart_distinguishes_never_submitted_from_ambiguous(fixture):
    store, project, _, _ = fixture
    for state in ["queued", "submitting"]:
        job = Job(project_id=project.id, kind="generation", state=state)
        store.write("jobs", job.id, job)
    service(store, FakeProvider()).recover()
    assert {j["state"] for j in store.list("jobs")} == {"failed", "unknown"}


def test_edit_between_confirmation_and_submit_aborts_without_cost(fixture):
    store, project, _, _ = fixture
    provider = FakeProvider()
    svc = service(store, provider)
    job = svc.start(project.id, project.scenes[0].id, True, 1)
    project.revision += 1
    store.write("projects", project.id, project)
    svc.run(job.id)
    assert store.read("jobs", job.id)["state"] == "failed"
    assert not provider.submissions


def test_edit_during_generation_keeps_take_but_marks_stale(fixture, video):
    store, project, _, _ = fixture
    provider = FakeProvider(video)
    svc = service(store, provider)
    job = svc.start(project.id, project.scenes[0].id, True, 1)
    def change_scene():
        project.scenes[0].prompt = "New prompt"
        project.revision += 1
        store.write("projects", project.id, project)
    provider.after_submit = change_scene
    svc.run(job.id)
    scene = store.read("projects", project.id)["scenes"][0]
    assert len(scene["takes"]) == 1 and scene["stale"]
    assert scene["selected_take_id"] is None
    assert "A slow pan" in scene["takes"][0]["prompt"]


def test_real_predecessor_last_frame_pinned_and_old_take_preserved(fixture, video):
    store, project, _, _ = fixture
    asset = store.add_asset(video, "previous.mp4", "video", "video/mp4")
    take = Take(asset_id=asset.id)
    previous = Scene(takes=[take], selected_take_id=take.id)
    target = project.scenes[0]
    target.predecessor_scene_id = previous.id
    target.start_asset_id = None
    project.scenes.insert(0, previous)
    store.write("projects", project.id, project)
    provider = FakeProvider(video)
    svc = service(store, provider)
    job = svc.start(project.id, target.id, True, 1)
    svc.run(job.id)
    assert store.read("jobs", job.id)["state"] == "complete"
    updated = store.read("projects", project.id)
    new_take = updated["scenes"][1]["takes"][0]
    assert new_take["predecessor_take_id"] == take.id
    assert new_take["start_asset_id"] is not None
    assert store.asset_path(new_take["start_asset_id"]).exists()
    assert updated["scenes"][0]["selected_take_id"] == take.id


def test_truncated_predecessor_rejected_before_paid_submission(fixture, video):
    store, project, _, _ = fixture
    asset = store.add_asset(video, "previous.mp4", "video", "video/mp4")
    take = Take(asset_id=asset.id, parameters={"actual_duration_s":5})
    previous = Scene(duration_s=4.97, takes=[take], selected_take_id=take.id)
    target = project.scenes[0]
    target.predecessor_scene_id, target.start_asset_id = previous.id, None
    project.scenes.insert(0, previous)
    store.write("projects", project.id, project)
    provider = FakeProvider(video)
    with pytest.raises(GenerationError, match="gekürzt"):
        service(store, provider).start(project.id, target.id, True, 1)
    assert store.list("jobs") == []
    assert provider.submissions == []


def test_router_returns_friendly_validation_and_capabilities(fixture, monkeypatch):
    store, project, _, _ = fixture
    app = FastAPI()
    app.include_router(create_router(store), prefix="/api")
    with TestClient(app) as client:
        models = client.get("/api/models").json()
        assert models[0]["modes"] == ["start", "start_end"]
        monkeypatch.delenv("FAL_KEY")
        response = client.post(f"/api/projects/{project.id}/scenes/{project.scenes[0].id}/generate",
                               json={"confirmed": True, "expected_revision": 1})
        assert response.status_code == 409
        assert "FAL_KEY" in response.json()["detail"]
        assert not store.list("jobs")


def test_provider_rejects_media_redirects_and_private_hosts(monkeypatch):
    provider = FalProvider("not-a-secret")
    for url in ["http://127.0.0.1/video", "https://evil.example/video", "https://fal.media.evil.example/video"]:
        with pytest.raises(ValueError, match="Unexpected provider"):
            provider.download(url)


def test_explicit_rejection_is_failed_not_unknown(fixture):
    store, project, _, _ = fixture
    provider = FakeProvider()
    provider.submit_error = Rejected("Provider rejected")
    svc = service(store, provider)
    job = svc.start(project.id, project.scenes[0].id, True, 1)
    svc.run(job.id)
    assert store.read("jobs", job.id)["state"] == "failed"


def test_unknown_submission_can_reconcile_explicit_id_without_resubmitting(fixture, video):
    store, project, _, _ = fixture
    provider = FakeProvider(video)
    provider.submit_error = TimeoutError()
    svc = service(store, provider)
    job = svc.start(project.id, project.scenes[0].id, True, 1)
    svc.run(job.id)
    with pytest.raises(GenerationError, match="Fortsetzung"):
        svc.resume(job.id, False, "request-test")
    with pytest.raises(GenerationError, match="Ungültige Anbieter-ID"):
        svc.resume(job.id, True, "https://private.example/id")
    svc.resume(job.id, True, "request-test")
    with pytest.raises(GenerationError, match="ungeklärten"):
        svc.resume(job.id, True, "different-request")
    svc.run(job.id, resume=True)
    result = store.read("jobs", job.id)
    assert result["state"] == "complete"
    assert result["input_snapshot"]["reconciliation"]["source"] == "explicit_user"
    assert len(provider.submissions) == 1


def test_spot_generation_invalidates_only_downstream_gates(fixture, video):
    store, project, _, _ = fixture
    project.recipe = "spot"
    project.gates = dict.fromkeys(["concept", "script", "storyboard", "clips", "final"], "approved")
    store.write("projects", project.id, project)
    svc = service(store, FakeProvider(video))
    job = svc.start(project.id, project.scenes[0].id, True, 1)
    svc.run(job.id)
    gates = store.read("projects", project.id)["gates"]
    assert gates == {"concept": "approved", "script": "approved", "storyboard": "approved", "clips": "todo", "final": "todo"}
