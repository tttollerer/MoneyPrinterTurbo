"""Local FastAPI surface. All mutable project state belongs to this process."""
import asyncio
import io
import json
import math
import mimetypes
import os
import subprocess
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image
from pydantic import ValidationError

from spotforge.models import Asset, Brand, Job, Project, RenderManifest, RenderScene, Scene, now
from spotforge.store import Store

REPO = Path(__file__).resolve().parents[1]
GATES = ["concept", "script", "storyboard", "clips", "final"]
MAX_UPLOAD = 100 * 1024 * 1024
SUFFIXES = {
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
    ".mp4": "video", ".mov": "video", ".webm": "video",
    ".mp3": "audio", ".wav": "audio", ".m4a": "audio",
    ".ttf": "font", ".otf": "font", ".woff": "font", ".woff2": "font",
    ".pdf": "document", ".txt": "document", ".md": "document",
}


def project(store, pid):
    return Project.model_validate(store.read("projects", pid))


def save_project(store, p, invalidate=True):
    p.revision += 1
    if invalidate:
        p.render = {}
    store.write("projects", p.id, p)
    return p


def invalidate_gates(p, gate="storyboard"):
    if p.recipe == "spot":
        for name in GATES[GATES.index(gate):]:
            p.gates[name] = "todo"


def mark_descendants(p, scene_id):
    visited = {scene_id}
    for _ in p.scenes:
        for item in p.scenes:
            if item.predecessor_scene_id in visited and item.id not in visited:
                item.stale = True
                visited.add(item.id)


def check_assets(store, p):
    for scene in p.scenes:
        for aid in [scene.start_asset_id, scene.end_asset_id]:
            if aid:
                a = Asset.model_validate(store.read("assets", aid))
                if a.kind != "image":
                    raise ValueError("Start- und Endbilder müssen Bilddateien sein.")
                store.asset_path(aid)
        if scene.source_asset_id:
            a = Asset.model_validate(store.read("assets", scene.source_asset_id))
            if a.kind not in {"image", "video"}:
                raise ValueError("Eine Szene benötigt ein Bild oder Video.")
            store.asset_path(a.id)
        if scene.predecessor_scene_id:
            ids = [x.id for x in p.scenes]
            if scene.predecessor_scene_id not in ids or ids.index(scene.predecessor_scene_id) >= ids.index(scene.id):
                raise ValueError("Die verknüpfte Szene muss vor dieser Szene stehen.")
    for aid in [p.audio.narration_asset_id, p.audio.music_asset_id]:
        if aid:
            a = Asset.model_validate(store.read("assets", aid))
            if a.kind != "audio":
                raise ValueError("Die Tonspur benötigt eine Audiodatei.")
            store.asset_path(aid)


def selected_asset(scene):
    if scene.selected_take_id:
        take = next((x for x in scene.takes if x.id == scene.selected_take_id), None)
        if not take:
            raise ValueError("Gewählter Take fehlt.")
        return take.asset_id
    return scene.source_asset_id


