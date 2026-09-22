"""Durable, explicitly confirmed fal image-to-video jobs for the local studio.

Submission is never retried: a lost submission response is an unknown paid state.
Polling and downloads may be resumed independently using the saved request ID.
"""
import base64
import hashlib
import io
import os
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, HTTPException
from PIL import Image
from pydantic import BaseModel, ConfigDict

from spotforge.models import Asset, Job, Project, Take, now

MODEL = "fal-ai/kling-video/v2.5-turbo/pro/image-to-video"
ACTIVE = {"queued", "submitting", "running", "interrupted", "unknown"}
MAX_IMAGE = 20 * 1024 * 1024
MAX_VIDEO = 250 * 1024 * 1024


class GenerationError(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


class Rejected(GenerationError):
    """Provider definitively rejected an operation."""


def capabilities():
    return [{"id": MODEL, "label": "Kling 2.5 Turbo Pro (fal)",
             "modes": ["start", "start_end"], "durations": [5, 10],
             "end_only": False, "cost": {"amount": None, "status": "unknown"},
             "configured": bool(os.environ.get("FAL_KEY")),
             "assistance": "Nur Endbild: zuerst ein Startbild auswählen und bestätigen; danach Start + Ende."}]


class FalProvider:
    """Small queue adapter; credentials never enter persisted state or errors."""
    def __init__(self, key):
        self.headers = {"Authorization": f"Key {key}"}

    def submit(self, payload):
        response = requests.post(f"https://queue.fal.run/{MODEL}",
                                 headers=self.headers, json=payload, timeout=(15, 90))
        if 400 <= response.status_code < 500 and response.status_code not in {408, 499}:
            raise Rejected(f"Anbieter hat die Anfrage abgelehnt (HTTP {response.status_code}).", 502)
        response.raise_for_status()
        data = response.json()
        request_id = data.get("request_id", "")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,200}", request_id):
            raise ValueError("Missing provider request ID")
        return request_id

    def poll(self, request_id):
        # fal queue operations use the model owner/name, not its endpoint suffix.
        response = requests.get(f"https://queue.fal.run/fal-ai/kling-video/requests/{request_id}/status",
                                headers=self.headers, timeout=(15, 30))
        response.raise_for_status()
        return response.json().get("status")

    def result(self, request_id):
        response = requests.get(f"https://queue.fal.run/fal-ai/kling-video/requests/{request_id}",
                                headers=self.headers, timeout=(15, 60))
        if response.status_code == 422:
            raise Rejected("Der Anbieter konnte den Clip nicht erzeugen.", 502)
        response.raise_for_status()
        return response.json()["video"]["url"]

    def download(self, url):
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if (parsed.scheme != "https" or parsed.username or parsed.password or
                not (hostname == "fal.media" or hostname.endswith(".fal.media") or
                     hostname == "storage.googleapis.com")):
            raise ValueError("Unexpected provider media URL")
        # No authorization header is forwarded to the media host, or to redirects.
        with requests.get(url, stream=True, allow_redirects=False, timeout=(15, 90)) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError("Unexpected media response")
            chunks, size = [], 0
            for chunk in response.iter_content(1024 * 1024):
                size += len(chunk)
                if size > MAX_VIDEO:
                    raise ValueError("Generated video too large")
                chunks.append(chunk)
            return b"".join(chunks)


def _image_bytes(store, asset_id):
    asset = Asset.model_validate(store.read("assets", asset_id))
    if asset.kind != "image" or asset.size > MAX_IMAGE:
        raise GenerationError("Referenz muss eine Bilddatei unter 20 MB sein.")
    content = store.asset_path(asset_id).read_bytes()
    if len(content) > MAX_IMAGE or hashlib.sha256(content).hexdigest() != asset.sha256:
        raise GenerationError("Referenzbild wurde verändert oder ist zu groß.")
    try:
        with Image.open(io.BytesIO(content)) as im:
            if im.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("format")
            mime = Image.MIME[im.format]
            im.verify()
    except Exception as exc:
        raise GenerationError("Referenzbild ist kein gültiges PNG, JPEG oder WebP.") from exc
    return content, mime


