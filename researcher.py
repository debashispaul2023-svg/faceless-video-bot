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
        "description": "Discover incredible facts about planet Jupiter! #shorts #science",
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
        "title": "The Dark Side of the Universe Explained!",
        "category": "universe mysteries and astrophysics",
        "description": "Most of our universe is completely invisible! #shorts #physics",
        "script": "Everything you can see, touch, and interact with—stars, planets, you, and me—makes up less than five percent of the universe. The rest is dark matter and dark energy, an invisible force tearing space apart. We have no idea what it actually is, yet it controls the fate of everything that exists. Reality is hiding in plain sight.",
        "hashtags": ["shorts", "science", "astrophysics", "universe"],
        "visual_scenes": [
            {"search_query": "galaxy cosmos", "ai_prompt": "swirling cosmic galaxy with dark matter web, cinematic, 9:16 vertical"},
            {"search_query": "deep space nebula", "ai_prompt": "colorful deep space nebula and cosmic dust, photorealistic, 9:16 vertical"},
            {"search_query": "universe expansion", "ai_prompt": "abstract representation of universe expansion and dark energy, cinematic, 9:16 vertical"},
            {"search_query": "astrophysics equations", "ai_prompt": "quantum physics equations glowing in space, futuristic, 9:16 vertical"},
            {"search_query": "spinning black hole", "ai_prompt": "massive black hole warping spacetime, cinematic, 9:16 vertical"},
        ],
    },
    {
        "title": "What Happens If You Fall Into a Black Hole?",
        "category": "quantum mechanics and cosmic phenomena",
        "description": "Spaghettification in deep space! #shorts #blackhole",
        "script": "If you ever fell into a black hole, gravity would pull your feet much harder than your head. You would be stretched out like a long strand of spaghetti in a fraction of a second. Time itself would slow down completely for you compared to the rest of the universe. Black holes are the ultimate cosmic trap.",
        "hashtags": ["shorts", "science", "blackhole", "space"],
        "visual_scenes": [
            {"search_query": "black hole accretion disk", "ai_prompt": "glowing accretion disk around a supermassive black hole, cinematic, 9:16 vertical"},
            {"search_query": "spacetime distortion", "ai_prompt": "gravitational lensing warping light in space, futuristic, 9:16 vertical"},
            {"search_query": "spiral galaxy", "ai_prompt": "bright spiral galaxy in deep space, photorealistic, 9:16 vertical"},
            {"search_query": "falling into black hole", "ai_prompt": "dramatic light bending into a dark void, cinematic, 9:16 vertical"},
            {"search_query": "deep space stars", "ai_prompt": "dense star cluster in the milky way, photorealistic, 9:16 vertical"},
        ],
    },
    {
        "title": "The Weirdest Law of Quantum Physics!",
        "category": "fascinating science facts and physics",
        "description": "Particles can be in two places at once. #shorts #quantum",
        "script": "Quantum mechanics breaks the rules of reality. Particles can exist in multiple places at the exact same time until someone looks at them. The moment you observe a quantum particle, it decides to act like a normal object. It is as if the universe only renders reality when someone is watching.",
        "hashtags": ["shorts", "science", "quantum", "physics"],
        "visual_scenes": [
            {"search_query": "quantum particles", "ai_prompt": "glowing subatomic quantum particles interacting, futuristic, 9:16 vertical"},
            {"search_query": "laser beam physics", "ai_prompt": "scientific laser experiment in a dark laboratory, cinematic, 9:16 vertical"},
            {"search_query": "abstract energy waves", "ai_prompt": "pulsing waves of pure energy and light, cinematic, 9:16 vertical"},
            {"search_query": "neural network grid", "ai_prompt": "interconnected matrix of light nodes, futuristic, 9:16 vertical"},
            {"search_query": "glowing atom model", "ai_prompt": "detailed atomic structure with orbiting electrons, photorealistic, 9:16 vertical"},
        ],
    },
    {
        "title": "We Found a Planet Made of Burning Ice!",
        "category": "deep space discoveries and planetary science",
        "description": "Strange exoplanets hidden in the cosmos. #shorts #exoplanet",
        "script": "Deep in the cosmos, astronomers discovered an exoplanet called Gliese 436 b. It is a planet the size of Neptune made entirely of ice, but it burns at a scorching four hundred degrees Celsius. The gravity is so immense that it crushes steam and water vapor into solid ice, forcing it to stay frozen despite the extreme heat. Space defies all logic.",
        "hashtags": ["shorts", "science", "exoplanet", "space"],
        "visual_scenes": [
            {"search_query": "alien exoplanet", "ai_prompt": "strange alien exoplanet with glowing ice surface, cinematic, 9:16 vertical"},
            {"search_query": "burning ice planet", "ai_prompt": "hot glowing ice crystals on an alien world, photorealistic, 9:16 vertical"},
            {"search_query": "distant star system", "ai_prompt": "planet orbiting a distant red dwarf star, cinematic, 9:16 vertical"},
            {"search_query": "ice texture abstract", "ai_prompt": "close up of frozen crystal patterns under extreme light, photorealistic, 9:16 vertical"},
            {"search_query": "deep space horizon", "ai_prompt": "sunrise over the horizon of an alien planet, cinematic, 9:16 vertical"},
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