def render_manifest(store, p, base_url):
    check_assets(store, p)
    if not p.scenes:
        raise ValueError("Bitte mindestens eine Szene hinzufügen.")
    if p.brand_snapshot:
        from spotforge.brands import validate_brand
        validate_brand(store, p.brand_snapshot)
    assets = {}

    def include(aid):
        if not aid:
            return
        a = Asset.model_validate(store.read("assets", aid))
        store.asset_path(aid)
        assets[aid] = {"url": f"{base_url.rstrip('/')}/api/assets/{aid}/file", "kind": a.kind,
                       "name": a.name, "sha256": a.sha256}

    scenes = []
    cursor = 0
    for scene in p.scenes:
        if scene.stale:
            raise ValueError(f"{scene.title}: Eingaben geändert. Take prüfen oder neu erzeugen.")
        aid = selected_asset(scene)
        if not aid:
            raise ValueError(f"{scene.title}: Es fehlt ein ausgewählter Clip oder ein lokales Bild.")
        include(aid)
        take = next((t for t in scene.takes if t.id == scene.selected_take_id), None)
        duration = max(1, round(scene.duration_s * p.format.fps))
        if take and (take.end_asset_id or any(s.predecessor_scene_id == scene.id for s in p.scenes)):
            actual = take.parameters.get("actual_duration_s")
            if actual is not None and duration < math.ceil(float(actual) * p.format.fps - 1e-6):
                raise ValueError(f"{scene.title}: Kürzen würde das Endbild oder den Übergang zur Folgeszene abschneiden. Dauer mindestens {actual:.2f}s wählen.")
        scenes.append(RenderScene(id=scene.id, asset_id=aid, from_frame=cursor,
                                  duration_frames=duration, onscreen_text=scene.onscreen_text))
        cursor += duration
    for aid in [p.audio.narration_asset_id, p.audio.music_asset_id]:
        include(aid)
    if p.brand_snapshot:
        include(p.brand_snapshot.font_asset_id)
        include(p.brand_snapshot.logo_asset_id)
    total_ms = cursor / p.format.fps * 1000
    if any(c.end_ms > total_ms + 1 for c in p.captions):
        raise ValueError("Untertitel reichen über die Videolänge hinaus.")
    if p.audio.narration_asset_id:
        duration = probe_duration(store.asset_path(p.audio.narration_asset_id))
        if duration > cursor / p.format.fps + 0.1:
            raise ValueError("Voiceover ist länger als das Video. Szenendauer anpassen.")
    return RenderManifest(project_id=p.id, project_revision=p.revision, format=p.format,
                          duration_frames=cursor, brand=p.brand_snapshot, assets=assets,
                          scenes=scenes, audio=p.audio, captions=p.captions)


def probe_duration(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                             "-of", "json", str(path)], capture_output=True, text=True, timeout=15, check=True)
    return float(json.loads(result.stdout)["format"]["duration"])


