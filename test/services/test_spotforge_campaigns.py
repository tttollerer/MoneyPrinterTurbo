"""Campaign boundaries: no paid work or approvals during import and assembly."""
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from spotforge.api import create_app
from spotforge.models import Scene, Take


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "data", tmp_path / "outputs")) as c:
        yield c


def image(client, color="red", size=(180, 320)):
    data = io.BytesIO()
    Image.new("RGB", size, color).save(data, "PNG")
    return client.post("/api/assets", files={"file": ("frame.png", data.getvalue(), "image/png")}).json()["id"]


def document():
    return {"title": "Demo campaign", "brief": "Shared story, audience endings", "motifs": [
        {"key": "pilot", "title": "Pilot", "script": "First the problem. Then the product.",
         "shots": [{"title": "Setup", "prompt": "A quiet desk", "start_frame": "01.png", "end_frame": "02.png"},
                   {"title": "Payoff", "prompt": "The product appears", "start_frame": "02.png", "end_frame": "03.png"}],
         "variants": [{"key": "private", "title": "Private", "audience": "Consumers", "cta": "Discover", "endcard_text": "Demo product"},
                      {"key": "business", "title": "Business", "audience": "Professionals", "cta": "Contact us"}]}]}


def campaign(client):
    response = client.post("/api/campaigns", json=document())
    assert response.status_code == 200, response.text
    c = response.json()
    return c, client.get(f"/api/projects/{c['motifs'][0]['project_id']}").json()


def test_import_preview_is_non_destructive_and_commit_idempotent(client):
    preview = client.post("/api/campaigns/import/preview", json={"document": document()}).json()
    assert preview["motifs_count"] == 1 and preview["variants_count"] == 2
    assert len(preview["missing_frames"]) == 4
    assert client.get("/api/projects").json() == []
    assert client.get("/api/jobs").json() == []
    body = {"preview_id": preview["preview_id"], "confirmed": True}
    first = client.post("/api/campaigns/import/commit", json=body)
    assert first.status_code == 200
    assert client.post("/api/campaigns/import/commit", json=body).json() == first.json()
    assert len(client.get("/api/projects").json()) == 1
    p = client.get("/api/projects").json()[0]
    assert set(p["gates"].values()) == {"todo"}
    assert p["scenes"][0]["start_asset_id"] is None
    assert client.get("/api/jobs").json() == []


def test_boundary_chain_requires_exact_shape_revision_and_format(client):
    c, p = campaign(client)
    route = f"/api/campaigns/{c['id']}/motifs/pilot/frames"
    ids = [image(client, color) for color in ("red", "blue", "green")]
    assert client.post(route, json={"expected_revision": 99, "asset_ids": ids}).status_code == 409
    assert client.post(route, json={"expected_revision": p["revision"], "asset_ids": ids[:2]}).status_code == 422
    bad = image(client, size=(320, 180))
    assert client.post(route, json={"expected_revision": p["revision"], "asset_ids": [ids[0], bad, ids[2]]}).status_code == 422
    response = client.post(route, json={"expected_revision": p["revision"], "asset_ids": ids})
    assert response.status_code == 200, response.text
    scenes = response.json()["scenes"]
    assert scenes[0]["end_asset_id"] == scenes[1]["start_asset_id"] == ids[1]
    assert scenes[0]["start_asset_id"] == ids[0] and scenes[1]["end_asset_id"] == ids[2]
    assert all(s["mode"] == "start_end" for s in scenes)
    assert client.get("/api/jobs").json() == []


def test_replacing_boundary_keeps_historical_takes_but_invalidates_review(client):
    c, p = campaign(client)
    aid = image(client)
    take = Take(asset_id=aid)
    p["scenes"][0].update(takes=[take.model_dump()], selected_take_id=take.id)
    p["gates"] = dict.fromkeys(p["gates"], "approved")
    client.app.state.store.write("projects", p["id"], p)
    response = client.post(f"/api/campaigns/{c['id']}/motifs/pilot/frames",
                           json={"expected_revision": p["revision"], "asset_ids": [aid, aid, aid]})
    assert response.status_code == 200
    result = response.json()
    assert result["scenes"][0]["stale"] and result["scenes"][0]["takes"][0]["id"] == take.id
    assert result["gates"]["storyboard"] == "todo"


def test_variants_reuse_assets_without_paid_work_and_require_new_approvals(client):
    c, p = campaign(client)
    route = f"/api/campaigns/{c['id']}/motifs/pilot/variants"
    body = {"expected_revision": p["revision"], "confirmed": True}
    assert client.post(route, json=body).status_code == 409
    aid = image(client)
    p["scenes"] = [Scene(source_asset_id=aid, duration_s=5).model_dump()]
    p["gates"] = dict.fromkeys(p["gates"], "approved")
    client.app.state.store.write("projects", p["id"], p)
    response = client.post(route, json=body)
    assert response.status_code == 200, response.text
    output = response.json()
    assert len(output["projects"]) == 2
    for project in output["projects"]:
        assert project["scenes"][0]["source_asset_id"] == aid
        assert project["scenes"][1]["title"] == "Schlusskarte"
        assert set(project["gates"].values()) == {"todo"}
        assert project["render"] == {}
        assert client.get(f"/api/projects/{project['id']}/manifest").status_code == 200
    assert output["projects"][0]["scenes"][-1]["onscreen_text"] != output["projects"][1]["scenes"][-1]["onscreen_text"]
    assert client.post(route, json=body).json() == output
    assert len(client.get("/api/projects").json()) == 3
    assert client.get("/api/jobs").json() == []


