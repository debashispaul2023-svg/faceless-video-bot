"""Small FFmpeg helpers used across the cartoon pipeline."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional


class FFmpegError(RuntimeError):
    pass


def run_ffmpeg(args: list, timeout: int = 180) -> None:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise FFmpegError(proc.stderr[-800:] or proc.stdout[-800:] or "ffmpeg failed")


def ffprobe_json(path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error", "-show_format", "-show_streams",
        "-of", "json", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise FFmpegError(proc.stderr[-400:] or "ffprobe failed")
    return json.loads(proc.stdout or "{}")


def media_duration(path: Optional[Path]) -> float:
    if not path or not Path(path).is_file():
        return 0.0
    try:
        info = ffprobe_json(Path(path))
        dur = info.get("format", {}).get("duration")
        if dur is not None:
            return float(dur)
        for stream in info.get("streams") or []:
            if stream.get("duration"):
                return float(stream["duration"])
    except Exception:
        return 0.0
    return 0.0


def is_valid_media(path: Optional[Path]) -> bool:
    if not path or not Path(path).is_file() or Path(path).stat().st_size < 64:
        return False
    try:
        info = ffprobe_json(Path(path))
        return bool(info.get("streams"))
    except Exception:
        return False


def write_silence(path: Path, duration: float, sample_rate: int = 44100) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.15, float(duration))
    run_ffmpeg([
        "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl=stereo",
        "-t", f"{duration:.3f}",
        "-q:a", "4",
        str(path),
    ])
    return path


def concat_videos(paths: list, output: Path, reencode: bool = False) -> Path:
    """Concatenate scene videos. Copy when possible; re-encode if needed."""
    output.parent.mkdir(parents=True, exist_ok=True)
    list_file = output.with_suffix(".concat.txt")
    lines = []
    for p in paths:
        escaped = str(Path(p).resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    list_file.write_text("\n".join(lines) + "\n")
    tmp = output.with_suffix(".tmp.mp4")
    try:
        if not reencode:
            try:
                run_ffmpeg([
                    "-f", "concat", "-safe", "0", "-i", str(list_file),
                    "-c", "copy", str(tmp),
                ])
                tmp.replace(output)
                return output
            except FFmpegError:
                pass
        run_ffmpeg([
            "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "160k",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(tmp),
        ], timeout=600)
        tmp.replace(output)
        return output
    finally:
        if list_file.exists():
            try:
                list_file.unlink()
            except OSError:
                pass
        if tmp.exists() and tmp != output:
            try:
                tmp.unlink()
            except OSError:
                pass
