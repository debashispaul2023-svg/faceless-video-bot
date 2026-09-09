import os
import json
import random
import re
import time
import traceback
from pathlib import Path
from google import genai
from google.genai import types

# Bump this any time the live prompt (the actual instructions sent to
# Gemini in generate_script(), not the fallback pool) changes meaningfully.
# It gets tagged onto every video published from that prompt, so
# analytics_feedback.py can later show whether "v2" actually outperformed
# "v1" with real view/retention data — instead of just trusting that a
# prompt edit was an improvement. Never overwrite performance history: bump
# the number, don't reuse an old one.
_PROMPT_VERSION = "v4"

# Where we remember which titles we've already used, so we stop repeating
# ourselves both within a single run and across days.
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
# the stock search comes up empty). `hook_technique` tags which of the
# three hook styles (see the live prompt below) this fallback actually
# uses, so it feeds the same success-pattern tracking as live-generated
# scripts instead of always logging as "unspecified".
_FALLBACK_SCRIPTS = [
    {
        "title": "4 Money Rules That Keep You Broke",
        "category": "budgeting and saving money",
        "hook_technique": "counter_intuitive_fact",
        "description": "Follow these 4 rules before you spend another dollar. #shorts #finance #budgeting",
        "script": "Most people who save money are STILL broke, and it's because they follow zero real rules. Rule one, pay yourself first, before any bill touches it. Rule two, track every dollar for one week, it will SHOCK you. Rule three, automate your savings so willpower never matters... Rule four, the real secret, is a 24 hour rule before any purchase over fifty. That habit CHANGES EVERYTHING. Comment which rule you break, or just hit follow.",
        "hashtags": ["shorts", "finance", "budgeting", "moneytips"],
        "visual_scenes": [
            {"search_query": "stressed person bills", "ai_prompt": "person looking shocked at a pile of bills, dramatic lighting, photorealistic, 9:16 vertical"},
            {"search_query": "savings jar coins", "ai_prompt": "person dropping coins into a labeled savings jar right after payday, photorealistic, 9:16 vertical"},
            {"search_query": "person tracking expenses", "ai_prompt": "person shocked while reviewing expenses on phone, exaggerated reaction, comedic staging, bright lighting, 9:16 vertical"},
            {"search_query": "phone automatic transfer", "ai_prompt": "smartphone banking app screen setting up an automatic recurring savings transfer, photorealistic, 9:16 vertical"},
            {"search_query": "hand hovering checkout", "ai_prompt": "hand hesitating over a phone checkout buy button before completing an online purchase, cinematic, 9:16 vertical"},
            {"search_query": "phone comment section", "ai_prompt": "smartphone showing a comments section, cinematic close up, 9:16 vertical"},
        ],
    },
    {
        "title": "4 Investing Rules Beginners Never Hear",
        "category": "investing and wealth building",
        "hook_technique": "counter_intuitive_fact",
        "description": "The 4 rules that separate wealthy investors from everyone else. #shorts #investing",
        "script": "Waiting to invest is the most expensive mistake you will EVER make, and nobody warns you. Rule one, start with any amount, consistency beats the perfect number. Rule two, index funds quietly outperform most expert stock pickers. Rule three, never touch your investments during a dip... Rule four, and this CHANGES EVERYTHING, time in the market always beats timing the market. Comment if you're actually investing yet, or just drop rule four and follow.",
        "hashtags": ["shorts", "finance", "investing", "wealth"],
        "visual_scenes": [
            {"search_query": "stock market screen", "ai_prompt": "glowing stock market graphs and data streams on a screen, futuristic, 9:16 vertical"},
            {"search_query": "investing app phone", "ai_prompt": "person starting a small investment on a phone investing app, photorealistic, 9:16 vertical"},
            {"search_query": "person shocked laptop", "ai_prompt": "person with an exaggerated mind-blown reaction looking at a laptop, comedic cinematic lighting, 9:16 vertical"},
            {"search_query": "stock chart dip", "ai_prompt": "red downward stock chart dip on a screen with a calm hand staying away from the sell button, cinematic, 9:16 vertical"},
            {"search_query": "hourglass growth chart", "ai_prompt": "an hourglass beside a steadily rising investment growth chart, futuristic, 9:16 vertical"},
            {"search_query": "phone comment section", "ai_prompt": "smartphone showing a comments section, cinematic close up, 9:16 vertical"},
        ],
    },
    {
        "title": "Banks Hope You Never Learn These 4 Rules",
        "category": "debt management and credit scores",
        "hook_technique": "counter_intuitive_fact",
        "description": "Fix your credit score before it's too late. #shorts #credit",
        "script": "Your credit score is quietly controlling your life, and most people have NO idea how badly they're wrecking it. Rule one, keep utilization under thirty percent, always. Rule two, never close your oldest card, age matters more than you think. Rule three, pay twice a month, it fools the algorithm... Rule four, the one nobody follows, is checking your report for errors yearly. That alone costs people THOUSANDS. Comment your score, or just hit follow.",
        "hashtags": ["shorts", "finance", "credit", "moneytips"],
        "visual_scenes": [
            {"search_query": "credit score meter", "ai_prompt": "digital credit score meter showing excellent rating, futuristic, 9:16 vertical"},
            {"search_query": "credit card calculator", "ai_prompt": "a credit card next to a calculator showing a low percentage, photorealistic, 9:16 vertical"},
            {"search_query": "person shocked statement", "ai_prompt": "person with exaggerated shocked facial expression looking at a credit card statement, comedic lighting, 9:16 vertical"},
            {"search_query": "calendar payment reminder", "ai_prompt": "a calendar with two payment dates circled each month, photorealistic, 9:16 vertical"},
            {"search_query": "credit report document", "ai_prompt": "person carefully reviewing a printed credit report document, cinematic, 9:16 vertical"},
            {"search_query": "phone comment section", "ai_prompt": "smartphone showing a comments section, cinematic close up, 9:16 vertical"},
        ],
    },
    {
        "title": "4 Side Hustle Rules That Actually Work",
        "category": "side hustles and making money online",
        "hook_technique": "everyday_comparison",
        "description": "Skip the fluff — these 4 rules actually work. #shorts #sidehustle",
        "script": "Some people make more in ten hours a week than others make full time, and they NEVER show up to an office. Rule one, pick a skill people already pay for. Rule two, charge more than feels comfortable, underpricing kills hustles fast. Rule three, reinvest your first profits into getting better... Rule four, the brutal one, is that consistency beats talent every time. Comment your side hustle, or drop rule four and follow.",
        "hashtags": ["shorts", "finance", "sidehustle", "entrepreneur"],
        "visual_scenes": [
            {"search_query": "laptop working remotely", "ai_prompt": "person working on a laptop in a cozy cafe, cinematic, 9:16 vertical"},
            {"search_query": "freelance portfolio screen", "ai_prompt": "a freelancer skill portfolio website open on a laptop screen, photorealistic, 9:16 vertical"},
            {"search_query": "person excited laptop", "ai_prompt": "person with an exaggerated excited shocked reaction at a laptop screen, comedic staging, bright lighting, 9:16 vertical"},
            {"search_query": "online course laptop", "ai_prompt": "person reinvesting freelance earnings into an online skills course on a laptop, cinematic, 9:16 vertical"},
            {"search_query": "daily planner checkmarks", "ai_prompt": "a daily planner page filled with consistent checkmarks across many days, photorealistic, 9:16 vertical"},
            {"search_query": "phone comment section", "ai_prompt": "smartphone showing a comments section, cinematic close up, 9:16 vertical"},
        ],
    },
    {
        "title": "4 Retirement Rules Almost Nobody Follows",
        "category": "financial independence and retirement planning",
        "hook_technique": "counter_intuitive_fact",
        "description": "Retiring comfortably comes down to these 4 rules. #shorts #retirement",
        "script": "Retiring at sixty five is a myth sold to people who never learned these four rules. Rule one, save AT LEAST twenty percent of every paycheck. Rule two, your expenses matter more than income, always. Rule three, let compound interest work for decades, not years... Rule four, the one that CHANGES EVERYTHING, is calculating your real retirement number today. Most people never do it. Comment your number, or drop rule four if math scares you.",
        "hashtags": ["shorts", "finance", "retirement", "wealthbuilding"],
        "visual_scenes": [
            {"search_query": "hourglass running sand", "ai_prompt": "close up of an hourglass measuring time passing by, cinematic, 9:16 vertical"},
            {"search_query": "paycheck savings portion", "ai_prompt": "a paycheck with a visible portion being set aside into a savings envelope, photorealistic, 9:16 vertical"},
            {"search_query": "person shocked calculator", "ai_prompt": "person with an exaggerated shocked reaction looking at a calculator, comedic cinematic lighting, 9:16 vertical"},
            {"search_query": "long term growth chart", "ai_prompt": "a decades-long exponential investment growth chart glowing on a screen, futuristic, 9:16 vertical"},
            {"search_query": "retirement calculator desk", "ai_prompt": "person calculating a retirement number on a calculator with a notepad, cinematic, 9:16 vertical"},
            {"search_query": "phone comment section", "ai_prompt": "smartphone showing a comments section, cinematic close up, 9:16 vertical"},
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
_MODEL_FALLBACK_CHAIN = ["gemini-3.5-flash"]
# NOTE: gemini-2.5-flash and gemini-2.0-flash were removed from this chain —
# logs showed they permanently fail for this API key/project (2.5-flash:
# 404 "no longer available to new users"; 2.0-flash: 429 quota=0 on the
# free tier). Retrying dead models 3x each before giving up wasted ~20-30s
# per video for zero benefit. If gemini-3.5-flash is ever deprecated too,
# check https://ai.google.dev/gemini-api/docs/models for current model IDs
# and add ONE replacement here after confirming it actually works for this key.


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
    change within the few minutes main.py takes to generate the video.
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


_MAX_SCRIPT_WORDS = 72  # tighter for better retention (~25-28 seconds)


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

        # REINFORCEMENT LEARNING (80/20 exploit/explore) — reads the
        # structural DNA of this channel's actual top-performing videos
        # (assets/top_performing_patterns.json, written by
        # analytics_feedback.get_top_performing_patterns()) and either:
        #   80% of the time: asks Gemini to replicate the STRUCTURE/psychology
        #     of what's already working (never the literal wording — that
        #     would be duplicate content and risk YouTube's repetitious-
        #     content policy), applied to a brand-new topic/angle.
        #   20% of the time: deliberately explores something untested, so
        #     the channel keeps discovering new formats instead of ossifying
        #     around one early pattern.
        # Smoothly falls back to no clause at all (default prompt behavior)
        # if there isn't enough data yet — this is a pure prompt-context
        # addition, so it can never change the JSON schema being returned.
        reinforcement_clause = ""
        try:
            patterns_path = Path(__file__).resolve().parent / "assets" / "top_performing_patterns.json"
            if patterns_path.is_file():
                data = json.loads(patterns_path.read_text())
                top_patterns = data.get("patterns", [])
                if top_patterns:
                    if random.random() < 0.8:
                        # 80% EXPLOIT
                        dna_lines = "\n".join(
                            f"  - \"{p.get('title', '')}\" — {p.get('structure_note', '')} "
                            f"(performance score: {p.get('score', '?')})"
                            for p in top_patterns
                        )
                        reinforcement_clause = (
                            "\n\nREINFORCEMENT LEARNING NOTE (80% exploit): these are this "
                            f"channel's actual top-performing videos so far:\n{dna_lines}\n"
                            "Deconstruct WHY they likely worked — the specific hook formula, the "
                            "emotional trigger it leans on, the pacing — and apply that SAME "
                            "structural formula to a brand-new topic/angle for THIS script. Do "
                            "NOT reuse their exact wording, title, specific examples, or numbers "
                            "— that would be duplicate content and risk YouTube's repetitious-"
                            "content policy. Replicate the STRUCTURE and PSYCHOLOGY only, never "
                            "the content itself, and this script must still be about the topic "
                            "category given above, not the topic of the videos listed here."
                        )
                    else:
                        # 20% EXPLORE
                        reinforcement_clause = (
                            "\n\nREINFORCEMENT LEARNING NOTE (20% explore): this run is a "
                            "deliberate experiment — lean toward a hook technique or angle this "
                            "channel hasn't been relying on recently, rather than just repeating "
                            "the proven pattern. The goal is discovering new high-performing "
                            "formats, not optimizing the same one further this time."
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
            f"{category}.{avoid_clause}{retention_clause}{reinforcement_clause}{trending_clause}\n\n"
            "CRITICAL QUALITY RULES (channel retention currently collapses around second 6 — this is "
            "the #1 priority to fix):\n"
            "- FIRST 3 SECONDS = make-or-break. The very first sentence must contain a specific "
            "number, dollar amount, percentage, or painful consequence. No soft opens.\n"
            "- FIRST 6-8 SECONDS must deliver at least TWO concrete stakes + the core promise. "
            "If a viewer can swipe away at second 5 without feeling FOMO, the script failed.\n"
            "- Every sentence must deliver NEW information, a number, a consequence, or a "
            "visceral image. Zero filler. Zero throat-clearing.\n"
            "- Use short punchy sentences (max 11-13 words). Prefer active voice and 'you'.\n"
            "- Create open loops early that only resolve at the final rule.\n"
            "- Speak like a sharp, slightly ruthless friend who hates wasting time.\n\n"
            "TITLE RULES (this decides impressions):\n"
            "- Max 6-8 words. Short, punchy, curiosity-gap driven.\n"
            "- Prefer formats like: 'The $X Trap Most People Fall For', 'Why [Common Habit] "
            "Is Making You Broke', 'Stop Doing This With Your Money', 'The Hidden Cost of X'.\n"
            "- Include a number, dollar amount, or strong emotional word when possible.\n"
            "- Avoid vague poetic titles like 'The Silent Partner Illusion' or 'The Anesthetic "
            "Effect'. Those underperform on Shorts.\n\n"
            "STRICT SCRIPT STRUCTURE:\n"
            "1. THE HOOK (first 3 seconds): a high-stakes, bold psychological hook that forces "
            "viewers to freeze — lead with the single most shocking claim, number, or consequence "
            "in the whole script. Pick WHICHEVER of these techniques fits the topic best (vary it "
            "across scripts, don't always use the same one):\n"
            "   a) counter_intuitive_fact: challenge something the viewer assumes is true. Style "
            "example: 'Smart people NEVER stay broke because they follow these 4 money rules.'\n"
            "   b) promise_of_reward: tell them exactly what they'll walk away knowing. Style "
            "example: 'By the end of this, you'll know the exact trick the wealthy use to dodge "
            "this penalty — and it takes 30 seconds to set up.'\n"
            "   c) everyday_comparison: tie an abstract money concept to something small "
            "and relatable. Style example: 'Your morning coffee habit is quietly costing you fifty "
            "thousand dollars.'\n"
            "After you write the hook, report which technique you actually used as a new top-level "
            "JSON field 'hook_technique' — its value MUST be exactly one of: 'counter_intuitive_fact', "
            "'promise_of_reward', 'everyday_comparison' (matching the a/b/c labels above, not the "
            "letter itself).\n"
            "Do NOT ease into it, and never open with 'Did you know...', 'Let's talk about...', or "
            "'Have you ever wondered...'.\n"
            "2. THREE TO FOUR DISTINCT FINANCIAL RULES: ultra-concise, practical money tips. "
            "Each rule must feel like a revelation, not a list item. RULE 4 (or the final rule) "
            "is the single most important, game-changing insight — build a tiny dramatic pause "
            "(using '...') right before it lands. Prefer 3 strong rules over 4 weak ones if needed.\n"
            "3. MEME-STYLE CTA: end with a lighthearted, slightly sarcastic comment-trigger. "
            "Style example: 'Which rule are you breaking right now? Drop the number below.'\n\n"
            "VOICE:\n"
            "- Ultra-short, fast-paced, punchy sentences. Zero filler.\n"
            "- Write key emphasis words in UPPERCASE (e.g. 'NEVER', 'STOP', 'THIS ONE RULE').\n"
            "- Use ellipses (...) for short dramatic pauses before key insights.\n"
            "- Tone: deep, authoritative, confident, slightly urgent — like someone who has seen "
            "people lose money from ignoring this.\n"
            "- STRICT WORD COUNT: the 'script' field must be 60 to 72 words total — keep it tight "
            "so the audio stays under 28 seconds. Do not go over.\n"
            "- The 'script' field must be pure flowing spoken narration ONLY — no bracket labels "
            "like '[HOOK]' or '[RULE 1]', no stage directions, no emojis. Just the words a voice "
            "actor would actually say, in order: hook → rules → CTA.\n\n"
            "COMPLIANCE (strict, this is financial content): NEVER recommend a specific stock, "
            "crypto coin, or investment product; NEVER promise or imply a guaranteed return or a "
            "specific dollar/wealth outcome (e.g. 'you will become a millionaire', 'guaranteed "
            "profit'); keep everything general and educational, framed as 'here's how X works', "
            "never 'here's what to buy'.\n\n"
            "Return ONLY a valid JSON object with the following keys: "
            "'title' (string), 'description' (string), 'hook_technique' (string, exactly one of "
            "the three values described above), 'script' (string, the full narration as "
            "specified above), 'hashtags' (list of strings), "
            "'visual_scenes' (a list of exactly 6 objects, one per beat, in this order: hook, "
            "rule 1, rule 2, rule 3, rule 4, cta). Each object MUST have the keys "
            "'search_query' and 'ai_prompt', plus the optional 'sfx_cue'.\n\n"
            "VISUAL QUALITY RULES (critical — weak first frames are killing retention):\n"
            "- HOOK scene (first visual) MUST be high-impact and emotional. Prefer: close-up of a "
            "face with strong emotion (shock, stress, realization), hands freezing over a big "
            "number on screen, money disappearing, red warning on phone, or a clear before/after "
            "money moment. NEVER use boring neutral actions like 'hand typing on laptop', "
            "'person sitting at desk', or generic office shots for the HOOK.\n"
            "- RULE 4 visual must match the same photorealistic quality as the rest. Never drop "
            "to cartoon, illustration, or flat graphic style. Keep cinematic photorealistic.\n"
            "- CTA visual: show a phone screen with comments or a simple follow gesture. Avoid "
            "random phones that have nothing to do with the topic.\n"
            "- All ai_prompts must be photorealistic, cinematic lighting, 9:16 vertical. "
            "No cartoon, no 3D render, no flat illustration.\n"
            "- search_query: 1-2 broad concrete English words (e.g. 'shocked face phone', "
            "'money vanishing hand', 'red bank alert'). Avoid abstract words.\n"
            "- sfx_cue: use 'whoosh' or 'pop' on the HOOK and 'ding' or 'click' on RULE 4 only.\n"
            "- For one of the middle rules, include one exaggerated reaction face that is "
            "specific to the topic (not a generic shocked face).\n"
            "- NEVER reference copyrighted characters, logos, or meme templates."
        )

        response = _call_gemini(client, prompt)

        raw_text = response.text.strip()
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:-3].strip()
        elif raw_text.startswith("```"):
            raw_text = raw_text[3:-3].strip()

        result = json.loads(raw_text)
        result["category"] = category
        result["prompt_version"] = _PROMPT_VERSION
        result.setdefault("hook_technique", "unspecified")  # in case Gemini forgets it — never let this block the pipeline
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
    choice["prompt_version"] = "fallback"  # not driven by the live prompt — kept separate so it never skews a prompt version's real performance comparison
    _remember_title(choice["title"])
    return choice
