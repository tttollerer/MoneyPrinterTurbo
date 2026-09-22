"""Batch persistence and concurrency tests; fake generator never uses a network."""
import copy

import pytest
from fastapi import HTTPException

from spotforge.batches import BatchService, public
from spotforge.generation import GenerationError
from spotforge.models import Job, Project, Scene, Take
from spotforge.store import Store


class FakeGeneration:
    def __init__(self, store):
        self.store = store
        self.starts = []
        self.resumes = []
        self.outcomes = []
        self.on_launch = None

    def preflight(self, pid, sid, expected_revision=None, check_active=False):
        project = Project.model_validate(self.store.read("projects", pid))
        scene = next(scene for scene in project.scenes if scene.id == sid)
        if project.recipe == "spot" and any(project.gates.get(gate) != "approved" for gate in ("concept", "script", "storyboard")):
            raise GenerationError("Freigaben fehlen.", 409)
        if scene.mode == "local":
            raise GenerationError("Lokale Szene ohne Medien.")
        assert expected_revision == project.revision
        return {"model": scene.model, "cost": {"amount": None}}

    def start(self, pid, sid, confirmed, expected_revision, *, batch_id=None):
        assert confirmed
        self.preflight(pid, sid, expected_revision)
        if getattr(self.store, "batch_owner", None) != batch_id:
            raise GenerationError("Anderer Batch besitzt die Generierung.", 409)
        self.starts.append((pid, sid, expected_revision))
        job = Job(project_id=pid, scene_id=sid, kind="generation", input_snapshot={"batch_id": batch_id})
        self.store.write("jobs", job.id, job)
        return job

    def resume(self, jid, confirmed, *, batch_id=None):
        assert confirmed
        self.resumes.append(jid)
        job = Job.model_validate(self.store.read("jobs", jid))
        assert job.provider_request_id
        return job

    def launch(self, jid, resume=False):
        job = Job.model_validate(self.store.read("jobs", jid))
        outcome = self.outcomes.pop(0) if self.outcomes else "complete"
        if self.on_launch:
            self.on_launch(job)
        if outcome == "not_submitted":
            job.state, job.result = "failed", {"not_submitted": True}
        elif outcome != "complete":
            job.state = outcome
            job.provider_request_id = "known-provider-id" if outcome != "unknown" else None
            job.error = "Needs explicit review"
        else:
            project = Project.model_validate(self.store.read("projects", job.project_id))
            scene = next(scene for scene in project.scenes if scene.id == job.scene_id)
            asset = self.store.add_asset(b"test generated video", "take.mp4", "video", "video/mp4")
            take = Take(asset_id=asset.id, provider_request_id="known-provider-id")
            scene.takes.append(take)
            if not scene.selected_take_id:
                scene.selected_take_id, scene.stale = take.id, False
            if project.recipe == "spot":
                project.gates.update(clips="todo", final="todo")
            project.revision += 1
            project.render = {}
            self.store.write("projects", project.id, project)
            job.state = "complete"
            job.result = {"take_id": take.id, "asset_id": asset.id}
        self.store.write("jobs", jid, job)


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path)
    generator = FakeGeneration(store)
    service = BatchService(store, generator, poll_seconds=0)
    project = Project(scenes=[Scene(mode="start", title="One"), Scene(mode="start", title="Two")])
    store.write("projects", project.id, project)
    return store, generator, service, project


def begin(service, project):
    batch = service.preview([project.id])
    service.confirm(batch["id"], True, batch["input_digest"])
    return batch


def test_ambiguous_recovered_jobs_cannot_be_submitted_again(setup):
    store, generator, service, project = setup
    batch = service.preview([project.id])
    batch["state"] = "running"
    store.write("batches", batch["id"], batch)
    for _ in range(2):
        job = Job(project_id=project.id, scene_id=project.scenes[0].id, kind="generation", state="failed",
                  provider_request_id="known", input_snapshot={"batch_id": batch["id"]})
        store.write("jobs", job.id, job)
    recovered = BatchService(store, generator)
    current = store.read("batches", batch["id"])
    assert current["items"][0]["status"] == "unknown"
    with pytest.raises(HTTPException, match="Mehrdeutige"):
        recovered.confirm(batch["id"], True, batch["input_digest"], resume=True)
    assert generator.starts == []


