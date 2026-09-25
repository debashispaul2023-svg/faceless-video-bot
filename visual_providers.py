"""Replaceable visual generation providers.

None of these claim to produce full character animation unless the backend
actually returns a video. Stills are valid scene sources and are later
given camera motion in the renderer.
"""

from __future__ import annotations

import hashlib
import io
import time
import traceback
import urllib.parse
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional

import requests
from PIL import Image, ImageDraw, ImageFont

import config
from characters import CHARACTERS, VISUAL_STYLE, WORLD_BIBLE, get_character


class VisualProvider(ABC):
    name: str = "base"

    @abstractmethod
    def generate_image(self, prompt: str, output_path: Path, width: int, height: int, seed: int = 0) -> Path:
        raise NotImplementedError

    def generate_video(self, prompt: str, output_path: Path, width: int, height: int, duration: float = 4.0) -> Optional[Path]:
        return None

    def generate_image_with_reference(
        self, prompt: str, output_path: Path, width: int, height: int,
        reference_images: Optional[List[Path]] = None, seed: int = 0,
    ) -> Path:
        # Default: ignore refs unless a provider overrides this.
        return self.generate_image(prompt, output_path, width, height, seed=seed)

    def generate_video_with_reference(
        self, prompt: str, output_path: Path, width: int, height: int,
        reference_images: Optional[List[Path]] = None, duration: float = 4.0,
    ) -> Optional[Path]:
        return self.generate_video(prompt, output_path, width, height, duration=duration)

    def supports_reference_images(self) -> bool:
        return False

    def supports_video(self) -> bool:
        return False

    def supports_video_generation(self) -> bool:
        return self.supports_video()


def build_scene_prompt(
    character_ids: List[str],
    environment: str,
    action: str,
    camera: str,
    style: str,
) -> str:
    """Deterministic prompt that locks character appearance."""
    parts = [style or VISUAL_STYLE, WORLD_BIBLE]
    for cid in character_ids or []:
        if cid == "narrator":
            continue
        try:
            ch = get_character(cid)
        except KeyError:
            continue
        parts.append(f"CHARACTER LOCK {ch.name}: {ch.canonical_prompt}")
        parts.append(f"Keep {ch.name} species={ch.species}, colors={ch.colors}, accessories={ch.accessories}, proportions={ch.proportions}.")
    parts.append(f"ENVIRONMENT: {environment}")
    parts.append(f"ACTION: {action}")
    parts.append(f"CAMERA: {camera}")
    parts.append("lock species colors accessories across shots")
    parts.append(
        "consistent character design sheet look, same faces across shots, "
        "wholesome kids comedy, no text, no watermark, no logos, no photoreal humans"
    )
    return ", ".join(p for p in parts if p)


class LocalPlaceholderProvider(VisualProvider):
    """Always-available stills. Used for --demo and as last-resort fallback."""

    name = "local"

    _PALETTES = {
        "toto": ((232, 176, 74), (196, 48, 48)),
        "momo": ((186, 154, 214), (244, 210, 72)),
        "piku": ((88, 176, 86), (64, 170, 170)),
        "fiko": ((92, 164, 214), (232, 132, 64)),
        "grove": ((126, 196, 126), (92, 164, 214)),
    }

    def generate_image(self, prompt: str, output_path: Path, width: int, height: int, seed: int = 0) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        key = "grove"
        for cid in CHARACTERS:
            if cid in (prompt or "").lower():
                key = cid
                break
        sky, accent = self._PALETTES.get(key, self._PALETTES["grove"])
        img = Image.new("RGB", (width, height), sky)
        draw = ImageDraw.Draw(img)
        # Simple original "set" so demo videos are not a flat color.
        ground_y = int(height * 0.68)
        draw.rectangle([0, ground_y, width, height], fill=(92, 148, 72))
        draw.ellipse([width * 0.72, height * 0.08, width * 0.90, height * 0.28], fill=(255, 236, 140))
        body = [width * 0.38, height * 0.36, width * 0.62, height * 0.72]
        draw.ellipse(body, fill=accent, outline=(40, 40, 40), width=6)
        draw.ellipse([width * 0.44, height * 0.28, width * 0.56, height * 0.44], fill=(255, 244, 220), outline=(40, 40, 40), width=4)
        font = _safe_font(36)
        label = _short_label(prompt)
        draw.rectangle([40, height - 90, width - 40, height - 30], fill=(20, 20, 20))
        draw.text((56, height - 80), label[:48], fill="white", font=font)
        img.save(output_path, quality=92)
        return output_path


