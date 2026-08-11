import os
import json
import traceback
import random
from pathlib import Path
from google import genai
from google.genai import types

_TOPIC_CATEGORIES = [
    "budgeting and saving",
    "investing and wealth building",
    "debt management",
    "credit score hacks",
    "passive income"
]

def _get_category():
    try:
        scores_file = Path("assets/category_scores.json")
        if scores_file.exists():
            scores = json.loads(scores_file.read_text())
            weights = [max(scores.get(cat, 1.0), 0.1) for cat in _TOPIC_CATEGORIES]
            return random.choices(_TOPIC_CATEGORIES, weights=weights, k=1)[0]
    except Exception:
        pass
    return random.choice(_TOPIC_CATEGORIES)

def _get_avoid_clause():
    try:
        history_file = Path("assets/used_topics.json")
        if history_file.exists():
            data = json.loads(history_file.read_text())
            titles = data.get("titles", [])
            if titles:
                recent = ", ".join(titles[-20:])
                return f" Do NOT repeat or closely resemble any of these previously covered titles/topics: {recent}."
    except Exception:
        pass
    return ""

def _record_title(title):
    try:
        history_file = Path("assets/used_topics.json")
        history_file.parent.mkdir(parents=True, exist_ok=True)

        titles = []
        if history_file.exists():
            data = json.loads(history_file.read_text())
            titles = data.get("titles", [])

        titles.append(title)
        titles = titles[-30:] # keep bounded

        history_file.write_text(json.dumps({"titles": titles}, indent=2))
    except Exception:
        pass


def generate_video_blueprint() -> dict:
    """
    Acts as the central orchestrator 'brain' for the video generation.
    Generates a complete video blueprint in strict JSON format.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is missing/empty in the environment.")

    try:
        client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=180.0))

        category = _get_category()
        avoid_clause = _get_avoid_clause()

        prompt = f"""
        You are an elite USA Personal Finance YouTube Shorts architect.
        Generate a complete video blueprint in strict JSON format.

        Channel Niche: USA Personal Finance.
        Focus specifically on this sub-category: {category}.
        Target Audience: USA (High CPM/RPM focused).{avoid_clause}\n

        STRICT Output JSON Schema:
        {
          "metadata": {
            "title": "High CTR Title with Emojis (under 50 chars)",
            "description": "Engaging description with USA finance keywords & hashtags",
            "tags": ["personal finance", "credit card hacks", "usa money tips", "shorts"]
          },
          "audio_direction": {
            "bg_music_genre": "suspenseful_lofi",
            "voice_pacing": "natural_fast"
          },
          "script": "Full voiceover text with explicit punctuation (commas, em-dashes —, ellipses...) for natural ElevenLabs pauses. Include [SFX: <name>] cues in the script where appropriate (e.g. 'This is crazy [SFX: whoosh]...').",
          "visual_scenes": [
            {
              "scene_id": 1,
              "text_segment": "First 3-second viral hook...",
              "broll_search_query": "specific context-aware Pexels query",
              "ai_fallback_prompt": "8k photorealistic AI image prompt if stock fetch fails",
              "sfx_cue": "whoosh / cash_register / pop / none"
            }
          ]
        }

        COMPLIANCE (strict, this is financial content): NEVER recommend a specific stock,
        crypto coin, or investment product; NEVER promise or imply a guaranteed return or a
        specific dollar/wealth outcome (e.g. 'you will become a millionaire', 'guaranteed
        profit'); keep everything general and educational, framed as 'here's how X works',
        never 'here's what to buy'. STRICTLY avoid clickbait titles and hooks; make bold but
        substantive and accurate claims. DO NOT generate repetitious content; each script must
        have a unique angle even within the same category. DO NOT output SSML `<break>` tags in the script;
        enforce natural speech pacing using ONLY proper punctuation (dashes, ellipses, commas).
        CRITICAL: The number of items in the `visual_scenes` array MUST STRICTLY MATCH the number of distinct sentence segments/beats in the `script`. Do not provide fewer visual scenes than segments.

        Return ONLY a valid JSON object matching the exact schema above.
        """

        # We will use gemini-3.5-flash
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )

        raw_text = response.text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:-3].strip()
        elif raw_text.startswith("```"):
            raw_text = raw_text[3:-3].strip()

        blueprint = json.loads(raw_text)
        if "metadata" in blueprint and "title" in blueprint["metadata"]:
            _record_title(blueprint["metadata"]["title"])
        return blueprint

    except Exception as e:
        print(f"[!] Error in AI Brain generation: {e}")
        traceback.print_exc()
        raise e
