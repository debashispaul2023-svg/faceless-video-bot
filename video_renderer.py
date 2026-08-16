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

# --- YouTube Shorts compliance -------------------------------------------
# Subtitle Safe Zone: Shorts UI (like/comment/share rail, caption/profile
# strip) reliably avoids the vertical band between 65%-75% of screen height,
# so captions are centered inside that band rather than pinned to a fixed
# pixel offset (which silently drifts out of the safe zone if VIDEO_HEIGHT
# ever changes).
_CAPTION_Y_RATIO = 0.70  # sits centered within the 65%-75% safe zone

# Audio mix levels.
_VOICEOVER_VOLUME = 1.0
_SFX_VOLUME = 0.7
# config.BGM_VOLUME (0.12) is already the "ducked under narration" level —
# since the narration track spans effectively the entire video in this
# pipeline, there's no separate louder/quieter section to duck between;
# keeping BGM constantly this low IS the ducking.

# Render settings.
_RENDER_FPS = getattr(config, "FPS", 30)
_RENDER_PRESET = "ultrafast"
_RENDER_THREADS = 4

# Strict duration bounds for YouTube Shorts.
_MIN_VIDEO_DURATION = 15.0
_MAX_VIDEO_DURATION = 58.0

# Optional sound-effect clips, matched by keyword against each scene's
# optional "sfx_cue" (see visual_generator.py's passthrough). Silently does
# nothing if the folder is empty — same graceful-degradation pattern as BGM.
_SFX_DIR = config.ASSETS_DIR / "sfx"
_SFX_DIR.mkdir(exist_ok=True)

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
    Builds karaoke-style captions: a few words are shown on screen together
    (in white, bold, black-stroked for high contrast), and the specific word
    being spoken right now is highlighted in yellow — just like popular
    Shorts/Reels captions. Centered inside the 65%-75% Shorts Safe Zone
    (_CAPTION_Y_RATIO) so the platform's UI chrome never overlaps the text.

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
    # Shorts Safe Zone: centered within the 65%-75% band of screen height,
    # computed from the actual video height rather than a hardcoded pixel
    # offset, so it stays correct even if VIDEO_HEIGHT is ever changed.
    y_pos = int(config.VIDEO_HEIGHT * _CAPTION_Y_RATIO)

    # Build chunks that respect sentence boundaries: a chunk can be shorter
    # than _CAPTION_CHUNK_SIZE if a word ends a sentence, so on-screen text
    # never mixes the tail of one sentence with the start of the next
    # (previously produced confusing groups like "RATE. THIRD, HIJACK").
    caption_chunks = []
    current_chunk = []
    for word_entry in word_times:
        current_chunk.append(word_entry)
        word_text = word_entry[0].strip()
        ends_sentence = bool(re.search(r'[.!?]+$', word_text)) or word_text == "..."
        if len(current_chunk) >= _CAPTION_CHUNK_SIZE or ends_sentence:
            caption_chunks.append(current_chunk)
            current_chunk = []
    if current_chunk:
        caption_chunks.append(current_chunk)

    for chunk in caption_chunks:
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
        # Bold font + thick black stroke = high contrast against any background.
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
    video's duration, and turns its volume down (config.BGM_VOLUME, 0.12 —
    "ducked" under the narration) so it sits under the narration instead of
    competing with it. Returns None (silently) if no music files are
    available — BGM is a nice-to-have, never a hard requirement, so its
    absence must never break rendering.
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
        print(f"[+] Added background music: {track_path.name} (ducked to {config.BGM_VOLUME})")
        return music
    except Exception as e:
        print(f"[!] Failed to load background music ({e}); continuing without it.")
        traceback.print_exc()
        return None


def _pick_sfx_clip(sfx_cue: str, start_time: float):
    """
    Best-effort: matches an optional per-scene `sfx_cue` keyword (see
    visual_generator.py's passthrough) against filenames in assets/sfx/
    (e.g. sfx_cue="whoosh" matches "whoosh_1.mp3"), positions it at the
    scene's start time, and mixes it at _SFX_VOLUME. Returns None silently
    if there's no cue, no folder contents, or no match — SFX is always
    optional, never a hard requirement.
    """
    if not sfx_cue or sfx_cue.lower() in ("none", "null", ""):
        return None

    candidates = [
        f for f in list(_SFX_DIR.glob("*.mp3")) + list(_SFX_DIR.glob("*.wav"))
        if sfx_cue.lower().replace(" ", "_") in f.stem.lower()
    ]
    if not candidates:
        return None

    try:
        sfx = AudioFileClip(str(random.choice(candidates)))
        sfx = sfx.with_effects([afx.MultiplyVolume(_SFX_VOLUME)]).with_start(start_time)
        print(f"[+] Added SFX '{sfx_cue}' at {start_time:.2f}s (volume {_SFX_VOLUME})")
        return sfx
    except Exception as e:
        print(f"[!] Failed to load SFX for cue '{sfx_cue}': {e}")
        return None


