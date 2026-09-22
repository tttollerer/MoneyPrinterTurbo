"""Credential lifecycle and safe batch seam; never call a real fal endpoint."""
import io
import json
import os
import sys
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from spotforge import credentials, generation
from spotforge.credentials import CredentialError, Credentials, MacKeychain
from spotforge.credentials_routes import create_router
from spotforge.models import Job, Project, Scene
from spotforge.store import Store

DUMMY = "test-only-fal-key-123456"


class FakeKeychain:
    def __init__(self):
        self.value = None
        self.reads = 0
        self.error = False

    def contains(self):
        if self.error:
            raise RuntimeError(DUMMY)
        return self.value is not None

    def get(self):
        self.reads += 1
        if self.error:
            raise RuntimeError(DUMMY)
        return self.value

    def set(self, value):
        if self.error:
            raise RuntimeError(DUMMY)
        self.value = value

    def delete(self):
        if self.error:
            raise RuntimeError(DUMMY)
        self.value = None


@pytest.fixture(autouse=True)
def isolate_vault(monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.setattr(credentials, "_credentials", Credentials())


def test_session_does_not_persist_or_change_environment(tmp_path):
    vault = Credentials()
    assert vault.status() == {"configured": False, "source": None, "persistence_available": False}
    assert vault.set(DUMMY, "session")["source"] == "session"
    assert vault.get() == DUMMY
    assert "FAL_KEY" not in os.environ
    assert Credentials().get() is None
    assert list(tmp_path.iterdir()) == []
    assert vault.delete()["configured"] is False


def test_environment_precedence_survives_deleting_local_keys(monkeypatch):
    backend = FakeKeychain()
    vault = Credentials(backend)
    vault.set(DUMMY, "keychain")
    vault.set("temporary-session-key", "session")
    monkeypatch.setenv("FAL_KEY", "environment-configured-key")
    assert vault.status()["source"] == "environment"
    assert vault.get() == "environment-configured-key"
    assert vault.delete()["source"] == "environment"
    assert backend.value is None
    monkeypatch.delenv("FAL_KEY")
    assert vault.get() is None


def test_keychain_replace_and_session_override_keep_sources_honest():
    backend = FakeKeychain()
    vault = Credentials(backend)
    vault.set(DUMMY, "keychain")
    assert vault.status()["source"] == "keychain" and backend.reads == 0
    assert Credentials(backend).get() == DUMMY
    vault.set("new-session-only-key", "session")
    assert vault.get() == "new-session-only-key"
    assert Credentials(backend).get() == DUMMY  # A session override never destroys a saved key.
    vault.set("replacement-saved-key", "keychain")
    assert vault.get() == "replacement-saved-key"
    vault.delete()
    assert Credentials(backend).get() is None


def test_locked_backend_never_falls_back_to_disk_or_echoes_secret():
    backend = FakeKeychain()
    backend.error = True
    vault = Credentials(backend)
    assert DUMMY not in json.dumps(vault.status())
    with pytest.raises(CredentialError) as error:
        vault.set(DUMMY, "keychain")
    assert DUMMY not in str(error.value)
    assert vault.set(DUMMY, "session")["source"] == "session"
    with pytest.raises(CredentialError):
        vault.delete()
    assert vault.get() == DUMMY  # Failed deletion is not falsely reported as success.
    with pytest.raises(CredentialError, match="nicht verfügbar"):
        Credentials().set(DUMMY, "keychain")


@pytest.mark.parametrize("body", [
    {"key": DUMMY, "persistence": "wrong"},
    {"key": DUMMY, "persistence": {"key": DUMMY}},
    {"key": {"secret": DUMMY}, "persistence": "session"},
    {"key": DUMMY, "persistence": "session", "extra": DUMMY},
    {"key": DUMMY + "\n", "persistence": "session"},
    [DUMMY],
])
def test_malformed_secret_requests_never_echo_input(body):
    app = FastAPI()
    app.include_router(create_router(credentials=Credentials()), prefix="/api")
    with TestClient(app) as client:
        response = client.put("/api/providers/fal", json=body)
        assert response.status_code in {409, 422}
        assert DUMMY not in response.text


def test_router_lifecycle_only_returns_metadata_no_provider_calls(monkeypatch, tmp_path):
    backend = FakeKeychain()
    vault = Credentials(backend)
    app = FastAPI()
    store = Store(tmp_path)
    app.include_router(create_router(store, credentials=vault), prefix="/api")
    monkeypatch.setattr(generation.requests, "post", lambda *a, **kw: pytest.fail("credential configuration must not validate through fal"))
    with TestClient(app) as client:
        assert client.get("/api/providers/fal").json()["configured"] is False
        response = client.put("/api/providers/fal", json={"key": DUMMY, "persistence": "keychain"})
        assert response.status_code == 200
        assert response.json()["source"] == "keychain"
        assert response.headers["cache-control"] == "no-store"
        assert DUMMY not in response.text
        assert client.delete("/api/providers/fal").json()["configured"] is False
    assert not list(tmp_path.rglob("*.json"))


def test_oversize_body_does_not_echo_key():
    app = FastAPI()
    app.include_router(create_router(credentials=Credentials()))
    with TestClient(app) as client:
        response = client.put("/providers/fal", content=DUMMY * 1000)
    assert response.status_code == 413
    assert DUMMY not in response.text


@pytest.fixture
def scene_store(tmp_path):
    store = Store(tmp_path)
    stream = io.BytesIO()
    Image.new("RGB", (360, 640), "green").save(stream, format="PNG")
    image = store.add_asset(stream.getvalue(), "start.png", "image", "image/png")
    project = Project(scenes=[Scene(mode="start", prompt="slow pan", start_asset_id=image.id)])
    store.write("projects", project.id, project)
    credentials.get_credentials().set(DUMMY, "session")
    return store, project


def test_preflight_is_read_only_and_uses_gateway_session_key(scene_store, monkeypatch):
    store, project = scene_store
    monkeypatch.setattr(generation.requests, "post", lambda *a, **kw: pytest.fail("preflight must be local"))
    service = generation.get_service(store)
    before = {str(p): p.read_bytes() for p in store.root.rglob("*.json")}
    result = service.preflight(project.id, project.scenes[0].id, expected_revision=1)
    assert result["configured"] and result["mode"] == "start"
    assert result["cost"]["amount"] is None
    assert DUMMY not in json.dumps(result)
    assert before == {str(p): p.read_bytes() for p in store.root.rglob("*.json")}
    assert generation.capabilities()[0]["configured"]
    generation.create_router(store)
    assert generation.get_service(store) is service


def test_batch_owner_blocks_individual_start_and_resume(scene_store):
    store, project = scene_store
    service = generation.get_service(store)
    store.batch_owner = "batch-1"
    with pytest.raises(generation.GenerationError, match="Kampagnenproduktion"):
        service.start(project.id, project.scenes[0].id, True, 1)
    job = service.start(project.id, project.scenes[0].id, True, 1, batch_id="batch-1")
    assert job.input_snapshot["batch_id"] == "batch-1"
    with pytest.raises(generation.GenerationError, match="Kampagne"):
        service.resume(job.id, True)
    assert DUMMY not in json.dumps(store.read("jobs", job.id))


@pytest.mark.parametrize("stop,owner", [(True, "batch-1"), (False, None), (False, "other-batch")])
def test_pause_or_owner_loss_before_submission_is_unambiguously_not_submitted(scene_store, stop, owner):
    store, project = scene_store
    class NoSubmit:
        def __init__(self, key):
            assert key == DUMMY
        def submit(self, *args):
            pytest.fail("paused or detached batch must not incur cost")
    service = generation.GenerationService(store, provider_factory=NoSubmit)
    store.batch_owner = "batch-1"
    job = service.start(project.id, project.scenes[0].id, True, 1, batch_id="batch-1")
    store.batch_stop_requested, store.batch_owner = stop, owner
    service.run(job.id)
    final = store.read("jobs", job.id)
    assert final["state"] == "failed"
    assert final["result"]["not_submitted"] is True
    assert final["provider_request_id"] is None


def test_key_removed_after_confirmation_prevents_submission(scene_store):
    store, project = scene_store
    service = generation.GenerationService(store)
    job = service.start(project.id, project.scenes[0].id, True, 1)
    credentials.get_credentials().delete()
    service.run(job.id)
    final = store.read("jobs", job.id)
    assert final["state"] == "failed" and final["provider_request_id"] is None


def test_native_keychain_lifecycle_opt_in():
    if sys.platform != "darwin" or os.environ.get("SPOTFORGE_TEST_KEYCHAIN") != "1":
        pytest.skip("Native Keychain acceptance is opt-in and uses its own disposable service")
    backend = MacKeychain(service="com.spotforge.fal.test." + uuid4().hex, account="disposable-test")
    try:
        assert not backend.contains()
        backend.set(DUMMY)
        assert backend.contains() and backend.get() == DUMMY
        backend.set("replacement-native-test-key")
        assert backend.get() == "replacement-native-test-key"
        backend.delete()
        assert not backend.contains()
    finally:
        backend.delete()


@pytest.mark.parametrize("state,request_id,expected", [
    ("queued", None, "failed"), ("submitting", None, "unknown"),
    ("running", "known-request", "interrupted"),
])
def test_recovery_distinguishes_unsent_from_ambiguous_submission(scene_store, state, request_id, expected):
    store, project = scene_store
    job = Job(project_id=project.id, kind="generation", state=state,
              provider_request_id=request_id, input_snapshot={"batch_id": "batch-1"})
    store.write("jobs", job.id, job)
    generation.get_service(store)
    result = store.read("jobs", job.id)
    assert result["state"] == expected
    assert bool(result["result"].get("not_submitted")) == (state == "queued")
    assert result["provider_request_id"] == request_id


@pytest.mark.parametrize("replacement", [None, "different-session-test-key"])
def test_credentials_changed_during_frame_preparation_prevent_submission(scene_store, monkeypatch, replacement):
    store, project = scene_store
    class NoSubmit:
        def __init__(self, key):
            pass
        def submit(self, *args):
            pytest.fail("credentials changed before submission; confirmation is no longer current")
    service = generation.GenerationService(store, provider_factory=NoSubmit)
    job = service.start(project.id, project.scenes[0].id, True, 1)
    original = generation._data_uri
    def change_after_read(*args):
        data = original(*args)
        vault = credentials.get_credentials()
        vault.delete() if replacement is None else vault.set(replacement, "session")
        return data
    monkeypatch.setattr(generation, "_data_uri", change_after_read)
    service.run(job.id)
    result = store.read("jobs", job.id)
    assert result["state"] == "failed" and result["provider_request_id"] is None
