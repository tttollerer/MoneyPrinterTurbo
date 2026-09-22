"""Local campaign documents: shared motif projects, boundary images and audience cuts.

This layer never calls a model. Import creates drafts; variant assembly reuses local
assets and leaves all approvals to the user. File names are labels, never paths.
"""
import hashlib
import io
import json
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, uuid5

from fastapi import APIRouter, HTTPException
from PIL import Image
from pydantic import Field, StrictBool, model_validator

from spotforge.models import Asset, Brand, Format, Model, Project, Scene, new_id, now

GATES = ("concept", "script", "storyboard", "clips", "final")
KEY = r"^[a-zA-Z0-9_-]{1,80}$"


class ShotInput(Model):
    title: str = Field(default="Szene", min_length=1, max_length=200)
    prompt: str = Field(default="", max_length=6000)
    duration_s: float = Field(default=5, ge=.1, le=120)
    start_asset_id: str | None = None
    end_asset_id: str | None = None
    start_frame: str = Field(default="", max_length=240)
    end_frame: str = Field(default="", max_length=240)


class Variant(Model):
    key: str = Field(pattern=KEY)
    title: str = Field(min_length=1, max_length=100)
    audience: str = Field(default="", max_length=500)
    cta: str = Field(default="", max_length=300)
    endcard_text: str = Field(default="", max_length=500)
    endcard_asset_id: str | None = None
    duration_s: float = Field(default=3, ge=1, le=10)


class MotifInput(Model):
    key: str = Field(pattern=KEY)
    title: str = Field(min_length=1, max_length=120)
    brief: str = Field(default="", max_length=8000)
    script: str = Field(default="", max_length=12000)
    format: Format | None = None
    brand_id: str | None = None
    brand_version: int | None = Field(default=None, ge=1)
    shots: list[ShotInput] = Field(default_factory=list, max_length=100)
    variants: list[Variant] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def unique_keys(self):
        if self.brand_version and not self.brand_id:
            raise ValueError("Markenversion benötigt eine Marke.")
        if len({v.key for v in self.variants}) != len(self.variants):
            raise ValueError("Fassungen brauchen eindeutige Schlüssel pro Motiv.")
        return self


class CampaignInput(Model):
    schema_version: Literal[1] = 1
    title: str = Field(min_length=1, max_length=120)
    brief: str = Field(default="", max_length=8000)
    format: Format = Field(default_factory=Format)
    brand_id: str | None = None
    brand_version: int | None = Field(default=None, ge=1)
    motifs: list[MotifInput] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def unique_keys(self):
        if len({m.key for m in self.motifs}) != len(self.motifs):
            raise ValueError("Motive brauchen eindeutige Schlüssel.")
        if self.brand_version and not self.brand_id:
            raise ValueError("Markenversion benötigt eine Marke.")
        return self


class VariantOutput(Model):
    variant_key: str
    project_id: str
    source_revision: int
    variant_digest: str = ""
    created_at: str = Field(default_factory=now)


class Motif(Model):
    key: str
    title: str
    project_id: str
    variants: list[Variant] = Field(default_factory=list)
    frame_labels: list[dict] = Field(default_factory=list)
    outputs: list[VariantOutput] = Field(default_factory=list)


class Campaign(Model):
    schema_version: Literal[1] = 1
    id: str = Field(default_factory=new_id)
    revision: int = 1
    title: str
    brief: str = ""
    format: Format = Field(default_factory=Format)
    brand_id: str | None = None
    brand_version: int | None = None
    motifs: list[Motif] = Field(default_factory=list)
    created_at: str = Field(default_factory=now)


class FrameRequest(Model):
    expected_revision: int
    asset_ids: list[str] = Field(min_length=2, max_length=101)
    prompts: list[str] | None = None
    durations: list[Annotated[float, Field(ge=.1, le=120, allow_inf_nan=False)]] | None = None


class AssembleRequest(Model):
    expected_revision: int
    confirmed: StrictBool = False


