"""Explicit, offline migration of a selected legacy project into a new copy.

Only declared files are read. Paths are opened component-by-component with
O_NOFOLLOW so even concurrent symlink replacements cannot escape the source root.
"""
import hashlib
import io
import json
import math
import mimetypes
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from PIL import Image
from pydantic import Field, StrictBool, ValidationError

from spotforge.brands import BRAND_FIELDS, validate_brand
from spotforge.media import local_input_options
from spotforge.models import Brand, Cue, Model, Project, new_id

LIMITS = {"manifest_bytes": 5 * 1024 * 1024, "file_bytes": 100 * 1024 * 1024,
          "total_bytes": 500 * 1024 * 1024, "files": 500, "scenes": 100}
KINDS = {".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
         ".mp4": "video", ".mov": "video", ".webm": "video", ".mp3": "audio",
         ".wav": "audio", ".m4a": "audio", ".ttf": "font", ".otf": "font",
         ".woff": "font", ".woff2": "font", ".pdf": "document", ".txt": "document", ".md": "document"}
GATES = ("concept", "script", "storyboard", "clips", "final")
PARAMS = {"seed", "negative_prompt", "guidance_scale", "cfg_scale", "steps", "num_inference_steps",
          "aspect_ratio", "resolution", "duration", "camera_fixed", "generate_audio", "enable_safety_checker"}


class ImportProblem(ValueError):
    """Safe, authored diagnostics; never echo unknown source values."""


class ImportRequest(Model):
    source_dir: str = Field(min_length=1)
    project_file: str = "project.json"
    brand_file: str | None = None


class CommitRequest(ImportRequest):
    token: str
    confirmed: StrictBool = False


def _text(value):
    return value if isinstance(value, str) else ""


def _object(value, label):
    if not isinstance(value, dict):
        raise ImportProblem(f"{label}: Objekt erwartet.")
    return value


def _unknown(raw, allowed, label, warnings):
    count = len(set(raw) - set(allowed))
    if count:
        warnings.append(f"{label}: {count} unbekannte oder geschützte Felder bleiben ausschließlich im Original.")


def _brief(data, warnings):
    value = data.get("brief") or _object(data.get("spot") or {}, "Spot").get("brief") or ""
    if isinstance(value, str):
        return value
    value = _object(value, "Briefing")
    fields = {"product": "Produkt/Thema", "goal": "Ziel", "extra": "Zusatzkontext", "audience": "Zielgruppe", "duration_s": "Dauer"}
    _unknown(value, fields, "Briefing", warnings)
    return "\n".join(f"{label}: {value[key]}" for key, label in fields.items() if isinstance(value.get(key), (str, int, float)))


class Source:
    def __init__(self, request):
        supplied = Path(request.source_dir).expanduser()
        if not supplied.is_absolute():
            raise ImportProblem("Bitte einen absoluten lokalen Quellordner angeben.")
        self.root = supplied.resolve(strict=True)
        if not self.root.is_dir() or self.root == Path(self.root.anchor):
            raise ImportProblem("Bitte einen konkreten Projekt- oder Arbeitsordner wählen.")
        self.hashes = {}
        self.media = {}

    def relative(self, value, base=""):
        if not isinstance(value, str) or not value or "\x00" in value or "://" in value or value.startswith("data:"):
            raise ImportProblem("Dateireferenz muss ein lokaler Pfad sein; URLs werden nicht geladen.")
        candidate = Path(os.path.normpath(str(self.root / base / value)))
        try:
            rel = candidate.relative_to(self.root)
        except ValueError as exc:
            raise ImportProblem("Dateireferenz verlässt den ausgewählten Quellordner.") from exc
        if not rel.parts or any(part.startswith(".") for part in rel.parts):
            raise ImportProblem("Versteckte Dateien und Konfigurationen werden nicht importiert.")
        return str(rel)

    def read(self, relative, limit):
        descriptors = []
        try:
            current = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(current)
            parts = Path(relative).parts
            for component in parts[:-1]:
                current = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                descriptors.append(current)
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
            descriptors.append(descriptor)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
                raise ImportProblem("Datei ist kein reguläres Dokument oder überschreitet das Größenlimit.")
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                content = stream.read(limit + 1)
            if len(content) > limit:
                raise ImportProblem("Datei überschreitet das Größenlimit.")
            self.hashes[relative] = hashlib.sha256(content).hexdigest()
            return content
        except OSError as exc:
            raise ImportProblem("Datei fehlt, ist nicht lesbar oder enthält eine symbolische Verknüpfung.") from exc
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def json(self, value, base=""):
        relative = self.relative(value, base)
        try:
            return json.loads(self.read(relative, LIMITS["manifest_bytes"])), relative
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ImportProblem("Ungültige JSON-Datei.") from exc

    def add(self, value, kind, base, role):
        relative = self.relative(value, base)
        if KINDS.get(Path(relative).suffix.lower()) != kind:
            raise ImportProblem(f"{role}: Dateityp passt nicht zu {kind}.")
        if relative not in self.media:
            if len(self.media) >= LIMITS["files"]:
                raise ImportProblem("Zu viele referenzierte Mediendateien.")
            content = self.read(relative, LIMITS["file_bytes"])
            if not content:
                raise ImportProblem(f"{role}: Datei ist leer.")
            self.media[relative] = {"path": relative, "kind": kind, "size": len(content),
                                    "sha256": self.hashes[relative], "roles": []}
            if sum(item["size"] for item in self.media.values()) > LIMITS["total_bytes"]:
                raise ImportProblem("Referenzierte Medien überschreiten zusammen 500 MB.")
        self.media[relative]["roles"].append(role)
        return relative