def test_invalid_import_never_creates_partial_projects(client):
    doc = document()
    doc["motifs"].append({"key": "other", "title": "Other", "shots": [{"start_asset_id": "doesnotexist"}]})
    assert client.post("/api/campaigns", json=doc).status_code == 404
    assert client.get("/api/projects").json() == []
    assert client.get("/api/campaigns").json() == []
    doc = document()
    doc["motifs"] *= 2
    assert client.post("/api/campaigns", json=doc).status_code == 422


def test_export_is_importable_plan_with_explicit_media_warning(client):
    c, p = campaign(client)
    exported = client.get(f"/api/campaigns/{c['id']}/export").json()
    assert exported["warnings"]
    preview = client.post("/api/campaigns/import/preview", json={"document": exported["document"]})
    assert preview.status_code == 200
    assert preview.json()["document"]["motifs"][0]["shots"][1]["start_frame"] == "02.png"
    assert preview.json()["document"]["motifs"][0]["script"] == p["script"]


def test_campaign_changes_use_revision_and_motif_keys_are_unique(client):
    c, _ = campaign(client)
    route = f"/api/campaigns/{c['id']}"
    assert client.patch(route, json={"expected_revision": 42, "title": "Wrong"}).status_code == 409
    assert client.post(route + "/motifs", json={"expected_revision": c["revision"], "motif": {"key": "pilot", "title": "Duplicate"}}).status_code == 422
    response = client.post(route + "/motifs", json={"expected_revision": c["revision"], "motif": {"key": "second", "title": "Second"}})
    assert response.status_code == 200
    detail = client.get(route).json()
    assert len(detail["motifs"]) == len(detail["projects"]) == 2


def test_changed_cta_preserves_previous_cuts_and_local_edits(client):
    c, p = campaign(client)
    aid = image(client)
    p["scenes"] = [Scene(source_asset_id=aid).model_dump()]
    p["gates"] = dict.fromkeys(p["gates"], "approved")
    client.app.state.store.write("projects", p["id"], p)
    route = f"/api/campaigns/{c['id']}/motifs/pilot"
    body = {"confirmed": True, "expected_revision": p["revision"]}
    old = client.post(route + "/variants", json=body).json()
    variants = old["campaign"]["motifs"][0]["variants"]
    variants[0]["cta"] = "New call to action"
    patched = client.patch(route, json={"expected_revision": old["campaign"]["revision"], "variants": variants})
    assert patched.status_code == 200, patched.text
    new = client.post(route + "/variants", json=body).json()
    assert len(new["campaign"]["motifs"][0]["outputs"]) == 4
    assert new["projects"][0]["id"] != old["projects"][0]["id"]
    previous = client.get(f"/api/projects/{old['projects'][0]['id']}").json()
    assert previous["scenes"][-1]["onscreen_text"] == "Demo product\nDiscover"
    # Repeating assembly may not clobber work in an already created cut.
    pid = new["projects"][0]["id"]
    client.patch(f"/api/projects/{pid}", json={"title": "Edited cut"})
    repeated = client.post(route + "/variants", json=body).json()
    assert repeated["projects"][0]["title"] == "Edited cut"


def test_fal_setup_is_mounted_and_rejects_cross_origin(client):
    status = client.get("/api/providers/fal")
    assert status.status_code == 200
    assert "configured" in status.json() and "key" not in status.json()
    response = client.put("/api/providers/fal", json={"key": "never-save-this", "persistence": "session"},
                          headers={"Origin": "https://foreign.example"})
    assert response.status_code == 403


def test_export_preserves_changed_motif_format_and_brand(client):
    c, p = campaign(client)
    landscape = {"width": 1920, "height": 1080, "fps": 25}
    assert client.patch(f"/api/projects/{p['id']}", json={"format": landscape}).status_code == 200
    brand = client.post("/api/brands", json={"name": "Changed brand"}).json()
    applied = client.post(f"/api/projects/{p['id']}/brand", json={"brand_id": brand["id"]})
    assert applied.status_code == 200, applied.text
    exported = client.get(f"/api/campaigns/{c['id']}/export").json()["document"]
    assert exported["motifs"][0]["format"] == landscape
    assert exported["motifs"][0]["brand_id"] == brand["id"]
    imported = client.post("/api/campaigns", json=exported)
    assert imported.status_code == 200, imported.text
    copy = client.get(f"/api/projects/{imported.json()['motifs'][0]['project_id']}").json()
    assert copy["format"] == landscape
    assert copy["brand_snapshot"]["id"] == brand["id"]
