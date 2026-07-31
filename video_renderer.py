import os
import json
import random
import traceback
from pathlib import Path
from PIL import ImageFont
from moviepy import (
    VideoFileClip, AudioFileClip, ImageClip, TextClip,
    CompositeVideoClip, CompositeAudioClip, concatenate_videoclips,
    concatenate_audioclips, vfx, afx
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
_CAPTION_FONT_SIZE = 70
_CAPTION_CHUNK_SIZE = 3  # words shown on screen together, with one highlighted at a time


def _resolve_font_path() -> str:
    for candidate in _FONT_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(
        "No usable font file found for captions. Install 'fonts-dejavu-core' "
        "in the workflow, or drop a .ttf file at assets/fonts/CaptionFont-Bold.ttf"
    )


def _load_word_timings(audio_path: Path):
    """Loads the real per-word timings tts_engine.py saves alongside the
    audio file, if present. Returns None if not available (caller falls
    back to an even-split estimate)."""
    sidecar = Path(audio_path).with_suffix(".words.json")
    try:
        if sidecar.is_file():
            data = json.loads(sidecar.read_text())
            if data:
                return data
    except Exception as e:
        print(f"[!] Could not read word-timing sidecar ({e}); using even-split captions.")
    return None


def _build_captions(narration_text: str, total_duration: float, audio_path: Path):
    """
    Builds karaoke-style captions: a few words are shown on screen together
    (in white), and the specific word being spoken right now is highlighted
    in yellow — just like popular Shorts/Reels captions.

    Uses REAL per-word timing from edge-tts's WordBoundary events when
    available (see tts_engine.py); otherwise falls back to splitting the
    total duration evenly across words, which is close enough to still look
    good but won't be frame-perfect.
    """
    words = narration_text.split()
    if not words:
        return []

    font_path = _resolve_font_path()
    font = ImageFont.truetype(font_path, _CAPTION_FONT_SIZE)
    space_w = font.getlength(" ")

    timings = _load_word_timings(audio_path)
    if timings and len(timings) >= len(words) * 0.6:
        # Good enough real timing data — use it directly (trusts edge-tts's
        # own word segmentation rather than forcing it to match `words`).
        word_times = [(t.get("text", "").strip() or "", t["start"], t["end"]) for t in timings]
    else:
        # Fallback: spread words evenly across the narration's duration.
        per_word = total_duration / len(words)
        word_times = [(w, i * per_word, (i + 1) * per_word) for i, w in enumerate(words)]

    clips = []
    y_pos = 1350

    for chunk_start_idx in range(0, len(word_times), _CAPTION_CHUNK_SIZE):
        chunk = word_times[chunk_start_idx: chunk_start_idx + _CAPTION_CHUNK_SIZE]
        if not chunk:
            continue

        chunk_words_upper = [w.upper() for (w, _, _) in chunk]
        widths = [font.getlength(w) for w in chunk_words_upper]
        total_w = sum(widths) + space_w * max(0, len(chunk) - 1)
        start_x = (config.VIDEO_WIDTH - total_w) / 2

        chunk_start_time = chunk[0][1]
        chunk_end_time = chunk[-1][2]
        chunk_duration = max(0.05, chunk_end_time - chunk_start_time)

        # Base layer: the whole chunk, visible in white for its full duration.
        base_text = " ".join(chunk_words_upper)
        base_clip = (
            TextClip(
                font=font_path, text=base_text, font_size=_CAPTION_FONT_SIZE,
                color='white', stroke_color='black', stroke_width=4,
                method='label'
            )
            .with_position((start_x, y_pos))
            .with_start(chunk_start_time)
            .with_duration(chunk_duration)
        )
        clips.append(base_clip)

        # Highlight layer: each word, in yellow, ONLY during its own slice —
        # sits on top of (and visually replaces) the white word beneath it.
        cursor_x = start_x
        for (word, w_start, w_end), width in zip(chunk, widths):
            if not word:
                cursor_x += width + space_w
                continue
            highlight_clip = (
                TextClip(
                    font=font_path, text=word.upper(), font_size=_CAPTION_FONT_SIZE,
                    color='yellow', stroke_color='black', stroke_width=4,
                    method='label'
                )
                .with_position((cursor_x, y_pos))
                .with_start(w_start)
                .with_duration(max(0.05, w_end - w_start))
            )
            clips.append(highlight_clip)
            cursor_x += width + space_w

    return clips


def _pick_background_music(duration: float):
    """
    Picks a random track from assets/music/, loops/trims it to match the
    video's duration, and turns its volume down so it sits under the
    narration instead of competing with it. Returns None (silently) if no
    music files are available — BGM is a nice-to-have, never a hard
    requirement, so its absence must never break rendering.
    """
    tracks = list(config.MUSIC_DIR.glob("*.mp3")) + list(config.MUSIC_DIR.glob("*.wav"))
    if not tracks:
        print(f"[i] No background music files found in {config.MUSIC_DIR} — skipping BGM. "
              f"Drop a few royalty-free .mp3 tracks there (e.g. from the YouTube Audio Library "
              f"or Pixabay Music) to enable this.")
        return None

    track_path = random.choice(tracks)
    try:
        music = AudioFileClip(str(track_path))
        if music.duration < duration:
            loops_needed = int(duration // music.duration) + 1
            music.close()
            music = concatenate_audioclips([AudioFileClip(str(track_path)) for _ in range(loops_needed)])
        music = music.subclipped(0, duration)
        music = music.with_effects([afx.MultiplyVolume(config.BGM_VOLUME)])
        print(f"[+] Added background music: {track_path.name}")
        return music
    except Exception as e:
        print(f"[!] Failed to load background music ({e}); continuing without it.")
        traceback.print_exc()
        return None


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
    get a Ken Burns zoom, scenes cross-fade into each other, karaoke-style
    word-highlighted subtitles are burned in on top, and background music
    (if any .mp3/.wav files exist in assets/music/) is mixed under the
    narration.

    `image_paths` accepts either:
      - a list of dicts: {"path": Path, "type": "video" | "image"}  (preferred,
        matches the stock-first visual_generator.py output), or
      - a plain list of image Paths (for backward compatibility).
    """
    print("[+] Assembling video with MoviePy (Ken Burns + crossfades + BGM + karaoke captions)...")

    assets = []
    for item in image_paths:
        if isinstance(item, dict):
            assets.append(item)
        else:
            assets.append({"path": item, "type": "image"})

    if not assets:
        raise ValueError("render_final_video received no visual assets to work with.")

    narration_clip = AudioFileClip(str(audio_path))
    # A touch of extra time per scene so the crossfade overlap doesn't eat
    # into the narration timing.
    duration_per_asset = narration_clip.duration / len(assets) + _CROSSFADE_DURATION

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
        duration_per_asset = narration_clip.duration / len(clips) + _CROSSFADE_DURATION
        clips = [c.with_duration(duration_per_asset) for c in clips]

    try:
        video_clip = _apply_crossfades(clips, _CROSSFADE_DURATION)
    except Exception as e:
        print(f"[!] Crossfade transitions failed ({e}), falling back to hard cuts.")
        traceback.print_exc()
        video_clip = concatenate_videoclips(clips, method="compose")

    # Trim back down to exactly the narration length (the crossfade padding
    # above can leave the video a hair longer than the audio).
    video_clip = video_clip.subclipped(0, narration_clip.duration)

    # --- Audio: mix narration (full volume) with background music (quiet) ---
    bgm_clip = _pick_background_music(narration_clip.duration)
    if bgm_clip is not None:
        try:
            final_audio = CompositeAudioClip([narration_clip, bgm_clip])
        except Exception as e:
            print(f"[!] Mixing background music failed ({e}); using narration only.")
            traceback.print_exc()
            final_audio = narration_clip
    else:
        final_audio = narration_clip

    video_with_audio = video_clip.with_audio(final_audio)

    try:
        captions = _build_captions(narration_text, narration_clip.duration, audio_path)
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

    narration_clip.close()
    final_clip.close()

    print(f"[✓] Render completed: {output_path}")
    return output_path
            
