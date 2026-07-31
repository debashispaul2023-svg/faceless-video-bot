import os
from pathlib import Path

# Directory Paths
BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
OUTPUT_DIR = BASE_DIR / "output"
MUSIC_DIR = ASSETS_DIR / "music"

ASSETS_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
MUSIC_DIR.mkdir(exist_ok=True, parents=True)

# API Keys and Secrets (Fetched directly from GitHub Secrets)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
YOUTUBE_REFRESH_TOKEN = os.getenv("YOUTUBE_REFRESH_TOKEN", "")
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET", "")

# Instagram Reels publishing (Meta Graph API) — see instagram_uploader.py
# docstring for the one-time setup this requires.
INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
INSTAGRAM_BUSINESS_ID = os.getenv("INSTAGRAM_BUSINESS_ID", "")

# Voice Settings (Microsoft Edge Free Neural Voice)
TTS_VOICE = "en-US-ChristopherNeural"

# Background music mixed under the narration (0.0-1.0, relative volume)
BGM_VOLUME = 0.12

# Video Specifications (Vertical 9:16)
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FPS = 30
