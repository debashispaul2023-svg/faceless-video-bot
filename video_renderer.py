import os
import json
import random
import re
import traceback
from pathlib import Path
import numpy as np
from PIL import ImageFont, Image, ImageDraw
from moviepy import (
    VideoFileClip, AudioFileClip, ImageClip, TextClip, VideoClip, ColorClip,
    CompositeVideoClip, CompositeAudioClip, concatenate_videoclips,
    concatenate_audioclips, vfx, afx
)
from moviepy.audio.fx.MultiplyVolume import MultiplyVolume
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

# --- Retention tuning ---------------------------------------------------
# The HOOK scene (the very first one) runs at this fraction of an even split,
# so the opening cut lands noticeably faster than the rest of the video. Most
# swipe-aways happen in the first 1-2 seconds, and a quick early cut is a
# well-documented pattern-interrupt that keeps thumbs from moving.
_HOOK_DURATION_RATIO = 0.65

# A thin bar along the bottom edge that fills up as the video plays, so
# viewers can see at a glance how much is left — this alone measurably
# reduces swipe-away on Shorts/Reels since it visually promises "this is
# quick." Purely cosmetic; safe to disable by flipping this to False.
_SHOW_PROGRESS_BAR = True
_PROGRESS_BAR_HEIGHT = 10
_PROGRESS_BAR_BG_COLOR = (60, 60, 60)
_PROGRESS_BAR_FILL_COLOR = (255, 200, 0)

