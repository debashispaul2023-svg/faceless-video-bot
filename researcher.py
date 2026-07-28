import os
import google.generativeai as genai

# Configure Gemini API
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

def discover_trending_topic() -> str:
    """Generates a high-retention trending history/fact topic."""
    try:
        model = genai.GenerativeModel("gemini-2.5-flash")
        prompt = (
            "Give me one single specific, fascinating, and lesser-known historical fact "
            "or real-world scientific discovery for a YouTube Short. Output ONLY the topic title in 1 short sentence."
        )
        response = model.generate_content(prompt)
        topic = response.text.strip()
        print(f"[+] Discovered Trending Topic: {topic}")
        return topic
    except Exception as e:
        print(f"[!] Topic discovery failed ({e}), using default fallback.")
        return "The Untold Story of Ancient Pyramid Engineering"