def _migrate_spot(data, warnings):
    warnings.append("spot.json: Shots werden zu Szenen; Kampagnenhistorie und alte Freigaben bleiben im Original.")
    shots = data.get("shots", [])
    if not isinstance(shots, list) or len(shots) > LIMITS["scenes"]:
        raise ImportProblem("shots muss eine Liste mit höchstens 100 Einträgen sein.")
    segments = []
    for index, shot in enumerate(shots):
        shot = _object(shot, "Shot")
        clip = shot.get("clip") or {}
        takes = shot.get("takes") or ([clip] if clip.get("file") else [])
        segments.append({"id": f"shot_{index}", "mode": "start" if shot.get("still_chosen") else "text",
                         "prompt": shot.get("motion") or shot.get("image_prompt") or "",
                         "duration_s": shot.get("duration_s", 6), "video_model": shot.get("video_model"),
                         "start_frame": {"file": shot.get("still_chosen"), "takes": shot.get("stills", [])},
                         "takes": takes, "take_chosen": clip.get("file"),
                         "onscreen_text": shot.get("onscreen_text", ""), "vo_line": shot.get("vo_line", "")})
    return {**data, "segments": segments, "title": data.get("title") or _object(data.get("concept") or {}, "concept").get("hook") or "Importierter Spot"}


