import os
import json
import random
import re
import time
import traceback
from pathlib import Path
from google import genai
from google.genai import types

# Where we remember which titles we've already used, so we stop repeating
# ourselves both within a single run (3 shorts) and across days.
_HISTORY_FILE = Path(__file__).resolve().parent / "assets" / "used_topics.json"

_TOPIC_CATEGORIES = [
    "budgeting and saving money",
    "investing and wealth building",
    "debt management and credit scores",
    "side hustles and making money online",
    "financial independence and retirement planning"
]

# A pool of varied fallback scripts (used only if Gemini is unreachable),
# so a bad API key doesn't make every single video identical.
# Each scene now carries BOTH a `search_query` (1-2 broad words, for the
# free stock API) and an `ai_prompt` (detailed description, used only if
# the stock search comes up empty).
_FALLBACK_SCRIPTS = [
    {
        "title": "The 50/30/20 Budget Rule Explained!",
        "category": "budgeting and saving money",
        "description": "Master your money with this simple budgeting rule! #shorts #finance",
        "script": "If you do not know the 50, 30, 20 rule, you are probably losing thousands of dollars a year without even realizing it. Allocate 50 percent of your income to needs like rent and groceries, 30 percent to wants like entertainment and dining out, and the remaining 20 percent straight into savings and investments. It is the easiest way to take control of your financial future without feeling restricted.",
        "hashtags": ["shorts", "finance", "budgeting", "money"],
        "visual_scenes": [
            {"search_query": "saving money cash", "ai_prompt": "stacks of cash and coins in a piggy bank, photorealistic, 9:16 vertical"},
            {"search_query": "budget planning notebook", "ai_prompt": "person writing a financial budget plan on a desk, cinematic, 9:16 vertical"},
            {"search_query": "credit card shopping", "ai_prompt": "modern credit card close up with glowing digital lines, futuristic, 9:16 vertical"},
            {"search_query": "successful person smiling", "ai_prompt": "confident person looking at successful stock portfolio on a smartphone, cinematic, 9:16 vertical"},
            {"search_query": "financial growth chart", "ai_prompt": "glowing financial growth chart going upwards, cinematic, 9:16 vertical"},
        ],
    },
    {
        "title": "Why Saving Money is Making You Poorer!",
        "category": "investing and wealth building",
        "description": "Inflation is eating your savings. #shorts #investing",
        "script": "Your savings account is quietly stealing your money every single day, and your bank is never going to tell you. With inflation rising, the purchasing power of your cash drops continuously. Wealthy people do not let cash sit idle; they put their money into assets that beat inflation, like stocks, real estate, and index funds. Your money needs to work harder than you do.",
        "hashtags": ["shorts", "finance", "investing", "wealth"],
        "visual_scenes": [
            {"search_query": "bank vault money", "ai_prompt": "modern bank vault door opening with bright light, cinematic, 9:16 vertical"},
            {"search_query": "stock market trading", "ai_prompt": "glowing stock market graphs and data streams on a screen, futuristic, 9:16 vertical"},
            {"search_query": "real estate investment", "ai_prompt": "luxury modern house exterior with sunset lighting, architectural, 9:16 vertical"},
            {"search_query": "gold coins pile", "ai_prompt": "shiny gold coins stacked neatly under warm studio light, photorealistic, 9:16 vertical"},
            {"search_query": "compound interest graph", "ai_prompt": "exponential growth chart showing wealth accumulation, cinematic, 9:16 vertical"},
        ],
    },
    {
        "title": "The Credit Score Secret Banks HATE!",
        "category": "debt management and credit scores",
        "description": "How to boost your credit score fast! #shorts #credit",
        "script": "One three-digit number is silently deciding whether banks trust you with money, and most people have no idea they are wrecking it. One of the fastest ways to boost your credit score is by keeping your credit utilization below thirty percent. Pay your balance twice a month instead of once. Banks love responsible borrowers, and a higher score means thousands of dollars saved on loan interest.",
        "hashtags": ["shorts", "finance", "credit", "moneytips"],
        "visual_scenes": [
            {"search_query": "credit score rating", "ai_prompt": "digital credit score meter showing excellent rating, futuristic, 9:16 vertical"},
            {"search_query": "person using laptop bank", "ai_prompt": "focused person reviewing bank statements on a laptop at night, cinematic, 9:16 vertical"},
            {"search_query": "calculator financial documents", "ai_prompt": "calculator and financial papers on an office desk, photorealistic, 9:16 vertical"},
            {"search_query": "approved loan stamp", "ai_prompt": "loan application document stamped with approved sign, cinematic, 9:16 vertical"},
            {"search_query": "financial freedom success", "ai_prompt": "successful entrepreneur celebrating financial milestone, cinematic, 9:16 vertical"},
        ],
    },
    {
        "title": "Best Side Hustles to Start With Zero Money!",
        "category": "side hustles and making money online",
        "description": "Make extra income from home! #shorts #sidehustle",
        "script": "Some people make more money in ten hours a week than others make in a forty-hour job, and they never once show up to an office. You do not need thousands of dollars to start a profitable side hustle. Skills like freelance video editing, copywriting, and digital marketing require zero upfront capital, only your time and internet connection. High-income skills are the ultimate asset in today economy.",
        "hashtags": ["shorts", "finance", "sidehustle", "entrepreneur"],
        "visual_scenes": [
            {"search_query": "laptop working remotely", "ai_prompt": "person working on a laptop in a cozy cafe, cinematic, 9:16 vertical"},
            {"search_query": "freelance workspace", "ai_prompt": "modern minimalist desk setup with dual monitors, cinematic, 9:16 vertical"},
            {"search_query": "digital nomad lifestyle", "ai_prompt": "digital nomad working outdoors with a laptop, cinematic, 9:16 vertical"},
            {"search_query": "online business growth", "ai_prompt": "creative professional brainstorming marketing strategies on a glass board, photorealistic, 9:16 vertical"},
            {"search_query": "financial independence goal", "ai_prompt": "person achieving financial goals looking at digital tablet, cinematic, 9:16 vertical"},
        ],
    },
    {
        "title": "The Power of Compound Interest Explained!",
        "category": "financial independence and retirement planning",
        "description": "Make your money multiply automatically! #shorts #wealth",
        "script": "Starting with just one hundred dollars a month in your twenties could make you richer at retirement than most doctors, and almost nobody actually does it. Albert Einstein called this compound interest the eighth wonder of the world. Time in the market always beats timing the market. The best day to start investing was ten years ago; the second best day is today.",
        "hashtags": ["shorts", "finance", "investing", "wealthbuilding"],
        "visual_scenes": [
            {"search_query": "hourglass running sand", "ai_prompt": "close up of an hourglass measuring time passing by, cinematic, 9:16 vertical"},
            {"search_query": "retirement planning success", "ai_prompt": "happy elderly couple enjoying retirement outdoors, cinematic, 9:16 vertical"},
            {"search_query": "growing plant money", "ai_prompt": "small plant sprouting from a pile of gold coins, photorealistic, 9:16 vertical"},
            {"search_query": "wealth accumulation graph", "ai_prompt": "glowing digital curve representing exponential investment growth, futuristic, 9:16 vertical"},
            {"search_query": "financial freedom future", "ai_prompt": "futuristic city skyline at sunset representing a secure future, cinematic, 9:16 vertical"},
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


def _is_quota_exhausted(e: Exception) -> bool:
    """
    A 429 RESOURCE_EXHAUSTED error means this model's quota is genuinely
    used up (sometimes permanently, e.g. 'limit: 0' on the free tier for
    that specific model) — retrying the SAME model won't help, it just
    burns time. We detect this defensively (attribute OR message text,
    since we can't rely on one specific SDK version's exact error shape).
    """
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    msg = str(e)
    return code == 429 or "RESOURCE_EXHAUSTED" in msg or " 429 " in f" {msg} "


def _call_gemini_single_model(client, prompt, model_name):
    """
    Tries one model. Quota-exhausted (429) errors fail IMMEDIATELY (no
    point retrying the same over-quota model). Everything else (timeouts,
    503 'model overloaded', transient network errors) gets a short
    exponential backoff retry before giving up on this model.
    """
    max_attempts = 3
    delay = 4
    last_err = None

    for attempt in range(1, max_attempts + 1):
        try:
            return client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    http_options=types.HttpOptions(timeout=30000)  # 30 second timeout
                )
            )
        except Exception as e:
            if _is_quota_exhausted(e):
                print(f"[!] Model '{model_name}' quota exhausted — skipping straight to the "
                      f"next model instead of wasting time retrying it.")
                raise
            last_err = e
            if attempt < max_attempts:
                print(f"[!] Model '{model_name}' attempt {attempt}/{max_attempts} failed "
                      f"({e}); retrying in {delay}s...")
                time.sleep(delay)
                delay *= 2

    raise last_err


# If the primary model is overloaded (Google returns 503 UNAVAILABLE /
# "high demand" — this is NOT a quota or key problem, just that specific
# model being busy), fall through to trying other Gemini models before
# giving up and using the local fallback script.
_MODEL_FALLBACK_CHAIN = ["gemini-3.5-flash", "gemini-2.5-flash", "gemini-2.0-flash"]


def _call_gemini(client, prompt):
    last_error = None
    for model_name in _MODEL_FALLBACK_CHAIN:
        try:
            return _call_gemini_single_model(client, prompt, model_name)
        except Exception as e:
            print(f"[!] Model '{model_name}' failed (likely overloaded/503, or truly unavailable): {e}")
            last_error = e
            continue

    if _is_quota_exhausted(last_error):
        print("[!] ALL models hit quota limits (limit: 0 on the free tier usually means the "
              "project behind this API key needs attention — check billing/quota status at "
              "https://ai.dev/rate-limit, or generate a fresh key at "
              "https://aistudio.google.com/apikey under a different Google Cloud project).")
    raise last_error


_trending_cache = {"titles": [], "fetched_at": None}


def _fetch_trending_finance_titles(limit: int = 8) -> list:
    """
    Best-effort: pulls titles of currently popular personal-finance videos
    on YouTube (last 7 days, US region) so the script prompt can lean
    toward angles that are actually resonating right now. Cached in-memory
    for this process's lifetime since trending topics don't meaningfully
    change within the few minutes main.py takes to generate 3 videos.
    Never raises.
    """
    import datetime as _dt

    if _trending_cache["titles"] and _trending_cache["fetched_at"]:
        age = (_dt.datetime.now(_dt.timezone.utc) - _trending_cache["fetched_at"]).total_seconds()
        if age < 6 * 3600:
            return _trending_cache["titles"]

    try:
        from youtube_uploader import authenticate_youtube
        youtube = authenticate_youtube()
        published_after = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = youtube.search().list(
            part="snippet", q="personal finance money tips saving investing",
            type="video", order="viewCount", publishedAfter=published_after,
            regionCode="US", maxResults=limit,
        ).execute()
        titles = [item["snippet"]["title"] for item in resp.get("items", [])]
        _trending_cache["titles"] = titles
        _trending_cache["fetched_at"] = _dt.datetime.now(_dt.timezone.utc)
        if titles:
            print(f"[+] Fetched {len(titles)} trending finance video titles for prompt inspiration.")
        return titles
    except Exception as e:
        print(f"[i] Trending-topic fetch skipped (non-fatal): {e}")
        return []


_MAX_SCRIPT_WORDS = 75  # ~25-30 seconds at a natural ~155 words/minute speaking pace


def _enforce_max_duration(script: str) -> str:
    """
    Gemini's '25-30 second' instruction is just a request — it doesn't
    reliably keep the model from writing a much longer script. This
    enforces it in code: if the script runs over budget, trims it down to
    the last COMPLETE sentence that still fits, rather than cutting off
    mid-sentence. Leaves short scripts untouched.
    """
    words = script.split()
    if len(words) <= _MAX_SCRIPT_WORDS:
        return script

    sentences = re.split(r'(?<=[.!?])\s+', script.strip())
    kept, word_count = [], 0
    for sentence in sentences:
        sentence_words = len(sentence.split())
        if kept and word_count + sentence_words > _MAX_SCRIPT_WORDS:
            break
        kept.append(sentence)
        word_count += sentence_words

    trimmed = " ".join(kept) if kept else " ".join(words[:_MAX_SCRIPT_WORDS])
    print(f"[i] Script ran long ({len(words)} words, ~{len(words)/155*60:.0f}s) — "
          f"trimmed to {len(trimmed.split())} words (~{len(trimmed.split())/155*60:.0f}s).")
    return trimmed


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
            recent = ", ".join(used_titles[-20:])
            avoid_clause = (
                f" Do NOT repeat or closely resemble any of these previously covered titles/topics — "
                f"this means not just avoiding the same words, but avoiding the same underlying idea "
                f"or angle even if phrased differently: {recent}."
            )

        retention_clause = ""
        try:
            insights_path = Path(__file__).resolve().parent / "assets" / "retention_insights.json"
            if insights_path.is_file():
                insight = json.loads(insights_path.read_text())
                dropoff = insight.get("typical_dropoff_second")
                if dropoff:
                    retention_clause = (
                        f"\n\nIMPORTANT PACING NOTE: analysis of past videos on this channel shows "
                        f"viewers tend to drop off heavily around the {dropoff:.0f}-second mark. Make sure "
                        f"the script stays punchy and keeps delivering new information/stakes well before "
                        f"that point — no lulls, no repeated ideas, no slow explanations."
                    )
        except Exception:
            pass

        trending_titles = _fetch_trending_finance_titles()
        trending_clause = ""
        if trending_titles:
            trending_clause = (
                "\n\nFor inspiration, here are titles of personal-finance videos that are "
                "CURRENTLY getting a lot of views on YouTube (last 7 days). Do NOT copy them — "
                "just notice which angles/hooks/formats seem to be resonating right now, and let "
                "that inform your own original take:\n- " + "\n- ".join(trending_titles)
            )

        prompt = (
            "You are an elite financial advisor and viral YouTube Shorts scriptwriter. Write a "
            "high-retention, psychology-backed finance script strictly tailored for a 25-30 second "
            f"Short, in the category of "
            f"{category}.{avoid_clause}{retention_clause}{trending_clause}\n\n"
            "STRICT SCRIPT STRUCTURE:\n"
            "1. THE HOOK (first 3 seconds): a high-stakes, bold psychological hook that forces "
            "viewers to freeze — lead with the single most shocking claim, number, or consequence "
            "in the whole script. Style example: 'Smart people NEVER stay broke because they follow "
            "these 4 money rules. Tell me in the comments if you follow rule number 4!' Do NOT ease "
            "into it, and never open with 'Did you know...', 'Let's talk about...', or 'Have you "
            "ever wondered...'.\n"
            "2. FOUR DISTINCT FINANCIAL RULES: ultra-concise, practical money tips (rules 1-3), "
            "each one earning its place. RULE 4 is the single most important, game-changing rule — "
            "build a tiny dramatic pause (using '...') right before it lands.\n"
            "3. MEME-STYLE CTA: end with a lighthearted, sarcastic/funny comment-trigger instead "
            "of a formal closing. Style example: 'Comment below how many rules you actually "
            "follow... or just drop 'Rule 4' if you're lazy like me and hit follow!'\n\n"
            "VOICE:\n"
            "- Ultra-short, fast-paced, punchy sentences. Zero filler.\n"
            "- Write key emphasis words in UPPERCASE (e.g. 'NEVER', 'STOP', 'CHANGES EVERYTHING').\n"
            "- Use ellipses (...) for short dramatic pauses before key insights.\n"
            "- Tone: deep, authoritative, confident, urgent.\n"
            "- STRICT WORD COUNT: the 'script' field must be 65 to 75 words total — this is "
            "critical to keep the audio within 25-30 seconds. Do not go over.\n"
            "- The 'script' field must be pure flowing spoken narration ONLY — no bracket labels "
            "like '[HOOK]' or '[RULE 1]', no stage directions, no emojis. Just the words a voice "
            "actor would actually say, in order: hook, rule 1, rule 2, rule 3, rule 4, then the CTA.\n\n"
            "COMPLIANCE (strict, this is financial content): NEVER recommend a specific stock, "
            "crypto coin, or investment product; NEVER promise or imply a guaranteed return or a "
            "specific dollar/wealth outcome (e.g. 'you will become a millionaire', 'guaranteed "
            "profit'); keep everything general and educational, framed as 'here's how X works', "
            "never 'here's what to buy'.\n\n"
            "Return ONLY a valid JSON object with the following keys: "
            "'title' (string), 'description' (string), 'script' (string, the full narration as "
            "specified above), 'hashtags' (list of strings), "
            "'visual_scenes' (a list of exactly 6 objects, one per beat, in this order: hook, "
            "rule 1, rule 2, rule 3, rule 4, cta). Each object MUST have exactly two keys: "
            "'search_query' (1-2 broad, generic English words suitable for searching a stock "
            "photo/video library — NOT a full sentence), and 'ai_prompt' (a detailed, vivid "
            "description of the same scene, to be used ONLY if no stock footage is found, suitable "
            "for an AI image generator). The visuals for the HOOK and RULE 4 should be the two most "
            "visually striking scenes in the list. For RULE 2 or RULE 3 (pick one), make the "
            "ai_prompt an exaggerated, funny reaction-style visual to break the monotony (e.g. "
            "'a person's shocked exaggerated facial expression, comedic staging, bright lighting') "
            "— this must be a wholly original AI-generated image: NEVER reference or describe any "
            "copyrighted character, anime, movie/show still, meme template, logo, or trademark."
        )

        response = _call_gemini(client, prompt)

        raw_text = response.text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:-3].strip()
        elif raw_text.startswith("```"):
            raw_text = raw_text[3:-3].strip()

        result = json.loads(raw_text)
        result["category"] = category
        if result.get("script"):
            result["script"] = _enforce_max_duration(result["script"])
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
    unused = [s for s in _FALLBACK_SCRIPTS if s["title"] not in used_titles]
    choice = dict(random.choice(unused or _FALLBACK_SCRIPTS))  # copy — don't mutate the shared pool
    choice["script"] = _enforce_max_duration(choice["script"])
    _remember_title(choice["title"])
    return choice
