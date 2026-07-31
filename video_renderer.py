import os
import random
import traceback
from pathlib import Path
from moviepy import (
    VideoFileClip, AudioFileClip, ImageClip, TextClip,
    CompositeVideoClip, concatenate_videoclips, vfx
)
import config

# moviepy>=2.0 REQUIRES an explicit font file path for TextClip — it will not
# silently fall back to a system default. If no valid path is given, TextClip
# raises an exception, which was previously being swallowed and made the
# whole caption feature silently disappear. We look for a few fonts that are
# already installed on the GitHub Actions ubuntu-latest runner (make sure the
# workflow also `apt-get install`s fonts-dejavu-core, see daily_bot.yml).
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    str(config.ASSETS_DIR / "fonts" / "CaptionFont-Bold.ttf"),
]

# How much two neighbouring scenes overlap/blend into each other.
_CROSSFADE_DURATION = 0.4
# How much an image "grows" over its on-screen duration for the Ken Burns effect.
_KEN_BURNS_ZOOM = 0.12


def _resolve_font_path() -> str:
    for candidate in _FONT_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(
        "No usable font file found for captions. Install 'fonts-dejavu-core' "
        "in the workflow, or drop a .ttf file at assets/fonts/CaptionFont-Bold.ttf"
    )


def _build_captions(narration_text: str, total_duration: float):
    """Builds word-chunked, Hormozi-style bold captions synced to audio duration."""
    words = narration_text.split()
    if not words:
        return []

    font_path = _resolve_font_path()

    chunk_size = 3
    num_chunks = max(1, len(words) // chunk_size)
    chunk_duration = total_duration / num_chunks

    sub_clips = []
    for i in range(0, len(words), chunk_size):
        chunk_text = " ".join(words[i:i + chunk_size]).upper()
        start_time = (i // chunk_size) * chunk_duration

        txt_clip = (
            TextClip(
                font=font_path,
                text=chunk_text,
                font_size=70,
                color='yellow',
                stroke_color='black',
                stroke_width=4,
                method='caption',
                size=(config.VIDEO_WIDTH - 120, None)
            )
            .with_position(('center', 1350))
            .with_start(start_time)
            .with_duration(chunk_duration)
        )
        sub_clips.append(txt_clip)

    return sub_clips


def _cover_resize_and_crop(clip, target_w: int, target_h: int):
    """Resizes `clip` to fully cover a target_w x target_h frame, then
    centre-crops any overflow. Works for both images and videos."""
    clip = clip.resized(height=target_h)
    if clip.w < target_w:
        clip = clip.resized(width=target_w)
    return clip.cropped(
        x_center=clip.w / 2, y_center=clip.h / 2,
        width=target_w, height=target_h
    )


def _ken_burns_image_clip(path: Path, duration: float, target_w: int, target_h: int):
    """
    Turns a still photo into a slow zoom (Ken Burns) clip. We first cover-fit
    the image to the target frame, then animate its scale (zooming in OR out,
    chosen at random per scene for variety) while it sits centred on a
    fixed-size canvas — anything that grows past the frame edge is simply
    clipped by the canvas, which is the standard, artefact-free way to do
    this in moviepy.
    """
    base = ImageClip(str(path)).with_duration(duration)
    base = _cover_resize_and_crop(base, target_w, target_h)

    zoom_in = random.choice([True, False])

    def scale_at(t):
        progress = (t / duration) if duration > 0 else 0
        if zoom_in:
            return 1.0 + _KEN_BURNS_ZOOM * progress
        return (1.0 + _KEN_BURNS_ZOOM) - _KEN_BURNS_ZOOM * progress

    animated = (
        base.resized(scale_at)
        .with_position(("center", "center"))
        .with_duration(duration)
    )

    canvas = CompositeVideoClip([animated], size=(target_w, target_h))
    return canvas.with_duration(duration)


def _clip_for_asset(asset: dict, duration: float):
    """
    Turns one visual asset into a moviepy clip that lasts exactly `duration`
    seconds, filling the 9:16 frame:
      - real stock VIDEO clips are looped/trimmed and played as-is
      - still IMAGES get a slow Ken Burns zoom instead of sitting frozen
    """
    path = asset["path"]
    asset_type = asset.get("type", "image")
    target_w, target_h = config.VIDEO_WIDTH, config.VIDEO_HEIGHT

    if asset_type == "video":
        raw = VideoFileClip(str(path))
        if raw.duration < duration:
            loops_needed = int(duration // raw.duration) + 1
            raw = concatenate_videoclips([raw] * loops_needed, method="compose")
        clip = raw.subclipped(0, duration)
        clip = _cover_resize_and_crop(clip, target_w, target_h)
        clip = clip.without_audio()  # keep only the narration track
        return clip.with_duration(duration)

    # image -> Ken Burns
    return _ken_burns_image_clip(path, duration, target_w, target_h)


def _apply_crossfades(clips: list, crossfade_duration: float):
    """
    Applies a fade-in to every clip after the first (so consecutive clips
    visually cross-blend), then concatenates them with negative padding so
    the fades actually overlap instead of just fading to black.
    """
    if len(clips) < 2:
        return clips[0] if clips else None

    faded = [clips[0]]
    for clip in clips[1:]:
        faded.append(clip.with_effects([vfx.CrossFadeIn(crossfade_duration)]))

    return concatenate_videoclips(faded, method="compose", padding=-crossfade_duration)


def render_final_video(image_paths: list, audio_path: Path, narration_text: str, output_filename: str) -> Path:
    """
    Renders the final video: real stock video clips play as-is, still images
    get a Ken Burns zoom, scenes cross-fade into each other, and word-synced
    subtitles are burned in on top.

    `image_paths` accepts either:
      - a list of dicts: {"path": Path, "type": "video" | "image"}  (preferred,
        matches the stock-first visual_generator.py output), or
      - a plain list of image Paths (for backward compatibility).
    """
    print("[+] Assembling video with MoviePy (Ken Burns + crossfades)...")

    assets = []
    for item in image_paths:
        if isinstance(item, dict):
            assets.append(item)
        else:
            assets.append({"path": item, "type": "image"})

    if not assets:
        raise ValueError("render_final_video received no visual assets to work with.")

    audio_clip = AudioFileClip(str(audio_path))
    # A touch of extra time per scene so the crossfade overlap doesn't eat
    # into the narration timing.
    duration_per_asset = audio_clip.duration / len(assets) + _CROSSFADE_DURATION

    clips = []
    for asset in assets:
        try:
            clips.append(_clip_for_asset(asset, duration_per_asset))
        except Exception:
            print(f"[!] Skipping unusable asset: {asset.get('path')}")
            traceback.print_exc()
            continue

    if not clips:
        raise RuntimeError("All visual assets failed to load; cannot render video.")

    if len(clips) != len(assets):
        duration_per_asset = audio_clip.duration / len(clips) + _CROSSFADE_DURATION
        clips = [c.with_duration(duration_per_asset) for c in clips]

    try:
        video_clip = _apply_crossfades(clips, _CROSSFADE_DURATION)
    except Exception as e:
        print(f"[!] Crossfade transitions failed ({e}), falling back to hard cuts.")
        traceback.print_exc()
        video_clip = concatenate_videoclips(clips, method="compose")

    # Trim back down to exactly the narration length (the crossfade padding
    # above can leave the video a hair longer than the audio).
    video_clip = video_clip.subclipped(0, audio_clip.duration)
    video_with_audio = video_clip.with_audio(audio_clip)

    try:
        captions = _build_captions(narration_text, audio_clip.duration)
        if captions:
            final_clip = CompositeVideoClip([video_with_audio] + captions)
        else:
            final_clip = video_with_audio
    except Exception as e:
        print(f"[!] Caption rendering failed, continuing WITHOUT captions: {e}")
        traceback.print_exc()
        final_clip = video_with_audio

    output_path = config.OUTPUT_DIR / output_filename
    try:
        final_clip.write_videofile(
            str(output_path),
            fps=24,
            codec="libx264",
            audio_codec="aac"
        )
    except Exception as e:
        print(f"[!] Render with captions failed ({e}), retrying without captions...")
        traceback.print_exc()
        video_with_audio.write_videofile(
            str(output_path),
            fps=24,
            codec="libx264",
            audio_codec="aac"
        )

    audio_clip.close()
    final_clip.close()

    print(f"[✓] Render completed: {output_path}")
    return output_path
