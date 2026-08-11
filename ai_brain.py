import json
import os
import random
import re
import traceback
from pathlib import Path

from google import genai
from google.genai import types

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
CATEGORY_SCORES_FILE = ASSETS_DIR / "category_scores.json"
USED_TOPICS_FILE = ASSETS_DIR / "used_topics.json"

PERSONAL_FINANCE_CATEGORIES = [
    "budgeting and saving",
    "investing and wealth building",
    "debt management",
    "credit score hacks",
    "passive income",
]

_CATEGORY_SCORE_ALIASES = {
    "budgeting and saving": ["budgeting and saving", "budgeting and saving money"],
    "investing and wealth building": ["investing and wealth building"],
    "debt management": ["debt management", "debt management and credit scores"],
    "credit score hacks": ["credit score hacks", "debt management and credit scores"],
    "passive income": ["passive income", "side hustles and making money online"],
}


def _read_json(path: Path, default):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"[i] Could not read {path}: {e}; using defaults.")
        return default


def _choose_weighted_category() -> str:
    scores = _read_json(CATEGORY_SCORES_FILE, {})
    weights = []
    for category in PERSONAL_FINANCE_CATEGORIES:
        score = max(float(scores.get(alias, 0) or 0) for alias in _CATEGORY_SCORE_ALIASES[category])
        weights.append(max(score, 0.1))
    return random.choices(PERSONAL_FINANCE_CATEGORIES, weights=weights, k=1)[0]


def _read_used_topics() -> list[str]:
    data = _read_json(USED_TOPICS_FILE, {"titles": []})
    if isinstance(data, list):
        raw_topics = data
    elif isinstance(data, dict):
        raw_topics = data.get("titles") or data.get("topics") or []
    else:
        raw_topics = []
    return [str(topic).strip() for topic in raw_topics if str(topic).strip()]


def _normalize_topic(topic: str) -> str:
    topic = re.sub(r"\s+", " ", topic or "").strip().casefold()
    return re.sub(r"[^a-z0-9 ]+", "", topic)


def _is_used_topic(topic: str, used_topics: list[str]) -> bool:
    normalized_topic = _normalize_topic(topic)
    return bool(normalized_topic) and normalized_topic in {
        _normalize_topic(used_topic) for used_topic in used_topics
    }


def _write_used_topic(topic: str):
    if not topic:
        return
    topics = _read_used_topics()
    if not _is_used_topic(topic, topics):
        topics.append(topic.strip())
    USED_TOPICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USED_TOPICS_FILE.write_text(json.dumps({"titles": topics[-500:]}, indent=2))


def _sentence_segments(script: str) -> list[str]:
    script_without_sfx = re.sub(r"\[SFX:\s*[^\]]+\]", "", script or "")
    return [s.strip() for s in re.split(r"(?<=[.!?…])\s+", script_without_sfx) if s.strip()]


def _validate_scene_count(blueprint: dict):
    segments = _sentence_segments(blueprint.get("script", ""))
    scenes = blueprint.get("visual_scenes", [])
    if len(scenes) != len(segments):
        raise ValueError(
            "AI blueprint visual_scenes count must match script sentence segments "
            f"({len(scenes)} scenes != {len(segments)} segments)."
        )


def generate_video_blueprint() -> dict:
    """
    Acts as the central orchestrator 'brain' for the video generation.
    Generates a complete video blueprint in strict JSON format.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is missing/empty in the environment.")

    category = _choose_weighted_category()
    used_topics = _read_used_topics()
    recent_topics = used_topics[-100:]
    avoid_topics = "\n".join(f"- {topic}" for topic in recent_topics) or "- None yet"

    try:
        client = genai.Client(api_key=api_key)

        prompt = f"""
        You are an elite USA Personal Finance YouTube Shorts architect.
        Generate a complete video blueprint in strict JSON format.

        Channel Niche: USA Personal Finance.
        Selected sub-category: {category}
        Target Audience: USA (High CPM/RPM focused).

        Already used topics/titles to avoid repeating:
        {avoid_topics}

        STRICT Output JSON Schema:
        {{
          "category": "{category}",
          "metadata": {{
            "title": "High CTR Title with Emojis (under 50 chars)",
            "description": "Engaging description with USA finance keywords & hashtags",
            "tags": ["personal finance", "{category}", "usa money tips", "shorts"]
          }},
          "audio_direction": {{
            "bg_music_genre": "suspenseful_lofi",
            "voice_pacing": "natural_fast"
          }},
          "script": "Full voiceover text with explicit punctuation (commas, em-dashes —, ellipses...) for natural ElevenLabs pauses. Include [SFX: <name>] cues in the script where appropriate (e.g. 'This is crazy [SFX: whoosh]...').",
          "visual_scenes": [
            {{
              "scene_id": 1,
              "text_segment": "Must exactly match one complete script sentence segment...",
              "broll_search_query": "specific context-aware Pexels query",
              "ai_fallback_prompt": "8k photorealistic AI image prompt if stock fetch fails",
              "sfx_cue": "whoosh / cash_register / pop / none"
            }}
          ]
        }}

        CRITICAL STRUCTURE RULE: Split the script into sentence segments using terminal
        punctuation (. ! ? …). The visual_scenes array count MUST STRICTLY MATCH the
        number of script sentence segments, one visual scene per sentence segment, in order.

        COMPLIANCE (strict, this is financial content): NEVER recommend a specific stock,
        crypto coin, or investment product; NEVER promise or imply a guaranteed return or a
        specific dollar/wealth outcome (e.g. 'you will become a millionaire', 'guaranteed
        profit'); keep everything general and educational, framed as 'here's how X works',
        never 'here's what to buy'. STRICTLY avoid clickbait titles and hooks; make bold but
        substantive and accurate claims. DO NOT generate repetitious content; each script must
        have a unique angle and must not duplicate any used topic/title listed above. DO NOT
        output SSML `<break>` tags in the script; enforce natural speech pacing using ONLY
        proper punctuation (dashes, ellipses, commas).

        Return ONLY a valid JSON object matching the exact schema above.
        """

        last_error = None
        for attempt in range(1, 4):
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    http_options=types.HttpOptions(timeout=60.0)
                )
            )

            try:
                raw_text = response.text.strip()
                clean_json = re.sub(
                    r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.MULTILINE
                ).strip()

                blueprint = json.loads(clean_json)
                blueprint["category"] = blueprint.get("category") or category
                title = blueprint.get("metadata", {}).get("title", "")
                if _is_used_topic(title, used_topics):
                    raise ValueError(f"AI generated a duplicate used topic/title: {title}")
                _validate_scene_count(blueprint)
                _write_used_topic(title)
                return blueprint
            except Exception as validation_error:
                last_error = validation_error
                if attempt == 3:
                    raise
                print(
                    "[i] AI blueprint failed validation "
                    f"(attempt {attempt}/3): {validation_error}; retrying."
                )

        raise RuntimeError(f"AI Brain failed after retries: {last_error}")

    except Exception as e:
        print(f"[!] Error in AI Brain generation: {e}")
        traceback.print_exc()
        raise e
