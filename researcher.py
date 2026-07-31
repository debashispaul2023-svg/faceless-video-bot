import os
import json
import random
import traceback
from pathlib import Path
from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

# Where we remember which titles we've already used, so we stop repeating
# ourselves both within a single run (3 shorts) and across days.
_HISTORY_FILE = Path(__file__).resolve().parent / "assets" / "used_topics.json"

_TOPIC_CATEGORIES = [
    "outer space and astronomy",
    "universe mysteries and astrophysics",
    "fascinating science facts and physics",
    "quantum mechanics and cosmic phenomena",
    "deep space discoveries and planetary science"
]
# A pool of varied fallback scripts (used only if Gemini is unreachable),
# so a bad API key doesn't make every single video identical.
# Each scene now carries BOTH a `search_query` (1-2 broad words, for the
# free stock API) and an `ai_prompt` (detailed description, used only if
# the stock search comes up empty).
_FALLBACK_SCRIPTS = [
    {
        "title": "Mind-Blowing Space Fact You Didn't Know!",
        "category": "outer space and astronomy",
        "description": "Discover incredible facts about planet Jupiter! #shorts #facts",
        "script": "You've been lied to about Jupiter. This planet is so massive, every other planet in our solar system could fit inside it. It has a storm called the Great Red Spot, and that storm alone is bigger than Earth. It's been raging for over 350 years straight. Scientists still don't fully know why it hasn't stopped. Space is way weirder than we think.",
        "hashtags": ["shorts", "facts", "space", "science"],
        "visual_scenes": [
            {"search_query": "jupiter planet", "ai_prompt": "giant planet jupiter in outer space, photorealistic, 9:16 vertical"},
            {"search_query": "space storm", "ai_prompt": "close up of jupiter's great red spot storm, photorealistic, 9:16 vertical"},
            {"search_query": "solar system", "ai_prompt": "scale comparison of planets in the solar system, photorealistic, 9:16 vertical"},
            {"search_query": "night sky stars", "ai_prompt": "dramatic starfield in deep space, photorealistic, 9:16 vertical"},
            {"search_query": "telescope observatory", "ai_prompt": "astronomer looking through a telescope at night, cinematic, 9:16 vertical"},
        ],
    },
    {
        "title": "This Ocean Fact Will Terrify You!",
        "category": "deep ocean and marine life",
        "description": "We know more about the moon than our own oceans. #shorts #facts",
        "script": "We've mapped the surface of Mars better than our own ocean floor. Humans have explored less than 5 percent of Earth's oceans. That means 95 percent of our own planet is still a total mystery. Down there, in the crushing dark, are creatures no human has ever seen alive. It's terrifying how little we actually know about our own world.",
        "hashtags": ["shorts", "facts", "ocean", "science"],
        "visual_scenes": [
            {"search_query": "deep ocean", "ai_prompt": "deep dark ocean trench, photorealistic, 9:16 vertical"},
            {"search_query": "deep sea creature", "ai_prompt": "mysterious bioluminescent deep sea creature, photorealistic, 9:16 vertical"},
            {"search_query": "ocean waves", "ai_prompt": "powerful ocean waves from above, cinematic, 9:16 vertical"},
            {"search_query": "submarine underwater", "ai_prompt": "submersible exploring the deep ocean, photorealistic, 9:16 vertical"},
            {"search_query": "coral reef", "ai_prompt": "colorful coral reef teeming with life, photorealistic, 9:16 vertical"},
        ],
    },
    {
        "title": "Your Brain Does THIS Every Night!",
        "category": "the human body and brain",
        "description": "The human brain is stranger than you think. #shorts #facts",
        "script": "Your brain is quietly shrinking, right now, every single day. It only fully resets while you sleep. Skip a night of sleep and brain tissue can physically shrink from the stress. Your brain literally cleans itself while you're unconscious, flushing out toxins that build up during the day. Sleep isn't optional. It's maintenance.",
        "hashtags": ["shorts", "facts", "brain", "science"],
        "visual_scenes": [
            {"search_query": "human brain", "ai_prompt": "glowing human brain illustration, photorealistic, 9:16 vertical"},
            {"search_query": "person sleeping", "ai_prompt": "person sleeping peacefully at night, cinematic, 9:16 vertical"},
            {"search_query": "neurons brain scan", "ai_prompt": "glowing neural network inside a human head, futuristic, 9:16 vertical"},
            {"search_query": "alarm clock night", "ai_prompt": "alarm clock on a nightstand in a dark bedroom, cinematic, 9:16 vertical"},
            {"search_query": "sunrise morning", "ai_prompt": "sunrise through a bedroom window, cinematic, 9:16 vertical"},
        ],
    },
    {
        "title": "This Ancient City Vanished Overnight!",
        "category": "ancient history and lost civilizations",
        "description": "History hides some incredible mysteries. #shorts #facts",
        "script": "In the year 79 AD, an entire city vanished in seconds. Pompeii was buried under volcanic ash so fast, it froze everyday life exactly as it happened. Bread was still in ovens. People were caught mid-step. Nearly two thousand years later, we're still uncovering exactly what that final morning looked like. It's one of history's most haunting time capsules.",
        "hashtags": ["shorts", "facts", "history", "ancient"],
        "visual_scenes": [
            {"search_query": "pompeii ruins", "ai_prompt": "ancient roman ruins of pompeii, photorealistic, 9:16 vertical"},
            {"search_query": "volcano eruption", "ai_prompt": "volcanic eruption in the distance, cinematic, 9:16 vertical"},
            {"search_query": "ancient roman city", "ai_prompt": "reconstructed ancient roman street scene, cinematic, 9:16 vertical"},
            {"search_query": "archaeology dig", "ai_prompt": "archaeologists excavating ancient ruins, photorealistic, 9:16 vertical"},
            {"search_query": "museum artifact", "ai_prompt": "preserved ancient artifact in a museum display, cinematic, 9:16 vertical"},
        ],
    },
    {
        "title": "Animals Are Smarter Than You Think!",
        "category": "animals and wildlife behavior",
        "description": "The animal kingdom hides shocking secrets. #shorts #facts",
        "script": "Octopuses have three hearts and blue blood. That's not even the strange part. Two of those hearts literally stop beating every single time they swim. They can taste with their arms, squeeze through a gap the size of a coin, and solve puzzles better than most house pets. The ocean is hiding one of the strangest minds on the planet.",
        "hashtags": ["shorts", "facts", "animals", "nature"],
        "visual_scenes": [
            {"search_query": "octopus underwater", "ai_prompt": "octopus underwater close up, photorealistic, 9:16 vertical"},
            {"search_query": "coral reef", "ai_prompt": "colorful coral reef with sea life, photorealistic, 9:16 vertical"},
            {"search_query": "ocean tide pool", "ai_prompt": "rocky tide pool with marine life, photorealistic, 9:16 vertical"},
            {"search_query": "octopus camouflage", "ai_prompt": "octopus camouflaging against rocks, photorealistic, 9:16 vertical"},
            {"search_query": "underwater blue water", "ai_prompt": "sunlight streaming through clear blue ocean water, cinematic, 9:16 vertical"},
        ],
    },
]


