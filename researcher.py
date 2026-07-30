import os
import json
from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=8))
def _call_gemini(client, prompt):
    return client.models.generate_content(
        model='gemini-3.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            http_options=types.HttpOptions(timeout=30000)  # 30 second timeout
        )
    )

def generate_script(topic_type="facts"):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[!] GEMINI_API_KEY missing. Using fallback script.")
        return get_fallback_script()

    try:
        client = genai.Client(api_key=api_key)

        prompt = (
            "Generate a 30-second viral YouTube Short script about an astounding, lesser-known fact. "
            "Return ONLY a valid JSON object with the following keys: "
            "'title' (string), 'description' (string), 'script' (string, full narration), "
            "'hashtags' (list of strings), 'visual_prompts' (list of 2 short visual descriptions for AI image generation)."
        )

        response = _call_gemini(client, prompt)

        raw_text = response.text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:-3].strip()
        elif raw_text.startswith("```"):
            raw_text = raw_text[3:-3].strip()

        return json.loads(raw_text)
    except Exception as e:
        print(f"[!] Error in script generation: {e}")
        return get_fallback_script()

def discover_trending_topic():
    return generate_script()

def get_fallback_script():
    return {
        "title": "Mind-Blowing Space Fact You Didn't Know!",
        "description": "Discover incredible facts about planet Jupiter! #shorts #facts",
        "script": "Did you know that Jupiter is so huge that all other planets in our solar system could fit inside it? It has a storm called the Great Red Spot that is larger than Earth itself!",
        "hashtags": ["shorts", "facts", "space", "science"],
        "visual_prompts": ["giant planet jupiter in outer space", "close up of jupiter red spot storm"]
    }
