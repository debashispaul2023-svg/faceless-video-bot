from google import genai
from google.genai import types
import config

def discover_trending_topic() -> str:
    """Uses Gemini 2.5 Flash with Search Grounding to find viral US topics in real-time."""
    print("[+] Conducting real-time trend research via Gemini Search Grounding...")
    
    client = genai.Client(api_key=config.GEMINI_API_KEY)
    
    prompt = (
        "Search for currently viral, highly engaging, or surprising topics in the US right now "
        "(focus on tech innovations, psychological facts, dark history, or high-stakes science). "
        "Select ONE topic that works best for a 30-45 second faceless YouTube Short. "
        "Return ONLY the topic title, nothing else."
    )

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[{"google_search": {}}]  # Enable Google Search Grounding
            )
        )
        topic = response.text.strip().replace('"', '')
        print(f"[+] Discovered Trending Topic: {topic}")
        return topic
    except Exception as e:
        print(f"[!] Grounding Search failed ({e}). Falling back to niche evergreen prompt.")
        return "Surprising Psychological Tricks People Use Daily Without Realizing It"
