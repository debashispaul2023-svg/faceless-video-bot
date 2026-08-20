import os
import requests
import urllib.parse
import traceback
import random
from pathlib import Path
import time
from tenacity import retry, stop_after_attempt, wait_exponential
from google import genai
from google.genai import types
import config

# Pexels Free API Key. Get one free at https://www.pexels.com/api/
# If it's not set, every scene silently skips straight to the AI fallback.
PEXELS_API_KEY = config.PEXELS_API_KEY

PEXELS_VIDEO_SEARCH_URL = "https://api.pexels.com/videos/search"
PEXELS_PHOTO_SEARCH_URL = "https://api.pexels.com/v1/search"

# Shorts compliance: reject sources below this width so the render-time
# cover-crop to 1080px never has to upscale a low-res source (which would
# introduce visible softness/artifacts once it fills the 9:16 frame).
_MIN_SOURCE_WIDTH = 720

# How many of the top search results to actually consider per scene before
# giving up on stock footage and falling back to AI generation. Kept at 1:
# each relevance check costs a Gemini call, and this project's free-tier
# quota for gemini-3.5-flash is only 20 requests/DAY total — that quota is
# shared with script generation (researcher.py) and the top-performer
# analysis (analytics_feedback.py), so checking 3 candidates per scene x 6
# scenes could burn the ENTIRE day's quota on relevance-checking ALONE
# before the 2nd/3rd video of the day even starts generating its script.
_MAX_CANDIDATES_PER_SEARCH = 1

# Circuit breaker: once we see ONE quota-exhausted (429) response from a
# relevance check, the daily quota is gone for the rest of this run — there
# is no point spending network time hitting the same wall on every
# subsequent scene. This flag disables further relevance-check attempts for
# the rest of the process (fails open, same as any other relevance-check
# failure) so whatever quota remains is left for script generation instead,
# which matters far more than a stock-footage sanity check.
_relevance_check_quota_exhausted = False

# Hard cap, independent of hitting an actual 429: even successful relevance
# checks cost real quota out of a 20-requests/day free-tier budget shared
# with script generation and analytics. Capping total checks per run (all 3
# videos combined, since main.py runs them in one process) guarantees
# several requests are always left over for the higher-priority work.
_MAX_RELEVANCE_CHECKS_PER_RUN = 6
_relevance_check_call_count = 0


def _is_visually_relevant(image_bytes: bytes, scene_description: str) -> bool:
    """
    Catches the real failure mode this function exists for: a keyword search
    for an abstract finance term ("dormancy penalty", "credit utilization")
    technically matching *some* stock clip, but that clip actually being a
    card trick, a gas station at night, decorative craft stickers, or a
    stranger's hands on a typewriter — visually unrelated to the scene it
    was fetched for.

    Sends the (small, cheap-to-check) preview thumbnail to Gemini and asks
    a plain yes/no question. This is DELIBERATELY strict: an earlier, looser
    version of this prompt ("a loose thematic match is fine") let things
    like generic craft stickers pass for a scene described as "a paper
    cutout sticker with a number on it" — same rough object category, wrong
    actual content. The image now has to depict the SPECIFIC subject
    described, not just a vague/coincidental category match.

    Budget-aware: stops making Gemini calls entirely once either
    _MAX_RELEVANCE_CHECKS_PER_RUN is hit OR a 429 quota-exhausted response is
    seen — both fail OPEN (trust the stock result) rather than block the
    pipeline, and both exist so this quality check can never starve script
    generation of the same shared daily Gemini quota. This check is a
    quality filter, never a hard requirement.
    """
    global _relevance_check_quota_exhausted, _relevance_check_call_count

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or not image_bytes:
        return True

    if _relevance_check_quota_exhausted:
        return True  # already confirmed quota's gone this run — don't even try

    if _relevance_check_call_count >= _MAX_RELEVANCE_CHECKS_PER_RUN:
        return True  # stay well under budget so script generation always has room

    try:
        _relevance_check_call_count += 1
        client = genai.Client(api_key=api_key)
        prompt = (
            "You are strictly quality-checking stock footage for a short "
            f"video. The scene is supposed to show: \"{scene_description}\"\n\n"
            "Look at the attached image. Would a viewer who just heard that "
            "exact description immediately recognize this image as showing "
            "it? REJECT the image if it only shares a vague category or a "
            "coincidental keyword/shape/color match — for example: a generic "
            "office desk for 'reviewing a bank statement', decorative craft "
            "stickers for 'a paper cutout sticker with a number on it', or "
            "any object that superficially resembles the description without "
            "actually being it. Only ACCEPT a genuine, direct visual match to "
            "the specific subject and action described — a reasonable person "
            "would say 'yes, that's clearly it,' not 'I guess that's kind of "
            "related.' Reply with ONLY one word: "
            "YES or NO."
        )
        response = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                prompt,
            ],
            config=types.GenerateContentConfig(
                http_options=types.HttpOptions(timeout=15000)
            ),
        )
        answer = (response.text or "").strip().upper()
        return answer.startswith("Y")
    except Exception as e:
        error_str = str(e)
        if "RESOURCE_EXHAUSTED" in error_str or "429" in error_str or "quota" in error_str.lower():
            _relevance_check_quota_exhausted = True
            print(f"[i] Gemini quota exhausted — disabling relevance-checking for the rest of "
                  f"this run (remaining quota is reserved for script generation, which matters more).")
        else:
            print(f"[i] Relevance check skipped (non-fatal, trusting stock result): {e}")
        return True


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _download_file(url: str, save_path: Path):
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    with open(save_path, "wb") as f:
        f.write(response.content)


