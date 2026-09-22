"""Local narration and explicitly requested stock acquisition.

Upstream material.py uses process-global config.toml credentials. This narrow
adapter follows its provider response mapping without importing that configuration
or temporarily mutating it. All external calls use explicit environment keys.
"""
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse, urlunparse

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from spotforge.models import Cue, Job, Project, Scene, new_id, now
from spotforge.media import local_input_options

KINDS = {"speech", "stock_import"}
MAX_DOWNLOAD = 250 * 1024 * 1024


class WorkflowError(Exception):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def run(args, timeout=60):
    return subprocess.run([str(x) for x in args], capture_output=True, text=True,
                          check=True, timeout=timeout)


def voices():
    if not shutil.which("say"):
        return []
    result = run(["say", "-v", "?"], timeout=10)
    found = []
    for line in result.stdout.splitlines():
        match = re.match(r"^(.+?)\s+([a-z]{2,3}_[A-Z]{2})\s+#", line)
        if match:
            name, language = match.groups()
            found.append({"id": name.strip(), "label": name.strip(), "language": language})
    return found


def capabilities():
    try:
        installed = voices()
    except Exception:
        installed = []
    available = bool(installed and shutil.which("ffmpeg") and shutil.which("ffprobe"))
    default = next((v["id"] for v in installed if v["id"] == "Anna"), installed[0]["id"] if installed else None)
    return {"speech": {"available": available, "voices": installed, "default_voice": default,
                       "rate_min": 80, "rate_max": 250,
                       "reason": "" if available else "Lokale macOS-Stimme sowie FFmpeg/FFprobe werden benötigt."},
            "stock": [{"id": p, "configured": bool(os.environ.get(f"{p.upper()}_API_KEY"))}
                      for p in ("pexels", "pixabay")], "script_assistance": False}


def _public_url(value, provider):
    parsed = urlparse(str(value or ""))
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}
            or not (host == f"{provider}.com" or host.endswith(f".{provider}.com"))):
        return None
    return urlunparse(parsed._replace(query="", fragment=""))


def _media_url(value, provider):
    # Only provider-owned HTTPS CDNs; never follow a redirect or forward credentials.
    url = _public_url(value, provider)
    if not url or urlparse(str(value)).query:
        raise WorkflowError("Die Medienadresse dieses Anbieters wird nicht unterstützt.")
    return url


def _read_response(response, limit, seconds):
    if int(response.headers.get("Content-Length", 0)) > limit:
        raise WorkflowError("Medienantwort überschreitet das Größenlimit.", 502)
    chunks, size, deadline = [], 0, time.monotonic() + seconds
    for chunk in response.iter_content(1024 * 1024):
        size += len(chunk)
        if size > limit or time.monotonic() > deadline:
            raise WorkflowError("Medienantwort überschreitet Größen- oder Zeitlimit.", 502)
        chunks.append(chunk)
    return b"".join(chunks)


class StockProvider:
    def search(self, provider, query, orientation):
        key = os.environ.get(f"{provider.upper()}_API_KEY")
        if not key:
            raise WorkflowError(f"{provider.upper()}_API_KEY fehlt. Keine Suche gestartet.", 409)
        with requests.Session() as session:
            session.trust_env = False  # No implicit .netrc credentials or global proxy config.
            if provider == "pexels":
                response = session.get("https://api.pexels.com/v1/videos/search",
                                       params={"query": query, "per_page": 20, "orientation": orientation},
                                       headers={"Authorization": key}, timeout=(10, 30), allow_redirects=False, stream=True)
            else:
                response = session.get("https://pixabay.com/api/videos/",
                                       params={"key": key, "q": query, "per_page": 20, "video_type": "all"},
                                       timeout=(10, 30), allow_redirects=False, stream=True)
            if response.status_code != 200:
                raise WorkflowError(f"Stock-Suche fehlgeschlagen (HTTP {response.status_code}).", 502)
            with response:
                data = json.loads(_read_response(response, 5 * 1024 * 1024, 45))
        return self.parse(provider, data, query, orientation)

    @staticmethod
    def parse(provider, data, query, orientation):
        results = []
        for item in data.get("videos" if provider == "pexels" else "hits", [])[:20]:
            try:
                variants = item.get("video_files", []) if provider == "pexels" else list(item.get("videos", {}).values())
                eligible = []
                for variant in variants:
                    w, h = int(variant.get("width", 0)), int(variant.get("height", 0))
                    if not w or not h or (orientation == "portrait") != (h > w):
                        continue
                    link = variant.get("link") or variant.get("url")
                    try:
                        url = _media_url(link, provider)
                    except WorkflowError:
                        continue
                    eligible.append((abs(w * h - 1920 * 1080), w, h, url))
                if not eligible:
                    continue
                _, w, h, url = min(eligible)
                duration = float(item["duration"])
                if not math.isfinite(duration) or duration <= 0:
                    continue
                source = _public_url(item.get("url") if provider == "pexels" else item.get("pageURL"), provider)
                if not source:
                    continue
                preview = item.get("image") if provider == "pexels" else item.get("thumbnailURL")
                results.append({"selection_id": new_id(), "provider": provider, "title": query,
                                "provider_asset_id": str(item.get("id", "")), "preview_url": _public_url(preview, provider),
                                "source_url": source, "duration_s": duration, "width": w, "height": h,
                                "download_url": url, "query": query, "created_at": now()})
            except (ValueError, TypeError, KeyError):
                continue
        return results[:12]

    def download(self, selection):
        url = _media_url(selection["download_url"], selection["provider"])
        with requests.Session() as session:
            session.trust_env = False
            with session.get(url, stream=True, timeout=(10, 30), allow_redirects=False) as response:
                if response.status_code != 200:
                    raise WorkflowError(f"Stock-Download fehlgeschlagen (HTTP {response.status_code}).", 502)
                return _read_response(response, MAX_DOWNLOAD, 90)


