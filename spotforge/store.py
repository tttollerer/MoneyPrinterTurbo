"""Single-process JSON store with atomic durable writes and bounded asset paths."""
import hashlib
import json
import os
import re
import threading
from pathlib import Path
from uuid import uuid4

from spotforge.models import Asset


class Store:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def path(self, collection, key):
        if not re.fullmatch(r"[a-z_]+", collection) or not re.fullmatch(r"[a-zA-Z0-9_-]+", key):
            raise ValueError("Invalid document identifier")
        return self.root / collection / f"{key}.json"

    def read(self, collection, key):
        with self.lock:
            return json.loads(self.path(collection, key).read_text())

    def write(self, collection, key, value):
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        with self.lock:
            target = self.path(collection, key)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(f".{uuid4().hex}.tmp")
            try:
                with temporary.open("w") as stream:
                    json.dump(value, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return value

    def list(self, collection):
        self.path(collection, "check")
        with self.lock:
            return [json.loads(p.read_text()) for p in sorted((self.root / collection).glob("*.json"))]

    def asset_path(self, asset_id):
        asset = Asset.model_validate(self.read("assets", asset_id))
        resolved = (self.root / asset.path).resolve()
        if not resolved.is_relative_to(self.root / "media") or not resolved.is_file():
            raise ValueError("Asset file missing or outside media directory")
        return resolved

    def add_asset(self, content, name, kind, mime):
        suffix = Path(name).suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
            suffix = ".bin"
        asset_id = uuid4().hex
        rel = f"media/{asset_id}{suffix}"
        dest = self.root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        asset = Asset(id=asset_id, name=Path(name).name, kind=kind, path=rel,
                      sha256=hashlib.sha256(content).hexdigest(), mime=mime, size=len(content))
        self.write("assets", asset.id, asset)
        return asset