def _brand(source, data, request, base, warnings):
    reference = request.brand_file
    if not reference and data.get("brand"):
        slug = data["brand"]
        if isinstance(slug, str) and re.fullmatch(r"[a-zA-Z0-9_-]+", slug):
            candidate = source.root / "brands" / slug / "brand.json"
            reference = str(candidate.relative_to(source.root)) if candidate.is_file() else None
    if not reference and (source.root / "brand.json").is_file():
        reference = "brand.json"
    if not reference:
        if data.get("brand"):
            warnings.append("Markenprofil nicht innerhalb der Quelle gefunden. Separaten relativen Markenpfad angeben oder Marke nach Import ausdrücklich zuweisen.")
        return None
    raw, filename = source.json(reference)
    raw = _object(raw, "Markenprofil")
    _unknown(raw, {"name", "colors", "font_family", "font", "language", "tone", "visual_style", "rules", "forbidden_claims", "claims_forbidden", "required_text", "logo_position", "safe_margin", "caption_style", "locked_fields", "logo_file", "font_file", "document_files", "audience", "cast", "cta_default", "notes", "music_style", "hook_patterns", "claims_allowed", "voice_id", "video_model", "image_model"}, "Markenprofil", warnings)
    brand_base = str(Path(filename).parent)
    colors = raw.get("colors") or {}
    mapped = {"name": raw.get("name") or "Importierte Marke",
              "colors": {"primary": colors.get("primary", "#ea765b"), "background": colors.get("background", colors.get("bg", "#10131a")), "text": colors.get("text", "#ffffff")},
              "font_family": raw.get("font_family", raw.get("font", "Arial")),
              "language": raw.get("language", "de"), "tone": raw.get("tone", ""),
              "visual_style": raw.get("visual_style", ""), "rules": raw.get("rules", []),
              "forbidden_claims": raw.get("forbidden_claims", raw.get("claims_forbidden", [])),
              "required_text": raw.get("required_text", "")}
    for key in ("logo_position", "safe_margin", "caption_style", "locked_fields"):
        if key in raw:
            mapped[key] = raw[key]
    for key, kind in (("logo_file", "image"), ("font_file", "font")):
        if raw.get(key):
            mapped[key.replace("_file", "_asset_id")] = source.add(raw[key], kind, brand_base, key)
    mapped["document_asset_ids"] = [source.add(value, "document", brand_base, "Styleguide") for value in raw.get("document_files", [])]
    if not mapped.get("font_asset_id") and mapped["font_family"] not in {"Arial", "sans-serif"}:
        warnings.append("Die alte Schrift hat keine kopierbare Font-Datei. Das Markenprofil wird nicht aktiviert; nach Import Schriftdatei hochladen und Profil anlegen.")
        return None
    # Preserve known text guidance, but never import model/provider configuration.
    guidance = [("audience", "Zielgruppe"), ("cast", "Figuren"), ("cta_default", "Standard-CTA"), ("notes", "Hinweis"), ("music_style", "Musikstil")]
    for key, label in guidance:
        if _text(raw.get(key)).strip():
            mapped["rules"] = [*mapped["rules"], f"{label}: {raw[key]}"]
    if raw.get("hook_patterns") or raw.get("claims_allowed") or raw.get("voice_id") or raw.get("video_model") or raw.get("image_model"):
        warnings.append("Marke: Hook-Muster, erlaubte Claims und Anbieter-/Stimmenvoreinstellungen werden nicht automatisch übertragen; Original bleibt erhalten.")
    try:
        profile = Brand.model_validate(mapped)
        if any(not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value) for value in profile.colors.values()):
            raise ImportProblem("Farben müssen sechsstellige Hexwerte sein.")
        if set(profile.locked_fields) - BRAND_FIELDS or len(set(profile.locked_fields)) != len(profile.locked_fields):
            raise ImportProblem("Ungültige Liste gesperrter Markenfelder.")
        for rules in (profile.rules, profile.forbidden_claims):
            if len(rules) > 100 or any(not item.strip() or len(item) > 2000 for item in rules):
                raise ImportProblem("Ungültige oder zu lange Markenregeln.")
        return profile.model_dump(mode="json")
    except ValidationError as exc:
        raise ImportProblem("Markenprofil enthält ungültige Farben, Abstände, Regeln oder Feldtypen.") from exc


