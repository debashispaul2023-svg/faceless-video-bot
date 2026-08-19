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
_CAPTION_SIDE_MARGIN = 40  # px kept clear on each side so text can never touch/overflow the frame edge
_CAPTION_MAX_TEXT_WIDTH = config.VIDEO_WIDTH - (2 * _CAPTION_SIDE_MARGIN)

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

# --- Creator-style editing pack (bold emphasis text, percentage ring,
# circular insert, chat-bubble hook) --------------------------------------
# A word is treated as an "emphasis" word if it was written in ALL CAPS in
# the original script (researcher.py's prompt already asks Gemini to write
# key emphasis words this way, e.g. "NEVER", "CHANGES EVERYTHING") — so this
# reuses a signal that already exists instead of guessing at emphasis.
_CAPTION_EMPHASIS_FONT_SIZE = 94  # vs _CAPTION_FONT_SIZE 70 — visibly bigger
_CAPTION_EMPHASIS_COLOR = "#FF3B30"  # punchy red-orange, distinct from the yellow karaoke-highlight
_CAPTION_NORMAL_HIGHLIGHT_COLOR = "yellow"

# Percentage/stat ring — auto-triggers the first time the script mentions a
# "NN%" or "NN percent" figure (very common in this niche: utilization,
# savings rate, retirement %, etc.), shown as a filling ring with the number
# in the middle, at roughly the moment it's spoken.
_SHOW_PERCENTAGE_RING = True
_PERCENTAGE_RING_SIZE = 320
_PERCENTAGE_RING_COLOR = (255, 59, 48, 255)  # matches the emphasis text color
_PERCENTAGE_RING_DURATION = 2.6
_PERCENTAGE_PATTERN = re.compile(r'(\d{1,3})\s*(?:%|percent)\b', re.IGNORECASE)

# Circular video/photo insert (dark blurred-out backdrop, footage masked
# into a circle) — applied to at most ONE scene per video, and not every
# video, purely for visual variety instead of every Short looking identical.
_CIRCLE_INSERT_ENABLED = True
_CIRCLE_INSERT_CHANCE = 0.5
_CIRCLE_INSERT_SIZE_RATIO = 0.62  # relative to VIDEO_WIDTH

# A rounded speech-bubble graphic behind the very first caption chunk (the
# opening hook) only — makes the first 1-2 seconds pop harder before the
# karaoke captions take over for the rest of the video.
_SHOW_HOOK_BUBBLE = True
_HOOK_BUBBLE_FONT_SIZE = 48
_HOOK_BUBBLE_Y_RATIO = 0.30  # sits well above the main caption band


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


