"""Settings for the GTA 6 clipper. Secrets come from environment variables only."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
OUTPUT_DIR = BASE_DIR / "output"

for _d in (ASSETS_DIR, OUTPUT_DIR):
    _d.mkdir(exist_ok=True, parents=True)

# Optional narrator voiceover (CLIP_VOICEOVER=1). Not needed for normal clips.
ELEVENLABS_API_KEYS = [
    k.strip() for k in os.getenv("ELEVENLABS_API_KEYS", "").split(",") if k.strip()
]
ELEVENLABS_MODEL_ID = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
ELEVENLABS_NARRATOR_VOICE = os.getenv("ELEVENLABS_VOICE_NARRATOR", "ErXwobaYiN019PkySvjV")
