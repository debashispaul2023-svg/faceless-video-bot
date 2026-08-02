import asyncio
import json
import os
import traceback
from pathlib import Path

import requests
import edge_tts

import config

# ---------------------------------------------------------------------------
# ElevenLabs settings
# ---------------------------------------------------------------------------
ELEVENLABS_VOICE_ID = "pNInz6obpgDQGcFmaJgB"  # Adam
ELEVENLABS_MODEL_ID = "eleven_multilingual_v2"
ELEVENLABS_TTS_URL = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"

# Load and split the comma-separated key list from the GitHub secret.
_raw_keys = os.getenv("ELEVENLABS_API_KEYS", "")
ELEVENLABS_API_KEYS = [k.strip() for k in _raw_keys.split(",") if k.strip()]


# ---------------------------------------------------------------------------
# ElevenLabs generation with automatic key rotation
# ---------------------------------------------------------------------------
def _generate_with_elevenlabs(text: str, output_path: Path) -> bool:
    """
    Tries every key in ELEVENLABS_API_KEYS in order. Returns True and writes
    the mp3 to output_path on the first success. Returns False (without
    raising) if every key fails, so the caller can fall back to edge-tts.
    """
    if not ELEVENLABS_API_KEYS:
        print("[!] No ELEVENLABS_API_KEYS found in environment. Skipping ElevenLabs.")
        return False

    headers_template = {
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": ELEVENLABS_MODEL_ID,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
        },
    }

    for index, api_key in enumerate(ELEVENLABS_API_KEYS, start=1):
        print(f"[+] ElevenLabs: trying API key #{index}/{len(ELEVENLABS_API_KEYS)}...")
        headers = dict(headers_template)
        headers["xi-api-key"] = api_key

        try:
            response = requests.post(
                ELEVENLABS_TTS_URL,
                headers=headers,
                json=payload,
                timeout=60,
            )

            if response.status_code == 200:
                with open(output_path, "wb") as f:
                    f.write(response.content)
                print(f"[+] ElevenLabs: success with API key #{index}.")
                return True

            # Detect the specific failure modes we should rotate past.
            if response.status_code in (401, 429):
                print(
                    f"[!] ElevenLabs key #{index} failed "
                    f"({response.status_code} {response.reason}). Trying next key..."
                )
                continue

            # Try to detect quota-exceeded errors that come back with a
            # different status code but a recognizable message body.
            body_text = response.text.lower()
            if "quota" in body_text or "credit" in body_text:
                print(f"[!] ElevenLabs key #{index} failed (quota exceeded). Trying next key...")
                continue

            print(
                f"[!] ElevenLabs key #{index} failed with unexpected status "
                f"{response.status_code}: {response.text[:200]}. Trying next key..."
            )
            continue

        except requests.exceptions.RequestException as e:
            print(f"[!] ElevenLabs key #{index} raised a request error: {e}. Trying next key...")
            continue
        except Exception as e:
            print(f"[!] ElevenLabs key #{index} raised an unexpected error: {e}. Trying next key...")
            traceback.print_exc()
            continue

    print("[!] All ElevenLabs API keys failed.")
    return False


# ---------------------------------------------------------------------------
# edge-tts fallback (original free engine, with word-timing capture)
# ---------------------------------------------------------------------------
async def _synthesize_speech_edge(text: str, output_path: Path):
    """
    Synthesizes speech with edge-tts AND captures real per-word timing (via
    edge-tts's WordBoundary events) so captions can highlight the exact word
    being spoken, instead of guessing with an even split.

    Returns a list of {"text": str, "start": float, "end": float} in
    seconds, or an empty list if word-boundary capture isn't available for
    any reason (this never blocks audio generation — captions just fall
    back to an even-split chunk timing in that case).
    """
    communicate = edge_tts.Communicate(text, config.TTS_VOICE)
    word_timings = []

    try:
        with open(output_path, "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    # offset/duration come back in 100-nanosecond units.
                    start = chunk["offset"] / 10_000_000
                    end = start + (chunk["duration"] / 10_000_000)
                    word_timings.append({
                        "text": chunk.get("text", ""),
                        "start": start,
                        "end": end,
                    })
    except Exception as e:
        print(f"[!] Word-boundary capture failed ({e}); will fall back to even-split captions.")
        traceback.print_exc()
        # Make sure we still have SOME audio file even if streaming broke
        # partway through — fall back to the simple save() path.
        if not output_path.exists() or output_path.stat().st_size == 0:
            await edge_tts.Communicate(text, config.TTS_VOICE).save(str(output_path))
        word_timings = []

    return word_timings


def _generate_with_edge_tts(text: str, output_path: Path):
    """Runs the edge-tts fallback and writes the word-timing sidecar file."""
    print(f"[+] Falling back to free Edge Neural TTS ({config.TTS_VOICE})...")
    word_timings = asyncio.run(_synthesize_speech_edge(text, output_path))

    if word_timings:
        sidecar_path = output_path.with_suffix(".words.json")
        try:
            sidecar_path.write_text(json.dumps(word_timings))
            print(f"[+] Captured {len(word_timings)} word timings for karaoke captions.")
        except Exception as e:
            print(f"[!] Could not write word-timing sidecar file: {e}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def generate_voiceover(text: str, output_filename: str = "voiceover.mp3") -> Path:
    """
    Converts text to natural audio.

    Primary engine: ElevenLabs (voice "Adam", model eleven_multilingual_v2),
    rotating through every key in ELEVENLABS_API_KEYS until one succeeds.

    Fallback engine: Microsoft Edge Neural TTS (edge-tts), used automatically
    if every ElevenLabs key fails (or none are configured). This keeps the
    pipeline from ever crashing on TTS.

    Returns the Path to the generated .mp3 file. Signature/return value is
    unchanged from the previous edge-tts-only version, so main.py needs no
    changes.
    """
    output_path = config.OUTPUT_DIR / output_filename

    preview = text[:60] + "..." if len(text) > 60 else text
    print(f"[+] Synthesizing voiceover for: '{preview}'")

    success = _generate_with_elevenlabs(text, output_path)

    if not success:
        _generate_with_edge_tts(text, output_path)

    print(f"[+] Voiceover generated at: {output_path}")
    return output_path