def speech_parts(script):
    # Each independently synthesized utterance has a measured PCM duration. These
    # are sentence/phrase cues, never claimed to be word-level alignment.
    paragraphs = re.split(r"(?<=[.!?])\s+|\n+", script.strip())
    result = []
    for paragraph in paragraphs:
        remainder = paragraph.strip()
        while len(remainder) > 400:
            at = remainder.rfind(" ", 0, 400)
            at = at if at > 0 else 400
            result.append(remainder[:at].strip())
            remainder = remainder[at:].strip()
        if remainder:
            result.append(remainder)
    if not result or len(result) > 200:
        raise WorkflowError("Skript benötigt 1 bis 200 Sätze oder Abschnitte.")
    return result


def synthesize(script, voice, rate, progress=None):
    chunks, cues, samples = [], [], 0
    deadline = time.monotonic() + 600
    parts = speech_parts(script)
    with tempfile.TemporaryDirectory(prefix="spotforge-speech-") as tmp:
        tmp = Path(tmp)
        for i, phrase in enumerate(parts):
            if time.monotonic() > deadline:
                raise WorkflowError("Lokale Sprecheraufnahme überschreitet zehn Minuten Laufzeit.")
            text, aiff, wav = tmp / "text.txt", tmp / "part.aiff", tmp / "part.wav"
            text.write_text(phrase, encoding="utf-8")
            run(["say", "-v", voice, "-r", rate, "-o", aiff, "-f", text], timeout=60)
            run(["ffmpeg", "-v", "error", "-y", "-i", aiff, "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", wav], timeout=30)
            with wave.open(str(wav), "rb") as audio:
                count = audio.getnframes()
                if count <= 0 or audio.getframerate() != 48000 or audio.getnchannels() != 1 or audio.getsampwidth() != 2:
                    raise WorkflowError("Lokale Stimme lieferte kein gültiges Audio. macOS-Sprachdienst muss erreichbar sein; außerhalb eingeschränkter Sandbox starten.")
                chunk = audio.readframes(count)
            cues.append(Cue(text=phrase, start_ms=round(samples / 48), end_ms=round((samples + count) / 48)).model_dump())
            samples += count
            if samples > 48000 * 1200:
                raise WorkflowError("Sprecheraufnahme darf höchstens zwanzig Minuten lang sein.")
            chunks.append(chunk)
            if progress:
                progress((i + 1) / len(parts) * 0.9)
        output = tmp / "narration.wav"
        with wave.open(str(output), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(48000)
            for chunk in chunks:
                audio.writeframes(chunk)
        return output.read_bytes(), cues, samples / 48000


def probe_video(content):
    with tempfile.TemporaryDirectory(prefix="spotforge-stock-") as tmp:
        path = Path(tmp) / "source.mp4"
        path.write_bytes(content)
        data = json.loads(run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                               "stream=codec_type,width,height:format=duration", "-of", "json", *local_input_options(path), path], timeout=30).stdout)
    stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    duration = float(data.get("format", {}).get("duration", 0))
    if not stream or not math.isfinite(duration) or duration <= 0:
        raise WorkflowError("Stock-Datei enthält keinen gültigen Videostream.", 502)
    return {"duration_s": duration, "width": stream["width"], "height": stream["height"]}