def _read(store, cid):
    return Campaign.model_validate(store.read("campaigns", cid))


def _project(store, pid):
    return Project.model_validate(store.read("projects", pid))


def _motif(campaign, key):
    result = next((m for m in campaign.motifs if m.key == key), None)
    if not result:
        raise HTTPException(404, "Motiv nicht gefunden.")
    return result


def _image(store, aid, fmt=None):
    asset = Asset.model_validate(store.read("assets", aid))
    if asset.kind != "image":
        raise ValueError("Grenzbilder und Schlusskarten müssen Bilder sein.")
    content = store.asset_path(aid).read_bytes()
    if hashlib.sha256(content).hexdigest() != asset.sha256:
        raise ValueError("Bilddatei wurde verändert. Bitte neu importieren.")
    with Image.open(io.BytesIO(content)) as image:
        if fmt and abs((image.width / image.height) / (fmt.width / fmt.height) - 1) > .02:
            raise ValueError("Bildformat passt nicht zum Motiv. Vorher passend zuschneiden.")
        image.verify()
    return asset


def _brand(store, document):
    if not document.brand_id:
        return None
    from spotforge.brands import validate_brand
    key = f"{document.brand_id}_{document.brand_version}" if document.brand_version else document.brand_id
    brand = Brand.model_validate(store.read("brand_versions" if document.brand_version else "brands", key))
    validate_brand(store, brand)
    return brand


def inspect_document(store, document):
    brand = _brand(store, document)
    missing = []
    for motif in document.motifs:
        if motif.brand_id:
            motif.brand_version = _brand(store, motif).version
        fmt = motif.format or document.format
        for shot in motif.shots:
            for role in ("start", "end"):
                aid = getattr(shot, role + "_asset_id")
                if aid:
                    _image(store, aid, fmt)
                else:
                    missing.append({"motif_key": motif.key, "scene_title": shot.title,
                                    "role": role, "name": getattr(shot, role + "_frame")})
        for variant in motif.variants:
            if variant.endcard_asset_id:
                _image(store, variant.endcard_asset_id, fmt)
    return brand, missing


def _build_motif(campaign, item, brand):
    pid = uuid5(NAMESPACE_URL, f"spotforge:{campaign.id}:{item.key}").hex
    scenes = [Scene(title=s.title, prompt=s.prompt, duration_s=s.duration_s,
                    mode="start_end", start_asset_id=s.start_asset_id, end_asset_id=s.end_asset_id)
              for s in item.shots]
    p = Project(id=pid, title=item.title, brief=item.brief or campaign.brief, script=item.script,
                recipe="spot", format=item.format or campaign.format, brand_snapshot=brand, scenes=scenes,
                gates=dict.fromkeys(GATES, "todo"))
    m = Motif(key=item.key, title=item.title, project_id=pid, variants=item.variants,
              frame_labels=[{"scene_id": scene.id, "start_frame": shot.start_frame,
                             "end_frame": shot.end_frame} for scene, shot in zip(scenes, item.shots)])
    return m, p


def create_campaign(store, document, campaign_id=None):
    with store.lock:
        brand, _ = inspect_document(store, document)
        campaign = Campaign(id=campaign_id or new_id(), title=document.title, brief=document.brief,
                            format=document.format, brand_id=brand.id if brand else None,
                            brand_version=brand.version if brand else None)
        # The campaign is the commit marker; deterministic project IDs make an interrupted
        # import safely repeatable without duplicate projects.
        if store.path("campaigns", campaign.id).exists():
            return _read(store, campaign.id)
        projects = []
        for item in document.motifs:
            motif, project = _build_motif(campaign, item, _brand(store, item) if item.brand_id else brand)
            campaign.motifs.append(motif)
            projects.append(project)
        for project in projects:
            store.write("projects", project.id, project)
        store.write("campaigns", campaign.id, campaign)
        return campaign