def _data_uri(store, asset_id, format=None):
    content, mime = _image_bytes(store, asset_id)
    if format:
        with Image.open(io.BytesIO(content)) as im:
            ratio = im.width / im.height
            if abs(ratio / (format["width"] / format["height"]) - 1) > 0.02:
                raise GenerationError("Bildformat passt nicht zum Projekt. Bild vorher passend zuschneiden; keine automatische Verzerrung.")
    return f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"


def _scene(project, scene_id):
    scene = next((s for s in project.scenes if s.id == scene_id), None)
    if scene is None:
        raise GenerationError("Szene nicht gefunden.", 404)
    return scene


def _invalidate_descendants(project, scene_id):
    pending = [scene_id]
    seen = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        for scene in project.scenes:
            if scene.predecessor_scene_id == current:
                scene.stale = True
                pending.append(scene.id)


class GenerationService:
    def __init__(self, store, provider_factory=FalProvider, poll_seconds=3, timeout_seconds=900):
        self.store = store
        self.provider_factory = provider_factory
        self.poll_seconds = poll_seconds
        self.timeout_seconds = timeout_seconds
        self.workers = set()
        self.worker_lock = threading.Lock()

    def recover(self):
        """Called once at API startup, before any workers are launched."""
        with self.store.lock:
            for raw in self.store.list("jobs"):
                job = Job.model_validate(raw)
                if job.kind == "generation" and job.state in {"queued", "submitting", "running"}:
                    job.state = "interrupted" if job.provider_request_id else ("failed" if job.state == "queued" else "unknown")
                    job.error = "Lauf unterbrochen. Bekannte Anbieter-ID fortsetzen; unklare Übermittlung nicht erneut absenden."
                    self._save(job)

    def _save(self, job):
        job.updated_at = now()
        self.store.write("jobs", job.id, job)

    def start(self, project_id, scene_id, confirmed, expected_revision):
        if not confirmed:
            raise GenerationError("Kostenpflichtige Generierung muss ausdrücklich bestätigt werden.", 409)
        if not os.environ.get("FAL_KEY"):
            raise GenerationError("FAL_KEY fehlt. Es wurde kein Anbieterauftrag gestartet.", 409)
        with self.store.lock:
            with self.worker_lock:
                if self.workers:
                    raise GenerationError("Ein Generierungsauftrag läuft bereits.", 409)
            project = Project.model_validate(self.store.read("projects", project_id))
            if project.revision != expected_revision:
                raise GenerationError("Projekt wurde geändert. Bitte neu laden und erneut bestätigen.", 409)
            if project.recipe == "spot" and any(project.gates.get(g) != "approved" for g in ("concept", "script", "storyboard")):
                raise GenerationError("Konzept, Skript und Storyboard müssen freigegeben sein.", 409)
            if any(j.get("kind") == "generation" and j.get("state") in ACTIVE for j in self.store.list("jobs")):
                raise GenerationError("Ein Generierungsauftrag läuft oder benötigt Klärung. Diesen zuerst fortsetzen.", 409)
            scene = _scene(project, scene_id)
            if scene.model != MODEL:
                raise GenerationError("Dieses Modell wird vom aktuellen Anbieteradapter nicht unterstützt.")
            if scene.mode not in {"start", "start_end", "end"}:
                raise GenerationError("Dieses Modell benötigt ein Startbild; Text-zu-Video wird nicht unterstützt.")
            if scene.duration_s not in {5, 10}:
                raise GenerationError("Dieses Modell unterstützt genau 5 oder 10 Sekunden.")
            if not scene.prompt.strip():
                raise GenerationError("Für die Generierung fehlt eine Szenenbeschreibung.")
            if scene.mode in {"start_end", "end"} and not scene.end_asset_id:
                raise GenerationError("Endbild fehlt.")
            if scene.mode == "start" and scene.end_asset_id:
                raise GenerationError("Endbild vorhanden: bitte ausdrücklich Start + Ende wählen.")
            predecessor = None
            if scene.predecessor_scene_id:
                prev = _scene(project, scene.predecessor_scene_id)
                if project.scenes.index(prev) >= project.scenes.index(scene) or prev.stale:
                    raise GenerationError("Vorgänger muss eine frühere, aktuelle Szene sein.")
                take = next((t for t in prev.takes if t.id == prev.selected_take_id), None)
                if take is None:
                    raise GenerationError("Bitte zuerst einen fertigen Vorgänger-Take auswählen.")
                asset = Asset.model_validate(self.store.read("assets", take.asset_id))
                if asset.kind != "video":
                    raise GenerationError("Vorgänger-Take muss ein Video sein.")
                self.store.asset_path(asset.id)
                predecessor = {"scene_id": prev.id, "take_id": take.id, "asset_id": asset.id, "sha256": asset.sha256}
                if scene.start_asset_id:
                    raise GenerationError("Startbild und Vorgänger zugleich: bitte eine Frame-Quelle auswählen.")
            if not scene.start_asset_id and not predecessor:
                raise GenerationError("Startbild erforderlich. Nur-Endbild-Assistent: Startbild auswählen und bestätigen, dann Start + Ende erzeugen.")
            assets = {}
            for aid in (scene.start_asset_id, scene.end_asset_id):
                if aid:
                    _data_uri(self.store, aid, project.format.model_dump())
                    assets[aid] = self.store.read("assets", aid)
            brand = project.brand_snapshot.model_dump(mode="json") if project.brand_snapshot else {}
            prompt = scene.prompt.strip()
            if brand.get("visual_style"):
                prompt += "\nVisual style: " + brand["visual_style"]
            job = Job(project_id=project_id, scene_id=scene_id, kind="generation", input_snapshot={
                "project_revision": project.revision, "scene": scene.model_dump(mode="json"),
                "brand_snapshot": brand, "assets": assets, "predecessor": predecessor,
                "format": project.format.model_dump(),
                "effective_prompt": prompt, "effective_mode": "start_end" if scene.end_asset_id else "start",
                "start_asset_id": scene.start_asset_id, "end_asset_id": scene.end_asset_id,
                "cost": {"amount": None, "status": "unknown", "confirmed": True},
            })
            self._save(job)
            return job

    def resume(self, job_id, confirmed):
        if not confirmed:
            raise GenerationError("Fortsetzung bitte bestätigen; ein neuer Auftrag wird nicht erzeugt.", 409)
        if not os.environ.get("FAL_KEY"):
            raise GenerationError("FAL_KEY fehlt.", 409)
        with self.store.lock:
            job = Job.model_validate(self.store.read("jobs", job_id))
            if job.kind != "generation":
                raise GenerationError("Kein Generierungsauftrag.")
            if job.state == "complete":
                return job
            if not job.provider_request_id:
                raise GenerationError("Keine Anbieter-ID vorhanden. Status muss geklärt werden; keine automatische erneute Übermittlung.", 409)
            if job.state == "failed":
                raise GenerationError("Anbieterauftrag ist fehlgeschlagen. Für einen neuen Take separat generieren.", 409)
            return job

    def launch(self, job_id, resume=False):
        with self.worker_lock:
            if job_id in self.workers:
                return
            if self.workers:
                raise GenerationError("Ein anderer Generierungsauftrag läuft bereits.", 409)
            self.workers.add(job_id)
        thread = threading.Thread(target=self._run_guarded, args=(job_id, resume), daemon=True)
        thread.start()

    def _run_guarded(self, job_id, resume):
        try:
            self.run(job_id, resume)
        finally:
            with self.worker_lock:
                self.workers.discard(job_id)

    def _extract_predecessor(self, job):
        predecessor = job.input_snapshot["predecessor"]
        source = self.store.asset_path(predecessor["asset_id"])
        if hashlib.sha256(source.read_bytes()).hexdigest() != predecessor["sha256"]:
            raise GenerationError("Vorgängerdatei wurde seit Bestätigung verändert.")
        with tempfile.TemporaryDirectory(prefix="spotforge-frame-") as tmp:
            target = Path(tmp) / "last.png"
            subprocess.run(["ffmpeg", "-v", "error", "-sseof", "-1", "-i", str(source),
                            "-update", "1", "-y", str(target)], check=True, capture_output=True, timeout=90)
            asset = self.store.add_asset(target.read_bytes(), "predecessor-last.png", "image", "image/png")
        job.input_snapshot["start_asset_id"] = asset.id
        job.input_snapshot["assets"][asset.id] = asset.model_dump(mode="json")
        with self.store.lock:
            self._save(job)

    def run(self, job_id, resume=False):
        job = Job.model_validate(self.store.read("jobs", job_id))
        if job.state == "complete":
            return
        try:
            key = os.environ.get("FAL_KEY")
            if not key:
                raise GenerationError("FAL_KEY fehlt. Keine neue Anbieteranfrage gesendet.", 409)
            provider = self.provider_factory(key)
            if not resume:
                if job.state != "queued" or job.provider_request_id:
                    raise GenerationError("Auftrag wurde bereits übermittelt; nur fortsetzen erlaubt.")
                if job.input_snapshot["predecessor"]:
                    self._extract_predecessor(job)
                payload = {"prompt": job.input_snapshot["effective_prompt"],
                           "image_url": _data_uri(self.store, job.input_snapshot["start_asset_id"], job.input_snapshot["format"]),
                           "duration": str(int(job.input_snapshot["scene"]["duration_s"]))}
                if job.input_snapshot["end_asset_id"]:
                    payload["tail_image_url"] = _data_uri(self.store, job.input_snapshot["end_asset_id"], job.input_snapshot["format"])
                job.state = "submitting"
                with self.store.lock:
                    current = Project.model_validate(self.store.read("projects", job.project_id))
                    if current.revision != job.input_snapshot["project_revision"]:
                        job.state = "queued"
                        raise GenerationError("Projekt wurde vor der Übermittlung verändert. Bitte neu bestätigen.", 409)
                    if current.recipe == "spot" and any(current.gates.get(g) != "approved" for g in ("concept", "script", "storyboard")):
                        job.state = "queued"
                        raise GenerationError("Freigaben wurden vor der Übermittlung geändert.", 409)
                    self._save(job)
                job.provider_request_id = provider.submit(payload)
                job.state = "running"
                with self.store.lock:
                    self._save(job)
            elif not job.provider_request_id:
                raise GenerationError("Keine Anbieter-ID für Wiederaufnahme vorhanden.")
            job.state, job.error = "running", None
            with self.store.lock:
                self._save(job)
            deadline = time.monotonic() + self.timeout_seconds
            while True:
                status = provider.poll(job.provider_request_id)
                if status == "COMPLETED":
                    break
                if status in {"FAILED", "ERROR", "CANCELLED"}:
                    raise Rejected("Anbieterauftrag ist fehlgeschlagen oder wurde abgebrochen.", 502)
                if status not in {"IN_QUEUE", "IN_PROGRESS"}:
                    raise ValueError("Unexpected queue state")
                if time.monotonic() >= deadline:
                    raise TimeoutError()
                time.sleep(self.poll_seconds)
            content = provider.download(provider.result(job.provider_request_id))
            self._finish(job, content)
        except Exception as exc:
            if isinstance(exc, Rejected):
                job.state, job.error = "failed", str(exc)
            elif job.provider_request_id:
                job.state, job.error = "interrupted", "Anbieter-ID gespeichert. Abfrage oder Download unterbrochen; gefahrlos fortsetzen."
            elif job.state == "submitting":
                job.state, job.error = "unknown", "Übermittlung unklar. Keine Wiederholung: Auftrag beim Anbieter prüfen."
            else:
                job.state = "failed"
                job.error = str(exc) if isinstance(exc, GenerationError) else "Vorbereitung fehlgeschlagen. Medien und lokale FFmpeg-Installation prüfen."
            with self.store.lock:
                self._save(job)

    def _finish(self, job, content):
        # Validate the returned binary before promoting it to a project take.
        with tempfile.TemporaryDirectory(prefix="spotforge-video-") as tmp:
            target = Path(tmp) / "clip.mp4"
            target.write_bytes(content)
            probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                    "stream=codec_type", "-of", "csv=p=0", str(target)],
                                   check=True, capture_output=True, text=True, timeout=30)
            if "video" not in probe.stdout:
                raise ValueError("No video stream")
        with self.store.lock:
            project = Project.model_validate(self.store.read("projects", job.project_id))
            scene = _scene(project, job.scene_id)
            # Crash after project write but before job write: reuse the saved take.
            existing = next((t for t in scene.takes if t.provider_request_id == job.provider_request_id), None)
            if existing:
                take = existing
            else:
                asset = self.store.add_asset(content, f"{scene.id}-take.mp4", "video", "video/mp4")
                snap = job.input_snapshot
                take = Take(asset_id=asset.id, model=MODEL, prompt=snap["effective_prompt"],
                            parameters={"duration": snap["scene"]["duration_s"], "mode": snap["effective_mode"],
                                        "input_assets": {aid: data["sha256"] for aid, data in snap["assets"].items()}},
                            start_asset_id=snap["start_asset_id"], end_asset_id=snap["end_asset_id"],
                            predecessor_take_id=(snap["predecessor"] or {}).get("take_id"),
                            brand_snapshot=snap["brand_snapshot"], provider_request_id=job.provider_request_id,
                            cost=snap["cost"])
                unchanged = scene.model_dump(mode="json") == snap["scene"] and (
                    project.brand_snapshot.model_dump(mode="json") if project.brand_snapshot else {}) == snap["brand_snapshot"]
                if snap["predecessor"]:
                    prev = next((s for s in project.scenes if s.id == snap["predecessor"]["scene_id"]), None)
                    unchanged = unchanged and prev is not None and not prev.stale and prev.selected_take_id == snap["predecessor"]["take_id"]
                scene.takes.append(take)
                if unchanged and not scene.selected_take_id:
                    scene.selected_take_id = take.id
                    scene.stale = False
                    _invalidate_descendants(project, scene.id)
                elif not unchanged:
                    scene.stale = True
                project.gates.update({"clips": "pending", "final": "pending"})
                project.render = {}
                project.revision += 1
                self.store.write("projects", project.id, project)
            job.state, job.progress, job.error = "complete", 1, None
            job.result = {"take_id": take.id, "asset_id": take.asset_id}
            self._save(job)


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: bool
    expected_revision: int


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: bool


def create_router(store):
    router = APIRouter()
    service = GenerationService(store)
    service.recover()

    def perform(action):
        try:
            return action()
        except GenerationError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, "Projekt, Job oder Asset nicht gefunden.") from exc
        except ValueError as exc:
            raise HTTPException(422, "Ungültige Projekt- oder Assetdaten.") from exc

    @router.get("/models")
    def models():
        return capabilities()

    @router.post("/projects/{project_id}/scenes/{scene_id}/generate", status_code=202)
    def generate(project_id: str, scene_id: str, body: GenerateRequest):
        job = perform(lambda: service.start(project_id, scene_id, body.confirmed, body.expected_revision))
        perform(lambda: service.launch(job.id))
        return job

    @router.post("/jobs/{job_id}/resume", status_code=202)
    def resume(job_id: str, body: ResumeRequest):
        job = perform(lambda: service.resume(job_id, body.confirmed))
        if job.state != "complete":
            perform(lambda: service.launch(job.id, resume=True))
        return job

    return router
