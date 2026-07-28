import json
from google import genai
from google.genai import types
import config

def generate_viral_script(topic: str) -> dict:
    """Generates high-retention script with pattern interrupts and image prompts."""
    print(f"[+] Generating script and visual prompts for: {topic}")
    
    client = genai.Client(api_key=config.GEMINI_API_KEY)
    
        prompt = f"""
    You are an expert YouTube Shorts creator specializing in "Mind-Blowing History & Real World Facts".
    Write a high-retention 30-45 second script on the topic: "{topic}".

    CRITICAL REQUIREMENTS:
    1. Focus on REAL-WORLD DATA, historical accuracy, verified surprising facts, and timeline realism.
    2. Start with a 0-3 second Visual & Verbal Pattern Interrupt (e.g., "History lied to you about...", "Scientists just discovered something terrifying about...").
    3. Keep narration text purely spoken without stage markers.
    4. Generate 4-5 dynamic visual prompt descriptions specifying real-world scenes, historical figures, or artifact visuals.

    Output MUST be strictly valid JSON matching this structure:
    {{
      "title": "Historical Fact Short Title #Shorts",
      "description": "Engaging real-world history description with hashtags.",
      "hashtags": ["#historyfacts", "#mindblowingfacts", "#shorts", "#realhistory"],
      "narration_text": "Spoken narration text here...",
      "visual_scenes": [
        "Photorealistic historical painting of Ancient Rome...",
        "Real world satellite view of Earth or ancient artifact...",
        "Dramatic museum style exhibit shot of..."
      ]
    }}
    """

    
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json"
        )
    )
    
    data = json.loads(response.text)
    print("[+] Script, metadata, and visual prompts generated successfully.")
    return data
