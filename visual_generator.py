import requests
import urllib.parse
import traceback
import random
from pathlib import Path
import time
from tenacity import retry, stop_after_attempt, wait_exponential
import config

# Pexels Free API Key. Get one free at https://www.pexels.com/api/
# If it's not set, every scene silently skips straight to the AI fallback.
PEXELS_API_KEY = config.PEXELS_API_KEY

PEXELS_VIDEO_SEARCH_URL = "https://api.pexels.com/videos/search"
PEXELS_PHOTO_SEARCH_URL = "https://api.pexels.com/v1/search"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _download_file(url: str, save_path: Path):
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    with open(save_path, "wb") as f:
        f.write(response.content)


def _fetch_stock_video(search_query: str, save_path: Path) -> bool:
    """Step 1 of the Primary Goal: try a real, free stock VIDEO clip first."""
    if not PEXELS_API_KEY:
        return False

    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": search_query, "orientation": "portrait", "per_page": 5}

    try:
        res = requests.get(PEXELS_VIDEO_SEARCH_URL, headers=headers, params=params, timeout=15)
        if res.status_code != 200:
            print(f"[!] Pexels video search returned status {res.status_code} for '{search_query}'.")
            return False

        data = res.json()
        videos = data.get("videos", [])
        if not videos:
            return False

        # Pick randomly among the top few results instead of always the #1
        # match — otherwise a generic search_query (e.g. "shocked reaction")
        # deterministically returns the SAME video every time it's searched,
        # which is why the same clip/thumbnail kept showing up across
        # different uploads. Some query variety comes from the script
        # prompt, but this is the safety net for when it doesn't.
        video_entry = random.choice(videos[:3]) if len(videos) >= 3 else videos[0]

        # Prefer a moderate-resolution vertical (portrait) mp4 file so we don't
        # download something huge or something in the wrong aspect ratio.
        video_files = video_entry.get("video_files", [])
        candidates = [
            f for f in video_files
            if f.get("file_type") == "video/mp4" and f.get("width", 0) < f.get("height", 1)
        ] or video_files

        if not candidates:
            return False

        candidates.sort(key=lambda f: f.get("width", 0))
        chosen = candidates[len(candidates) // 2]  # middle-of-the-road quality/size

        _download_file(chosen["link"], save_path)
        print(f"[+] Downloaded REAL stock VIDEO for: '{search_query}'")
        return True
    except Exception as e:
        print(f"[!] Pexels video fetch failed for '{search_query}': {e}")
        return False


def _fetch_stock_image(search_query: str, save_path: Path) -> bool:
    """Step 2 of the Primary Goal: no video found, try a real stock PHOTO."""
    if not PEXELS_API_KEY:
        return False

    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": search_query, "orientation": "portrait", "per_page": 5}

    try:
        res = requests.get(PEXELS_PHOTO_SEARCH_URL, headers=headers, params=params, timeout=15)
        if res.status_code != 200:
            print(f"[!] Pexels photo search returned status {res.status_code} for '{search_query}'.")
            return False

        data = res.json()
        photos = data.get("photos", [])
        if not photos:
            return False

        # Same reasoning as the video search above — avoid always fetching
        # the #1 result so generic queries don't produce identical images.
        photo_entry = random.choice(photos[:3]) if len(photos) >= 3 else photos[0]
        photo_url = photo_entry["src"]["portrait"]
        _download_file(photo_url, save_path)
        print(f"[+] Downloaded REAL stock IMAGE for: '{search_query}'")
        return True
    except Exception as e:
        print(f"[!] Pexels photo fetch failed for '{search_query}': {e}")
        return False


def _generate_ai_image(ai_prompt: str, save_path: Path, seed: int) -> bool:
    """Fallback Goal: only reached if BOTH stock searches above found nothing."""
    enhanced_prompt = f"{ai_prompt}, 8k resolution, cinematic lighting, 9:16 vertical"
    encoded_prompt = urllib.parse.quote(enhanced_prompt)
    url = (
        f"https://image.pollinations.ai/prompt/{encoded_prompt}"
        f"?width=1080&height=1920&model=flux&seed={seed}"
    )

    print(f"[+] No stock footage found — generating AI visual for: '{ai_prompt[:60]}...'")
    try:
        _download_file(url, save_path)
        return True
    except Exception as e:
        print(f"[!] AI visual generation failed: {e}")
        traceback.print_exc()
        return False


def generate_visual_assets(visual_scenes: list) -> list:
    """
    Stock-First, AI-Fallback visual pipeline.

    For every scene:
      1. Try a real, free stock VIDEO clip (Pexels Videos API).
      2. If that returns nothing, try a real, free stock PHOTO (Pexels Photos API).
      3. ONLY if both of the above fail/return zero results, fall back to
         generating the visual with AI (Pollinations Flux).

    `visual_scenes` is a list of dicts, each with:
        - "search_query": short broad term(s) for the stock search
        - "ai_prompt":    detailed description for the AI fallback

    For backward compatibility, plain strings are also accepted (treated as
    both the search query and the AI prompt).

    Returns a list of dicts: {"path": Path, "type": "video" | "image"}
    """
    assets = []

    for idx, scene in enumerate(visual_scenes):
        if isinstance(scene, str):
            search_query = scene[:40]
            ai_prompt = scene
        else:
            search_query = scene.get("search_query") or scene.get("ai_prompt", "")[:40]
            ai_prompt = scene.get("ai_prompt") or scene.get("search_query", "")

        if not search_query and not ai_prompt:
            print(f"[!] Scene {idx} has no usable query/prompt, skipping.")
            continue

        video_path = config.OUTPUT_DIR / f"scene_{idx}.mp4"
        image_path = config.OUTPUT_DIR / f"scene_{idx}.jpg"

        # --- PRIMARY GOAL: real internet footage --------------------------
        if search_query and _fetch_stock_video(search_query, video_path):
            assets.append({"path": video_path, "type": "video"})
            continue

        if search_query and _fetch_stock_image(search_query, image_path):
            assets.append({"path": image_path, "type": "image"})
            continue

        # --- FALLBACK GOAL: AI generation (only if stock search failed) ---
        seed = int(time.time()) + idx
        if _generate_ai_image(ai_prompt or search_query, image_path, seed):
            assets.append({"path": image_path, "type": "image"})
        else:
            print(f"[!] Both stock and AI generation failed for scene {idx}, skipping this scene.")

    return assets
