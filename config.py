import os
from pathlib import Path

# Directory Paths
BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
OUTPUT_DIR = BASE_DIR / "output"

ASSETS_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# API Keys and Secrets (Fetched directly from GitHub Secrets)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
YOUTUBE_REFRESH_TOKEN = os.getenv("YOUTUBE_REFRESH_TOKEN", "")
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET", "")

# Voice Settings (Microsoft Edge Free Neural Voice)
TTS_VOICE = "en-US-ChristopherNeural"

# Video Specifications (Vertical 9:16)
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FPS = 30
