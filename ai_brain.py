import os
import json
import traceback
from google import genai
from google.genai import types

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

        prompt = """
        You are an elite USA Personal Finance YouTube Shorts architect.
        Generate a complete video blueprint in strict JSON format.

        Channel Niche: USA Personal Finance (Credit cards, High-yield savings, Tax hacks, Passive income, Wealth building).
        Target Audience: USA (High CPM/RPM focused).

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

        Return ONLY a valid JSON object matching the exact schema above.
        """

        # We will use gemini-3.5-flash
        response = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=prompt
        )

        raw_text = response.text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:-3].strip()
        elif raw_text.startswith("```"):
            raw_text = raw_text[3:-3].strip()

        blueprint = json.loads(raw_text)
        return blueprint

    except Exception as e:
        print(f"[!] Error in AI Brain generation: {e}")
        traceback.print_exc()
        raise e
