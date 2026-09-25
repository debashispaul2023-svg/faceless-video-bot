"""Replaceable TTS providers with caching and bounded retries."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time
import traceback
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

import config
from media_utils import media_duration, write_silence


class TTSResult:
    def __init__(self, path: Path, duration: float, words: Optional[List[dict]] = None, provider: str = ""):
        self.path = path
        self.duration = duration
        self.words = words or []
        self.provider = provider


class TTSProvider(ABC):
    name: str = "base"

    @abstractmethod
    def synthesize(self, text: str, voice_id: str, output_path: Path) -> TTSResult:
        raise NotImplementedError


def _clean_text(text: str) -> str:
    return re.sub(r"<break[^>]*>", "", text or "").strip()


def cache_key(character_id: str, text: str, voice_id: str, provider: str) -> str:
    raw = f"{provider}|{character_id}|{voice_id}|{_clean_text(text)}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def cached_path(key: str, ext: str = ".mp3") -> Path:
    return config.AUDIO_CACHE_DIR / f"{key}{ext}"


class LocalSilentProvider(TTSProvider):
    """Deterministic placeholder audio for --demo / offline runs."""

    name = "local"

    def synthesize(self, text: str, voice_id: str, output_path: Path) -> TTSResult:
        words = _clean_text(text).split()
        duration = max(1.2, min(12.0, 0.28 * max(1, len(words))))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Soft tone instead of digital silence so mix checks have a signal.
        from media_utils import run_ffmpeg
        run_ffmpeg([
            "-f", "lavfi",
            "-i", f"sine=frequency=440:sample_rate=44100:duration={duration:.3f}",
            "-af", "volume=0.08",
            str(output_path.with_suffix(".wav")),
        ])
        wav = output_path.with_suffix(".wav")
        if output_path.suffix.lower() != ".wav":
            run_ffmpeg(["-i", str(wav), "-q:a", "4", str(output_path)])
            try:
                wav.unlink()
            except OSError:
                pass
            path = output_path
        else:
            path = wav
        return TTSResult(path, media_duration(path) or duration, provider=self.name)


class EdgeTTSProvider(TTSProvider):
    name = "edge"

    def synthesize(self, text: str, voice_id: str, output_path: Path) -> TTSResult:
        import edge_tts

        text = _clean_text(text)
        voice = voice_id or config.TTS_VOICE
        output_path.parent.mkdir(parents=True, exist_ok=True)

        async def _run():
            communicate = edge_tts.Communicate(text, voice)
            word_timings = []
            with open(output_path, "wb") as f:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        start = chunk["offset"] / 10_000_000
                        end = start + (chunk["duration"] / 10_000_000)
                        word_timings.append({"text": chunk.get("text", ""), "start": start, "end": end})
            return word_timings

        last_err = None
        for attempt in range(config.TTS_MAX_RETRIES):
            try:
                words = asyncio.run(_run())
                dur = media_duration(output_path)
                if dur <= 0:
                    raise RuntimeError("edge-tts wrote empty audio")
                return TTSResult(output_path, dur, words, provider=self.name)
            except Exception as e:
                last_err = e
                time.sleep(1.2 * (attempt + 1))
        raise RuntimeError(f"Edge TTS failed: {last_err}")


class ElevenLabsTTSProvider(TTSProvider):
    name = "elevenlabs"

    def synthesize(self, text: str, voice_id: str, output_path: Path) -> TTSResult:
        if not config.ELEVENLABS_API_KEYS:
            raise RuntimeError("ELEVENLABS_API_KEYS empty")
        text = _clean_text(text)
        last_err = None
        for key_i, api_key in enumerate(config.ELEVENLABS_API_KEYS, start=1):
            for attempt in range(config.TTS_MAX_RETRIES):
                try:
                    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
                    headers = {"xi-api-key": api_key, "Content-Type": "application/json"}
                    payload = {
                        "text": text,
                        "model_id": config.ELEVENLABS_MODEL_ID,
                        "voice_settings": {
                            "stability": 0.4,
                            "similarity_boost": 0.75,
                            "style": 0.35,
                            "use_speaker_boost": True,
                        },
                    }
                    resp = requests.post(
                        url, headers=headers, json=payload,
                        params={"output_format": "mp3_44100_128"}, timeout=60,
                    )
                    if resp.status_code != 200:
                        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:180]}")
                    data = resp.json()
                    audio_bytes = base64.b64decode(data["audio_base64"])
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_bytes(audio_bytes)
                    words = _words_from_elevenlabs(text, data.get("alignment") or {})
                    dur = media_duration(output_path)
                    return TTSResult(output_path, dur, words, provider=self.name)
                except Exception as e:
                    last_err = e
                    print(f"[!] ElevenLabs key #{key_i} attempt {attempt + 1} failed: {e}")
                    time.sleep(1.2 * (attempt + 1))
        raise RuntimeError(f"All ElevenLabs keys failed: {last_err}")


def _words_from_elevenlabs(text: str, alignment: dict) -> List[dict]:
    try:
        chars = alignment.get("characters") or []
        starts = alignment.get("character_start_times_seconds") or []
        ends = alignment.get("character_end_times_seconds") or []
        if not chars or len(chars) != len(starts) or len(chars) != len(ends):
            return []
        words = text.split()
        timings = []
        pos = 0
        for word in words:
            while pos < len(chars) and chars[pos].strip() == "" and chars[pos] != word[0]:
                pos += 1
            if pos >= len(chars):
                return []
            word_start_idx = pos
            matched = 0
            while pos < len(chars) and matched < len(word) and chars[pos] == word[matched]:
                pos += 1
                matched += 1
            if matched != len(word):
                return []
            timings.append({"text": word, "start": starts[word_start_idx], "end": ends[pos - 1]})
        return timings
    except Exception:
        return []


def get_tts_provider(name: Optional[str] = None) -> TTSProvider:
    choice = (name or config.TTS_PROVIDER or "auto").lower()
    if choice == "local":
        return LocalSilentProvider()
    if choice == "edge":
        return EdgeTTSProvider()
    if choice == "elevenlabs":
        return ElevenLabsTTSProvider()
    # auto
    if config.ELEVENLABS_API_KEYS:
        return ElevenLabsTTSProvider()
    return EdgeTTSProvider()


def voice_id_for(character_id: str, provider: TTSProvider) -> str:
    cid = (character_id or "narrator").lower()
    if provider.name == "elevenlabs":
        return config.CHARACTER_ELEVENLABS_VOICES.get(cid, config.CHARACTER_ELEVENLABS_VOICES["narrator"])
    if provider.name == "edge":
        return config.CHARACTER_EDGE_VOICES.get(cid, config.CHARACTER_EDGE_VOICES["narrator"])
    return cid


def synthesize_line(character_id: str, text: str, dest: Path, provider: Optional[TTSProvider] = None) -> TTSResult:
    provider = provider or get_tts_provider()
    voice = voice_id_for(character_id, provider)
    key = cache_key(character_id, text, voice, provider.name)
    cached = cached_path(key, dest.suffix or ".mp3")
    dest.parent.mkdir(parents=True, exist_ok=True)

    if cached.is_file() and cached.stat().st_size > 256:
        dest.write_bytes(cached.read_bytes())
        sidecar = cached.with_suffix(".words.json")
        words = []
        if sidecar.is_file():
            try:
                words = json.loads(sidecar.read_text())
            except Exception:
                words = []
        return TTSResult(dest, media_duration(dest), words, provider=provider.name + "+cache")

    chain: List[TTSProvider] = [provider]
    if provider.name != "edge":
        chain.append(EdgeTTSProvider())
    chain.append(LocalSilentProvider())

    last_err = None
    for p in chain:
        try:
            v = voice_id_for(character_id, p) if p.name != "local" else character_id
            result = p.synthesize(text, v, dest)
            try:
                cached.write_bytes(Path(result.path).read_bytes())
                if result.words:
                    cached.with_suffix(".words.json").write_text(json.dumps(result.words))
            except Exception:
                pass
            return result
        except Exception as e:
            last_err = e
            print(f"[!] TTS provider '{p.name}' failed for {character_id}: {e}")
            traceback.print_exc()
    # Should not reach here because local always works, but be safe.
    write_silence(dest.with_suffix(".wav"), 1.5)
    fallback = dest.with_suffix(".wav")
    return TTSResult(fallback, media_duration(fallback) or 1.5, provider="silence")