def boundary_frames(store, cid, key, request):
    from spotforge.api import invalidate_gates, save_project
    with store.lock:
        campaign = _read(store, cid)
        motif = _motif(campaign, key)
        p = _project(store, motif.project_id)
        if request.expected_revision != p.revision:
            raise HTTPException(409, "Storyboard wurde geändert. Neu laden.")
        count = len(request.asset_ids) - 1
        if p.scenes and len(p.scenes) != count:
            raise ValueError("Für N Szenen werden genau N+1 Grenzbilder benötigt. Szenen bleiben erhalten.")
        if request.prompts is not None and len(request.prompts) != count:
            raise ValueError("Genau eine Beschreibung pro Übergang angeben.")
        if request.durations is not None and len(request.durations) != count:
            raise ValueError("Genau eine Dauer pro Übergang angeben.")
        if any(j.get("project_id") == p.id and j.get("state") in {"queued", "submitting", "running", "unknown", "interrupted"}
               for j in store.list("jobs")):
            raise HTTPException(409, "Laufenden oder ungeklärten Auftrag zuerst abschließen.")
        for aid in request.asset_ids:
            _image(store, aid, p.format)
        if not p.scenes:
            p.scenes = [Scene(title=f"Szene {i+1}") for i in range(count)]
        for i, scene in enumerate(p.scenes):
            before = scene.model_dump()
            scene.mode = "start_end"
            scene.start_asset_id, scene.end_asset_id = request.asset_ids[i:i+2]
            scene.predecessor_scene_id = None
            scene.source_asset_id = None
            if request.prompts is not None:
                scene.prompt = request.prompts[i]
            if request.durations is not None:
                scene.duration_s = request.durations[i]
            if scene.model_dump() != before:
                scene.stale = bool(scene.takes)
        invalidate_gates(p, "script" if request.prompts is not None else "storyboard")
        return save_project(store, p)


def assemble_variants(store, cid, key, request):
    from spotforge.api import render_manifest
    if not request.confirmed:
        raise HTTPException(409, "Erstellung der Fassungen bitte bestätigen.")
    with store.lock:
        campaign = _read(store, cid)
        motif = _motif(campaign, key)
        source = _project(store, motif.project_id)
        if source.revision != request.expected_revision:
            raise HTTPException(409, "Motiv wurde verändert. Neu laden.")
        if any(source.gates.get(g) != "approved" for g in GATES[:4]):
            raise HTTPException(409, "Motiv zuerst bis einschließlich Clips freigeben.")
        if not motif.variants:
            raise ValueError("Für dieses Motiv fehlen Zielgruppenfassungen.")
        if len(source.scenes) >= 100:
            raise ValueError("Für die Schlusskarte muss eine Szene frei bleiben (höchstens 99 Handlungsszenen).")
        render_manifest(store, source, "http://127.0.0.1")
        for variant in motif.variants:
            if variant.endcard_asset_id:
                _image(store, variant.endcard_asset_id, source.format)
        # A repeated click after a lost response returns the existing immutable cuts.
        digest = hashlib.sha256(json.dumps([v.model_dump(mode="json") for v in motif.variants], sort_keys=True).encode()).hexdigest()
        outputs = {o.variant_key: o for o in motif.outputs if o.source_revision == source.revision and o.variant_digest == digest}
        if len(outputs) == len(motif.variants):
            return {"campaign": campaign, "projects": [_project(store, outputs[v.key].project_id) for v in motif.variants]}
        background = None
        if any(not v.endcard_asset_id for v in motif.variants):
            data = io.BytesIO()
            color = source.brand_snapshot.colors.get("background", "#10131a") if source.brand_snapshot else "#10131a"
            Image.new("RGB", (source.format.width, source.format.height), color).save(data, "PNG")
            background = store.add_asset(data.getvalue(), "endcard-background.png", "image", "image/png").id
        projects = []
        for variant in motif.variants:
            pid = uuid5(NAMESPACE_URL, f"spotforge:{cid}:{key}:{source.revision}:{digest}:{variant.key}").hex
            if store.path("projects", pid).exists():
                output = _project(store, pid)
            else:
                output = source.model_copy(deep=True)
                output.id, output.revision, output.created_at = pid, 1, now()
                output.title = f"{source.title} · {variant.title}"[:200]
                output.brief = (source.brief + f"\nZielgruppe: {variant.audience}")[:8000]
                text = "\n".join(v for v in (variant.endcard_text, variant.cta) if v)
                output.scenes.append(Scene(title="Schlusskarte", mode="local", duration_s=variant.duration_s,
                                           source_asset_id=variant.endcard_asset_id or background, onscreen_text=text))
                output.render = {}
                output.gates = dict.fromkeys(GATES, "todo")
                store.write("projects", output.id, output)
            projects.append(output)
            if variant.key not in outputs:
                motif.outputs.append(VariantOutput(variant_key=variant.key, project_id=pid, source_revision=source.revision, variant_digest=digest))
        campaign.revision += 1
        store.write("campaigns", cid, campaign)
        return {"campaign": campaign, "projects": projects}


