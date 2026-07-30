import os
import json
import google.generativeai as genai

# Configure Gemini API
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

def generate_viral_script(topic: str) -> dict:
    model = genai.GenerativeModel('gemini-3.5-flash')

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

    try:
        response = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"[!] Error in script generation: {e}")
        return {
            "title": "Mind-Blowing History Fact #Shorts",
            "description": "Unbelievable history fact you never knew!",
            "hashtags": ["#history", "#facts", "#shorts"],
            "narration_text": "Did you know that history is full of unbelievable secrets? Here is one fact that will blow your mind.",
            "visual_scenes": [
                "Photorealistic historical cinematic shot of ancient ruins",
                "Cinematic portrait of an ancient historical ruler"
            ]
        }