def _load_used_titles() -> list:
    try:
        return json.loads(_HISTORY_FILE.read_text()).get("titles", [])
    except Exception:
        return []


_CATEGORY_SCORES_FILE = Path(__file__).resolve().parent / "assets" / "category_scores.json"


def _choose_category() -> str:
    """
    Picks a topic category, weighted by past performance if the analytics
    feedback loop (analytics_feedback.py) has written any scores yet.
    Categories with no data default to a neutral weight, so everything still
    gets explored — this never turns into "always pick the one winner."
    """
    try:
        scores = json.loads(_CATEGORY_SCORES_FILE.read_text())
    except Exception:
        scores = {}

    if not scores:
        return random.choice(_TOPIC_CATEGORIES)

    weights = [max(scores.get(cat, 1.0), 0.1) for cat in _TOPIC_CATEGORIES]
    return random.choices(_TOPIC_CATEGORIES, weights=weights, k=1)[0]


def _remember_title(title: str):
    try:
        _HISTORY_FILE.parent.mkdir(exist_ok=True)
        titles = _load_used_titles()
        titles.append(title)
        titles = titles[-30:]  # keep it bounded
        _HISTORY_FILE.write_text(json.dumps({"titles": titles}, indent=2))
    except Exception:
        pass  # never let history bookkeeping break the pipeline


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
        print("[!] GEMINI_API_KEY is missing/empty in the environment. Using fallback script.")
        print("[!] Double check the GEMINI_API_KEY secret is set in GitHub repo Settings > Secrets and Actions.")
        return get_fallback_script()

    used_titles = _load_used_titles()
    category = _choose_category()

    try:
        client = genai.Client(api_key=api_key)

        avoid_clause = ""
        if used_titles:
            recent = ", ".join(used_titles[-10:])
            avoid_clause = f" Do NOT repeat or closely resemble any of these previously used titles/topics: {recent}."

        prompt = (
            "You are a viral YouTube Shorts scriptwriter. Your scripts are fast-paced, punchy, and built "
            "to stop the scroll in the first 2 seconds. Short sentences. No filler. Every line earns its "
            "place or gets cut.\n\n"
            f"Write a 30-45 second Short script about an astounding, lesser-known fact in the category of "
            f"{category}.{avoid_clause}\n\n"
            "Requirements:\n"
            "- Open with a scroll-stopping hook in the first sentence (a bold claim, a shocking number, or "
            "a 'you've been lied to about X' style line) — not a slow intro.\n"
            "- Keep the narration conversational and energetic, like you're telling a friend something wild.\n"
            "- End with a punchy closing line or a reason to keep watching/follow.\n\n"
            "Return ONLY a valid JSON object with the following keys: "
            "'title' (string), 'description' (string), 'script' (string, full narration), "
            "'hashtags' (list of strings), "
            "'visual_scenes' (a list of 5 to 6 objects, one per beat of the script). "
            "Each object in 'visual_scenes' MUST have exactly two keys: "
            "'search_query' (1-2 broad, generic English words suitable for searching a stock photo/video "
            "library, e.g. 'ocean waves', 'human brain', 'ancient ruins' — NOT a full sentence), and "
            "'ai_prompt' (a detailed, vivid description of the same scene, to be used ONLY if no stock "
            "footage is found, suitable for an AI image generator)."
        )

        response = _call_gemini(client, prompt)

        raw_text = response.text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:-3].strip()
        elif raw_text.startswith("```"):
            raw_text = raw_text[3:-3].strip()

        result = json.loads(raw_text)
        result["category"] = category
        _remember_title(result.get("title", ""))
        return result
    except Exception as e:
        print(f"[!] Error in script generation: {e}")
        traceback.print_exc()  # show the REAL reason in the Actions log
        return get_fallback_script()


def discover_trending_topic():
    return generate_script()


def get_fallback_script():
    used_titles = _load_used_titles()
    # Prefer a fallback we haven't used recently, so a bad API key doesn't
    # produce 3 identical shorts in the same run (or every day after).
    unused = [s for s in _FALLBACK_SCRIPTS if s["title"] not in used_titles[-len(_FALLBACK_SCRIPTS):]]
    choice = random.choice(unused or _FALLBACK_SCRIPTS)
    _remember_title(choice["title"])
    return choice