def invalidate(project, changed_scene=None):
    project.render = {}
    if project.recipe == "spot":
        project.gates.update({"storyboard": "todo", "clips": "todo", "final": "todo"})
    if changed_scene:
        affected = {changed_scene}
        for _ in project.scenes:
            for scene in project.scenes:
                if scene.predecessor_scene_id in affected:
                    scene.stale = True
                    affected.add(scene.id)
    project.revision += 1


class WorkflowService:
    def __init__(self, store, provider=None, speech_engine=synthesize):
        self.store, self.provider, self.speech_engine = store, provider or StockProvider(), speech_engine
        self.workers = set()
        self.worker_lock = threading.Lock()

    def recover(self):
        with self.store.lock:
            for raw in self.store.list("jobs"):
                if raw.get("kind") in KINDS and raw["state"] in {"queued", "running"}:
                    raw.update(state="interrupted", error="Durch Neustart unterbrochen. Bei Bedarf ausdrücklich neu starten.", updated_at=now())
                    self.store.write("jobs", raw["id"], raw)

    def _save(self, job):
        job.updated_at = now()
        self.store.write("jobs", job.id, job)

    def search(self, provider, query, orientation, confirmed):
        if not confirmed:
            raise WorkflowError("Die externe Suche und Übermittlung des Suchbegriffs bitte bestätigen.", 409)
        results = self.provider.search(provider, query, orientation)
        with self.store.lock:
            for result in results:
                self.store.write("stock_selections", result["selection_id"], result)
        return [{k: v for k, v in r.items() if k not in {"download_url", "query"}} for r in results]

    def start(self, project_id, kind, expected_revision, confirmed, **options):
        if not confirmed:
            raise WorkflowError("Aktion bitte ausdrücklich bestätigen.", 409)
        if kind == "speech":
            cap = capabilities()["speech"]
            if not cap["available"]:
                raise WorkflowError(cap["reason"], 409)
            if options["voice"] not in {v["id"] for v in cap["voices"]}:
                raise WorkflowError("Stimme ist auf diesem Mac nicht installiert.")
        with self.store.lock:
            if any(j.get("kind") in KINDS and j.get("state") in {"queued", "running"} for j in self.store.list("jobs")):
                raise WorkflowError("Es läuft bereits ein lokaler Medienauftrag.", 409)
            project = Project.model_validate(self.store.read("projects", project_id))
            if project.revision != expected_revision:
                raise WorkflowError("Projekt wurde geändert. Neu laden und erneut bestätigen.", 409)
            snapshot = {"project_revision": project.revision, "brand_snapshot": project.brand_snapshot.model_dump(mode="json") if project.brand_snapshot else None,
                        "options": options, "script": project.script}
            if kind == "speech":
                speech_parts(project.script)
                if project.recipe == "spot" and any(project.gates.get(g) != "approved" for g in ("concept", "script")):
                    raise WorkflowError("Konzept und Skript zuerst freigeben.", 409)
            else:
                selection = self.store.read("stock_selections", options["selection_id"])
                _media_url(selection["download_url"], selection["provider"])
                snapshot["selection"] = selection
                if options.get("scene_id") and not any(s.id == options["scene_id"] for s in project.scenes):
                    raise WorkflowError("Zielszene existiert nicht.", 404)
                if not options.get("scene_id") and len(project.scenes) >= 100:
                    raise WorkflowError("Maximal 100 Szenen pro Projekt.")
            job = Job(project_id=project_id, scene_id=options.get("scene_id"), kind=kind, input_snapshot=snapshot)
            self._save(job)
            return job

    def launch(self, job_id):
        with self.worker_lock:
            if job_id in self.workers:
                return
            self.workers.add(job_id)
        threading.Thread(target=self._guarded_run, args=(job_id,), daemon=True).start()

    def _guarded_run(self, job_id):
        try:
            self.run(job_id)
        finally:
            with self.worker_lock:
                self.workers.discard(job_id)

    def run(self, job_id):
        job = Job.model_validate(self.store.read("jobs", job_id))
        if job.state != "queued":
            return
        try:
            with self.store.lock:
                project = Project.model_validate(self.store.read("projects", job.project_id))
                if project.revision != job.input_snapshot["project_revision"]:
                    raise WorkflowError("Projekt vor dem Start geändert. Bitte neu bestätigen.", 409)
                job.state = "running"
                self._save(job)
            if job.kind == "speech":
                def progress(value):
                    with self.store.lock:
                        job.progress = value
                        self._save(job)
                options = job.input_snapshot["options"]
                content, captions, duration = self.speech_engine(job.input_snapshot["script"], options["voice"], options["rate"], progress)
                asset = self.store.add_asset(content, "Sprecheraufnahme.wav", "audio", "audio/wav")
                result = {"asset_id": asset.id, "captions": captions, "duration_s": duration}
            else:
                content = self.provider.download(job.input_snapshot["selection"])
                if len(content) > MAX_DOWNLOAD:
                    raise WorkflowError("Stock-Datei ist zu groß.")
                measured = probe_video(content)
                asset = self.store.add_asset(content, "Stock-Video.mp4", "video", "video/mp4")
                provenance = {k: v for k, v in job.input_snapshot["selection"].items() if k not in {"download_url", "preview_url"}}
                self.store.write("asset_provenance", asset.id, provenance)
                result = {"asset_id": asset.id, **measured, "source": provenance}
            # Save finished artifacts before project application, so restart never loses
            # a completed local result and never causes implicit provider resubmission.
            with self.store.lock:
                job.result = {**result, "applied": False}
                self._save(job)
                project = Project.model_validate(self.store.read("projects", job.project_id))
                if project.revision == job.input_snapshot["project_revision"]:
                    if job.kind == "speech":
                        project.audio.narration_asset_id = asset.id
                        project.captions = [Cue.model_validate(c) for c in result["captions"]]
                        if result["duration_s"] > sum(s.duration_s for s in project.scenes) + 0.1:
                            job.result["warning"] = "Sprecheraufnahme ist länger als die Szenen. Szenendauer vor dem Export anpassen."
                        invalidate(project)
                    else:
                        if job.scene_id:
                            scene = next(s for s in project.scenes if s.id == job.scene_id)
                            scene.mode, scene.source_asset_id, scene.selected_take_id = "local", asset.id, None
                            scene.start_asset_id = scene.end_asset_id = scene.predecessor_scene_id = None
                            scene.stale = False
                        else:
                            scene = Scene(title=job.input_snapshot["selection"]["title"], mode="local", source_asset_id=asset.id,
                                          duration_s=min(5, result["duration_s"]))
                            project.scenes.append(scene)
                        job.result["scene_id"] = scene.id
                        invalidate(project, scene.id)
                    self.store.write("projects", project.id, project)
                    job.result["applied"] = True
                else:
                    job.result["warning"] = "Projekt inzwischen geändert. Ergebnis ist gesichert; bei Bedarf bewusst übernehmen."
                job.state, job.progress = "complete", 1
                self._save(job)
        except Exception as exc:
            job.state = "failed"
            job.error = str(exc) if isinstance(exc, WorkflowError) else (
                "Lokale Sprecheraufnahme fehlgeschlagen. Stimme, FFmpeg und Skript prüfen." if job.kind == "speech"
                else "Stock-Import fehlgeschlagen. Anbieter und Medienformat prüfen; keine automatische Wiederholung.")
            with self.store.lock:
                self._save(job)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SpeechRequest(Input):
    confirmed: bool
    expected_revision: int
    voice: str = Field(default="Anna", min_length=1, max_length=100)
    rate: int = Field(default=145, ge=80, le=250)


