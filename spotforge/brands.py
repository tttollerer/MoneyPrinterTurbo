"""Structured brand profiles, immutable versions and explicitly pinned projects.

There are intentionally no project/scene style overrides in this baseline. Locks
record brand policy for future permitted overrides; snapshots are immutable now.
"""
import hashlib
import re

from fastapi import APIRouter, HTTPException
from pydantic import Field, ValidationError

from spotforge.models import Asset, Brand, Model, Project, new_id, now


BRAND_FIELDS = set(Brand.model_fields) - {"schema_version", "id", "version", "created_at", "locked_fields"}
COLOR_KEYS = {"primary", "background", "text"}
GATES = ("concept", "script", "storyboard", "clips", "final")


class ApplyBrand(Model):
    brand_id: str
    version: int | None = Field(default=None, ge=1)


def validate_brand(store, brand):
    """Validate effective settings and required files; never mutate the profile."""
    brand = Brand.model_validate(brand)
    if not brand.name.strip():
        raise ValueError("Brand name must not be empty")
    if set(brand.colors) != COLOR_KEYS or any(
        not re.fullmatch(r"#[0-9a-fA-F]{6}", value) for value in brand.colors.values()
    ):
        raise ValueError("Colors must contain primary, background and text as six-digit hex colors")
    if not brand.font_family.strip():
        raise ValueError("Font family must not be empty")
    if not brand.font_asset_id and brand.font_family not in {"Arial", "sans-serif"}:
        raise ValueError("Custom fonts require an uploaded font file")
    if len(set(brand.locked_fields)) != len(brand.locked_fields) or set(brand.locked_fields) - BRAND_FIELDS:
        raise ValueError("Locked fields must be distinct editable brand field names")
    for field in ("rules", "forbidden_claims"):
        values = getattr(brand, field)
        if len(values) > 100 or any(not value.strip() or len(value) > 2000 for value in values):
            raise ValueError(f"{field} requires at most 100 non-empty rules of at most 2000 characters")
    if any(not asset_id for asset_id in brand.document_asset_ids) or len(set(brand.document_asset_ids)) != len(brand.document_asset_ids):
        raise ValueError("Document references must be non-empty and distinct")
    references = [(brand.logo_asset_id, "image"), (brand.font_asset_id, "font")]
    references.extend((asset_id, "document") for asset_id in brand.document_asset_ids)
    for asset_id, expected in references:
        if not asset_id:
            continue
        try:
            asset = Asset.model_validate(store.read("assets", asset_id))
            path = store.asset_path(asset_id)
        except (FileNotFoundError, ValueError) as exc:
            raise ValueError(f"Required brand asset is unavailable: {asset_id}") from exc
        if asset.kind != expected:
            raise ValueError(f"Brand asset {asset_id} must have kind {expected}")
        if expected == "font" and path.suffix.lower() not in {".ttf", ".otf", ".woff", ".woff2"}:
            raise ValueError("Font files must use TTF, OTF, WOFF or WOFF2 format")
        if hashlib.sha256(path.read_bytes()).hexdigest() != asset.sha256:
            raise ValueError(f"Brand asset content changed: {asset_id}")
    return brand


def get_brand(store, brand_id, version=None):
    if version is None:
        return Brand.model_validate(store.read("brands", brand_id))
    # Validate the id independently: an appended version must not hide a bad id.
    store.path("brands", brand_id)
    return Brand.model_validate(store.read("brand_versions", f"{brand_id}_{version}"))


def apply_brand(store, project_id, brand_id, version=None):
    """Explicit application is the only way a project follows a brand update."""
    with store.lock:
        project = Project.model_validate(store.read("projects", project_id))
        brand = validate_brand(store, get_brand(store, brand_id, version))
        previous = project.brand_snapshot
        if previous == brand:
            return project
        old_style = previous.visual_style if previous else ""
        if old_style != brand.visual_style:
            for scene in project.scenes:
                if scene.mode != "local" and scene.takes:
                    scene.stale = True
        project.brand_snapshot = brand.model_copy(deep=True)
        project.render = {}
        project.gates = {gate: "todo" for gate in (GATES if project.recipe == "spot" else project.gates)}
        project.revision += 1
        store.write("projects", project.id, project)
        return project


def _http_error(exc):
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail="Brand, version or project not found")
    return HTTPException(status_code=422, detail=str(exc))


def create_router(store):
    router = APIRouter(prefix="/api")

    @router.get("/brands")
    def list_brands():
        return sorted(store.list("brands"), key=lambda brand: brand["name"].casefold())

    @router.post("/brands", status_code=201)
    def create_brand(data: dict):
        try:
            with store.lock:
                brand = validate_brand(store, Brand.model_validate({
                    **data, "id": new_id(), "version": 1, "created_at": now(),
                }))
                store.write("brand_versions", f"{brand.id}_1", brand)
                store.write("brands", brand.id, brand)
                return brand
        except (ValueError, ValidationError) as exc:
            raise _http_error(exc) from exc

    @router.get("/brands/{brand_id}")
    def read_brand(brand_id: str, version: int | None = None):
        if version is not None and version < 1:
            raise HTTPException(status_code=422, detail="Version must be positive")
        try:
            return get_brand(store, brand_id, version)
        except (FileNotFoundError, ValueError) as exc:
            raise _http_error(exc) from exc

    @router.post("/brands/{brand_id}/versions", status_code=201)
    def create_version(brand_id: str, data: dict):
        try:
            with store.lock:
                current = get_brand(store, brand_id)
                # A crash after the immutable version write but before updating
                # the latest pointer may leave an orphan. Preserve it and choose
                # a fresh number instead of overwriting or blocking future edits.
                next_version = max([current.version, *(
                    Brand.model_validate(item).version for item in store.list("brand_versions")
                    if item.get("id") == brand_id
                )]) + 1
                # Accept a full form or a partial update, but own version metadata.
                payload = {**current.model_dump(), **data, "id": brand_id,
                           "version": next_version, "created_at": now()}
                brand = validate_brand(store, Brand.model_validate(payload))
                key = f"{brand_id}_{brand.version}"
                if store.path("brand_versions", key).exists():
                    raise HTTPException(status_code=409, detail="Brand version already exists")
                store.write("brand_versions", key, brand)
                store.write("brands", brand_id, brand)
                return brand
        except (FileNotFoundError, ValueError) as exc:
            raise _http_error(exc) from exc

    @router.post("/projects/{project_id}/brand")
    def pin_brand(project_id: str, data: ApplyBrand):
        try:
            return apply_brand(store, project_id, data.brand_id, data.version)
        except (FileNotFoundError, ValueError) as exc:
            raise _http_error(exc) from exc

    return router
