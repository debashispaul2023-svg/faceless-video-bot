"""Validate production plans and finished episode packages."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import config
from media_utils import is_valid_media, media_duration
from models import ProductionPlan, ValidationResult
from story_engine import validate_plan


def validate_episode_package(plan: ProductionPlan, episode_dir: Path, video_path: Optional[Path] = None,
                             thumbnail_path: Optional[Path] = None) -> ValidationResult:
    base = validate_plan(plan)
    errors = list(base.errors)
    warnings = list(base.warnings)
    details = {"scenes": []}

    total = 0.0
    for scene in plan.scenes:
        info = {"scene_id": scene.scene_id, "ok": True}
        visual = Path(scene.visual_path) if scene.visual_path else episode_dir / "scenes" / f"{scene.index:03d}" / "visual.jpg"
        render = Path(scene.render_path) if scene.render_path else episode_dir / "scenes" / f"{scene.index:03d}" / f"scene_{scene.index:03d}.mp4"
        if not visual.is_file():
            errors.append(f"Missing visual for {scene.scene_id}")
            info["ok"] = False
        if not render.is_file() or not is_valid_media(render):
            errors.append(f"Missing or corrupt scene render for {scene.scene_id}")
            info["ok"] = False
        else:
            dur = media_duration(render)
            info["duration"] = dur
            total += dur
            if dur < 1.0:
                errors.append(f"{scene.scene_id} render is too short ({dur:.2f}s)")
        for i, line in enumerate(scene.dialogue):
            if not line.audio_path or not Path(line.audio_path).is_file():
                errors.append(f"Missing dialogue audio {scene.scene_id} line {i} ({line.character_id})")
                info["ok"] = False
            elif line.duration and abs(media_duration(Path(line.audio_path)) - line.duration) > 1.25:
                warnings.append(f"Audio duration drift on {scene.scene_id} line {i}")
        details["scenes"].append(info)

    details["episode_duration"] = total
    if total and total < config.MIN_EPISODE_SECONDS:
        warnings.append(f"Episode is short ({total:.1f}s < {config.MIN_EPISODE_SECONDS}s)")
    if total > config.MAX_EPISODE_SECONDS:
        warnings.append(f"Episode is long ({total:.1f}s > {config.MAX_EPISODE_SECONDS}s)")

    if video_path:
        if not is_valid_media(video_path):
            errors.append(f"Final video missing or corrupt: {video_path}")
        else:
            vdur = media_duration(video_path)
            details["final_duration"] = vdur
            if total and abs(vdur - total) > 2.5:
                warnings.append(f"Final video duration {vdur:.1f}s vs scene sum {total:.1f}s")

    if thumbnail_path and not Path(thumbnail_path).is_file():
        errors.append("Missing thumbnail")

    return ValidationResult(valid=not errors, errors=errors, warnings=warnings, details=details)