def build_preview(request):
    source = Source(request)
    warnings = ["Import erstellt eine neue Kopie. Alte Freigaben werden nicht übernommen; alte Exporte werden nur archiviert.",
                "Keine API-Schlüssel, Agent-Chats, Konfigurationen oder URLs werden geladen. Nicht unterstützte Informationen bleiben im Original.",
                "Historische Marken- und Referenzbild-Zuordnungen alter Videotakes werden nicht nachträglich erfunden. Das Projekt erhält die gewählte Marke als Importsnapshot."]
    data, manifest = source.json(request.project_file)
    data = _object(data, "Projekt")
    legacy = "shots" in data and data.get("schema_version", 0) in {0, 1, 2}
    if legacy:
        data = _migrate_spot(data, warnings)
    elif data.get("schema_version") != 3:
        raise ImportProblem("Unterstützt werden project.json Version 3 und alte spot.json mit shots. Andere Versionen werden nicht geraten.")
    base = str(Path(manifest).parent)
    entries = data.get("segments", [])
    if not isinstance(entries, list) or len(entries) > LIMITS["scenes"]:
        raise ImportProblem("Segmente müssen eine Liste mit höchstens 100 Einträgen sein.")
    audio = _object(data.get("audio") or {}, "Audio")
    fmt = _object(data.get("format") or {}, "Format")
    plan = {"title": data.get("title") or "Importiertes Projekt", "recipe": data.get("recipe") or ("spot" if data.get("brand") else "free"),
            "format": {"width": fmt.get("w", 1080), "height": fmt.get("h", 1920), "fps": fmt.get("fps", 30)},
            "brief": _brief(data, warnings), "script": _text(data.get("script")) or _text(audio.get("vo_text")),
            "scenes": [], "audio": {"clip_audio": audio.get("use_clip_audio", True)}, "captions": []}
    report = {"frame_takes": [], "archived_exports": [], "legacy_segment_ids": []}
    known = {"schema_version", "id", "title", "recipe", "brand", "format", "segments", "audio", "render", "gates", "cost", "agent", "created_at", "_seq", "brief", "script", "spot", "_migrated_from", "shots", "concept", "campaign", "concepts", "stage", "rejections", "lineage", "vo_words", "error"}
    if set(data) - known:
        warnings.append(f"{len(set(data) - known)} unbekannte Projektfelder werden nicht übertragen.")
    if data.get("spot"):
        warnings.append("Spot-Historie bleibt im Original; unterstützte Briefingfelder werden als Text übernommen.")
    _unknown(audio, {"vo_file", "music_file", "vo_text", "word_timings", "use_clip_audio", "total_s"}, "Audio", warnings)
    for index, raw in enumerate(entries):
        raw = _object(raw, f"Segment {index + 1}")
        label = f"Szene {index + 1}"
        _unknown(raw, {"id", "mode", "prompt", "duration_s", "video_model", "onscreen_text", "vo_line", "start_frame", "end_frame", "takes", "take_chosen", "params", "legacy"}, label, warnings)
        scene = {"id": f"legacy_{index}", "title": label, "mode": raw.get("mode", "text"),
                 "prompt": raw.get("prompt", ""), "duration_s": raw.get("duration_s", 6),
                 "model": raw.get("video_model") or "legacy/unspecified", "onscreen_text": raw.get("onscreen_text", ""), "takes": []}
        report["legacy_segment_ids"].append(_text(raw.get("id")))
        if raw.get("vo_line") and not data.get("script") and not audio.get("vo_text"):
            plan["script"] += ("\n" if plan["script"] else "") + _text(raw["vo_line"])
        for which in ("start", "end"):
            frame = _object(raw.get(f"{which}_frame") or {}, f"{label} {which}")
            _unknown(frame, {"source", "file", "prompt", "takes"}, f"{label} {which}-Bild", warnings)
            if frame.get("prompt"):
                report.setdefault("frame_prompts", []).append({"scene_index": index, "slot": which, "prompt": _text(frame["prompt"])})
            if frame.get("file"):
                scene[f"{which}_asset_id"] = source.add(frame["file"], "image", base, f"{label} {which}")
            if frame.get("source") == "prev_last_frame":
                # Frozen extracted images are safer than inventing historic take lineage.
                warnings.append(f"{label}: Vorgänger-Verknüpfung wird als vorhandenes Standbild eingefroren; bei Bedarf neu verknüpfen.")
            for take in frame.get("takes", []):
                take = _object(take, f"{label} Bildtake")
                _unknown(take, {"file", "model"}, f"{label} Bildtake", warnings)
                media = source.add(take.get("file"), "image", base, f"{label} Bildtake")
                report["frame_takes"].append({"scene_index": index, "slot": which, "asset_id": media, "model": _text(take.get("model"))})
        takes = raw.get("takes", [])
        if not isinstance(takes, list) or len(takes) > 100:
            raise ImportProblem(f"{label}: Höchstens 100 Takes pro Szene unterstützt.")
        selected = raw.get("take_chosen")
        selected_relative = source.relative(selected, base) if selected else None
        for number, take in enumerate(takes):
            take = _object(take, f"{label} Take")
            _unknown(take, {"n", "file", "model", "prompt", "params", "seed", "mode", "duration_s", "note", "cost_eur", "measured", "at", "audio"}, f"{label} Take {number + 1}", warnings)
            media = source.add(take.get("file"), "video", base, f"{label} Take {number + 1}")
            params = _object(take.get("params") or {}, f"{label} Parameter")
            safe_params = {key: value for key, value in params.items() if key in PARAMS and isinstance(value, (str, int, float, bool))}
            if set(params) - set(safe_params):
                warnings.append(f"{label}, Take {number + 1}: Unbekannte/geschützte Anbieterparameter werden nicht kopiert.")
            for key in ("seed", "mode", "duration_s", "note"):
                if isinstance(take.get(key), (str, int, float, bool)):
                    safe_params[key] = take[key]
            new_take = {"id": f"legacy_{index}_{number}", "asset_id": media, "model": take.get("model") or "legacy/unspecified",
                        "prompt": _text(take.get("prompt")), "parameters": safe_params,
                        "cost": {"eur": take.get("cost_eur") if isinstance(take.get("cost_eur"), (int, float)) and math.isfinite(take["cost_eur"]) else None, "measured": take.get("measured") is True}}
            if take.get("at"):
                new_take["created_at"] = _text(take["at"])
            scene["takes"].append(new_take)
            if media == selected_relative:
                scene["selected_take_id"] = new_take["id"]
        if selected_relative and not scene.get("selected_take_id"):
            raise ImportProblem(f"{label}: Ausgewählter Take fehlt in der Take-Liste.")
        if takes and not selected:
            scene["selected_take_id"] = scene["takes"][-1]["id"]
            warnings.append(f"{label}: Kein expliziter Take gewählt; letzter Take entspricht dem alten Standard.")
        if not takes:
            warnings.append(f"{label}: Kein fertiger Videotake vorhanden; Szene muss noch fertiggestellt werden.")
        if not scene["model"].endswith("kling-video/v2.5-turbo/pro/image-to-video") or scene["duration_s"] not in {5, 10}:
            warnings.append(f"{label}: Alte Modell-/Dauereinstellung ist nicht direkt generierbar; vorhandene Takes bleiben renderbar.")
        if raw.get("params") or raw.get("legacy"):
            warnings.append(f"{label}: Szenen-Parameter und alte Grafikvorlagen werden nicht als ausführbare Vorlagen übertragen.")
        if scene["model"].startswith("fal:"):
            scene["model"] = "fal-ai/" + scene["model"][4:] if not scene["model"][4:].startswith("fal-ai/") else scene["model"][4:]
        plan["scenes"].append(scene)
    for old, new in (("vo_file", "narration_asset_id"), ("music_file", "music_asset_id")):
        if audio.get(old):
            plan["audio"][new] = source.add(audio[old], "audio", base, old)
    if audio.get("word_timings"):
        words, _ = source.json(audio["word_timings"], base)
        if not isinstance(words, list) or len(words) > 5000:
            raise ImportProblem("Wortzeiten müssen eine Liste mit höchstens 5000 Wörtern sein.")
        for word in words:
            word = _object(word, "Wortzeit")
            plan["captions"].append(Cue(text=word["w"], start_ms=round(float(word["start"]) * 1000), end_ms=round(float(word["end"]) * 1000)).model_dump())
    rendered = _object(data.get("render") or {}, "Export")
    if rendered.get("file"):
        report["archived_exports"].append(source.add(rendered["file"], "video", base, "Alter Export (Archiv)"))
    plan["brand_snapshot"] = _brand(source, data, request, base, warnings)
    plan["gates"] = dict.fromkeys(GATES, "todo") if plan["recipe"] == "spot" else {}
    try:
        Project.model_validate(plan)
    except ValidationError as exc:
        locations = ", ".join(".".join(map(str, item["loc"])) for item in exc.errors(include_input=False))
        raise ImportProblem(f"Inkompatible Projektfelder: {locations}.") from exc
    token = hashlib.sha256(json.dumps({"root": str(source.root), "request": request.model_dump(), "files": source.hashes}, sort_keys=True).encode()).hexdigest()
    result = {"token": token, "can_import": True, "summary": {"title": plan["title"], "scenes": len(plan["scenes"]),
              "takes": sum(len(scene["takes"]) for scene in plan["scenes"]), "files": len(source.media),
              "bytes": sum(item["size"] for item in source.media.values()), "brand": plan["brand_snapshot"]["name"] if plan["brand_snapshot"] else None},
              "media": list(source.media.values()), "warnings": list(dict.fromkeys(warnings)), "errors": [], "limits": LIMITS}
    return source, plan, report, result


