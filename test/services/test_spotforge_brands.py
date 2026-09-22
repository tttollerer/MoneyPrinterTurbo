"""Brand API integration tests with isolated local fixtures and no network."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from spotforge.brands import create_router, validate_brand
from spotforge.models import Brand, Project, Scene, Take
from spotforge.store import Store


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path)
    app = FastAPI()
    app.include_router(create_router(store))
    return store, TestClient(app)


def test_immutable_versions_and_explicit_apply_preserve_take_history(setup):
    store, client = setup
    first = client.post("/api/brands", json={"name": "Fixture", "visual_style": "natural"}).json()
    take = Take(asset_id="fixture", brand_snapshot=first)
    generated = Scene(mode="text", takes=[take], selected_take_id=take.id)
    local = Scene(mode="local", takes=[take], selected_take_id=take.id)
    project = Project(recipe="spot", brand_snapshot=Brand(**first), scenes=[generated, local],
                      gates={"final": "approved"}, render={"output": "old.mp4"})
    store.write("projects", project.id, project)
    second = client.post(f"/api/brands/{first['id']}/versions", json={"visual_style": "illustrated"})
    assert second.status_code == 201
    assert second.json()["version"] == 2
    assert client.get(f"/api/brands/{first['id']}?version=1").json() == first
    assert store.read("projects", project.id)["brand_snapshot"] == first
    applied = client.post(f"/api/projects/{project.id}/brand", json={"brand_id": first["id"], "version": 2})
    assert applied.status_code == 200
    result = applied.json()
    assert result["brand_snapshot"] == second.json()
    assert result["revision"] == project.revision + 1
    assert result["render"] == {}
    assert set(result["gates"].values()) == {"todo"}
    assert result["scenes"][0]["stale"] is True
    assert result["scenes"][1]["stale"] is False
    assert result["scenes"][0]["takes"][0]["brand_snapshot"] == first
    # Applying the same immutable version is a no-op, avoiding false stale writes.
    repeated = client.post(f"/api/projects/{project.id}/brand", json={"brand_id": first["id"], "version": 2})
    assert repeated.json() == result


@pytest.mark.parametrize("update", [
    {"colors": {"primary": "red", "background": "#000000", "text": "#ffffff"}},
    {"colors": {"primary": "#ffffff"}},
    {"locked_fields": ["colors", "unknown"]},
    {"locked_fields": ["colors", "colors"]},
    {"rules": [""]},
    {"forbidden_claims": [" "]},
    {"name": " "},
    {"font_family": "Custom Font"},
    {"safe_margin": 1},
    {"logo_asset_id": "missing"},
    {"document_asset_ids": ["missing"]},
    {"document_asset_ids": [""]},
])
def test_invalid_profile_is_rejected_without_persistence(setup, update):
    store, client = setup
    response = client.post("/api/brands", json={"name": "Fixture", **update})
    assert response.status_code == 422
    assert store.list("brands") == []
    assert store.list("brand_versions") == []


def test_assets_require_correct_kind_existing_file_and_unchanged_content(setup):
    store, client = setup
    logo = store.add_asset(b"fixture logo", "logo.png", "image", "image/png")
    font = store.add_asset(b"fixture font", "font.woff2", "font", "font/woff2")
    guide = store.add_asset(b"fixture guide", "guide.pdf", "document", "application/pdf")
    payload = {"name": "Fixture", "logo_asset_id": logo.id, "font_asset_id": font.id,
               "font_family": "Fixture Font", "document_asset_ids": [guide.id]}
    assert client.post("/api/brands", json={**payload, "logo_asset_id": font.id}).status_code == 422
    assert client.post("/api/brands", json={**payload, "document_asset_ids": [logo.id]}).status_code == 422
    created = client.post("/api/brands", json=payload)
    assert created.status_code == 201
    store.asset_path(logo.id).write_bytes(b"tampered content")
    with pytest.raises(ValueError, match="content changed"):
        validate_brand(store, Brand(**created.json()))
    store.asset_path(font.id).unlink()
    assert client.post("/api/brands", json={**payload, "logo_asset_id": None}).status_code == 422


def test_invalid_update_and_unknown_version_leave_project_untouched(setup):
    store, client = setup
    brand = client.post("/api/brands", json={"name": "Fixture"}).json()
    project = Project(brand_snapshot=Brand(**brand), render={"output": "good.mp4"})
    before = store.write("projects", project.id, project)
    invalid = client.post(f"/api/brands/{brand['id']}/versions", json={"font_asset_id": "missing"})
    assert invalid.status_code == 422
    assert client.get(f"/api/brands/{brand['id']}").json()["version"] == 1
    missing = client.post(f"/api/projects/{project.id}/brand", json={"brand_id": brand["id"], "version": 99})
    assert missing.status_code == 404
    assert store.read("projects", project.id) == before


def test_concurrent_version_creation_allocates_distinct_immutable_versions(setup):
    store, client = setup
    first = client.post("/api/brands", json={"name": "Fixture"}).json()
    def update(number):
        return client.post(f"/api/brands/{first['id']}/versions", json={"tone": f"tone {number}"})
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(update, range(4)))
    assert all(response.status_code == 201 for response in responses)
    assert sorted(response.json()["version"] for response in responses) == [2, 3, 4, 5]
    assert len(store.list("brand_versions")) == 5
    assert client.get(f"/api/brands/{first['id']}?version=1").json() == first


def test_render_only_brand_change_preserves_generated_scenes(setup):
    store, client = setup
    first = client.post("/api/brands", json={"name": "Fixture"}).json()
    project = Project(brand_snapshot=Brand(**first), scenes=[Scene(mode="text", takes=[Take(asset_id="fixture")])])
    store.write("projects", project.id, project)
    client.post(f"/api/brands/{first['id']}/versions", json={"required_text": "Fixture disclaimer"})
    response = client.post(f"/api/projects/{project.id}/brand", json={"brand_id": first["id"]})
    assert response.status_code == 200
    assert response.json()["scenes"][0]["stale"] is False


def test_interrupted_latest_pointer_write_preserves_orphan_and_allows_next_save(setup, monkeypatch):
    store, client = setup
    first = client.post("/api/brands", json={"name": "Fixture"}).json()
    write = store.write
    def interrupted_write(collection, key, value):
        if collection == "brands":
            raise OSError("Simulated interruption before latest pointer update")
        return write(collection, key, value)
    with monkeypatch.context() as patch:
        patch.setattr(store, "write", interrupted_write)
        with pytest.raises(OSError, match="Simulated interruption"):
            client.post(f"/api/brands/{first['id']}/versions", json={"tone": "orphan"})
    orphan = client.get(f"/api/brands/{first['id']}?version=2").json()
    assert orphan["tone"] == "orphan"
    assert client.get(f"/api/brands/{first['id']}").json() == first
    saved = client.post(f"/api/brands/{first['id']}/versions", json={"tone": "new save"})
    assert saved.status_code == 201
    assert saved.json()["version"] == 3
    assert client.get(f"/api/brands/{first['id']}?version=2").json() == orphan
    assert client.get(f"/api/brands/{first['id']}?version=1").json() == first
    assert client.get(f"/api/brands/{first['id']}").json() == saved.json()