def _cover_resize_and_crop(clip, target_w: int, target_h: int):
    """Resizes `clip` to fully cover a target_w x target_h frame (no
    stretching — aspect ratio is preserved), then centre-crops any overflow
    to land exactly on target_w x target_h. This is the single source of
    truth for 9:16 Shorts framing compliance; works for both images, videos,
    and the fully-composited final clip."""
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
    seconds, filling the 9:16 frame via cover-crop (never stretched):
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


def render_final_video(image_paths: list, audio_path: Path, narration_text: str, output_filename: str) -> Path:
    """
    Renders the final video: real stock video clips play as-is, still images
    get a Ken Burns zoom, scenes cross-fade into each other, karaoke-style
    word-highlighted subtitles are burned in on top (inside the 65%-75%
    Shorts Safe Zone), optional SFX and background music are mixed under the
    narration, and the whole thing is guaranteed to land at exactly 9:16
    (1080x1920, cover-cropped, never stretched) and between 15-58 seconds.

    `image_paths` accepts either:
      - a list of dicts: {"path": Path, "type": "video" | "image"}  (preferred,
        matches the stock-first visual_generator.py output), or
      - a plain list of image Paths (for backward compatibility).
    """
    print("[+] Assembling video with MoviePy (Ken Burns + crossfades + BGM + SFX + karaoke captions)...")

    assets = []
    for item in image_paths:
        if isinstance(item, dict):
            assets.append(item)
        else:
            assets.append({"path": item, "type": "image"})

    if not assets:
        raise ValueError("render_final_video received no visual assets to work with.")

    narration_clip = AudioFileClip(str(audio_path))
    # Explicit voiceover level (1.0 = unity gain) so the mix ratio against
    # BGM/SFX is deliberate and documented, not just "whatever the file has."
    narration_clip = narration_clip.with_effects([afx.MultiplyVolume(_VOICEOVER_VOLUME)])

    # --- Strict duration bounds (YouTube Shorts compliance) -----------------
    if narration_clip.duration > _MAX_VIDEO_DURATION:
        print(f"[!] Narration is {narration_clip.duration:.1f}s, over the "
              f"{_MAX_VIDEO_DURATION}s Shorts limit — trimming.")
        narration_clip = narration_clip.subclipped(0, _MAX_VIDEO_DURATION)
    elif narration_clip.duration < _MIN_VIDEO_DURATION:
        print(f"[!] Narration is only {narration_clip.duration:.1f}s, under the "
              f"{_MIN_VIDEO_DURATION}s Shorts minimum — script should be lengthened "
              f"upstream (researcher.py); proceeding anyway rather than blocking the pipeline.")

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

    scenes_consistent = len(clips) == len(assets)
    if not scenes_consistent:
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

    # --- 9:16 Shorts framing guarantee ---------------------------------
    # Every per-scene clip is already cover-cropped to VIDEO_WIDTH x
    # VIDEO_HEIGHT, but this is a final, unconditional safety net so the
    # composited/concatenated clip is GUARANTEED to be exactly 1080x1920 —
    # no letterboxing, no stretching — regardless of any edge case above.
    video_clip = _cover_resize_and_crop(video_clip, config.VIDEO_WIDTH, config.VIDEO_HEIGHT)

    # --- Audio: mix narration (1.0) + background music (0.12, ducked) + optional SFX (0.7) ---
    audio_tracks = [narration_clip]

    bgm_clip = _pick_background_music(narration_clip.duration)
    if bgm_clip is not None:
        audio_tracks.append(bgm_clip)

    if scenes_consistent:
        cursor = 0.0
        scene_start_times = []
        for d in scene_durations:
            scene_start_times.append(cursor)
            cursor += d - _CROSSFADE_DURATION

        for asset, start_time in zip(assets, scene_start_times):
            sfx_clip = _pick_sfx_clip(asset.get("sfx_cue"), start_time)
            if sfx_clip is not None:
                audio_tracks.append(sfx_clip)
    else:
        scene_start_times = []

    if len(audio_tracks) > 1:
        try:
            final_audio = CompositeAudioClip(audio_tracks)
        except Exception as e:
            print(f"[!] Mixing BGM/SFX failed ({e}); using narration only.")
            traceback.print_exc()
            final_audio = narration_clip
    else:
        final_audio = narration_clip

    # Peak-normalize the final mix so BGM+SFX layered on top of the
    # narration can never clip, and the loudest moment sits right at the
    # ceiling instead of leaving unused headroom.
    try:
        final_audio = final_audio.with_effects([afx.AudioNormalize()])
    except Exception as e:
        print(f"[!] Audio normalization failed ({e}); using unnormalized mix.")
        traceback.print_exc()

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
    if _SHOW_RULE_STICKERS and len(assets) == 6 and len(clips) == 6 and scene_start_times:
        try:
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
    render_kwargs = dict(
        fps=_RENDER_FPS,
        codec="libx264",
        audio_codec="aac",
        preset=_RENDER_PRESET,
        threads=_RENDER_THREADS,
    )
    try:
        final_clip.write_videofile(str(output_path), **render_kwargs)
    except Exception as e:
        print(f"[!] Render with captions failed ({e}), retrying without captions...")
        traceback.print_exc()
        video_with_audio.write_videofile(str(output_path), **render_kwargs)

    narration_clip.close()
    final_clip.close()

    print(f"[✓] Render completed: {output_path}")
    return output_path
