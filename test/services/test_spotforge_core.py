"""Meaningful boundary and mutation checks for the local editor API."""
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from spotforge.models import Project, Scene, Take
from spotforge.store import Store


def image_bytes():
    out = io.BytesIO()
    Image.new("RGB", (64, 64), "red").save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def client(tmp_path):
    from spotforge.api import create_app
    with TestClient(create_app(tmp_path / "data", tmp_path / "outputs")) as client:
        yield client


def make_scene(client):
    asset = client.post("/api/assets", files={"file": ("frame.png", image_bytes(), "image/png")}).json()
    p = client.post("/api/projects", json={"title": "Test"}).json()
    p = client.post(f"/api/projects/{p['id']}/scenes", json={"mode":"local", "source_asset_id":asset["id"]}).json()
    return p, asset


def test_store_survives_reopen_and_rejects_traversal(tmp_path):
    store = Store(tmp_path)
    store.write("projects", "one", {"title": "Persisted"})
    assert Store(tmp_path).read("projects", "one")["title"] == "Persisted"
    with pytest.raises(ValueError):
        store.read("projects", "../private")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/escape.json").write_text(json.dumps({"id":"escape", "name":"bad", "kind":"image", "path":"../private.png", "sha256":"abc", "mime":"image/png", "size":1}))
    with pytest.raises(ValueError):
        store.asset_path("escape")


def test_invalid_asset_references_cannot_corrupt_project(client):
    p, _ = make_scene(client)
    res = client.patch(f"/api/projects/{p['id']}/scenes/{p['scenes'][0]['id']}", json={"start_asset_id":"missing"})
    assert res.status_code == 404
    actual = client.get(f"/api/projects/{p['id']}").json()
    assert actual == p


def test_manifest_matches_scene_order_and_has_browser_urls(client):
    p, asset = make_scene(client)
    res = client.get(f"/api/projects/{p['id']}/manifest")
    assert res.status_code == 200, res.text
    manifest = res.json()
    assert manifest["duration_frames"] == 150
    assert manifest["scenes"][0]["asset_id"] == asset["id"]
    assert manifest["assets"][asset["id"]]["url"].startswith("http://testserver/api/assets/")
    assert client.head(f"/api/assets/{asset['id']}/file").status_code == 200


def test_gate_and_revision_enforcement(client):
    p = client.post("/api/projects", json={"title":"Spot", "recipe":"spot"}).json()
    assert client.post(f"/api/projects/{p['id']}/gates/final", json={"approve": True}).status_code == 409
    assert client.post(f"/api/projects/{p['id']}/render").status_code == 409
    assert client.patch(f"/api/projects/{p['id']}", json={"title":"Changed", "expected_revision":99}).status_code == 409
    assert client.patch(f"/api/projects/{p['id']}", json={"gates":{"final":"approved"}}).status_code == 422


def test_changing_parent_take_marks_descendant_stale(client):
    store = client.app.state.store
    a = store.add_asset(image_bytes(), "frame.png", "image", "image/png")
    t1, t2 = Take(asset_id=a.id), Take(asset_id=a.id)
    parent = Scene(takes=[t1,t2], selected_take_id=t1.id)
    child = Scene(predecessor_scene_id=parent.id, source_asset_id=a.id)
    p = Project(scenes=[parent,child])
    store.write("projects",p.id,p)
    res = client.post(f"/api/projects/{p.id}/scenes/{parent.id}/select",json={"take_id":t2.id})
    assert res.status_code == 200
    assert res.json()["scenes"][1]["stale"]
    assert client.get(f"/api/projects/{p.id}/manifest").status_code == 422
    assert client.delete(f"/api/projects/{p.id}/scenes/{parent.id}").status_code == 409


def test_foreign_origin_cannot_mutate_local_state(client):
    assert client.post("/api/projects", json={"title":"bad"}, headers={"Origin":"https://foreign.example"}).status_code == 403
    assert client.get("/api/projects").json() == []


def test_empty_or_disguised_upload_rejected(client):
    assert client.post("/api/assets", files={"file":("empty.png", b"", "image/png")}).status_code == 422
    assert client.post("/api/assets", files={"file":("fake.ttf", b"not-a-font", "font/ttf")}).status_code == 422


def test_startup_marks_incomplete_render_interrupted(tmp_path):
    from spotforge.api import create_app
    from spotforge.models import Job
    store=Store(tmp_path)
    job=Job(project_id="one",kind="render",state="running")
    store.write("jobs",job.id,job)
    with TestClient(create_app(tmp_path)) as client:
        res=client.get(f"/api/jobs/{job.id}").json()
        assert res["state"] == "interrupted"


def test_scene_with_unresolved_paid_job_cannot_be_deleted(client):
    from spotforge.models import Job
    p, _ = make_scene(client)
    sid = p["scenes"][0]["id"]
    job = Job(project_id=p["id"], scene_id=sid, kind="generation", state="interrupted", provider_request_id="known")
    client.app.state.store.write("jobs", job.id, job)
    assert client.delete(f"/api/projects/{p['id']}/scenes/{sid}").status_code == 409
    assert len(client.get(f"/api/projects/{p['id']}").json()["scenes"]) == 1


def test_changed_advertising_copy_requires_script_review(client):
    p, _ = make_scene(client)
    store = client.app.state.store
    p["recipe"] = "spot"
    p["gates"] = dict.fromkeys(["concept", "script", "storyboard", "clips", "final"], "approved")
    store.write("projects", p["id"], p)
    res = client.patch(f"/api/projects/{p['id']}/scenes/{p['scenes'][0]['id']}", json={"onscreen_text":"Neue Werbeaussage"})
    assert res.status_code == 200
    assert res.json()["gates"]["concept"] == "approved"
    assert res.json()["gates"]["script"] == "todo"


def test_format_change_marks_generated_scenes_stale(client):
    store = client.app.state.store
    a = store.add_asset(image_bytes(), "frame.png", "image", "image/png")
    take = Take(asset_id=a.id)
    p = Project(scenes=[Scene(mode="start", takes=[take], selected_take_id=take.id)])
    store.write("projects", p.id, p)
    res = client.patch(f"/api/projects/{p.id}", json={"format":{"width":1920,"height":1080,"fps":30}})
    assert res.status_code == 200
    assert res.json()["scenes"][0]["stale"]


def test_changed_end_conditioned_duration_cannot_truncate_reference(client):
    store = client.app.state.store
    a = store.add_asset(image_bytes(), "frame.png", "image", "image/png")
    take = Take(asset_id=a.id, end_asset_id=a.id, parameters={"actual_duration_s":10})
    p = Project(scenes=[Scene(duration_s=5, takes=[take], selected_take_id=take.id)])
    store.write("projects", p.id, p)
    res = client.get(f"/api/projects/{p.id}/manifest")
    assert res.status_code == 422
    assert "Endbild" in res.json()["detail"]