def export_document(store, campaign):
    motifs = []
    warnings = ["JSON enthält den Produktionsplan und lokale Asset-IDs, keine Mediendateien, Takes, Tonspuren oder Freigaben. Auf einem anderen Rechner Bilder erneut zuordnen."]
    for motif in campaign.motifs:
        p = _project(store, motif.project_id)
        labels = {v["scene_id"]: v for v in motif.frame_labels}
        shots = []
        for scene in p.scenes:
            label = labels.get(scene.id, {})
            shots.append(ShotInput(title=scene.title, prompt=scene.prompt, duration_s=scene.duration_s,
                                   start_asset_id=scene.start_asset_id, end_asset_id=scene.end_asset_id,
                                   start_frame=label.get("start_frame", ""), end_frame=label.get("end_frame", "")))
        motifs.append(MotifInput(key=motif.key, title=p.title[:120], brief=p.brief, script=p.script,
                                 format=p.format, brand_id=p.brand_snapshot.id if p.brand_snapshot else None,
                                 brand_version=p.brand_snapshot.version if p.brand_snapshot else None,
                                 shots=shots, variants=motif.variants))
    return {"document": CampaignInput(title=campaign.title, brief=campaign.brief, format=campaign.format,
                                      brand_id=campaign.brand_id, brand_version=campaign.brand_version, motifs=motifs),
            "warnings": warnings}


