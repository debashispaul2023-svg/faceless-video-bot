"""Structured production data for the cartoon pipeline.

The renderer consumes these objects — it does not parse free-form AI text.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


def _as_dict(obj: Any) -> Any:
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, Path):
        return str(obj)
    return obj


@dataclass
class DialogueLine:
    character_id: str
    text: str
    emotion: str = "neutral"
    audio_path: Optional[str] = None
    duration: float = 0.0
    start: float = 0.0
    end: float = 0.0
    words: List[Dict[str, Any]] = field(default_factory=list)
    cache_key: str = ""
    voice_id: str = ""
    estimated_duration: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DialogueLine":
        allowed = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class AudioCue:
    kind: str  # ambience | sfx | music
    name: str
    path: Optional[str] = None
    volume: float = 0.4
    start: float = 0.0
    duration: Optional[float] = None
    offset: float = 0.0
    optional: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AudioCue":
        allowed = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class ScenePlan:
    scene_id: str
    index: int
    title: str
    environment: str
    action: str
    camera: str
    style: str
    characters: List[str]
    dialogue: List[DialogueLine] = field(default_factory=list)
    cues: List[AudioCue] = field(default_factory=list)
    transition: str = "cut"
    target_duration: float = 8.0
    visual_path: Optional[str] = None
    visual_type: str = "image"  # image | video
    render_path: Optional[str] = None
    duration: float = 0.0
    notes: str = ""
    location: str = ""
    expressions: str = ""
    lighting: str = "warm daylight"
    visual_prompt: str = ""
    animation_instructions: str = ""
    continuity_notes: str = ""
    beat: str = ""  # hook|setup|problem|escalation|comedy|resolution|ending

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["dialogue"] = [d.to_dict() for d in self.dialogue]
        data["cues"] = [c.to_dict() for c in self.cues]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScenePlan":
        raw = dict(data)
        raw["dialogue"] = [DialogueLine.from_dict(d) for d in raw.get("dialogue") or []]
        raw["cues"] = [AudioCue.from_dict(c) for c in raw.get("cues") or []]
        allowed = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in raw.items() if k in allowed})


@dataclass
class StoryDraft:
    idea: str
    title: str
    logline: str
    synopsis: str
    moral: str
    characters: List[str]
    setting: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StoryDraft":
        allowed = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class EpisodeOutline:
    episode_id: str
    title: str
    youtube_title: str
    logline: str
    synopsis: str
    moral: str
    characters: List[str]
    scene_beats: List[Dict[str, Any]] = field(default_factory=list)
    series: str = "Sunny Grove Friends"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EpisodeOutline":
        allowed = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class ProductionPlan:
    episode_id: str
    title: str
    logline: str
    synopsis: str
    moral: str
    characters: List[str]
    scenes: List[ScenePlan]
    style: str
    series: str = "Sunny Grove Friends"
    language: str = "en"
    thumbnail_prompt: str = ""
    tags: List[str] = field(default_factory=list)
    description: str = ""
    youtube_title: str = ""
    idea: str = ""
    target_duration: float = 180.0
    structure: List[str] = field(default_factory=lambda: [
        "hook", "setup", "problem", "escalation", "comedy", "resolution", "ending"
    ])

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["scenes"] = [s.to_dict() for s in self.scenes]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProductionPlan":
        raw = dict(data)
        raw["scenes"] = [ScenePlan.from_dict(s) for s in raw.get("scenes") or []]
        allowed = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in raw.items() if k in allowed})


@dataclass
class ValidationResult:
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "details": self.details,
        }
