"""Stitch already-rendered episodes into a longer compilation.

Does not regenerate episode assets.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import config
from media_utils import concat_videos, is_valid_media, run_ffmpeg
from quality_control import ValidationResult


def _title_card(text: str, dest: Path, seconds: float = 3.0) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    safe = text.replace(":", " -").replace("'", "")[:48]
    run_ffmpeg([
        "-f", "lavfi", "-i", f"color=c=0x1B3B2F:s={config.VIDEO_WIDTH}x{config.VIDEO_HEIGHT}:d={seconds:.2f}:r={config.FPS}",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-vf", f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
               f"text='{safe}':fontcolor=white:fontsize=56:x=(w-text_w)/2:y=(h-text_h)/2",
        "-t", f"{seconds:.2f}",
        "-c:v", "libx264", "-preset", config.FFMPEG_PRESET, "-crf", config.FFMPEG_CRF,
        "-c:a", "aac", "-shortest", "-pix_fmt", "yuv420p",
        str(dest),
    ])
    return dest


def compile_episodes(
    episode_videos: List[Path],
    output_path: Optional[Path] = None,
    compilation_id: str = "compilation_001",
    add_title_card: bool = True,
) -> Path:
    usable = [Path(p) for p in episode_videos if is_valid_media(Path(p))]
    if len(usable) < 1:
        raise FileNotFoundError("No valid episode videos to compile.")
    out = Path(output_path or (config.COMPILATIONS_DIR / f"{compilation_id}.mp4"))
    out.parent.mkdir(parents=True, exist_ok=True)
    pieces: List[Path] = []
    tmp_dir = config.TEMP_DIR / compilation_id
    tmp_dir.mkdir(parents=True, exist_ok=True)
    if add_title_card:
        pieces.append(_title_card(config.SERIES_NAME, tmp_dir / "title.mp4", 3.0))
    pieces.extend(usable)
    concat_videos(pieces, out, reencode=False)
    print(f"[+] Compilation written: {out}")
    return out


def find_completed_episodes(limit: int = 8) -> List[Path]:
    found: List[Path] = []
    if not config.EPISODES_DIR.is_dir():
        return found
    for folder in sorted(config.EPISODES_DIR.iterdir()):
        final = folder / "final.mp4"
        if is_valid_media(final):
            found.append(final)
        if len(found) >= limit:
            break
    return found
