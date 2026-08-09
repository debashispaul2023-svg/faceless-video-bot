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

        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw_overlay = ImageDraw.Draw(overlay)
        draw_overlay.rectangle([0, 0, img.width, img.height * 0.4], fill=(0, 0, 0, 130))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

        draw = ImageDraw.Draw(img)
        font = ImageFont.truetype(_resolve_thumbnail_font(), 100)
        max_text_width = img.width - 100
        lines = _wrap_text(draw, title, font, max_text_width)[:3]

        line_height = 115
        start_y = 80
        for i, line in enumerate(lines):
            line_width = draw.textlength(line, font=font)
            x = (img.width - line_width) / 2
            y = start_y + i * line_height
            for dx, dy in [(-4, 0), (4, 0), (0, -4), (0, 4), (-3, -3), (3, 3), (-3, 3), (3, -3)]:
                draw.text((x + dx, y + dy), line, font=font, fill="black")
            draw.text((x, y), line, font=font, fill="yellow")

        img.save(save_path, quality=92)
        return True
    except Exception as e:
        print(f"[!] Thumbnail generation failed: {e}")
        traceback.print_exc()
        return False