def test_preview_is_durable_free_and_skips_current_selected_takes(setup):
    store, generator, service, project = setup
    asset = store.add_asset(b"local", "done.mp4", "video", "video/mp4")
    take = Take(asset_id=asset.id)
    project.scenes[0].takes = [take]
    project.scenes[0].selected_take_id = take.id
    store.write("projects", project.id, project)
    batch = service.preview([project.id])
    assert batch["state"] == "preview" and batch["skipped"] == 1
    assert batch["cost"]["amount"] is None
    assert store.read("batches", batch["id"])["input_digest"] == batch["input_digest"]
    assert store.list("jobs") == [] and generator.starts == []
    assert "inputs" not in public(batch)


def test_unapproved_gates_and_dynamic_predecessors_block_entire_batch(setup):
    store, _, service, project = setup
    project.recipe = "spot"
    project.scenes[1].predecessor_scene_id = project.scenes[0].id
    store.write("projects", project.id, project)
    batch = service.preview([project.id])
    assert batch["blocked"] == 2
    assert "Vorgänger" in batch["items"][1]["reason"]
    with pytest.raises(HTTPException) as error:
        service.confirm(batch["id"], True, batch["input_digest"])
    assert error.value.status_code == 409


@pytest.mark.parametrize("change", ["confirmation", "digest", "project", "asset"])
def test_start_requires_exact_confirmed_inputs(setup, change):
    store, _, service, project = setup
    asset = store.add_asset(b"input", "start.png", "image", "image/png")
    project.scenes[0].start_asset_id = asset.id
    store.write("projects", project.id, project)
    batch = service.preview([project.id])
    if change == "project":
        project.title = "Changed"
        project.revision += 1
        store.write("projects", project.id, project)
    elif change == "asset":
        store.asset_path(asset.id).write_bytes(b"changed")
    with pytest.raises((HTTPException, ValueError)):
        service.confirm(batch["id"], change != "confirmation", "wrong" if change == "digest" else batch["input_digest"])
    assert store.list("jobs") == []


def test_sequential_jobs_allow_only_their_own_revision_advances(setup):
    store, generator, service, project = setup
    original = project.model_dump(mode="json")
    batch = begin(service, project)
    service.run(batch["id"])
    result = store.read("batches", batch["id"])
    assert result["state"] == "complete"
    assert [call[2] for call in generator.starts] == [1, 2]
    assert all(item["status"] == "complete" for item in result["items"])
    assert result["inputs"]["projects"][project.id] == original  # Preview stays immutable.
    assert result["expected_projects"][project.id]["revision"] == 3
    assert store.batch_owner is None


def test_external_edit_during_job_pauses_before_another_paid_submission(setup):
    store, generator, service, project = setup
    def edit(_):
        changed = store.read("projects", project.id)
        changed["script"] = "External edit"
        changed["revision"] += 1
        store.write("projects", project.id, changed)
    generator.on_launch = edit
    batch = begin(service, project)
    service.run(batch["id"])
    assert len(generator.starts) == 1
    assert store.read("batches", batch["id"])["state"] == "paused"
    assert store.read("projects", project.id)["scenes"][0]["takes"]


@pytest.mark.parametrize("outcome", ["failed", "unknown"])
def test_failed_or_unknown_paid_job_is_never_blindly_resubmitted(setup, outcome):
    store, generator, service, project = setup
    generator.outcomes = [outcome]
    batch = begin(service, project)
    service.run(batch["id"])
    assert store.read("batches", batch["id"])["state"] == "paused"
    with pytest.raises(HTTPException):
        service.confirm(batch["id"], True, batch["input_digest"], resume=True)
    assert len(generator.starts) == 1 and generator.resumes == []