def create_router(store):
    router = APIRouter()

    @router.get("/campaigns")
    def listing():
        return sorted(store.list("campaigns"), key=lambda c: c["created_at"], reverse=True)

    @router.post("/campaigns")
    def create(body: CampaignInput):
        return create_campaign(store, body)

    @router.post("/campaigns/import/preview")
    def preview(body: dict):
        if set(body) != {"document"}:
            raise ValueError("Ein Kampagnen-Dokument wird benötigt.")
        if len(json.dumps(body)) > 2_000_000:
            raise HTTPException(413, "Kampagnen-Dokument ist zu groß.")
        document = CampaignInput.model_validate(body["document"])
        with store.lock:
            brand, missing = inspect_document(store, document)
            if brand:
                document.brand_version = brand.version
            result = {"preview_id": new_id(), "campaign_id": new_id(), "document": document.model_dump(mode="json"),
                      "motifs_count": len(document.motifs), "variants_count": sum(len(m.variants) for m in document.motifs),
                      "warnings": ["Es entstehen neue Entwürfe ohne Freigaben. Es werden keine KI-Aufträge gestartet.",
                                   "Dateinamen dienen als Platzhalter. Bilder separat hochladen und zuordnen."],
                      "missing_frames": missing}
            store.write("campaign_previews", result["preview_id"], result)
            return result

    @router.post("/campaigns/import/commit")
    def commit(body: dict):
        if set(body) != {"preview_id", "confirmed"} or body.get("confirmed") is not True:
            raise HTTPException(409, "Geprüften Import ausdrücklich bestätigen.")
        with store.lock:
            preview = store.read("campaign_previews", body["preview_id"])
            return create_campaign(store, CampaignInput.model_validate(preview["document"]), preview["campaign_id"])

    @router.get("/campaigns/{cid}")
    def detail(cid: str):
        with store.lock:
            campaign = _read(store, cid)
            ids = {m.project_id for m in campaign.motifs} | {o.project_id for m in campaign.motifs for o in m.outputs}
            return {**campaign.model_dump(mode="json"), "projects": {pid: _project(store, pid) for pid in ids}}

    @router.patch("/campaigns/{cid}")
    def update(cid: str, body: dict):
        if set(body) - {"expected_revision", "title", "brief"}:
            raise ValueError("Unbekannte Kampagnenfelder.")
        with store.lock:
            campaign = _read(store, cid)
            if body.get("expected_revision") != campaign.revision:
                raise HTTPException(409, "Kampagne wurde verändert. Neu laden.")
            # Validate editable fields with the bounded input contract.
            fields = CampaignInput(title=body.get("title", campaign.title), brief=body.get("brief", campaign.brief))
            campaign.title, campaign.brief = fields.title, fields.brief
            campaign.revision += 1
            store.write("campaigns", cid, campaign)
            return campaign

    @router.post("/campaigns/{cid}/motifs")
    def add_motif(cid: str, body: dict):
        if set(body) != {"expected_revision", "motif"}:
            raise ValueError("Motiv und Kampagnenrevision erforderlich.")
        item = MotifInput.model_validate(body["motif"])
        with store.lock:
            campaign = _read(store, cid)
            if campaign.revision != body["expected_revision"]:
                raise HTTPException(409, "Kampagne wurde verändert. Neu laden.")
            if len(campaign.motifs) >= 30 or any(m.key == item.key for m in campaign.motifs):
                raise ValueError("Motivschlüssel bereits vorhanden oder 30 Motive erreicht.")
            document = CampaignInput(title=campaign.title, format=campaign.format, brand_id=campaign.brand_id,
                                     brand_version=campaign.brand_version, motifs=[item])
            brand, _ = inspect_document(store, document)
            motif, p = _build_motif(campaign, item, _brand(store, item) if item.brand_id else brand)
            campaign.motifs.append(motif)
            campaign.revision += 1
            store.write("projects", p.id, p)
            store.write("campaigns", cid, campaign)
            return campaign

    @router.post("/campaigns/{cid}/motifs/{key}/frames")
    def frames(cid: str, key: str, body: FrameRequest):
        return boundary_frames(store, cid, key, body)

    @router.patch("/campaigns/{cid}/motifs/{key}")
    def edit_motif(cid: str, key: str, body: dict):
        if set(body) != {"expected_revision", "variants"}:
            raise ValueError("Fassungen und Kampagnenrevision erforderlich.")
        variants = MotifInput(key=key, title="Validation", variants=body["variants"]).variants
        with store.lock:
            campaign = _read(store, cid)
            if body["expected_revision"] != campaign.revision:
                raise HTTPException(409, "Kampagne wurde verändert. Neu laden.")
            motif = _motif(campaign, key)
            project = _project(store, motif.project_id)
            for variant in variants:
                if variant.endcard_asset_id:
                    _image(store, variant.endcard_asset_id, project.format)
            motif.variants = variants
            campaign.revision += 1
            store.write("campaigns", cid, campaign)
            return campaign

    @router.post("/campaigns/{cid}/motifs/{key}/variants")
    def variants(cid: str, key: str, body: AssembleRequest):
        return assemble_variants(store, cid, key, body)

    @router.get("/campaigns/{cid}/export")
    def export(cid: str):
        with store.lock:
            return export_document(store, _read(store, cid))

    return router
