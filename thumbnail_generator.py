"""
Shared thumbnail generator — used by youtube_uploader.py (to give every
NEW video a unique custom thumbnail at upload time) and channel_manager.py
(to regenerate a thumbnail when rewriting an underperformer).

Centralizing this in one place means every video gets a purpose-made
thumbnail (unique background + burned-in title) instead of relying on
YouTube's auto-picked frame, which kept converging on similar-looking
stock footage across different videos and made many videos look identical
in the Content list.
"""

import os
import io
import time
import urllib.parse
import traceback
from pathlib import Path
import requests
from PIL import Image, ImageDraw, ImageFont
import config

_THUMBNAIL_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    str(config.ASSETS_DIR / "fonts" / "CaptionFont-Bold.ttf"),
]


def _resolve_thumbnail_font() -> str:
    for candidate in _THUMBNAIL_FONT_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError("No usable font found for thumbnail text (install fonts-dejavu-core).")


def _wrap_text(draw, text, font, max_width):
    words = text.upper().split()
    lines, current = [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def generate_thumbnail(title: str, save_path: Path, seed_extra: str = "") -> bool:
    """
    Generates a punchy, Shorts-style vertical thumbnail: a unique AI
    background image (via Pollinations — no API key needed) with the
    video's own title burned on top in bold text.

    `seed_extra` (e.g. the video_id or a timestamp) is folded into the
    generation seed so two videos with very similar titles still get
    visually distinct backgrounds.

    Returns True/False; never raises — a failed thumbnail should never
    block an upload or a metadata rewrite.
    """
    try:
        prompt = f"{title}, personal finance concept, dramatic cinematic lighting, photorealistic, no text, no watermark"
        encoded = urllib.parse.quote(prompt)
        seed = abs(hash(f"{title}{seed_extra}{time.time()}")) % (10 ** 8)
        url = (f"https://image.pollinations.ai/prompt/{encoded}"
               f"?width=1080&height=1920&model=flux&seed={seed}")

        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")

        # Stronger top gradient for better text readability on any background
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw_overlay = ImageDraw.Draw(overlay)
        draw_overlay.rectangle([0, 0, img.width, int(img.height * 0.42)], fill=(0, 0, 0, 160))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

        draw = ImageDraw.Draw(img)
        # Larger, more aggressive font for Shorts thumbnails (titles are shorter now)
        font = ImageFont.truetype(_resolve_thumbnail_font(), 110)
        max_text_width = img.width - 80
        lines = _wrap_text(draw, title, font, max_text_width)[:3]

        line_height = 125
        start_y = 70
        for i, line in enumerate(lines):
            line_width = draw.textlength(line, font=font)
            x = (img.width - line_width) / 2
            y = start_y + i * line_height
            # Thicker black outline for maximum contrast
            for dx, dy in [(-5, 0), (5, 0), (0, -5), (0, 5), (-4, -4), (4, 4), (-4, 4), (4, -4),
                           (-3, 0), (3, 0), (0, -3), (0, 3)]:
                draw.text((x + dx, y + dy), line, font=font, fill="black")
            draw.text((x, y), line, font=font, fill="#FFD600")  # bright yellow

        img.save(save_path, quality=92)
        return True
    except Exception as e:
        print(f"[!] Thumbnail generation failed: {e}")
        traceback.print_exc()
        return False


def generate_cartoon_thumbnail(plan, episode_dir, save_path=None):
    """
    1280x720 original cartoon thumbnail from episode title + main characters.
    Prefers AI stills; falls back to compositing an existing scene visual.
    """
    from visual_providers import build_scene_prompt, generate_with_fallback

    episode_dir = Path(episode_dir)
    dest = Path(save_path or (episode_dir / "thumbnail.jpg"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    width, height = config.THUMBNAIL_WIDTH, config.THUMBNAIL_HEIGHT

    main_chars = list(plan.characters or [])[:3]
    first = plan.scenes[0] if plan.scenes else None
    prompt = plan.thumbnail_prompt or build_scene_prompt(
        character_ids=main_chars,
        environment=first.environment if first else "Sunny Grove hill",
        action=first.action if first else "friends smiling at the camera",
        camera="close heroic group shot",
        style=plan.style,
    )
    prompt += ", thumbnail composition, faces large and readable, no text, no watermark"

    raw = dest.with_name("thumbnail_raw.jpg")
    try:
        generate_with_fallback(prompt, raw, width, height, seed=abs(hash(plan.episode_id)) % 10**8)
        img = Image.open(raw).convert("RGB").resize((width, height))
    except Exception:
        img = _fallback_from_scene(plan, episode_dir, width, height)

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_o = ImageDraw.Draw(overlay)
    draw_o.rectangle([0, int(height * 0.62), width, height], fill=(12, 40, 28, 170))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(_resolve_thumbnail_font(), 52)
    title = (plan.title or config.SERIES_NAME).upper()
    lines = _wrap_text(draw, title, font, width - 80)[:2]
    y = height - 70 - 58 * len(lines)
    for line in lines:
        tw = draw.textlength(line, font=font)
        x = (width - tw) / 2
        for dx, dy in [(-3, 0), (3, 0), (0, -3), (0, 3), (-2, -2), (2, 2)]:
            draw.text((x + dx, y + dy), line, font=font, fill="black")
        draw.text((x, y), line, font=font, fill="#FFF4A3")
        y += 58
    img.save(dest, quality=93)
    print(f"[+] Thumbnail: {dest}")
    return dest


def _fallback_from_scene(plan, episode_dir, width, height):
    episode_dir = Path(episode_dir)
    candidates = list(episode_dir.glob("scenes/*/visual.jpg"))
    if candidates:
        img = Image.open(candidates[0]).convert("RGB")
        img = img.resize((width, height), Image.LANCZOS)
        return img
    return Image.new("RGB", (width, height), (70, 150, 110))

