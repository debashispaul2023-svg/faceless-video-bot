import os
from pathlib import Path

# Directory Paths
BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
OUTPUT_DIR = BASE_DIR / "output"
MUSIC_DIR = ASSETS_DIR / "music"
EPISODES_DIR = OUTPUT_DIR / "episodes"
COMPILATIONS_DIR = OUTPUT_DIR / "compilations"
CACHE_DIR = OUTPUT_DIR / "cache"
AUDIO_CACHE_DIR = CACHE_DIR / "audio"
IMAGE_CACHE_DIR = CACHE_DIR / "images"
TEMP_DIR = OUTPUT_DIR / "tmp"

for _d in (
    ASSETS_DIR,
    OUTPUT_DIR,
    MUSIC_DIR,
    EPISODES_DIR,
    COMPILATIONS_DIR,
    CACHE_DIR,
    AUDIO_CACHE_DIR,
    IMAGE_CACHE_DIR,
    TEMP_DIR,
):
    _d.mkdir(exist_ok=True, parents=True)

# API Keys and Secrets (environment only — never hardcode)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
YOUTUBE_REFRESH_TOKEN = os.getenv("YOUTUBE_REFRESH_TOKEN", "")
YOUTUBE_CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET", "")

INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
INSTAGRAM_BUSINESS_ID = os.getenv("INSTAGRAM_BUSINESS_ID", "")

ELEVENLABS_API_KEYS = [
    k.strip() for k in os.getenv("ELEVENLABS_API_KEYS", "").split(",") if k.strip()
]
ELEVENLABS_MODEL_ID = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")

# Visual / story providers (replaceable)
STORY_PROVIDER = os.getenv("STORY_PROVIDER", "gemini")  # gemini | local
VISUAL_PROVIDER = os.getenv("VISUAL_PROVIDER", "pollinations")  # pollinations | gemini | local
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "auto")  # auto | elevenlabs | edge | local
POLLINATIONS_MODEL = os.getenv("POLLINATIONS_MODEL", "flux")

# Voice Settings (Microsoft Edge Free Neural Voice) — legacy shorts default
TTS_VOICE = os.getenv("TTS_VOICE", "en-US-ChristopherNeural")

# Per-character Edge TTS voices (kids-cartoon pipeline)
CHARACTER_EDGE_VOICES = {
    "toto": os.getenv("TTS_VOICE_TOTO", "en-US-GuyNeural"),
    "momo": os.getenv("TTS_VOICE_MOMO", "en-US-JennyNeural"),
    "piku": os.getenv("TTS_VOICE_PIKU", "en-US-AnaNeural"),
    "fiko": os.getenv("TTS_VOICE_FIKO", "en-GB-RyanNeural"),
    "narrator": os.getenv("TTS_VOICE_NARRATOR", "en-US-ChristopherNeural"),
}

# Per-character ElevenLabs voice IDs (override via env)
CHARACTER_ELEVENLABS_VOICES = {
    "toto": os.getenv("ELEVENLABS_VOICE_TOTO", "pNInz6obpgDQGcFmaJgB"),
    "momo": os.getenv("ELEVENLABS_VOICE_MOMO", "EXAVITQu4vr4xnSDxMaL"),
    "piku": os.getenv("ELEVENLABS_VOICE_PIKU", "jBpfuIE2acCO8z3wKNLl"),
    "fiko": os.getenv("ELEVENLABS_VOICE_FIKO", "VR6AewLTigWg4xSOukaG"),
    "narrator": os.getenv("ELEVENLABS_VOICE_NARRATOR", "ErXwobaYiN019PkySvjV"),
}

# Background music mixed under the narration (0.0-1.0, relative volume)
BGM_VOLUME = float(os.getenv("BGM_VOLUME", "0.12"))
DIALOGUE_VOLUME = float(os.getenv("DIALOGUE_VOLUME", "1.0"))
SFX_VOLUME = float(os.getenv("SFX_VOLUME", "0.55"))
AMBIENCE_VOLUME = float(os.getenv("AMBIENCE_VOLUME", "0.18"))

# Legacy Shorts specs (preserved for the old faceless-shorts path)
SHORTS_WIDTH = 1080
SHORTS_HEIGHT = 1920
# Cartoon pipeline default: landscape 16:9
VIDEO_WIDTH = int(os.getenv("VIDEO_WIDTH", "1920"))
VIDEO_HEIGHT = int(os.getenv("VIDEO_HEIGHT", "1080"))
FPS = int(os.getenv("FPS", "30"))

# Cartoon episode defaults
DEFAULT_SCENE_COUNT = 7
MIN_EPISODE_SECONDS = 20.0
TARGET_EPISODE_SECONDS = float(os.getenv("TARGET_EPISODE_SECONDS", "180"))
MAX_EPISODE_SECONDS = float(os.getenv("MAX_EPISODE_SECONDS", "360"))
THUMBNAIL_WIDTH = 1280
THUMBNAIL_HEIGHT = 720

# Rendering
FFMPEG_PRESET = os.getenv("FFMPEG_PRESET", "veryfast")
FFMPEG_CRF = os.getenv("FFMPEG_CRF", "23")
TTS_MAX_RETRIES = int(os.getenv("TTS_MAX_RETRIES", "3"))
VISUAL_MAX_RETRIES = int(os.getenv("VISUAL_MAX_RETRIES", "3"))

# YouTube
YOUTUBE_CATEGORY_FILM_ANIMATION = "1"
YOUTUBE_MADE_FOR_KIDS = os.getenv("YOUTUBE_MADE_FOR_KIDS", "true").lower() == "true"
AUTO_UPLOAD = os.getenv("AUTO_UPLOAD", "false").lower() == "true"

STUDIO_NAME = os.getenv("STUDIO_NAME", "Sunny Grove Tales")
SERIES_NAME = os.getenv("SERIES_NAME", "Sunny Grove Friends")
