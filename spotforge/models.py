"""Versioned shared contracts. No provider/network or storage side effects."""
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def new_id() -> str:
    return uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Format(Model):
    width: int = Field(default=1080, ge=128, le=3840)
    height: int = Field(default=1920, ge=128, le=3840)
    fps: int = Field(default=30, ge=1, le=60)


class Asset(Model):
    id: str = Field(default_factory=new_id)
    name: str
    kind: Literal["image", "video", "audio", "font", "document"]
    path: str
    sha256: str
    mime: str
    size: int = Field(ge=0)
    created_at: str = Field(default_factory=now)


class Brand(Model):
    schema_version: Literal[1] = 1
    id: str = Field(default_factory=new_id)
    version: int = Field(default=1, ge=1)
    name: str = Field(min_length=1, max_length=120)
    colors: dict[str, str] = Field(default_factory=lambda: {"primary": "#ea765b", "background": "#10131a", "text": "#ffffff"})
    font_family: str = "Arial"
    font_asset_id: str | None = None
    logo_asset_id: str | None = None
    logo_position: Literal["top-left", "top-right", "bottom-left", "bottom-right"] = "top-right"
    safe_margin: float = Field(default=0.07, ge=0, le=0.25)
    caption_style: Literal["sentence", "karaoke"] = "sentence"
    language: str = "de"
    tone: str = ""
    visual_style: str = ""
    rules: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    required_text: str = ""
    document_asset_ids: list[str] = Field(default_factory=list)
    locked_fields: list[str] = Field(default_factory=lambda: ["logo_asset_id", "font_asset_id", "colors", "required_text"])
    created_at: str = Field(default_factory=now)


class Take(Model):
    id: str = Field(default_factory=new_id)
    asset_id: str
    model: str = "local"
    prompt: str = ""
    parameters: dict = Field(default_factory=dict)
    start_asset_id: str | None = None
    end_asset_id: str | None = None
    predecessor_take_id: str | None = None
    brand_snapshot: dict = Field(default_factory=dict)
    provider_request_id: str | None = None
    cost: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=now)


class Scene(Model):
    id: str = Field(default_factory=new_id)
    title: str = "Szene"
    mode: Literal["text", "start", "end", "start_end", "local"] = "local"
    prompt: str = ""
    duration_s: float = Field(default=5, ge=0.1, le=120)
    start_asset_id: str | None = None
    end_asset_id: str | None = None
    predecessor_scene_id: str | None = None
    source_asset_id: str | None = None
    model: str = "fal-ai/kling-video/v2.5-turbo/pro/image-to-video"
    resolution: Literal["480p", "720p"] = "720p"
    generate_audio: bool = True
    bitrate_mode: Literal["standard", "high"] = "standard"
    takes: list[Take] = Field(default_factory=list)
    selected_take_id: str | None = None
    stale: bool = False
    onscreen_text: str = ""


class Word(Model):
    text: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("End time must follow start time")
        return self


class Cue(Word):
    words: list[Word] = Field(default_factory=list)


class Audio(Model):
    narration_asset_id: str | None = None
    music_asset_id: str | None = None
    narration_gain: float = Field(default=1, ge=0, le=2)
    music_gain: float = Field(default=0.15, ge=0, le=1)
    clip_audio: bool = False


class Project(Model):
    schema_version: Literal[1] = 1
    id: str = Field(default_factory=new_id)
    revision: int = Field(default=1, ge=1)
    title: str = Field(default="Neues Video", min_length=1, max_length=200)
    brief: str = Field(default="", max_length=8000)
    script: str = Field(default="", max_length=12000)
    recipe: Literal["free", "spot"] = "free"
    format: Format = Field(default_factory=Format)
    brand_snapshot: Brand | None = None
    scenes: list[Scene] = Field(default_factory=list)
    audio: Audio = Field(default_factory=Audio)
    captions: list[Cue] = Field(default_factory=list)
    gates: dict[str, str] = Field(default_factory=dict)
    render: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=now)


class Job(Model):
    id: str = Field(default_factory=new_id)
    project_id: str
    scene_id: str | None = None
    kind: Literal["generation", "render", "speech", "stock_import"]
    state: Literal["queued", "submitting", "running", "complete", "failed", "interrupted", "unknown"] = "queued"
    progress: float = Field(default=0, ge=0, le=1)
    provider_request_id: str | None = None
    input_snapshot: dict = Field(default_factory=dict)
    result: dict = Field(default_factory=dict)
    error: str | None = None
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)


class RenderScene(Model):
    id: str
    asset_id: str
    from_frame: int = Field(ge=0)
    duration_frames: int = Field(gt=0)
    onscreen_text: str = ""


class RenderManifest(Model):
    schema_version: Literal[1] = 1
    project_id: str
    project_revision: int
    format: Format
    duration_frames: int = Field(gt=0)
    brand: Brand | None = None
    assets: dict[str, dict]
    scenes: list[RenderScene]
    audio: Audio = Field(default_factory=Audio)
    captions: list[Cue] = Field(default_factory=list)