# A "paper cutout" countdown badge (1, 2, 3, 4) shown during the matching rule
# scene. Drawn entirely in code with PIL rather than asking the AI image
# generator to render the number into the background — AI image models
# routinely botch drawn digits/text (illegible or wrong numbers look
# amateurish), and drawing it ourselves means zero external/copyrighted
# assets are involved. Only applied when visual_scenes has exactly the
# expected [hook, rule1, rule2, rule3, rule4, cta] shape (6 scenes) — skipped
# silently otherwise, since position-based number mapping wouldn't be
# reliable if any scene failed to generate.
_SHOW_RULE_STICKERS = True
_RULE_STICKER_SIZE = 260
_RULE_STICKER_TOP_MARGIN = 140
_RULE_STICKER_POP_IN_SECONDS = 0.25


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
    Builds dynamic word-by-word bold animated subtitles.
    Instead of showing a chunk of words, it displays one or two words at a time,
    scaled up slightly and bold, with a pop-in animation to grab attention.
    """
    # Remove any SFX cues before word splitting
    clean_text = re.sub(r'\[SFX:[^\]]+\]', '', narration_text)
    words = clean_text.split()
    if not words:
        return []

    font_path = _resolve_font_path()

    timings = _load_word_timings(audio_path)
    if timings and len(timings) >= len(words) * 0.6:
        # Some word timings might contain SFX cues in text if the TTS read it (though it shouldn't)
        # We clean them out just in case
        cleaned_timings = []
        for t in timings:
            t_text = t.get("text", "").strip()
            # Ignore [SFX: ...] if it got captured
            if re.match(r'^\[SFX:', t_text) or t_text == "":
                continue
            cleaned_timings.append((t_text, t["start"], t["end"]))
        word_times = cleaned_timings
    else:
        # Fallback: spread words evenly across the narration's duration.
        per_word = total_duration / len(words)
        word_times = [(w, i * per_word, (i + 1) * per_word) for i, w in enumerate(words)]

    clips = []
    y_pos = 1350

    # We will display chunks of 1 or 2 words based on length.
    caption_chunks = []
    current_chunk = []
    current_char_count = 0
    for word_entry in word_times:
        word_text = word_entry[0]
        # Only group if it's very short, max 2 words
        if len(current_chunk) == 0:
            current_chunk.append(word_entry)
            current_char_count += len(word_text)
        elif len(current_chunk) < 2 and (current_char_count + len(word_text)) < 12:
            current_chunk.append(word_entry)
            caption_chunks.append(current_chunk)
            current_chunk = []
            current_char_count = 0
        else:
            caption_chunks.append(current_chunk)
            current_chunk = [word_entry]
            current_char_count = len(word_text)
    if current_chunk:
        caption_chunks.append(current_chunk)

    for chunk in caption_chunks:
        if not chunk:
            continue

        chunk_text = " ".join([w[0].upper() for w in chunk])
        start_time = chunk[0][1]
        end_time = chunk[-1][2]
        duration = max(0.05, end_time - start_time)

        # Word-by-word bold animated subtitle:
        # A pop-in animation effect for each word chunk
        base_clip = TextClip(
            font=font_path, text=chunk_text, font_size=_CAPTION_FONT_SIZE,
            color='white', stroke_color='black', stroke_width=4,
            method='label'
        ).with_position(('center', y_pos)).with_start(start_time).with_duration(duration)

        # Animation: scale up slightly in the first 0.1s
        pop_in = 0.1
        def anim_scale(t):
            if t < pop_in:
                return 0.8 + 0.2 * (t / pop_in)
            return 1.0

        animated_clip = base_clip.resized(anim_scale).with_position(('center', y_pos))

        clips.append(animated_clip)

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
        music = music.with_effects([MultiplyVolume(config.BGM_VOLUME)])
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


def _build_progress_bar(total_duration: float, target_w: int, target_h: int):
    """
    A thin bar along the bottom edge: a dim full-width track, plus a bright
    fill that grows from 0 to full width as the video plays. Built as a raw
    numpy frame (rather than resizing a clip) so the fill's width is exactly
    proportional to elapsed time, independent of height. Best-effort — any
    failure here just means no progress bar, never a broken render.
    """
    track = (
        ColorClip(size=(target_w, _PROGRESS_BAR_HEIGHT), color=_PROGRESS_BAR_BG_COLOR)
        .with_duration(total_duration)
        .with_position((0, target_h - _PROGRESS_BAR_HEIGHT))
    )

    def make_fill_frame(t):
        progress = min(max(t / total_duration, 0.0), 1.0) if total_duration > 0 else 0.0
        frame = np.zeros((_PROGRESS_BAR_HEIGHT, target_w, 3), dtype='uint8')
        filled_w = int(target_w * progress)
        if filled_w > 0:
            frame[:, :filled_w] = _PROGRESS_BAR_FILL_COLOR
        return frame

    fill = (
        VideoClip(frame_function=make_fill_frame, duration=total_duration)
        .with_position((0, target_h - _PROGRESS_BAR_HEIGHT))
    )

    return [track, fill]


def _draw_rule_sticker_frame(number: int) -> np.ndarray:
    """
    Draws a simple 'paper cutout' countdown badge for a rule number as an
    RGBA numpy array — an off-white circle with a soft drop shadow and a
    dark bold number. 100% code-drawn: no AI generation, no internet asset,
    no copyright exposure of any kind.
    """
    size = _RULE_STICKER_SIZE
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad, shadow_offset = 12, 6
    draw.ellipse(
        [pad + shadow_offset, pad + shadow_offset, size - pad + shadow_offset, size - pad + shadow_offset],
        fill=(0, 0, 0, 90)
    )
    draw.ellipse([pad, pad, size - pad, size - pad], fill=(250, 246, 235, 255), outline=(30, 30, 30, 255), width=4)

    font = ImageFont.truetype(_resolve_font_path(), int(size * 0.5))
    text = str(number)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((size - text_w) / 2 - bbox[0], (size - text_h) / 2 - bbox[1]),
        text, font=font, fill=(20, 20, 20, 255)
    )

    return np.array(img)


def _rule_sticker_clip(number: int, start_time: float, duration: float):
    """Returns a positioned, pop-in-animated ImageClip of one rule badge,
    timed to sit on screen for exactly that scene's duration."""
    frame = _draw_rule_sticker_frame(number)
    rgb = frame[:, :, :3]
    alpha = frame[:, :, 3].astype("float32") / 255.0

    img_clip = ImageClip(rgb).with_duration(duration)
    mask_clip = ImageClip(alpha, is_mask=True).with_duration(duration)
    img_clip = img_clip.with_mask(mask_clip)

    pop_in = _RULE_STICKER_POP_IN_SECONDS

    def scale_at(t):
        if t < pop_in:
            return 0.6 + 0.4 * (t / pop_in)  # quick pop from 60% to 100% size
        return 1.0

    img_clip = (
        img_clip.resized(scale_at)
        .with_position(("center", _RULE_STICKER_TOP_MARGIN))
        .with_start(start_time)
    )
    return img_clip


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



