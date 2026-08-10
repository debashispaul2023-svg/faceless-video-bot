import os
import json
import base64
import asyncio
import traceback
from pathlib import Path
import requests
import edge_tts  # the PREVIOUS voiceover system — kept as the fallback if EVERY ElevenLabs key fails
import config

# ─────────────────────────────────────────────────────────────────────────
# Your ElevenLabs keys go in the GitHub Secret ELEVENLABS_API_KEYS as a
# comma-separated string (e.g. "key1,key2,key3,...,key10") — NOT hardcoded
# here. This project has already had multiple Google Cloud projects
# suspended from keys leaked directly in code/chat; don't repeat that with
# ElevenLabs. Add the secret in: repo Settings > Secrets and variables >
# Actions > New repository secret.
# ─────────────────────────────────────────────────────────────────────────
ELEVENLABS_API_KEYS = [
    k.strip() for k in os.getenv("ELEVENLABS_API_KEYS", "").split(",") if k.strip()
]

ELEVENLABS_VOICE_ID = "pNInz6obpgDQGcFmaJgB"  # "Adam" — deep, professional male voice
# Alternative professional male voice: "Antoni" = "ErXwobaYiN019PkySvjV"
ELEVENLABS_MODEL_ID = "eleven_multilingual_v2"
ELEVENLABS_BASE_URL = "https://api.elevenlabs.io/v1"


import re

def _try_one_elevenlabs_key(api_key: str, text: str, output_path: Path):
    """
    Attempts to generate the voiceover with a single ElevenLabs API key,
    using the /with-timestamps endpoint so we get real per-character timing
    back (used to build accurate word-by-word karaoke captions).

    Raises on ANY failure — bad/revoked key (401), quota/rate limit
    exceeded (429), server error, network timeout, whatever — so the
    caller's rotation loop knows to move on to the next key.
    """

    # Remove SSML <break> tags since ElevenLabs reads them literally
    clean_text = re.sub(r'<break[^>]*>', '', text)

    url = f"{ELEVENLABS_BASE_URL}/text-to-speech/{ELEVENLABS_VOICE_ID}/with-timestamps"
    headers = {"xi-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "text": clean_text,
        "model_id": ELEVENLABS_MODEL_ID,
        "voice_settings": {"stability": 0.35, "similarity_boost": 0.85},
    }
    params = {"output_format": "mp3_44100_128"}

    response = requests.post(url, headers=headers, json=payload, params=params, timeout=60)
    if response.status_code != 200:
        # Covers 401 (invalid/revoked key), 429 (free-tier quota exhausted
        # or rate limited), and anything else the API might return.
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")

    data = response.json()
    audio_bytes = base64.b64decode(data["audio_base64"])
    with open(output_path, "wb") as f:
        f.write(audio_bytes)

    return data.get("alignment")


def _build_word_timings_from_elevenlabs(text: str, alignment: dict):
    """
    Aggregates ElevenLabs' character-level alignment into word-level
    timings, in the same {"text","start","end"} sidecar format
    video_renderer.py already reads for karaoke captions. Bails out and
    returns [] (never raises) if anything doesn't line up cleanly —
    video_renderer.py falls back to an even-split estimate in that case.
    """
    try:
        chars = alignment.get("characters", [])
        starts = alignment.get("character_start_times_seconds", [])
        ends = alignment.get("character_end_times_seconds", [])
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
                return []  # alignment drifted from the text — don't guess

            timings.append({"text": word, "start": starts[word_start_idx], "end": ends[pos - 1]})
        return timings
    except Exception:
        return []


async def _synthesize_with_edge_tts(text: str, output_path: Path):
    """
    The PREVIOUS voiceover system (Microsoft Edge Neural TTS, free, no API
    key needed) — used as the fallback when every ElevenLabs key fails.
    Also captures real per-word timing via edge-tts's own WordBoundary
    events, same as it always did, so captions stay accurate even when
    running on this fallback path.
    """
    communicate = edge_tts.Communicate(text, config.TTS_VOICE)
    word_timings = []
    try:
        with open(output_path, "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / 10_000_000
                    end = start + (chunk["duration"] / 10_000_000)
                    word_timings.append({"text": chunk.get("text", ""), "start": start, "end": end})
    except Exception as e:
        print(f"[!] Edge TTS word-boundary capture failed ({e}); retrying with plain save.")
        traceback.print_exc()
        if not output_path.exists() or output_path.stat().st_size == 0:
            await edge_tts.Communicate(text, config.TTS_VOICE).save(str(output_path))
        word_timings = []
    return word_timings


def _fallback_to_edge_tts(text: str, output_path: Path):
    """Called only when EVERY key in ELEVENLABS_API_KEYS has failed."""
    print("[!] All ElevenLabs keys failed — falling back to the previous voiceover "
          "system (Edge TTS) so this video still gets made.")
    word_timings = asyncio.run(_synthesize_with_edge_tts(text, output_path))
    return word_timings


def _write_word_timings_sidecar(output_path: Path, word_timings: list):
    if not word_timings:
        return
    sidecar_path = output_path.with_suffix(".words.json")
    try:
        sidecar_path.write_text(json.dumps(word_timings))
        print(f"[+] Captured {len(word_timings)} word timings for karaoke captions.")
    except Exception as e:
        print(f"[!] Could not write word-timing sidecar file: {e}")


def generate_voiceover(text: str, output_filename: str = "voiceover.mp3") -> Path:
    """
    Converts text to speech using ElevenLabs, rotating through
    ELEVENLABS_API_KEYS in order until one succeeds. Never crashes: if a
    key is out of free-tier quota (429), invalid/revoked (401), or fails
    for any other reason, it's skipped and the next one is tried
    automatically.

    If EVERY ElevenLabs key fails, automatically falls back to the
    PREVIOUS voiceover system (free Edge TTS) so a video still gets made
    no matter what.

    Returns the Path to the saved .mp3 — same signature/return type as
    before, so main.py doesn't need any changes.
    """
    output_path = config.OUTPUT_DIR / output_filename

    if not ELEVENLABS_API_KEYS:
        print("[!] ELEVENLABS_API_KEYS is empty — set it as a comma-separated GitHub "
              "secret (key1,key2,...). Using the previous voiceover system (Edge TTS) for now.")
        word_timings = _fallback_to_edge_tts(text, output_path)
        _write_word_timings_sidecar(output_path, word_timings)
        print(f"[+] Voiceover generated at: {output_path}")
        return output_path

    print(f"[+] Synthesizing voiceover with ElevenLabs ({ELEVENLABS_MODEL_ID}, voice=Adam)...")
    for i, api_key in enumerate(ELEVENLABS_API_KEYS, start=1):
        masked = f"...{api_key[-4:]}" if len(api_key) > 4 else "****"
        try:
            print(f"[+] Trying ElevenLabs key #{i}/{len(ELEVENLABS_API_KEYS)} ({masked})...")
            alignment = _try_one_elevenlabs_key(api_key, text, output_path)
            print(f"[✓] Voiceover generated with ElevenLabs key #{i} at: {output_path}")

            word_timings = _build_word_timings_from_elevenlabs(text, alignment) if alignment else []
            _write_word_timings_sidecar(output_path, word_timings)
            return output_path

        except Exception as e:
            print(f"[!] ElevenLabs key #{i} failed ({e}); trying the next key...")
            continue

    # Every single ElevenLabs key failed — use the previous system instead.
    traceback.print_exc()
    word_timings = _fallback_to_edge_tts(text, output_path)
    _write_word_timings_sidecar(output_path, word_timings)
    print(f"[+] Voiceover generated at: {output_path}")
    return output_path