def _verify_media(content, path, kind):
    if kind == "image":
        with Image.open(io.BytesIO(content)) as image:
            image.verify()
    elif kind == "font":
        if content[:4] not in {b"\x00\x01\x00\x00", b"OTTO", b"wOFF", b"wOF2", b"true"}:
            raise ImportProblem("Ungültige Schriftdatei.")
    elif kind in {"video", "audio"}:
        with tempfile.NamedTemporaryFile(suffix=Path(path).suffix) as temporary:
            temporary.write(content)
            temporary.flush()
            result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", *local_input_options(temporary.name), temporary.name], capture_output=True, text=True, timeout=20, check=True)
        if kind not in {stream.get("codec_type") for stream in json.loads(result.stdout).get("streams", [])}:
            raise ImportProblem("Datei enthält keine passende Medien-Spur.")
    elif Path(path).suffix.lower() == ".pdf" and not content.startswith(b"%PDF-"):
        raise ImportProblem("Ungültige PDF-Datei.")


def commit_import(store, request):
    if not request.confirmed:
        raise HTTPException(409, "Import bitte nach Prüfung der Vorschau ausdrücklich bestätigen.")
    selected = ImportRequest(source_dir=request.source_dir, project_file=request.project_file, brand_file=request.brand_file)
    source, plan, report, preview = build_preview(selected)
    if preview["token"] != request.token:
        raise HTTPException(409, "Quelle hat sich seit der Vorschau geändert. Vorschau neu laden.")
    created = []
    brand = None
    project = None
    with store.lock:
        try:
            mapping = {}
            for relative, meta in source.media.items():
                content = source.read(relative, LIMITS["file_bytes"])
                if hashlib.sha256(content).hexdigest() != meta["sha256"]:
                    raise HTTPException(409, "Mediendatei wurde während des Imports verändert. Vorschau neu laden.")
                _verify_media(content, relative, meta["kind"])
                asset = store.add_asset(content, Path(relative).name, meta["kind"], mimetypes.guess_type(relative)[0] or "application/octet-stream")
                mapping[relative] = asset.id
                created.append(asset)
            for scene in plan["scenes"]:
                scene["id"] = new_id()
                for key in ("start_asset_id", "end_asset_id"):
                    if scene.get(key):
                        scene[key] = mapping[scene[key]]
                for take in scene["takes"]:
                    old_id, take["id"] = take["id"], new_id()
                    take["asset_id"] = mapping[take["asset_id"]]
                    if scene.get("selected_take_id") == old_id:
                        scene["selected_take_id"] = take["id"]
            for key in ("narration_asset_id", "music_asset_id"):
                if plan["audio"].get(key):
                    plan["audio"][key] = mapping[plan["audio"][key]]
            if plan["brand_snapshot"]:
                raw = plan["brand_snapshot"]
                for key in ("logo_asset_id", "font_asset_id"):
                    if raw.get(key):
                        raw[key] = mapping[raw[key]]
                raw["document_asset_ids"] = [mapping[value] for value in raw["document_asset_ids"]]
                brand = validate_brand(store, raw)
                plan["brand_snapshot"] = brand
            project = Project.model_validate(plan)
            for item in report["frame_takes"]:
                item["asset_id"] = mapping[item["asset_id"]]
            report["archived_exports"] = [mapping[value] for value in report["archived_exports"]]
            report.update(project_id=project.id, warnings=preview["warnings"], source_fingerprint=preview["token"],
                          media=[{"source_path": path, "asset_id": aid} for path, aid in mapping.items()])
            if brand:
                store.write("brand_versions", f"{brand.id}_1", brand)
                store.write("brands", brand.id, brand)
            store.write("imports", project.id, report)
            store.write("projects", project.id, project)
            return project
        except Exception:
            # Only delete files created by this attempt; originals never change.
            for asset in created:
                (store.root / asset.path).unlink(missing_ok=True)
                store.path("assets", asset.id).unlink(missing_ok=True)
            if brand:
                store.path("brand_versions", f"{brand.id}_1").unlink(missing_ok=True)
                store.path("brands", brand.id).unlink(missing_ok=True)
            if project:
                store.path("imports", project.id).unlink(missing_ok=True)
                store.path("projects", project.id).unlink(missing_ok=True)
            raise


def create_router(store):
    router = APIRouter()

    @router.post("/imports/preview")
    def preview(request: ImportRequest):
        try:
            return build_preview(request)[3]
        except (ValueError, OSError, KeyError, TypeError) as exc:
            message = str(exc) if isinstance(exc, ImportProblem) else "Quelle unvollständig, inkompatibel oder nicht lesbar. Manifest und Dateiverweise prüfen."
            return {"can_import": False, "token": None, "summary": None, "media": [], "warnings": [], "errors": [message], "limits": LIMITS}

    @router.post("/imports/commit", status_code=201)
    def commit(request: CommitRequest):
        try:
            return commit_import(store, request)
        except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            raise HTTPException(422, "Import fehlgeschlagen: Quelle, Medienformat oder FFmpeg prüfen. Originale wurden nicht verändert.") from exc

    @router.get("/imports/{project_id}/report")
    def report(project_id: str):
        try:
            return store.read("imports", project_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(404, "Importbericht nicht gefunden.") from exc

    return router
