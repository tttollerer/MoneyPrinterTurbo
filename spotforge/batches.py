"""Durable sequential generation batches over explicitly reviewed project inputs."""
import copy
import hashlib
import json
import threading
from contextlib import asynccontextmanager

from fastapi import APIRouter, HTTPException
from pydantic import Field, StrictBool

from spotforge.generation import GenerationError
from spotforge.models import Asset, Job, Model, Project, new_id, now

ACTIVE = {"queued", "submitting", "running"}
BUSY = {"running", "pausing", "cancelling"}


class PreviewRequest(Model):
    project_ids: list[str] = Field(min_length=1, max_length=20)


class ConfirmRequest(Model):
    confirmed: StrictBool = False
    input_digest: str


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def public(batch):
    return {key: value for key, value in batch.items() if key not in {"inputs", "expected_projects", "expected_assets"}}


class BatchService:
    def __init__(self, store, generation=None, poll_seconds=0.2):
        self.store = store
        if generation is None:
            from spotforge.generation import get_service
            generation = get_service(store)
        self.generation = generation
        self.poll_seconds = poll_seconds
        self.stop = threading.Event()
        self.workers = set()
        self.recover()

    def save(self, batch):
        batch["updated_at"] = now()
        self.store.write("batches", batch["id"], batch)

    def asset_hashes(self, project):
        ids = set()
        for scene in project["scenes"]:
            ids.update(value for value in (scene.get("start_asset_id"), scene.get("end_asset_id"), scene.get("source_asset_id")) if value)
            if scene.get("selected_take_id"):
                selected = next((take for take in scene["takes"] if take["id"] == scene["selected_take_id"]), None)
                if not selected:
                    raise ValueError("Ausgewählter Take fehlt.")
                ids.add(selected["asset_id"])
        brand = project.get("brand_snapshot") or {}
        ids.update(value for value in (brand.get("font_asset_id"), brand.get("logo_asset_id")) if value)
        ids.update(brand.get("document_asset_ids", []))
        values = {}
        for aid in sorted(ids):
            asset = Asset.model_validate(self.store.read("assets", aid))
            with self.store.asset_path(aid).open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != asset.sha256:
                raise ValueError("Eine Eingabedatei wurde verändert.")
            values[aid] = {"sha256": actual, "kind": asset.kind}
        return values

    def preview(self, project_ids):
        if not 1 <= len(project_ids) <= 20 or len(set(project_ids)) != len(project_ids):
            raise HTTPException(422, "Bitte 1 bis 20 unterschiedliche Projekte auswählen.")
        with self.store.lock:
            projects, assets, items = {}, {}, []
            for pid in project_ids:
                p = Project.model_validate(self.store.read("projects", pid)).model_dump(mode="json")
                projects[pid] = p
                media_error = None
                try:
                    assets[pid] = self.asset_hashes(p)
                except (ValueError, FileNotFoundError):
                    media_error = "Projektmedien fehlen oder wurden verändert. Bitte einzeln prüfen."
                    assets[pid] = {}
                generating = {scene["id"] for scene in p["scenes"] if scene["stale"] or not (scene.get("selected_take_id") or scene.get("source_asset_id"))}
                if not p["scenes"]:
                    items.append({"id": new_id(), "project_id": pid, "project_title": p["title"], "scene_id": None,
                                  "scene_title": "Keine Szenen", "revision": p["revision"], "model": None, "mode": None,
                                  "duration_s": 0, "action": "blocked", "status": "blocked", "reason": "Projekt hat keine Szenen.", "job_id": None})
                for scene in p["scenes"]:
                    item = {"id": new_id(), "project_id": pid, "project_title": p["title"], "scene_id": scene["id"],
                            "scene_title": scene["title"], "revision": p["revision"], "model": scene["model"], "mode": scene["mode"],
                            "duration_s": scene["duration_s"], "action": "generate", "status": "pending", "reason": None, "job_id": None}
                    if media_error:
                        item.update(action="blocked", status="blocked", reason=media_error)
                    elif scene.get("predecessor_scene_id") in generating:
                        item.update(action="blocked", status="blocked", reason="Vorgänger muss zuerst einzeln fertiggestellt werden. Dynamische Ketten werden im Batch nicht neu geplant.")
                    elif not scene["stale"] and (scene.get("selected_take_id") or scene["mode"] == "local" and scene.get("source_asset_id")):
                        item.update(action="skip", status="skipped", reason="Bereits ausgewählter, aktueller Take oder lokales Medium.")
                    else:
                        try:
                            meta = self.generation.preflight(pid, scene["id"], p["revision"], check_active=False)
                            item["model"] = meta.get("model", item["model"])
                        except GenerationError as exc:
                            item.update(action="blocked", status="blocked", reason=str(exc))
                        except (ValueError, FileNotFoundError):
                            item.update(action="blocked", status="blocked", reason="Ungültige oder fehlende Szene-/Markeneingaben.")
                    items.append(item)
            if len(items) > 200:
                raise HTTPException(422, "Ein Batch unterstützt höchstens 200 Szenen. Auswahl verkleinern.")
            inputs = {"project_ids": project_ids, "projects": projects, "assets": assets,
                      "actions": [{key: item[key] for key in ("project_id", "scene_id", "action")} for item in items]}
            batch = {"id": new_id(), "state": "preview", "input_digest": digest(inputs), "inputs": inputs,
                     "project_ids": project_ids, "project_revisions": {pid: p["revision"] for pid, p in projects.items()},
                     "expected_projects": copy.deepcopy(projects), "expected_assets": copy.deepcopy(assets), "items": items,
                     "blocked": sum(item["action"] == "blocked" for item in items), "skipped": sum(item["action"] == "skip" for item in items),
                     "cost": {"amount": None, "status": "unknown", "currency": None}, "error": None, "created_at": now()}
            self.save(batch)
            return batch

    def check_inputs(self, batch):
        for pid, expected in batch["expected_projects"].items():
            current = Project.model_validate(self.store.read("projects", pid)).model_dump(mode="json")
            if current != expected or self.asset_hashes(current) != batch["expected_assets"][pid]:
                raise HTTPException(409, "Projekte oder Medien wurden außerhalb dieses Batches geändert. Neue Vorschau und Bestätigung erforderlich.")

    def _claim(self, batch):
        owner = getattr(self.store, "batch_owner", None)
        if owner and owner != batch["id"]:
            raise HTTPException(409, "Ein anderer Batch läuft bereits.")
        for job in self.store.list("jobs"):
            if job.get("kind") == "generation" and job.get("state") in ACTIVE | {"interrupted", "unknown"} and job.get("input_snapshot", {}).get("batch_id") != batch["id"]:
                raise HTTPException(409, "Ein Einzelauftrag oder anderer Anbieterauftrag muss zuerst beendet oder geklärt werden.")
        self.store.batch_owner = batch["id"]
        self.store.batch_stop_requested = False

    def confirm(self, batch_id, confirmed, input_digest, resume=False):
        if not confirmed:
            raise HTTPException(409, "Kostenpflichtigen Batch trotz unbekannter Gesamtkosten ausdrücklich bestätigen.")
        with self.store.lock:
            if self.stop.is_set():
                raise HTTPException(409, "Batchdienst wird beendet.")
            batch = self.store.read("batches", batch_id)
            if input_digest != batch["input_digest"] or digest(batch["inputs"]) != input_digest:
                raise HTTPException(409, "Bestätigung gehört nicht zu dieser Vorschau.")
            allowed = {"paused"} if resume else {"preview"}
            if batch["state"] not in allowed:
                raise HTTPException(409, "Batch kann in diesem Zustand nicht gestartet werden.")
            if batch["blocked"]:
                raise HTTPException(409, "Vorschau enthält blockierte Szenen. Eingaben korrigieren und neue Vorschau erstellen.")
            # A known completed job may have finished after shutdown/pause. Its
            # exact, constrained output delta is the only accepted input update.
            for item in batch["items"]:
                if item["status"] == "unknown" and not item.get("job_id"):
                    raise HTTPException(409, "Mehrdeutige Anbieteraufträge ohne eindeutige Zuordnung. Manuell klären; keine erneute Übermittlung.")
                if item.get("job_id") and item["status"] not in {"complete", "skipped", "cancelled"}:
                    job = Job.model_validate(self.store.read("jobs", item["job_id"]))
                    if job.state == "complete":
                        self.accept_result(batch, item, job)
                    elif job.state == "unknown" and not job.provider_request_id:
                        raise HTTPException(409, "Unklarer bezahlter Auftrag: Anbieter-ID zuerst einzeln klären. Keine erneute Übermittlung.")
                    elif job.state == "failed":
                        if job.result.get("not_submitted"):
                            item.update(status="pending", job_id=None, reason=None)
                        else:
                            raise HTTPException(409, "Fehlgeschlagene Aufträge werden nicht automatisch erneut bezahlt. Neue Vorschau erforderlich.")
                    elif job.state in {"interrupted", "unknown"}:
                        item["status"] = job.state
            self.check_inputs(batch)
            if any(item["status"] == "needs_review" for item in batch["items"]):
                raise HTTPException(409, "Neue Takes zuerst ausdrücklich auswählen und anschließend neue Batchvorschau erstellen.")
            self._claim(batch)
            batch.update(state="running", error=None)
            try:
                self.save(batch)
            except Exception:
                self.store.batch_owner = None
                raise
            return batch

    def control(self, batch_id, cancel=False):
        with self.store.lock:
            batch = self.store.read("batches", batch_id)
            if batch["state"] in {"complete", "cancelled"}:
                return batch
            if cancel:
                for item in batch["items"]:
                    if item["status"] == "pending" and not item.get("job_id"):
                        item["status"] = "cancelled"
                batch["state"] = "cancelling" if batch_id in self.workers else "cancelled"
            else:
                if batch["state"] == "preview":
                    raise HTTPException(409, "Batch wurde noch nicht gestartet.")
                batch["state"] = "pausing" if batch_id in self.workers else "paused"
            if getattr(self.store, "batch_owner", None) == batch_id:
                self.store.batch_stop_requested = True
                if batch_id not in self.workers:
                    self.store.batch_owner = None
            self.save(batch)
            return batch

    def accept_result(self, batch, item, job):
        if job.input_snapshot.get("batch_id") != batch["id"] or job.project_id != item["project_id"] or job.scene_id != item["scene_id"]:
            raise HTTPException(409, "Auftrag gehört nicht zu diesem Batch-Eintrag.")
        pid = item["project_id"]
        expected = copy.deepcopy(batch["expected_projects"][pid])
        current = Project.model_validate(self.store.read("projects", pid)).model_dump(mode="json")
        scene = next((scene for scene in current["scenes"] if scene["id"] == item["scene_id"]), None)
        before = next((scene for scene in expected["scenes"] if scene["id"] == item["scene_id"]), None)
        take = next((take for take in (scene or {}).get("takes", []) if take["id"] == job.result.get("take_id")), None)
        if not take or not before:
            raise HTTPException(409, "Batch-Ergebnis fehlt oder Szene wurde verändert.")
        before["takes"].append(take)
        if not before["selected_take_id"]:
            before.update(selected_take_id=take["id"], stale=False)
            visited = {before["id"]}
            for _ in expected["scenes"]:
                for descendant in expected["scenes"]:
                    if descendant.get("predecessor_scene_id") in visited:
                        descendant["stale"] = True
                        visited.add(descendant["id"])
        if expected["recipe"] == "spot":
            expected["gates"].update(clips="todo", final="todo")
        expected["render"] = {}
        expected["revision"] += 1
        if current != expected:
            raise HTTPException(409, "Projekt wurde während der Generierung außerhalb dieses Batches geändert. Ergebnis erhalten; neue Vorschau erforderlich.")
        batch["expected_projects"][pid] = current
        batch["expected_assets"][pid] = self.asset_hashes(current)
        if scene["stale"]:
            item.update(status="needs_review", reason="Neuer Take vorhanden; Auswahl muss ausdrücklich geprüft werden.")
        else:
            item.update(status="complete", reason=None)

    def recover(self):
        with self.store.lock:
            for batch in self.store.list("batches"):
                if batch["state"] not in BUSY:
                    continue
                for item in batch["items"]:
                    if item["status"] in {"pending", "running"} and not item.get("job_id"):
                        matches = [job for job in self.store.list("jobs") if job.get("input_snapshot", {}).get("batch_id") == batch["id"] and job.get("scene_id") == item["scene_id"] and job.get("project_id") == item["project_id"] and not job.get("result", {}).get("not_submitted")]
                        if len(matches) == 1:
                            item["job_id"] = matches[0]["id"]
                        elif not matches:
                            item["status"] = "pending"
                        else:
                            item.update(status="unknown", reason="Mehrere Anbieteraufträge gefunden. Manuelle Klärung nötig.")
                batch.update(state="cancelled" if batch["state"] == "cancelling" else "paused",
                             error="Neustart: Batch pausiert. Bekannte Aufträge werden nur nach erneuter Bestätigung abgefragt.")
                self.save(batch)

    def launch(self, batch_id):
        with self.store.lock:
            if batch_id in self.workers:
                return
            self.workers.add(batch_id)
        threading.Thread(target=self.run, args=(batch_id,), daemon=True).start()

    def generation_busy(self):
        lock = getattr(self.generation, "worker_lock", None)
        if lock is None:
            return False
        with lock:
            return bool(self.generation.workers)

    def run(self, batch_id):
        try:
            while not self.stop.is_set():
                with self.store.lock:
                    batch = self.store.read("batches", batch_id)
                    # A previously resumed individual reconciliation can finish
                    # between confirmation and this worker acquiring the lock.
                    for item in batch["items"]:
                        if item["status"] in {"interrupted", "unknown"} and item.get("job_id"):
                            known = Job.model_validate(self.store.read("jobs", item["job_id"]))
                            if known.state == "complete":
                                self.accept_result(batch, item, known)
                                self.save(batch)
                            elif known.state in ACTIVE:
                                item["status"] = "running"
                                self.save(batch)
                    if any(item["status"] == "needs_review" for item in batch["items"]):
                        batch.update(state="paused", error="Neue Takes benötigen eine ausdrückliche Auswahl.")
                        self.save(batch)
                        return
                    active = next((item for item in batch["items"] if item["status"] == "running"), None)
                    if active:
                        job = Job.model_validate(self.store.read("jobs", active["job_id"]))
                        if job.state == "complete":
                            self.accept_result(batch, active, job)
                            self.save(batch)
                            if active["status"] == "needs_review":
                                batch.update(state="paused", error=active["reason"])
                                self.save(batch)
                                return
                        elif job.state not in ACTIVE:
                            if job.result.get("not_submitted"):
                                active.update(status="cancelled" if batch["state"] == "cancelling" else "pending", job_id=None)
                            else:
                                active.update(status=job.state, reason=job.error)
                            batch.update(state="cancelled" if batch["state"] == "cancelling" else "paused", error=job.error or "Auftrag benötigt Prüfung.")
                            self.save(batch)
                            return
                        else:
                            job = None
                    elif batch["state"] != "running":
                        batch["state"] = "cancelled" if batch["state"] in {"cancelling", "cancelled"} else "paused"
                        self.save(batch)
                        return
                    else:
                        item = next((item for item in batch["items"] if item["status"] in {"pending", "interrupted", "unknown"}), None)
                        if not item:
                            batch["state"] = "complete"
                            self.save(batch)
                            return
                        if not self.generation_busy():
                            if item["status"] == "unknown" and not item.get("job_id"):
                                raise HTTPException(409, "Mehrdeutiger Anbieterauftrag. Keine erneute Übermittlung.")
                            self.check_inputs(batch)
                            if item.get("job_id"):
                                job = self.generation.resume(item["job_id"], True, batch_id=batch_id)
                                resume = True
                            else:
                                job = self.generation.start(item["project_id"], item["scene_id"], True,
                                                            batch["expected_projects"][item["project_id"]]["revision"], batch_id=batch_id)
                                resume = False
                            item.update(status="running", job_id=job.id)
                            self.save(batch)
                            self.generation.launch(job.id, resume=resume)
                self.stop.wait(self.poll_seconds)
        except Exception as exc:
            with self.store.lock:
                batch = self.store.read("batches", batch_id)
                message = exc.detail if isinstance(exc, HTTPException) else str(exc) if isinstance(exc, GenerationError) else "Batch unterbrochen. Gespeicherte Aufträge prüfen; keine automatische Wiederholung."
                batch.update(state="paused", error=message)
                self.save(batch)
        finally:
            with self.store.lock:
                self.workers.discard(batch_id)
                if getattr(self.store, "batch_owner", None) == batch_id:
                    self.store.batch_owner = None
                    self.store.batch_stop_requested = False

    def close(self):
        self.stop.set()
        with self.store.lock:
            self.store.batch_stop_requested = True
            for batch in self.store.list("batches"):
                if batch["state"] in BUSY:
                    batch.update(state="paused", error="Lokaler Dienst beendet. Aufträge vor Fortsetzung prüfen.")
                    self.save(batch)