def test_interrupted_known_request_resumes_without_new_paid_submission(setup):
    store, generator, service, project = setup
    generator.outcomes = ["interrupted"]
    batch = begin(service, project)
    service.run(batch["id"])
    known = store.read("batches", batch["id"])["items"][0]["job_id"]
    service.confirm(batch["id"], True, batch["input_digest"], resume=True)
    service.run(batch["id"])
    assert generator.resumes == [known]
    assert len(generator.starts) == 2  # Exactly one submission for each scene.
    assert store.read("batches", batch["id"])["state"] == "complete"


def test_known_reconciliation_finishing_after_confirmation_is_acknowledged(setup):
    store, generator, service, project = setup
    generator.outcomes = ["interrupted"]
    batch = begin(service, project)
    service.run(batch["id"])
    known = store.read("batches", batch["id"])["items"][0]["job_id"]
    service.confirm(batch["id"], True, batch["input_digest"], resume=True)
    # The already-known provider request completes before the batch worker runs.
    generator.launch(known, resume=True)
    service.run(batch["id"])
    assert len(generator.starts) == 2
    assert store.read("batches", batch["id"])["state"] == "complete"


def test_cancel_keeps_current_paid_result_and_cancels_only_unstarted_items(setup):
    store, generator, service, project = setup
    batch = begin(service, project)
    service.workers.add(batch["id"])
    generator.on_launch = lambda _: service.control(batch["id"], cancel=True)
    service.run(batch["id"])
    result = store.read("batches", batch["id"])
    assert result["state"] == "cancelled"
    assert [item["status"] for item in result["items"]] == ["complete", "cancelled"]
    assert len(generator.starts) == 1


def test_pause_before_submission_can_only_restart_after_confirmation(setup):
    store, generator, service, project = setup
    batch = begin(service, project)
    service.workers.add(batch["id"])
    generator.on_launch = lambda _: service.control(batch["id"])
    generator.outcomes = ["not_submitted"]
    service.run(batch["id"])
    result = store.read("batches", batch["id"])
    assert result["state"] == "paused" and result["items"][0]["status"] == "pending"
    assert result["items"][0]["job_id"] is None
    generator.on_launch = None
    service.confirm(batch["id"], True, batch["input_digest"], resume=True)
    service.run(batch["id"])
    assert store.read("batches", batch["id"])["state"] == "complete"


def test_restart_recovers_completed_job_delta_but_never_submits_automatically(setup):
    store, generator, service, project = setup
    batch = begin(service, project)
    job = generator.start(project.id, project.scenes[0].id, True, 1, batch_id=batch["id"])
    batch = store.read("batches", batch["id"])
    batch["items"][0].update(status="running", job_id=job.id)
    service.save(batch)
    generator.launch(job.id)
    # Simulate a process death after completion but before the batch acknowledged it.
    store.batch_owner = None
    recovered = BatchService(store, generator, poll_seconds=0)
    assert store.read("batches", batch["id"])["state"] == "paused"
    assert len(generator.starts) == 1
    recovered.confirm(batch["id"], True, batch["input_digest"], resume=True)
    recovered.run(batch["id"])
    assert len(generator.starts) == 2
    assert store.read("batches", batch["id"])["state"] == "complete"


@pytest.mark.parametrize("status", ["pending", "running"])
def test_restart_reattaches_job_persisted_before_batch_pointer(setup, status):
    store, generator, service, project = setup
    batch = begin(service, project)
    job = generator.start(project.id, project.scenes[0].id, True, 1, batch_id=batch["id"])
    batch = store.read("batches", batch["id"])
    batch["items"][0]["status"] = status
    service.save(batch)
    BatchService(store, generator)
    assert store.read("batches", batch["id"])["items"][0]["job_id"] == job.id


