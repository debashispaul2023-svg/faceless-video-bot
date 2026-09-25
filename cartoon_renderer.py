"""Independent scene renderer for the cartoon pipeline (FFmpeg, 1920x1080 @ 30fps).

Stills receive camera motion (zoom/pan). This is not full AI animation.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import config
from audio_cues import attach_cues
from audio_timeline import SceneTimeline, build_scene_timeline, format_timeline
from checkpoint import is_done, mark_done
from media_utils import concat_videos, is_valid_media, media_duration, run_ffmpeg, write_silence
from models import ProductionPlan, ScenePlan


def _scene_dir(episode_dir: Path, scene: ScenePlan) -> Path:
    path = Path(episode_dir) / "scenes" / f"{scene.index:03d}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _mix_scene_audio(scene: ScenePlan, timeline: SceneTimeline, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    duration = max(2.0, float(timeline.duration))
    inputs = []
    filters = []
    labels = []

    bed = dest.with_name("bed_silence.wav")
    write_silence(bed, duration)
    inputs.extend(["-i", str(bed)])
    filters.append("[0:a]volume=0.0[bed]")
    labels.append("[bed]")
    idx = 1

    def add_clip(path: str, start: float, volume: float, tag: str):
        nonlocal idx
        if not path or not Path(path).is_file():
            return
        inputs.extend(["-i", str(path)])
        delay_ms = max(0, int(start * 1000))
        filters.append(
            f"[{idx}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
            f"volume={volume:.3f},adelay={delay_ms}|{delay_ms}[{tag}]"
        )
        labels.append(f"[{tag}]")
        idx += 1

    d_i = 0
    s_i = 0
    for event in timeline.events:
        if event.kind == "dialogue" and event.path:
            add_clip(event.path, event.start, config.DIALOGUE_VOLUME, f"d{d_i}")
            d_i += 1
        elif event.kind in {"sfx", "ambience", "music"} and event.path:
            vol = event.volume
            if event.kind == "music":
                vol = min(vol, config.BGM_VOLUME)
            elif event.kind == "ambience":
                vol = min(vol, config.AMBIENCE_VOLUME)
            else:
                vol = min(vol, config.SFX_VOLUME)
            add_clip(event.path, event.start, vol, f"s{s_i}")
            s_i += 1

    if len(labels) == 1:
        write_silence(dest, duration)
        return dest

    mix = "".join(labels) + f"amix=inputs={len(labels)}:duration=first:dropout_transition=0:normalize=0[outa]"
    filters.append(mix)
    args = inputs + [
        "-filter_complex", ";".join(filters),
        "-map", "[outa]",
        "-t", f"{duration:.3f}",
        "-q:a", "4",
        str(dest),
    ]
    run_ffmpeg(args)
    return dest


def _camera_filter(duration: float, index: int) -> str:
    frames = max(int(duration * config.FPS), config.FPS)
    # Alternate zoom-in / slight pan so neighbouring scenes do not feel identical.
    if index % 2 == 0:
        zoom = "min(1.0+0.0015*on,1.12)"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    else:
        zoom = "min(1.04+0.0009*on,1.14)"
        x = "iw/2-(iw/zoom/2)+8*sin(on/25)"
        y = "ih/2-(ih/zoom/2)"
    return (
        f"scale={config.VIDEO_WIDTH * 2}:{config.VIDEO_HEIGHT * 2},"
        f"zoompan=z='{zoom}':x='{x}':y='{y}':d={frames}:s={config.VIDEO_WIDTH}x{config.VIDEO_HEIGHT}:fps={config.FPS},"
        f"format=yuv420p"
    )


def render_scene(scene: ScenePlan, episode_dir: Path, skip_done: bool = True) -> Path:
    episode_id = Path(episode_dir).name
    scene_dir = _scene_dir(episode_dir, scene)
    out = scene_dir / f"scene_{scene.index:03d}.mp4"
    flag = f"{scene.scene_id}_render_done"
    if skip_done and is_done(episode_id, flag) and is_valid_media(out):
        scene.render_path = str(out)
        scene.duration = media_duration(out) or scene.duration
        print(f"[i] Skip render {scene.scene_id} (checkpoint)")
        return out

    attach_cues(scene)
    timeline = build_scene_timeline(scene)
    print(format_timeline(timeline))
    (scene_dir / "timeline.json").write_text(
        __import__("json").dumps(timeline.to_dict(), indent=2)
    )

    visual = Path(scene.visual_path) if scene.visual_path else scene_dir / "visual.jpg"
    if not visual.is_file():
        raise FileNotFoundError(f"Scene {scene.scene_id} has no visual at {visual}")

    mixed = scene_dir / "mix.wav"
    _mix_scene_audio(scene, timeline, mixed)
    duration = max(timeline.duration, media_duration(mixed), 2.0)

    tmp = out.with_suffix(".tmp.mp4")
    vf = _camera_filter(duration, scene.index)
    if scene.visual_type == "video" and visual.suffix.lower() in {".mp4", ".mov", ".webm"}:
        run_ffmpeg([
            "-i", str(visual),
            "-i", str(mixed),
            "-vf", f"scale={config.VIDEO_WIDTH}:{config.VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
                   f"crop={config.VIDEO_WIDTH}:{config.VIDEO_HEIGHT},fps={config.FPS},format=yuv420p",
            "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", config.FFMPEG_PRESET, "-crf", config.FFMPEG_CRF,
            "-c:a", "aac", "-b:a", "160k",
            "-shortest", "-movflags", "+faststart",
            str(tmp),
        ], timeout=300)
    else:
        run_ffmpeg([
            "-loop", "1", "-i", str(visual),
            "-i", str(mixed),
            "-vf", vf,
            "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", config.FFMPEG_PRESET, "-crf", config.FFMPEG_CRF,
            "-c:a", "aac", "-b:a", "160k",
            "-shortest", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(tmp),
        ], timeout=300)
    tmp.replace(out)
    scene.render_path = str(out)
    scene.duration = media_duration(out) or duration
    mark_done(episode_id, flag)
    print(f"[+] Rendered {scene.scene_id}: {out} ({scene.duration:.2f}s)")
    return out


def render_episode(plan: ProductionPlan, episode_dir: Path, skip_done: bool = True) -> Path:
    episode_dir = Path(episode_dir)
    rendered: List[Path] = []
    failures = []
    for scene in plan.scenes:
        try:
            rendered.append(render_scene(scene, episode_dir, skip_done=skip_done))
        except Exception as e:
            failures.append(f"{scene.scene_id}: {e}")
            print(f"[!] Scene {scene.scene_id} failed ({e}). Earlier scenes kept.")
    if not rendered:
        raise RuntimeError("No scenes rendered: " + "; ".join(failures))
    final = episode_dir / "final.mp4"
    concat_videos(rendered, final, reencode=False)
    print(f"[+] Episode assembled: {final}")
    if failures:
        print(f"[!] Incomplete episode, missing scenes: {failures}")
    return final
