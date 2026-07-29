import os
import json
import google.generativeai as genai

def generate_script(topic_type="facts"):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[!] GEMINI_API_KEY missing. Using fallback script.")
        return get_fallback_script()

    genai.configure(api_key=api_key)
    
    prompt = (
        "Generate a 30-second viral YouTube Short script about an astounding, lesser-known fact. "
        "Return ONLY a valid JSON object with the following keys: "
        "'title' (string), 'description' (string), 'script' (string, full narration), "
        "'hashtags' (list of strings), 'visual_prompts' (list of 2 short visual descriptions for AI image generation)."
    )

    try:
        # Using current supported model
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(prompt)
        
        # Clean response if wrapped in markdown codeblock
        raw_text = response.text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:-3].strip()
        elif raw_text.startswith("```"):
            raw_text = raw_text[3:-3].strip()
            
        return json.loads(raw_text)
    except Exception as e:
        print(f"[!] Error in script generation: {e}")
        return get_fallback_script()

def get_fallback_script():
    return {
        "title": "Mind-Blowing Space Fact You Didn't Know!",
        "description": "Discover incredible facts about planet Jupiter! #shorts #facts",
        "script": "Did you know that Jupiter is so huge that all other planets in our solar system could fit inside it? It has a storm called the Great Red Spot that is larger than Earth itself!",
        "hashtags": ["shorts", "facts", "space", "science"],
        "visual_prompts": ["giant planet jupiter in outer space", "close up of jupiter red spot storm"]
    }