def test_other_batch_and_individual_job_interlocks(setup):
    store, generator, service, project = setup
    first = begin(service, project)
    second = service.preview([project.id])
    with pytest.raises(HTTPException):
        service.confirm(second["id"], True, second["input_digest"])
    service.control(first["id"], cancel=True)
    foreign = Job(project_id=project.id, kind="generation", state="running")
    store.write("jobs", foreign.id, foreign)
    with pytest.raises(HTTPException):
        service.confirm(second["id"], True, second["input_digest"])
    assert generator.starts == []


def test_shutdown_persists_pause_without_auto_approval(setup):
    store, _, service, project = setup
    batch = begin(service, project)
    before = copy.deepcopy(store.read("projects", project.id))
    service.close()
    assert store.read("batches", batch["id"])["state"] == "paused"
    assert store.read("projects", project.id) == before


def test_http_contract_requires_strict_confirmation_and_keeps_inputs_private(setup, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from spotforge.batches import create_router
    store, generator, _, project = setup
    app = FastAPI()
    app.include_router(create_router(store, generation=generator), prefix="/api")
    monkeypatch.setattr(BatchService, "launch", lambda self, bid: self.run(bid))
    with TestClient(app) as client:
        response = client.post("/api/batches/preview", json={"project_ids": [project.id]})
        assert response.status_code == 201
        shown = response.json()
        assert "inputs" not in shown and "expected_projects" not in shown
        start = f"/api/batches/{shown['id']}/start"
        assert client.post(start, json={"confirmed": "yes", "input_digest": shown["input_digest"]}).status_code == 422
        assert generator.starts == []
        assert client.post(start, json={"confirmed": True, "input_digest": shown["input_digest"]}).status_code == 200
        result = client.get(f"/api/batches/{shown['id']}").json()
        assert result["state"] == "complete"
        assert client.get("/api/batches").json()[0]["id"] == shown["id"]


@pytest.mark.parametrize("cancel_before_submit", [False, True])
def test_real_generation_service_contract_has_sequential_ownership_and_final_stop_guard(tmp_path, monkeypatch, cancel_before_submit):
    import io
    import json
    from types import SimpleNamespace
    from PIL import Image
    from spotforge.generation import GenerationService
    from spotforge.models import Format
    store = Store(tmp_path)
    frame = io.BytesIO()
    Image.new("RGB", (128, 128), "blue").save(frame, format="PNG")
    asset = store.add_asset(frame.getvalue(), "frame.png", "image", "image/png")
    project = Project(format=Format(width=128, height=128), scenes=[Scene(mode="start", start_asset_id=asset.id, prompt="First"), Scene(mode="start", start_asset_id=asset.id, prompt="Second")])
    store.write("projects", project.id, project)
    submissions = []
    class Provider:
        def submit(self, payload):
            submissions.append(payload)
            return f"request-{len(submissions)}"
        def poll(self, _):
            return "COMPLETED"
        def result(self, _):
            return "mock"
        def download(self, _):
            return b"local video fixture"
    monkeypatch.setenv("FAL_KEY", "synthetic-test-key")
    monkeypatch.setattr("spotforge.generation.subprocess.run", lambda *a, **k: SimpleNamespace(stdout=json.dumps({"streams": [{"codec_type": "video", "width": 128, "height": 128}], "format": {"duration": 5}})))
    generator = GenerationService(store, provider_factory=lambda _: Provider(), poll_seconds=0)
    service = BatchService(store, generator, poll_seconds=0.001)
    batch = begin(service, project)
    if cancel_before_submit:
        original_launch = generator.launch
        def stopped_launch(jid, resume=False):
            service.control(batch["id"], cancel=True)
            original_launch(jid, resume=resume)
        monkeypatch.setattr(generator, "launch", stopped_launch)
    service.workers.add(batch["id"])
    service.run(batch["id"])
    result = store.read("batches", batch["id"])
    assert result["state"] == ("cancelled" if cancel_before_submit else "complete")
    assert len(submissions) == (0 if cancel_before_submit else 2)
    assert len(store.list("jobs")) == (1 if cancel_before_submit else 2)