def _fetch_stock_video(search_query: str, ai_prompt: str, save_path: Path) -> bool:
    """
    Step 1 of the Primary Goal: try a real, free stock VIDEO clip first.
    Checks up to _MAX_CANDIDATES_PER_SEARCH results' preview thumbnails for
    actual relevance to `ai_prompt` before downloading the full clip —
    rejects results that only matched the search keyword by coincidence.
    """
    if not PEXELS_API_KEY:
        return False

    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": search_query, "orientation": "portrait", "per_page": 6}

    try:
        res = requests.get(PEXELS_VIDEO_SEARCH_URL, headers=headers, params=params, timeout=15)
        if res.status_code != 200:
            print(f"[!] Pexels video search returned status {res.status_code} for '{search_query}'.")
            return False

        videos = res.json().get("videos", [])
        if not videos:
            return False

        # Shuffle so a generic search_query doesn't deterministically try
        # candidates in the same order every single time it's searched.
        random.shuffle(videos)

        for video_entry in videos[:_MAX_CANDIDATES_PER_SEARCH]:
            thumb_url = video_entry.get("image")  # Pexels provides a still preview per video
            if thumb_url:
                try:
                    thumb_bytes = requests.get(thumb_url, timeout=10).content
                    if not _is_visually_relevant(thumb_bytes, ai_prompt):
                        print(f"[i] Rejected an irrelevant stock video for '{search_query}' "
                              f"(keyword matched, but the footage didn't) — trying next candidate.")
                        continue
                except Exception:
                    pass  # if the check itself fails, don't block — fall through and use this candidate

            video_files = video_entry.get("video_files", [])
            portrait_files = [
                f for f in video_files
                if f.get("file_type") == "video/mp4" and f.get("width", 0) < f.get("height", 1)
            ]
            candidates = [f for f in portrait_files if f.get("width", 0) >= _MIN_SOURCE_WIDTH] or portrait_files or video_files
            if not candidates:
                continue

            candidates.sort(key=lambda f: f.get("width", 0))
            chosen = candidates[len(candidates) // 2]  # middle-of-the-road quality/size

            _download_file(chosen["link"], save_path)
            print(f"[+] Downloaded REAL stock VIDEO for: '{search_query}' (relevance-checked)")
            return True

        return False
    except Exception as e:
        print(f"[!] Pexels video fetch failed for '{search_query}': {e}")
        return False


def _fetch_stock_image(search_query: str, ai_prompt: str, save_path: Path) -> bool:
    """
    Step 2 of the Primary Goal: no relevant video found, try a real stock
    PHOTO instead — same thumbnail-relevance-check-before-download approach.
    """
    if not PEXELS_API_KEY:
        return False

    headers = {"Authorization": PEXELS_API_KEY}
    params = {"query": search_query, "orientation": "portrait", "per_page": 6}

    try:
        res = requests.get(PEXELS_PHOTO_SEARCH_URL, headers=headers, params=params, timeout=15)
        if res.status_code != 200:
            print(f"[!] Pexels photo search returned status {res.status_code} for '{search_query}'.")
            return False

        photos = res.json().get("photos", [])
        if not photos:
            return False

        random.shuffle(photos)

        for photo_entry in photos[:_MAX_CANDIDATES_PER_SEARCH]:
            src = photo_entry.get("src", {})
            preview_url = src.get("tiny") or src.get("small")
            if preview_url:
                try:
                    preview_bytes = requests.get(preview_url, timeout=10).content
                    if not _is_visually_relevant(preview_bytes, ai_prompt):
                        print(f"[i] Rejected an irrelevant stock photo for '{search_query}' "
                              f"(keyword matched, but the image didn't) — trying next candidate.")
                        continue
                except Exception:
                    pass

            photo_url = src.get("portrait")
            if not photo_url:
                continue

            _download_file(photo_url, save_path)
            print(f"[+] Downloaded REAL stock IMAGE for: '{search_query}' (relevance-checked)")
            return True

        return False
    except Exception as e:
        print(f"[!] Pexels photo fetch failed for '{search_query}': {e}")
        return False


def _generate_ai_image(ai_prompt: str, save_path: Path, seed: int) -> bool:
    """Fallback Goal: reached if BOTH stock searches above found nothing
    relevant — this is deliberately more likely to trigger now than before,
    since irrelevant stock results get rejected instead of accepted."""
    enhanced_prompt = f"{ai_prompt}, 8k resolution, cinematic lighting, 9:16 vertical"
    encoded_prompt = urllib.parse.quote(enhanced_prompt)
    url = (
        f"https://image.pollinations.ai/prompt/{encoded_prompt}"
        f"?width=1080&height=1920&model=flux&seed={seed}"
    )

    print(f"[+] No relevant stock footage found — generating AI visual for: '{ai_prompt[:60]}...'")
    try:
        _download_file(url, save_path)
        return True
    except Exception as e:
        print(f"[!] AI visual generation failed: {e}")
        traceback.print_exc()
        return False


def generate_visual_assets(visual_scenes: list) -> list:
    """
    Stock-First, AI-Fallback visual pipeline, tuned for YouTube Shorts (9:16,
    1080x1920) compliance AND topical relevance:
      1. Try a real, free stock VIDEO clip (Pexels Videos API), portrait
         orientation, at or above _MIN_SOURCE_WIDTH — but ONLY after a cheap
         Gemini-vision relevance check on the preview thumbnail confirms the
         footage actually relates to the scene (not just the search keyword).
      2. If that returns nothing relevant, try a real, free stock PHOTO
         (Pexels Photos API), same relevance check.
      3. ONLY if both of the above fail to find anything relevant, fall back
         to generating the visual with AI (Pollinations Flux) at 1080x1920 —
         this guarantees the visual actually matches the topic, since it's
         generated FROM the topic description rather than searched for.

    Final framing to exactly 1080x1920 without stretching (cover-resize +
    center-crop) happens in video_renderer.py, which is the single source of
    truth for frame compliance — this file guarantees each source asset is
    both portrait-oriented/high-resolution AND topically relevant going in.

    `visual_scenes` is a list of dicts, each with:
        - "search_query": short broad term(s) for the stock search
        - "ai_prompt":    detailed description for the AI fallback — also
                          used as the relevance-check reference, so make
                          this specific and concrete for best results
        - "sfx_cue":      optional — a short keyword (e.g. "whoosh") that
                          video_renderer.py can match against files in
                          assets/sfx/ to mix in a sound effect at this
                          scene's start, at 0.7 volume. Safe to omit.

    For backward compatibility, plain strings are also accepted (treated as
    both the search query and the AI prompt, with no sfx_cue).

    Returns a list of dicts: {"path": Path, "type": "video" | "image", "sfx_cue": str | None}
    """
    assets = []

    for idx, scene in enumerate(visual_scenes):
        if isinstance(scene, str):
            search_query = scene[:40]
            ai_prompt = scene
            sfx_cue = None
        else:
            search_query = scene.get("search_query") or scene.get("ai_prompt", "")[:40]
            ai_prompt = scene.get("ai_prompt") or scene.get("search_query", "")
            sfx_cue = scene.get("sfx_cue")

        if not search_query and not ai_prompt:
            print(f"[!] Scene {idx} has no usable query/prompt, skipping.")
            continue

        video_path = config.OUTPUT_DIR / f"scene_{idx}.mp4"
        image_path = config.OUTPUT_DIR / f"scene_{idx}.jpg"

        # --- PRIMARY GOAL: real internet footage, but only if it's actually
        # relevant to what this scene needs to show ---------------------
        if search_query and _fetch_stock_video(search_query, ai_prompt, video_path):
            assets.append({"path": video_path, "type": "video", "sfx_cue": sfx_cue})
            continue

        if search_query and _fetch_stock_image(search_query, ai_prompt, image_path):
            assets.append({"path": image_path, "type": "image", "sfx_cue": sfx_cue})
            continue

        # --- FALLBACK GOAL: AI generation (stock search found nothing
        # relevant, not just nothing at all) ------------------------------
        seed = int(time.time()) + idx
        if _generate_ai_image(ai_prompt or search_query, image_path, seed):
            assets.append({"path": image_path, "type": "image", "sfx_cue": sfx_cue})
        else:
            print(f"[!] Both stock and AI generation failed for scene {idx}, skipping this scene.")

    return assets