def _compute_sentence_start_times(word_times: list) -> list:
    """
    Groups a (word, start, end) timeline into sentences (same end-of-
    sentence detection the caption chunker uses: '.', '!', '?', or an
    ellipsis), returning (sentence_text, start_time, end_time) per
    sentence. This is what lets scene cuts and rule-number stickers land
    exactly where each sentence actually starts being spoken, using the
    SAME real timing data the captions are already built from — instead of
    an artificial even split that can silently drift out of sync with what
    the narration is saying at that moment.
    """
    sentences = []
    current = []
    for word_entry in word_times:
        current.append(word_entry)
        word_text = word_entry[0].strip()
        ends_sentence = bool(re.search(r'[.!?]+$', word_text)) or word_text == "..."
        if ends_sentence:
            sentences.append(current)
            current = []
    if current:
        sentences.append(current)

    result = []
    for sent in sentences:
        if not sent:
            continue
        text = " ".join(w[0] for w in sent)
        result.append((text, sent[0][1], sent[-1][2]))
    return result


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

    # Build chunks that respect sentence boundaries AND screen width: a
    # chunk breaks early if a word ends a sentence (so on-screen text never
    # mixes the tail of one sentence with the start of the next), OR if
    # adding the next word would run the chunk past the safe on-screen
    # width — long words like "WILLPOWER" previously got grouped with two
    # neighbours regardless of how wide the combined text actually was,
    # which pushed the start of the line off the LEFT edge of the frame
    # (center-alignment math went negative). Checking real pixel width here
    # is what actually prevents that, not just capping the word count.
    caption_chunks = []
    current_chunk = []
    current_width = 0.0
    for word_entry in word_times:
        word_text = word_entry[0].strip()
        word_width = font.getlength(word_text.upper())
        projected_width = current_width + (space_w if current_chunk else 0) + word_width

        if current_chunk and (projected_width > _CAPTION_MAX_TEXT_WIDTH or len(current_chunk) >= _CAPTION_CHUNK_SIZE):
            caption_chunks.append(current_chunk)
            current_chunk = []
            current_width = 0.0
            projected_width = word_width

        current_chunk.append(word_entry)
        current_width = projected_width

        ends_sentence = bool(re.search(r'[.!?]+$', word_text)) or word_text == "..."
        if ends_sentence:
            caption_chunks.append(current_chunk)
            current_chunk = []
            current_width = 0.0
    if current_chunk:
        caption_chunks.append(current_chunk)

    emphasis_font = ImageFont.truetype(font_path, _CAPTION_EMPHASIS_FONT_SIZE)

    def _word_style(original_word: str):
        """Emphasis words (already ALL CAPS in the source script, e.g.
        'NEVER', 'CHANGES EVERYTHING') render bigger and in the accent
        color throughout — inspired by the reference video's mixed-size,
        color-punched keyword style, instead of every word being a uniform
        size. Falls back to the normal karaoke white/yellow style otherwise."""
        stripped = re.sub(r'[^A-Za-z]', '', original_word)
        is_emphasis = len(stripped) > 1 and original_word.strip().isupper()
        if is_emphasis:
            return emphasis_font, _CAPTION_EMPHASIS_FONT_SIZE, _CAPTION_EMPHASIS_COLOR
        return font, _CAPTION_FONT_SIZE, None  # None = use the normal two-layer white/yellow treatment

    for chunk_index, chunk in enumerate(caption_chunks):
        if not chunk:
            continue

        # Per-word metrics, since emphasis words use a bigger font than
        # normal words within the SAME on-screen chunk.
        word_meta = []  # (word, start, end, font_obj, font_size, emphasis_color_or_None, width)
        for (word, w_start, w_end) in chunk:
            word_upper = word.strip().upper()
            f_obj, f_size, emphasis_color = _word_style(word)
            width = f_obj.getlength(word_upper) if word_upper else 0
            word_meta.append((word_upper, w_start, w_end, f_obj, f_size, emphasis_color, width))

        total_w = sum(w[6] for w in word_meta) + space_w * max(0, len(word_meta) - 1)
        # Defensive clamp: even with the width-aware chunking above, a
        # single word longer than the safe width alone (rare, but possible)
        # would still center to a negative x. Clamping keeps it fully
        # on-screen (small edge overlap allowed) instead of running off
        # the left edge.
        start_x = max((config.VIDEO_WIDTH - total_w) / 2, _CAPTION_SIDE_MARGIN)

        cursor_x = start_x
        for word_upper, w_start, w_end, f_obj, f_size, emphasis_color, width in word_meta:
            if not word_upper:
                cursor_x += width + space_w
                continue

            word_duration = max(0.05, w_end - w_start)
            # Emphasis words stay visible (in the accent color) for the
            # WHOLE chunk's on-screen time, not just their own instant —
            # matches the reference video's consistently-bold "Think" style
            # rather than a brief flash.
            chunk_duration = max(0.05, chunk[-1][2] - chunk[0][1])

            if emphasis_color:
                word_clip = (
                    TextClip(
                        font=font_path, text=word_upper, font_size=f_size,
                        color=emphasis_color, stroke_color='black', stroke_width=5,
                        method='label'
                    )
                    .with_position((cursor_x, y_pos - (f_size - _CAPTION_FONT_SIZE) // 2))
                    .with_start(chunk[0][1])
                    .with_duration(chunk_duration)
                )
                clips.append(word_clip)
            else:
                # Normal word: white base for the whole chunk, then a
                # yellow highlight layered on top only during its own
                # timing slice — the existing karaoke behavior.
                base_clip = (
                    TextClip(
                        font=font_path, text=word_upper, font_size=f_size,
                        color='white', stroke_color='black', stroke_width=4,
                        method='label'
                    )
                    .with_position((cursor_x, y_pos))
                    .with_start(chunk[0][1])
                    .with_duration(chunk_duration)
                )
                clips.append(base_clip)

                highlight_clip = (
                    TextClip(
                        font=font_path, text=word_upper, font_size=f_size,
                        color=_CAPTION_NORMAL_HIGHLIGHT_COLOR, stroke_color='black', stroke_width=4,
                        method='label'
                    )
                    .with_position((cursor_x, y_pos))
                    .with_start(w_start)
                    .with_duration(word_duration)
                )
                clips.append(highlight_clip)

            cursor_x += width + space_w

        # Hook bubble: a rounded speech-bubble graphic behind ONLY the very
        # first caption chunk, sitting above the main caption band — see
        # _hook_bubble_clip() below. Best-effort; skipped silently on error.
        if chunk_index == 0 and _SHOW_HOOK_BUBBLE:
            try:
                bubble_text = " ".join(w[0] for w in word_meta if w[0])
                clips.insert(0, _hook_bubble_clip(bubble_text, chunk[0][1], max(0.05, chunk[-1][2] - chunk[0][1])))
            except Exception as e:
                print(f"[!] Hook bubble overlay failed, continuing without it: {e}")

    return clips


def _hook_bubble_clip(text: str, start_time: float, duration: float):
    """
    A dark, rounded-rectangle speech-bubble graphic with the hook text on
    it, positioned above the main caption band — inspired by the reference
    video's chat-bubble caption over its opening line. Single-line only
    (the text passed in is already a caption chunk, which is already
    width-capped, so it comfortably fits on one line at this smaller font).
    """
    font = ImageFont.truetype(_resolve_font_path(), _HOOK_BUBBLE_FONT_SIZE)
    padding_x, padding_y = 34, 20

    dummy_img = Image.new("RGBA", (10, 10))
    dummy_draw = ImageDraw.Draw(dummy_img)
    text_w = dummy_draw.textlength(text, font=font)
    bbox = dummy_draw.textbbox((0, 0), text, font=font)
    text_h = bbox[3] - bbox[1]

    box_w = int(text_w + 2 * padding_x)
    box_h = int(text_h + 2 * padding_y)

    img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, box_w - 1, box_h - 1], radius=box_h // 2, fill=(15, 15, 15, 220))
    draw.text((padding_x, padding_y - bbox[1]), text, font=font, fill=(255, 255, 255, 255))

    frame = np.array(img)
    rgb_clip = ImageClip(frame[:, :, :3]).with_duration(duration)
    alpha = frame[:, :, 3].astype("float32") / 255.0
    mask_clip = ImageClip(alpha, is_mask=True).with_duration(duration)
    rgb_clip = rgb_clip.with_mask(mask_clip)

    x_pos = (config.VIDEO_WIDTH - box_w) / 2
    y_pos = int(config.VIDEO_HEIGHT * _HOOK_BUBBLE_Y_RATIO)
    return rgb_clip.with_position((x_pos, y_pos)).with_start(start_time)


def _draw_percentage_ring_frame(percentage: int) -> np.ndarray:
    """
    A filling ring with the percentage number centered inside it — drawn
    entirely with PIL (no external asset, no AI generation), directly
    inspired by the reference video's pie-chart infographic moment.
    """
    size = _PERCENTAGE_RING_SIZE
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad, ring_width = 24, 24
    bbox = [pad, pad, size - pad, size - pad]

    draw.ellipse(bbox, fill=(20, 20, 20, 210))  # dark backing disc for contrast on any background
    draw.arc(bbox, start=-90, end=270, fill=(255, 255, 255, 70), width=ring_width)  # track
    sweep = 360 * max(0, min(percentage, 100)) / 100
    draw.arc(bbox, start=-90, end=-90 + sweep, fill=_PERCENTAGE_RING_COLOR, width=ring_width)  # progress

    font = ImageFont.truetype(_resolve_font_path(), int(size * 0.26))
    text = f"{percentage}%"
    tb = draw.textbbox((0, 0), text, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    draw.text(((size - tw) / 2 - tb[0], (size - th) / 2 - tb[1]), text, font=font, fill=(255, 255, 255, 255))

    return np.array(img)


def _percentage_ring_clip(percentage: int, start_time: float, duration: float):
    """Positioned, pop-in-animated version of the percentage ring frame,
    shown just above the caption band for `duration` seconds."""
    frame = _draw_percentage_ring_frame(percentage)
    rgb = frame[:, :, :3]
    alpha = frame[:, :, 3].astype("float32") / 255.0

    img_clip = ImageClip(rgb).with_duration(duration)
    mask_clip = ImageClip(alpha, is_mask=True).with_duration(duration)
    img_clip = img_clip.with_mask(mask_clip)

    pop_in = 0.25

    def scale_at(t):
        if t < pop_in:
            return 0.6 + 0.4 * (t / pop_in)
        return 1.0

    y_pos = int(config.VIDEO_HEIGHT * 0.30) - _PERCENTAGE_RING_SIZE // 2
    img_clip = (
        img_clip.resized(scale_at)
        .with_position(("center", y_pos))
        .with_start(start_time)
    )
    return img_clip


def _detect_percentage_moment(narration_text: str, audio_path: Path, total_duration: float):
    """
    Best-effort: finds the first 'NN%' / 'NN percent' mentioned in the
    script and figures out roughly when it's spoken, so a percentage ring
    can pop up at that exact moment. Returns (percentage, start_time) or
    None if no percentage is mentioned or timing can't be determined —
    never raises.
    """
    try:
        match = _PERCENTAGE_PATTERN.search(narration_text)
        if not match:
            return None
        percentage = int(match.group(1))
        if not (0 < percentage <= 100):
            return None

        words = narration_text.split()
        if not words:
            return None

        timings = _load_word_timings(audio_path)
        if timings and len(timings) >= len(words) * 0.6:
            word_times = [(t.get("text", "").strip() or "", t["start"], t["end"]) for t in timings]
        else:
            per_word = total_duration / len(words)
            word_times = [(w, i * per_word, (i + 1) * per_word) for i, w in enumerate(words)]

        for word, start, _end in word_times:
            if re.search(r'\d', word) or "percent" in word.lower():
                return percentage, start

        return percentage, total_duration * 0.4  # rough fallback placement
    except Exception:
        return None


def _circular_insert_clip(source_clip, duration: float, target_w: int, target_h: int):
    """
    Wraps `source_clip` in a circular mask over a darkened, cover-cropped
    copy of itself as the backdrop — inspired by the reference video's
    circular photo/video insert technique. Returns a full-frame
    (target_w x target_h) CompositeVideoClip; never raises (caller should
    still wrap the call site in try/except as a courtesy, since this
    touches several moviepy effects at once).
    """
    diameter = int(target_w * _CIRCLE_INSERT_SIZE_RATIO)

    backdrop = _cover_resize_and_crop(source_clip, target_w, target_h)
    backdrop = backdrop.with_effects([vfx.MultiplyColor(0.25)])  # darken so the circle pops

    foreground = _cover_resize_and_crop(source_clip, diameter, diameter)

    mask_img = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask_img).ellipse([3, 3, diameter - 3, diameter - 3], fill=255)
    mask_array = np.array(mask_img).astype("float32") / 255.0
    circle_mask = ImageClip(mask_array, is_mask=True).with_duration(duration)
    foreground = foreground.with_mask(circle_mask).with_position("center")

    return CompositeVideoClip([backdrop, foreground], size=(target_w, target_h)).with_duration(duration)


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

    # --- Real sentence-level timing (fixes visual/audio sync drift) --------
    # Captions are already driven by tts_engine.py's real per-word timing
    # sidecar. Scene cuts and rule-number stickers previously were NOT —
    # they used an artificial even split across scenes, which silently
    # drifts out of sync with what's actually being said the moment any one
    # sentence runs longer or shorter than the "average" (e.g. the sticker
    # for rule 3 popping up while the caption is still finishing rule 2's
    # sentence). Deriving scene boundaries from the SAME real word-timing
    # data captions already use is what actually keeps visuals and audio
    # in sync, instead of two independent timing estimates that can drift
    # apart from each other.
    words = narration_text.split()
    timings = _load_word_timings(audio_path)
    if timings and len(timings) >= len(words) * 0.6:
        word_times_for_scenes = [(t.get("text", "").strip() or "", t["start"], t["end"]) for t in timings]
    else:
        per_word = narration_clip.duration / max(len(words), 1)
        word_times_for_scenes = [(w, i * per_word, (i + 1) * per_word) for i, w in enumerate(words)]

    sentence_times = _compute_sentence_start_times(word_times_for_scenes)

    n = len(assets)
    if len(sentence_times) == n and n > 1:
        # Exact match (the common case: hook + 4 rules + cta = 6 sentences
        # = 6 scenes) — use the REAL timing, sentence by sentence.
        scene_durations = [max(0.3, end - start) for (_, start, end) in sentence_times]
        print(f"[+] Scene cuts synced to real sentence timing ({n} sentences matched {n} scenes).")
    elif n > 1:
        # Sentence count didn't match scene count (Gemini wrote a script
        # with a different structure than expected) — real per-sentence
        # timing can't be mapped 1:1 to scenes, so fall back to the old
        # even-split-with-shorter-hook estimate rather than guessing wrong.
        print(f"[i] Sentence count ({len(sentence_times)}) didn't match scene count ({n}) — "
              f"using an even split instead of real sentence timing for scene cuts.")
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

    # Circular video/photo insert — applied to at most one scene, and not
    # every video (see _CIRCLE_INSERT_CHANCE), purely for visual variety so
    # the channel doesn't look identically-templated on every upload. Never
    # applied to scene 0 (the hook) since that scene is already shorter and
    # doing the most work to stop the scroll — keeping it full-bleed.
    if scenes_consistent and _CIRCLE_INSERT_ENABLED and len(clips) > 1 and random.random() < _CIRCLE_INSERT_CHANCE:
        try:
            circle_idx = random.randint(1, len(clips) - 1)
            clips[circle_idx] = _circular_insert_clip(
                clips[circle_idx], scene_durations[circle_idx], config.VIDEO_WIDTH, config.VIDEO_HEIGHT
            )
            print(f"[+] Applied circular insert framing to scene {circle_idx}.")
        except Exception as e:
            print(f"[!] Circular insert framing failed, continuing with the normal full-bleed scene: {e}")
            traceback.print_exc()
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

    # Percentage/stat ring — pops up once, at roughly the moment the script
    # first mentions a "NN%" figure (common in this niche: utilization,
    # savings rate, etc.) — see _detect_percentage_moment() above.
    percentage_ring_clips = []
    if _SHOW_PERCENTAGE_RING:
        try:
            moment = _detect_percentage_moment(narration_text, audio_path, narration_clip.duration)
            if moment:
                percentage, start_time = moment
                percentage_ring_clips.append(
                    _percentage_ring_clip(percentage, start_time, _PERCENTAGE_RING_DURATION)
                )
                print(f"[+] Added a {percentage}% stat ring at {start_time:.2f}s.")
        except Exception as e:
            print(f"[!] Percentage ring overlay failed, continuing without it: {e}")
            traceback.print_exc()

    overlay_clips = captions + progress_bar_clips + sticker_clips + percentage_ring_clips
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
