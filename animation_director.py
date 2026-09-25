"""Character animation layer.

Long-term target: reference-conditioned character motion.
Current supported path: still + camera move + timed audio.
Do not label the fallback as true character animation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from visual_providers import get_visual_provider


def can_animate() -> bool:
    return bool(get_visual_provider().supports_video_generation())


def generate_scene_motion(scene, dest: Path) -> Optional[Path]:
    provider = get_visual_provider()
    if not provider.supports_video_generation():
        print("[i] No video provider available — renderer will use still + camera motion.")
        return None
    refs = []
    return provider.generate_video_with_reference(
        prompt=scene.animation_instructions or scene.action,
        output_path=dest,
        width=1920,
        height=1080,
        reference_images=refs,
        duration=max(4.0, scene.target_duration or 4.0),
    )
