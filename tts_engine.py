import asyncio
import json
import traceback
from pathlib import Path
import edge_tts
import config


async def _synthesize_speech(text: str, output_path: Path):
    """
    Synthesizes speech AND captures real per-word timing (via edge-tts's
    WordBoundary events) so captions can highlight the exact word being
    spoken, instead of guessing with an even split.

    Returns a list of {"text": str, "start": float, "end": float} in seconds,
    or an empty list if word-boundary capture isn't available for any reason
    (this never blocks audio generation — captions just fall back to an
    even-split chunk timing in that case).
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


def generate_voiceover(text: str, output_filename: str = "voiceover.mp3") -> Path:
    """
    Converts text to natural audio using Microsoft Edge Neural TTS.

    Also writes a sidecar JSON file (same name, ".words.json" suffix) with
    real per-word timing, IF it could be captured. video_renderer.py looks
    for this file automatically to drive word-by-word karaoke captions; if
    it's missing, captions fall back to an even-split estimate. This keeps
    the function signature/return value unchanged for main.py.
    """
    output_path = config.OUTPUT_DIR / output_filename
    print(f"[+] Synthesizing Neural Voiceover ({config.TTS_VOICE})...")

    word_timings = asyncio.run(_synthesize_speech(text, output_path))

    if word_timings:
        sidecar_path = output_path.with_suffix(".words.json")
        try:
            sidecar_path.write_text(json.dumps(word_timings))
            print(f"[+] Captured {len(word_timings)} word timings for karaoke captions.")
        except Exception as e:
            print(f"[!] Could not write word-timing sidecar file: {e}")

    print(f"[+] Voiceover generated at: {output_path}")
    return output_path