def create_app(data_dir=None, output_dir=None):
    data_dir = data_dir or os.environ.get("SPOTFORGE_DATA_DIR", REPO / "spotforge-data")
    store = Store(data_dir)
    outputs = Path(output_dir or os.environ.get("SPOTFORGE_OUTPUT_DIR", store.root / "Ausgaben")).resolve()
    render_lock = threading.Lock()
    processes = {}
    cancellations = set()

    @asynccontextmanager
    async def lifespan(app):
        with store.lock:
            for raw in store.list("jobs"):
                if raw.get("kind") == "render" and raw["state"] in {"queued", "running"}:
                    raw.update(state="interrupted", error="Render durch Neustart unterbrochen. Erneut rendern.")
                    store.write("jobs", raw["id"], raw)
        yield
        for proc in list(processes.values()):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

    app = FastAPI(title="SpotForge Local", lifespan=lifespan)
    app.state.store = store
    app.state.outputs = outputs

    @app.middleware("http")
    async def local_only(request, call_next):
        # Prevent arbitrary websites from submitting requests to a localhost tool.
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            allowed = {f"http://127.0.0.1:{request.url.port}", f"http://localhost:{request.url.port}",
                       "http://127.0.0.1:4832", "http://localhost:4832"}
            if origin and origin not in allowed:
                return JSONResponse({"detail": "Fremder Ursprung ist nicht erlaubt."}, status_code=403)
        hostname = urlsplit(str(request.url)).hostname
        if hostname not in {"localhost", "127.0.0.1", "testserver"}:
            return JSONResponse({"detail": "Nur lokaler Zugriff ist erlaubt."}, status_code=403)
        return await call_next(request)

    @app.exception_handler(FileNotFoundError)
    async def missing(request, exc):
        return JSONResponse({"detail": "Projekt, Profil oder Datei nicht gefunden."}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(ValidationError)
    async def validation(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/api/health")
    def health():
        return {"ok": True, "renderer_installed": (REPO / "renderer/node_modules/@remotion/renderer").exists()}

    @app.get("/api/projects")
    def projects():
        return sorted(store.list("projects"), key=lambda p: p.get("created_at", ""), reverse=True)

    @app.post("/api/projects")
    def create_project(body: dict):
        fields = {k: v for k, v in body.items() if k not in {"brand_id", "brand_version"}}
        if set(fields) - {"title", "recipe", "format"}:
            raise ValueError("Unbekannte Projektfelder.")
        p = Project.model_validate(fields)
        if p.recipe == "spot":
            p.gates = dict.fromkeys(GATES, "todo")
        if body.get("brand_id"):
            key = f"{body['brand_id']}_{body['brand_version']}" if body.get("brand_version") else body["brand_id"]
            p.brand_snapshot = Brand.model_validate(store.read("brand_versions" if body.get("brand_version") else "brands", key))
        store.write("projects", p.id, p)
        return p

    @app.get("/api/projects/{pid}")
    def get_project(pid: str):
        return project(store, pid)

    @app.patch("/api/projects/{pid}")
    def patch_project(pid: str, body: dict):
        with store.lock:
            p = project(store, pid)
            expected = body.pop("expected_revision", None)
            if expected is not None and expected != p.revision:
                raise HTTPException(409, "Projekt wurde inzwischen geändert. Neu laden.")
            if set(body) - {"title", "format", "audio", "captions"}:
                raise ValueError("Unbekannte oder geschützte Projektfelder.")
            old_format = p.format.model_dump()
            p = Project.model_validate({**p.model_dump(), **body})
            check_assets(store, p)
            if old_format != p.format.model_dump():
                for scene in p.scenes:
                    if scene.takes and scene.mode != "local":
                        scene.stale = True
                invalidate_gates(p, "storyboard")
            if set(body) & {"captions", "audio"}:
                invalidate_gates(p, "script")
            return save_project(store, p)

    @app.post("/api/projects/{pid}/scenes")
    def add_scene(pid: str, body: dict):
        with store.lock:
            p = project(store, pid)
            if len(p.scenes) >= 100:
                raise ValueError("Maximal 100 Szenen pro Projekt.")
            if set(body) & {"takes", "selected_take_id", "stale", "id"}:
                raise ValueError("Take-/Systemfelder dürfen hier nicht gesetzt werden.")
            p.scenes.append(Scene.model_validate(body))
            check_assets(store, p)
            invalidate_gates(p)
            return save_project(store, p)

    @app.patch("/api/projects/{pid}/scenes/{sid}")
    def patch_scene(pid: str, sid: str, body: dict):
        with store.lock:
            p = project(store, pid)
            scene = next((s for s in p.scenes if s.id == sid), None)
            if scene is None:
                raise HTTPException(404, "Szene fehlt.")
            if set(body) - {"title", "mode", "prompt", "duration_s", "start_asset_id", "end_asset_id", "predecessor_scene_id", "source_asset_id", "model", "onscreen_text"}:
                raise ValueError("Unbekannte oder geschützte Szenenfelder.")
            updated = Scene.model_validate({**scene.model_dump(), **body})
            changed = {k for k, v in body.items() if getattr(scene, k) != v}
            if changed & {"mode", "prompt", "start_asset_id", "end_asset_id", "predecessor_scene_id", "model"}:
                updated.stale = bool(scene.takes)
                mark_descendants(p, sid)
            if "duration_s" in changed:
                mark_descendants(p, sid)
            if "source_asset_id" in changed:
                updated.selected_take_id = None
                updated.stale = False
                mark_descendants(p, sid)
            p.scenes[p.scenes.index(scene)] = updated
            check_assets(store, p)
            invalidate_gates(p, "script" if changed & {"prompt", "onscreen_text"} else "storyboard")
            return save_project(store, p)

    @app.delete("/api/projects/{pid}/scenes/{sid}")
    def delete_scene(pid: str, sid: str):
        with store.lock:
            p = project(store, pid)
            if any(j.get("project_id") == pid and j.get("scene_id") == sid and j.get("state") in {"queued", "submitting", "running", "interrupted", "unknown"} for j in store.list("jobs")):
                raise HTTPException(409, "Für diese Szene läuft ein Anbieterauftrag oder benötigt Klärung. Ergebnis zuerst sichern.")
            if any(s.predecessor_scene_id == sid for s in p.scenes):
                raise HTTPException(409, "Folgeszenen sind verknüpft. Verknüpfung zuerst lösen.")
            if not any(s.id == sid for s in p.scenes):
                raise HTTPException(404, "Szene fehlt.")
            p.scenes = [s for s in p.scenes if s.id != sid]
            invalidate_gates(p)
            return save_project(store, p)

    @app.post("/api/projects/{pid}/scenes/{sid}/select")
    def select_take(pid: str, sid: str, body: dict):
        with store.lock:
            p = project(store, pid)
            s = next((s for s in p.scenes if s.id == sid), None)
            if not s or not any(t.id == body.get("take_id") for t in s.takes):
                raise HTTPException(404, "Take gehört nicht zur Szene.")
            s.selected_take_id = body["take_id"]
            s.stale = False  # Explicit user acceptance of the selected historical take.
            mark_descendants(p, sid)
            invalidate_gates(p, "clips")
            return save_project(store, p)

    @app.post("/api/projects/{pid}/gates/{gate}")
    def approve_gate(pid: str, gate: str, body: dict):
        with store.lock:
            p = project(store, pid)
            if p.recipe != "spot" or gate not in GATES:
                raise ValueError("Dieses Projekt kennt dieses Freigabetor nicht.")
            if not isinstance(body.get("approve"), bool):
                raise ValueError("Explizite Freigabeentscheidung fehlt.")
            if body["approve"]:
                if any(p.gates.get(g) != "approved" for g in GATES[:GATES.index(gate)]):
                    raise HTTPException(409, "Vorherige Freigaben fehlen.")
                if gate in {"script", "storyboard", "clips"} and not p.scenes:
                    raise ValueError("Bitte zuerst Szenen planen.")
                if gate == "clips" and any(s.stale or not selected_asset(s) for s in p.scenes):
                    raise ValueError("Bitte alle Szenen fertigstellen und Takes prüfen.")
                if gate == "final" and not p.render.get("job_id"):
                    raise ValueError("Bitte zuerst ein Video rendern und prüfen.")
                p.gates[gate] = "approved"
            else:
                invalidate_gates(p, gate)
            return save_project(store, p, invalidate=False)

    @app.get("/api/assets")
    def assets():
        return store.list("assets")

    @app.post("/api/assets")
    async def upload(file: UploadFile):
        name = Path((file.filename or "").replace("\\", "/")).name
        kind = SUFFIXES.get(Path(name).suffix.lower())
        if not kind:
            raise ValueError("Unterstützt: PNG/JPG/WebP, MP4/MOV/WebM, MP3/WAV/M4A, Fonts, PDF/TXT/MD.")
        content = bytearray()
        while chunk := await file.read(1024 * 1024):
            content.extend(chunk)
            if len(content) > MAX_UPLOAD:
                raise HTTPException(413, "Datei ist größer als 100 MB.")
        if not content:
            raise ValueError("Datei ist leer.")
        if kind == "image":
            try:
                with Image.open(io.BytesIO(content)) as img:
                    if img.width * img.height > 40_000_000:
                        raise ValueError("Bild hat mehr als 40 Megapixel.")
                    img.verify()
            except (OSError, Image.DecompressionBombError) as exc:
                raise ValueError("Ungültiges oder zu großes Bild.") from exc
        if kind == "font" and bytes(content[:4]) not in {b"\x00\x01\x00\x00", b"OTTO", b"wOFF", b"wOF2", b"true"}:
            raise ValueError("Ungültige Schriftdatei.")
        if Path(name).suffix.lower() == ".pdf" and not content.startswith(b"%PDF-"):
            raise ValueError("Ungültige PDF-Datei.")
        a = store.add_asset(bytes(content), name, kind, mimetypes.guess_type(name)[0] or "application/octet-stream")
        if kind in {"audio", "video"}:
            try:
                result = await asyncio.to_thread(subprocess.run, ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(store.asset_path(a.id))], capture_output=True, text=True, timeout=15, check=True)
                types = [s["codec_type"] for s in json.loads(result.stdout)["streams"]]
                if kind not in types:
                    raise ValueError("Datei enthält keine passende Medien-Spur.")
            except (ValueError, subprocess.SubprocessError, KeyError):
                store.asset_path(a.id).unlink(missing_ok=True)
                store.path("assets", a.id).unlink(missing_ok=True)
                raise ValueError("Mediendatei ist ungültig oder FFmpeg fehlt.") from None
        return a

    @app.head("/api/assets/{aid}/file")
    @app.get("/api/assets/{aid}/file")
    def asset_file(aid: str, request: Request):
        a = Asset.model_validate(store.read("assets", aid))
        headers = {"X-Content-Type-Options": "nosniff"}
        origin = request.headers.get("origin", "")
        if urlsplit(origin).hostname in {"localhost", "127.0.0.1"} and urlsplit(origin).scheme == "http":
            headers.update({"Access-Control-Allow-Origin": origin, "Vary": "Origin"})
        return FileResponse(store.asset_path(aid), media_type=a.mime, headers=headers)

    @app.get("/api/jobs")
    def jobs():
        return sorted(store.list("jobs"), key=lambda j: j.get("created_at", ""), reverse=True)

    @app.get("/api/jobs/{jid}")
    def job(jid: str):
        return Job.model_validate(store.read("jobs", jid))

    @app.get("/api/projects/{pid}/manifest")
    def manifest(pid: str, request: Request):
        with store.lock:
            return render_manifest(store, project(store, pid), str(request.base_url))

    def execute_render(jid, mf):
        j = Job.model_validate(store.read("jobs", jid))
        timer = None
        timed_out = threading.Event()
        try:
            with store.lock:
                j = Job.model_validate(store.read("jobs", jid))
                j.state = "running"
                store.write("jobs", jid, j)
            output = outputs / jid / "video.mp4"
            output.parent.mkdir(parents=True, exist_ok=True)
            manifest_path = store.path("renders", jid)
            store.write("renders", jid, mf)
            proc = subprocess.Popen(["node", str(REPO / "renderer/render.mjs"), str(manifest_path), str(output)],
                                    cwd=REPO / "renderer", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            processes[jid] = proc
            def stop_after_timeout():
                timed_out.set()
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            timer = threading.Timer(15 * 60, stop_after_timeout)
            timer.daemon = True
            timer.start()
            error_lines = []
            for line in proc.stdout:
                error_lines.append(line.strip())
                error_lines = error_lines[-20:]
                try:
                    ev = json.loads(line)
                    if "progress" in ev:
                        j.progress = max(0, min(1, float(ev["progress"])))
                        j.updated_at = now()
                        store.write("jobs", jid, j)
                except (ValueError, TypeError):
                    pass
            if proc.wait() != 0 or not output.is_file():
                if timed_out.is_set():
                    raise RuntimeError("Render nach 15 Minuten beendet. Browser und Medien prüfen.")
                raise RuntimeError("Remotion konnte nicht rendern: " + " ".join(error_lines)[-1500:])
            j.state, j.progress = "complete", 1
            j.result = {"url": f"/api/outputs/{jid}", "duration_s": probe_duration(output), "output": str(output)}
            with store.lock:
                p = project(store, j.project_id)
                if p.revision == mf.project_revision:
                    p.render = {"job_id": jid, "project_revision": p.revision, **j.result}
                    store.write("projects", p.id, p)
        except Exception as exc:
            if jid in cancellations:
                j.state, j.error = "interrupted", "Render vom Nutzer abgebrochen."
            else:
                j.state, j.error = "failed", str(exc)[-1800:]
        finally:
            if timer:
                timer.cancel()
            cancellations.discard(jid)
            j.updated_at = now()
            store.write("jobs", jid, j)
            processes.pop(jid, None)
            render_lock.release()

    @app.post("/api/projects/{pid}/render")
    def render(pid: str, request: Request):
        with store.lock:
            p = project(store, pid)
            if p.recipe == "spot" and any(p.gates.get(g) != "approved" for g in GATES[:4]):
                raise HTTPException(409, "Vor dem Rendern fehlen Freigaben bis einschließlich Clips.")
            mf = render_manifest(store, p, str(request.base_url))
            if not (REPO / "renderer/render.mjs").is_file():
                raise HTTPException(503, "Remotion-Renderer fehlt. Einrichtung ausführen.")
            if not render_lock.acquire(blocking=False):
                raise HTTPException(409, "Es läuft bereits ein Renderauftrag.")
            j = Job(project_id=pid, kind="render", input_snapshot=mf.model_dump(mode="json"))
            store.write("jobs", j.id, j)
            threading.Thread(target=execute_render, args=(j.id, mf), daemon=True).start()
            return j

    @app.post("/api/jobs/{jid}/cancel")
    def cancel_render(jid: str):
        with store.lock:
            j = Job.model_validate(store.read("jobs", jid))
            if j.kind != "render":
                raise HTTPException(409, "Anbieteraufträge können hier nicht abgebrochen werden.")
            proc = processes.get(jid)
            if not proc or proc.poll() is not None:
                raise HTTPException(409, "Dieser Render läuft nicht mehr.")
            cancellations.add(jid)
            proc.terminate()
            return {"id": jid, "state": "interrupted"}

    @app.get("/api/outputs/{jid}/preview")
    def preview_output(jid: str):
        j = Job.model_validate(store.read("jobs", jid))
        if j.kind != "render" or j.state != "complete":
            raise HTTPException(409, "Die Vorschau ist noch nicht fertig.")
        return FileResponse(outputs / j.id / "video.mp4", media_type="video/mp4")

    @app.get("/api/outputs/{jid}")
    def download(jid: str):
        j = Job.model_validate(store.read("jobs", jid))
        p = project(store, j.project_id)
        if j.kind != "render" or j.state != "complete":
            raise HTTPException(409, "Die Ausgabe ist noch nicht fertig.")
        if p.recipe == "spot" and (p.gates.get("final") != "approved" or p.render.get("job_id") != jid):
            raise HTTPException(409, "Die finale Freigabe für diesen Export fehlt.")
        return FileResponse(outputs / j.id / "video.mp4", media_type="video/mp4", filename="video.mp4")

    from spotforge.brands import create_router as brand_router
    from spotforge.generation import create_router as generation_router
    app.include_router(brand_router(store))
    app.include_router(generation_router(store), prefix="/api")

    @app.get("/{rest:path}")
    def frontend(rest: str):
        if rest.startswith("api/"):
            raise HTTPException(404, "Unbekannter API-Endpunkt.")
        dist = REPO / "studio/dist"
        requested = (dist / rest).resolve()
        if requested.is_relative_to(dist.resolve()) and requested.is_file():
            return FileResponse(requested)
        if (dist / "index.html").is_file():
            return FileResponse(dist / "index.html")
        return JSONResponse({"detail": "Studio noch nicht gebaut. npm --prefix studio run build ausführen."}, status_code=503)

    return app
