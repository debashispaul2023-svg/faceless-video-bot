import os
from pathlib import Path

# Directory Paths
BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
OUTPUT_DIR = BASE_DIR / "output"

ASSETS_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# API Keys and Secrets
# "YOUR_GEMINI_API_KEY_HERE"-এর জায়গায় আপনার আসল Key-টি পেস্ট করুন
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AQ.Ab8RN6Lpkc0Y_RLZYCC5Lr7VoElCx2aLy6KuvGFrj0SNkdhH-Q")
CLIENT_SECRET_FILE = BASE_DIR / "client_secret.json"

# Voice Settings (Microsoft Edge Free Neural Voice)
TTS_VOICE = "en-US-ChristopherNeural"

# Video Specifications (Vertical 9:16)
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FPS = 30