class SearchRequest(Input):
    confirmed: bool
    provider: Literal["pexels", "pixabay"]
    query: str = Field(min_length=1, max_length=200)
    orientation: Literal["portrait", "landscape"] = "portrait"


class ImportRequest(Input):
    confirmed: bool
    expected_revision: int
    selection_id: str
    scene_id: str | None = None


def create_router(store):
    router, service = APIRouter(), WorkflowService(store)
    service.recover()

    def perform(action):
        try:
            return action()
        except WorkflowError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(404, "Projekt oder Stock-Auswahl nicht gefunden.") from exc
        except Exception as exc:
            raise HTTPException(502, "Medienaktion fehlgeschlagen. Anbieter-Konfiguration oder lokale Werkzeuge prüfen.") from exc

    @router.get("/workflow/capabilities")
    def get_capabilities():
        return capabilities()

    @router.post("/stock/search")
    def search(body: SearchRequest):
        return perform(lambda: service.search(body.provider, body.query, body.orientation, body.confirmed))

    @router.post("/projects/{pid}/speech", status_code=202)
    def speech(pid: str, body: SpeechRequest):
        job = perform(lambda: service.start(pid, "speech", body.expected_revision, body.confirmed, voice=body.voice, rate=body.rate))
        service.launch(job.id)
        return job

    @router.post("/projects/{pid}/stock/import", status_code=202)
    def stock_import(pid: str, body: ImportRequest):
        job = perform(lambda: service.start(pid, "stock_import", body.expected_revision, body.confirmed,
                                           selection_id=body.selection_id, scene_id=body.scene_id))
        service.launch(job.id)
        return job

    return router