def _apply_sfx_and_ducking(narration_text: str, total_duration: float, bgm_clip, narration_clip, audio_path: Path):
    """
    Parses [SFX: <name>] cues, inserts the corresponding sound effect,
    and applies auto audio ducking to the BGM whenever voiceover or SFX is active.
    """
    sfx_dir = config.ASSETS_DIR / "sfx"
    sfx_dir.mkdir(exist_ok=True)

    timings = _load_word_timings(audio_path)

    tokens = re.findall(r'(\[SFX:\s*[^\]]+\]|\S+)', narration_text)
    word_count = len([t for t in tokens if not t.startswith("[SFX:")])

    # Map words to actual timestamps or fallback
    if timings and len(timings) >= word_count * 0.6:
        # Use real timings
        word_starts = [t["start"] for t in timings]
        word_ends = [t["end"] for t in timings]
    else:
        per_word = total_duration / max(1, word_count)
        word_starts = [i * per_word for i in range(word_count)]
        word_ends = [(i + 1) * per_word for i in range(word_count)]

    current_word_idx = 0
    sfx_clips = []
    ducking_intervals = []

    # 1. Add voiceover ducking intervals (combine close words)
    for start, end in zip(word_starts, word_ends):
        if not ducking_intervals:
            ducking_intervals.append([start, end])
        else:
            # If gap is less than 0.5s, keep ducking
            if start - ducking_intervals[-1][1] < 0.5:
                ducking_intervals[-1][1] = end
            else:
                ducking_intervals.append([start, end])

    # 2. Add SFX and their ducking intervals
    for token in tokens:
        if token.startswith("[SFX:"):
            match = re.search(r'\[SFX:\s*([^\]]+)\]', token)
            if match:
                sfx_name = match.group(1).strip().lower()

                # Estimate time: right before the *next* word starts, or at current word end
                sfx_time = 0.0
                if current_word_idx < len(word_starts):
                    sfx_time = word_starts[current_word_idx]
                elif len(word_ends) > 0:
                    sfx_time = word_ends[-1]

                sfx_file = None
                for ext in [".mp3", ".wav"]:
                    candidate = sfx_dir / f"{sfx_name}{ext}"
                    if candidate.is_file():
                        sfx_file = candidate
                        break

                if sfx_file:
                    try:
                        sfx_clip = AudioFileClip(str(sfx_file)).with_start(sfx_time)
                        sfx_clips.append(sfx_clip)

                        # Add ducking for this SFX
                        ducking_intervals.append([sfx_time - 0.2, sfx_time + sfx_clip.duration + 0.2])
                    except Exception as e:
                        print(f"[!] Failed to load SFX {sfx_file}: {e}")
        else:
            current_word_idx += 1

    # Apply ducking to BGM
    if bgm_clip:
        def duck_volume(t):
            import numpy as np
            t_is_scalar = np.isscalar(t)
            t = np.atleast_1d(t)
            vols = np.full(t.shape, 1.0, dtype=float)

            for start, end in ducking_intervals:
                fade = 0.2

                # Create a temporary array for this interval's ducking curve
                interval_vols = np.full(t.shape, 1.0, dtype=float)

                fully_ducked = (t >= start + fade) & (t <= end - fade)
                interval_vols[fully_ducked] = 0.2

                fading_out = (t >= start) & (t < start + fade)
                if np.any(fading_out):
                    interval_vols[fading_out] = 1.0 - 0.8 * ((t[fading_out] - start) / fade)

                fading_in = (t > end - fade) & (t <= end)
                if np.any(fading_in):
                    interval_vols[fading_in] = 0.2 + 0.8 * ((t[fading_in] - (end - fade)) / fade)

                # Take the minimum volume between existing ducking and this new interval
                # so overlapping voiceover and SFX don't cause volume spikes
                vols = np.minimum(vols, interval_vols)

            return vols[0] if t_is_scalar else vols

        bgm_clip = bgm_clip.with_effects([MultiplyVolume(duck_volume)])

    return bgm_clip, sfx_clips


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

    # Every scene gets an even slice, EXCEPT the hook (first scene), which
    # runs shorter on purpose — see _HOOK_DURATION_RATIO above. The time
    # saved there is spread across the rest so the total still matches the
    # narration exactly.
    n = len(assets)
    if n > 1:
        even_split = narration_clip.duration / n
        hook_duration = even_split * _HOOK_DURATION_RATIO
        rest_duration = (narration_clip.duration - hook_duration) / (n - 1)
        scene_durations = [hook_duration] + [rest_duration] * (n - 1)
    else:
        scene_durations = [narration_clip.duration]

    # Each clip needs a touch of extra time so the crossfade overlap doesn't
    # eat into the narration timing.
    scene_durations = [d + _CROSSFADE_DURATION for d in scene_durations]

    clips = []
    for asset, duration_per_asset in zip(assets, scene_durations):
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

    # --- Audio: mix narration (full volume) with background music (quiet) and SFX ---
    raw_bgm_clip = _pick_background_music(narration_clip.duration)
    bgm_clip, sfx_clips = _apply_sfx_and_ducking(narration_text, narration_clip.duration, raw_bgm_clip, narration_clip, audio_path)

    audio_tracks = [narration_clip]
    if bgm_clip is not None:
        audio_tracks.append(bgm_clip)
    audio_tracks.extend(sfx_clips)

    try:
        final_audio = CompositeAudioClip(audio_tracks)
    except Exception as e:
        print(f"[!] Mixing audio tracks failed ({e}); using narration only.")
        traceback.print_exc()
        final_audio = narration_clip

    video_with_audio = video_clip.with_audio(final_audio)

    try:
        captions = _build_captions(narration_text, narration_clip.duration, audio_path)
    except Exception as e:
        print(f"[!] Caption rendering failed, continuing WITHOUT captions: {e}")
        traceback.print_exc()
        captions = []

    progress_bar_clips = []
    if _SHOW_PROGRESS_BAR:
        try:
            progress_bar_clips = _build_progress_bar(narration_clip.duration, config.VIDEO_WIDTH, config.VIDEO_HEIGHT)
        except Exception as e:
            print(f"[!] Progress bar rendering failed, continuing WITHOUT it: {e}")
            traceback.print_exc()

    # Rule-number stickers (1-4) — only when the scene list has exactly the
    # expected [hook, rule1, rule2, rule3, rule4, cta] shape. If any scene
    # failed earlier (len(clips) != len(assets)), the position->rule-number
    # mapping can't be trusted, so this is skipped silently rather than risk
    # showing the wrong number over the wrong scene.
    sticker_clips = []
    if _SHOW_RULE_STICKERS and len(assets) == 6 and len(clips) == 6:
        try:
            scene_start_times, cursor = [], 0.0
            for d in scene_durations:
                scene_start_times.append(cursor)
                cursor += d - _CROSSFADE_DURATION
            for rule_num, scene_idx in zip([1, 2, 3, 4], [1, 2, 3, 4]):
                sticker_clips.append(
                    _rule_sticker_clip(rule_num, scene_start_times[scene_idx], scene_durations[scene_idx])
                )
        except Exception as e:
            print(f"[!] Rule-number sticker overlay failed, continuing WITHOUT it: {e}")
            traceback.print_exc()
            sticker_clips = []

    overlay_clips = captions + progress_bar_clips + sticker_clips
    final_clip = CompositeVideoClip([video_with_audio] + overlay_clips) if overlay_clips else video_with_audio

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