def create_router(store, generation=None):
    service = BatchService(store, generation=generation)

    @asynccontextmanager
    async def lifespan(app):
        yield
        service.close()

    router = APIRouter(lifespan=lifespan)

    def perform(action):
        try:
            return action()
        except FileNotFoundError as exc:
            raise HTTPException(404, "Batch, Projekt oder Medien fehlen.") from exc
        except GenerationError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, "Ungültige Batch- oder Projektdaten.") from exc

    @router.post("/batches/preview", status_code=201)
    def preview(body: PreviewRequest):
        return public(perform(lambda: service.preview(body.project_ids)))

    @router.get("/batches")
    def batches():
        return [public(batch) for batch in sorted(store.list("batches"), key=lambda batch: batch["created_at"], reverse=True)]

    @router.get("/batches/{batch_id}")
    def batch(batch_id: str):
        return public(perform(lambda: store.read("batches", batch_id)))

    @router.post("/batches/{batch_id}/start")
    def start(batch_id: str, body: ConfirmRequest):
        value = perform(lambda: service.confirm(batch_id, body.confirmed, body.input_digest))
        service.launch(batch_id)
        return public(value)

    @router.post("/batches/{batch_id}/resume")
    def resume(batch_id: str, body: ConfirmRequest):
        value = perform(lambda: service.confirm(batch_id, body.confirmed, body.input_digest, resume=True))
        service.launch(batch_id)
        return public(value)

    @router.post("/batches/{batch_id}/pause")
    def pause(batch_id: str):
        return public(perform(lambda: service.control(batch_id)))

    @router.post("/batches/{batch_id}/cancel")
    def cancel(batch_id: str):
        return public(perform(lambda: service.control(batch_id, cancel=True)))

    return router