class PollinationsProvider(VisualProvider):
    name = "pollinations"

    def generate_image(self, prompt: str, output_path: Path, width: int, height: int, seed: int = 0) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        encoded = urllib.parse.quote(prompt[:900])
        url = (
            f"https://image.pollinations.ai/prompt/{encoded}"
            f"?width={width}&height={height}&model={config.POLLINATIONS_MODEL}&seed={seed}&nologo=true"
        )
        last_err = None
        for attempt in range(config.VISUAL_MAX_RETRIES):
            try:
                resp = requests.get(url, timeout=60)
                resp.raise_for_status()
                img = Image.open(io.BytesIO(resp.content)).convert("RGB")
                img = img.resize((width, height), Image.LANCZOS)
                img.save(output_path, quality=92)
                return output_path
            except Exception as e:
                last_err = e
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"Pollinations image failed: {last_err}")


class GeminiImageProvider(VisualProvider):
    """Uses Gemini image generation when the installed SDK supports it."""

    name = "gemini"

    def generate_image(self, prompt: str, output_path: Path, width: int, height: int, seed: int = 0) -> Path:
        if not config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY missing")
        try:
            from google import genai
            from google.genai import types
        except Exception as e:
            raise RuntimeError(f"google-genai not available: {e}") from e

        client = genai.Client(api_key=config.GEMINI_API_KEY)
        last_err = None
        for attempt in range(config.VISUAL_MAX_RETRIES):
            try:
                response = client.models.generate_content(
                    model="gemini-2.0-flash-preview-image-generation",
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE", "TEXT"],
                    ),
                )
                for cand in getattr(response, "candidates", []) or []:
                    for part in getattr(cand.content, "parts", []) or []:
                        inline = getattr(part, "inline_data", None)
                        if inline and getattr(inline, "data", None):
                            img = Image.open(io.BytesIO(inline.data)).convert("RGB")
                            img = img.resize((width, height), Image.LANCZOS)
                            output_path.parent.mkdir(parents=True, exist_ok=True)
                            img.save(output_path, quality=92)
                            return output_path
                last_err = RuntimeError("Gemini response had no image bytes")
            except Exception as e:
                last_err = e
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"Gemini image failed: {last_err}")


def get_visual_provider(name: Optional[str] = None) -> VisualProvider:
    choice = (name or config.VISUAL_PROVIDER or "pollinations").lower()
    if choice == "local":
        return LocalPlaceholderProvider()
    if choice == "gemini":
        return GeminiImageProvider()
    if choice == "pollinations":
        return PollinationsProvider()
    return PollinationsProvider()


def generate_with_fallback(prompt: str, output_path: Path, width: int, height: int, seed: int = 0) -> Path:
    providers: List[VisualProvider] = []
    primary = get_visual_provider()
    providers.append(primary)
    if primary.name != "pollinations":
        providers.append(PollinationsProvider())
    providers.append(LocalPlaceholderProvider())

    seen = set()
    last_err = None
    for provider in providers:
        if provider.name in seen:
            continue
        seen.add(provider.name)
        try:
            print(f"[+] Visual provider '{provider.name}' generating still...")
            return provider.generate_image(prompt, output_path, width, height, seed=seed)
        except Exception as e:
            last_err = e
            print(f"[!] Visual provider '{provider.name}' failed: {e}")
            traceback.print_exc()
    # Last line of defense: local always works.
    return LocalPlaceholderProvider().generate_image(prompt, output_path, width, height, seed=seed)


def cache_key_for_prompt(prompt: str, width: int, height: int) -> str:
    raw = f"{prompt}|{width}x{height}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _safe_font(size: int):
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _short_label(prompt: str) -> str:
    text = (prompt or "Sunny Grove scene").replace("\n", " ")
    return text[:60]
