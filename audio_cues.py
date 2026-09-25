"""Resolve scene audio cues to local files. Missing optional SFX is fine."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

import config
from models import AudioCue, ScenePlan


def _index_sfx() -> Dict[str, Path]:
    mapping: Dict[str, Path] = {}
    sfx_dir = config.ASSETS_DIR / "sfx"
    if not sfx_dir.is_dir():
        return mapping
    for path in sfx_dir.iterdir():
        if path.suffix.lower() not in {".mp3", ".wav", ".m4a", ".ogg"}:
            continue
        key = _normalize(path.stem)
        mapping[key] = path
        for token in key.split():
            mapping.setdefault(token, path)
    # Friendly aliases for the cartoon world using existing local files only.
    aliases = {
        "pop": "pop",
        "whoosh": "rake swing whoosh close",
        "whoosh_hall": "rake swing whoosh hall",
        "tick": "clock ticking",
        "click": "twisting dial clicks",
        "swipe": "credit card swipe",
        "ding": "ding",
    }
    extra = {}
    ding = config.ASSETS_DIR / "ding.mp3"
    if ding.is_file():
        extra["ding"] = ding
        extra["chime"] = ding
    for alias, target in aliases.items():
        if target in mapping:
            extra[alias] = mapping[target]
    mapping.update(extra)
    return mapping


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def resolve_cue_path(name: str) -> Optional[Path]:
    if not name:
        return None
    raw = Path(name)
    if raw.is_file():
        return raw
    catalog = _index_sfx()
    key = _normalize(name)
    if key in catalog:
        return catalog[key]
    for token in key.split():
        if token in catalog:
            return catalog[token]
    music = config.MUSIC_DIR / name
    if music.is_file():
        return music
    return None


def attach_cues(scene: ScenePlan) -> ScenePlan:
    """Fill cue.path from local assets. Leave missing optional cues empty."""
    resolved: List[AudioCue] = []
    for cue in scene.cues or []:
        path = resolve_cue_path(cue.name) or (Path(cue.path) if cue.path else None)
        if path and path.is_file():
            cue.path = str(path)
            resolved.append(cue)
        elif cue.optional:
            print(f"[i] Optional {cue.kind} '{cue.name}' missing — skipped.")
        else:
            print(f"[!] Required {cue.kind} '{cue.name}' missing.")
            resolved.append(cue)
    scene.cues = resolved
    return scene


def default_cues_for_scene(index: int) -> List[AudioCue]:
    cues = [
        AudioCue(kind="ambience", name="soft_grove", volume=config.AMBIENCE_VOLUME, start=0.0, optional=True),
        AudioCue(kind="sfx", name="whoosh" if index == 0 else "pop", volume=config.SFX_VOLUME, start=0.15, duration=1.2, optional=True),
    ]
    music_files = []
    if config.MUSIC_DIR.is_dir():
        music_files = [p for p in config.MUSIC_DIR.iterdir() if p.suffix.lower() in {".mp3", ".wav", ".m4a"}]
    if music_files:
        cues.append(AudioCue(
            kind="music",
            name=music_files[0].name,
            path=str(music_files[0]),
            volume=config.BGM_VOLUME,
            start=0.0,
            optional=True,
        ))
    return cues
