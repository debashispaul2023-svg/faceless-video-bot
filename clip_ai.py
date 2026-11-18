from __future__ import annotations

from google import genai
import json
import os
import time
from clip_core import MAX_TRANSCRIPT_CHARS_FOR_AI, WINDOWS_TO_SAMPLE, WINDOW_SECONDS


GEMINI_MODELS = [m for m in [
    os.getenv("GEMINI_MODEL", "").strip(),
    # Gemini 3 family first (unknown/retired IDs just 404 and are skipped)
    "gemini-3.8-flash",       # named by the API itself in a 404 message
    "gemini-3-flash-preview",
    "gemini-3-flash",
    "gemini-3.0-flash",
    "gemini-3-pro-preview",
    # older fallbacks
    "gemini-2.5-flash",
    "gemini-2.0-flash",
] if m]
_WORKING_MODEL: str | None = None


GEMINI_HTTP_TIMEOUT_SEC = int(os.getenv("GEMINI_HTTP_TIMEOUT_SEC", "180"))


def make_client(api_key: str):
    """Gemini client with a hard HTTP timeout so a stuck request can never hang the job."""
    try:
        from google.genai import types as _t
        c = genai.Client(
            api_key=api_key,
            http_options=_t.HttpOptions(timeout=GEMINI_HTTP_TIMEOUT_SEC * 1000),
        )
    except Exception as e:
        print(f"[!] Could not set Gemini timeout ({e}); using default client", flush=True)
        c = genai.Client(api_key=api_key)
    _CLIENT_KEYS[id(c)] = api_key
    return c


def wait_file_active(client, f, max_wait: int = 120) -> None:
    """Uploaded files start in PROCESSING; using them early can fail or stall."""
    start = time.time()
    while time.time() - start < max_wait:
        try:
            cur = client.files.get(name=f.name)
            state = getattr(getattr(cur, "state", None), "name", None) or str(getattr(cur, "state", ""))
            if "ACTIVE" in state or not state or state == "None":
                return
            if "FAILED" in state:
                raise RuntimeError(f"uploaded file processing failed: {state}")
        except RuntimeError:
            raise
        except Exception:
            return  # can't query state: just proceed
        time.sleep(3)


class GeminiQuotaExhausted(RuntimeError):
    """Every configured Gemini model has hit its daily quota (or the key itself is unusable)."""


def _is_bad_key(msg: str) -> bool:
    low = msg.lower()
    return any(k in low for k in (
        "api key not valid", "api_key_invalid", "api key expired", "permission_denied",
        "permission denied", "has been suspended", "http 403", "403 forbidden", "error code: 403",
    ))


_EXHAUSTED_MODELS: dict[str, set[str]] = {}   # api_key -> models out of quota / unavailable
_CLIENT_KEYS: dict[int, str] = {}              # id(client) -> api_key


def get_api_keys() -> list[str]:
    """Keys from GEMINI_API_KEYS (comma/newline separated) plus GEMINI_API_KEY, de-duplicated."""
    raw = os.getenv("GEMINI_API_KEYS", "") + "," + os.getenv("GEMINI_API_KEY", "")
    keys: list[str] = []
    for k in raw.replace("\n", ",").split(","):
        k = k.strip()
        if k and k not in keys:
            keys.append(k)
    return keys


def key_label(key: str) -> str:
    return f"key ...{key[-4:]}"


def key_is_exhausted(key: str) -> bool:
    return all(m in _EXHAUSTED_MODELS.get(key, set()) for m in GEMINI_MODELS)


def next_usable_key(after_key: str | None = None) -> str | None:
    """Next key (in order, after `after_key`) that still has a usable model."""
    keys = get_api_keys()
    start = keys.index(after_key) + 1 if after_key in keys else 0
    for k in keys[start:] + keys[:start]:
        if k != after_key and not key_is_exhausted(k):
            return k
    return None


def _is_daily_quota(msg: str) -> bool:
    low = msg.lower()
    return ("429" in low or "resource_exhausted" in low) and any(
        k in low for k in ("perday", "per day", "per_day", "daily")
    )


_TRANSIENT_KEYS = ("503", "unavailable", "500", "internal", "overloaded", "high demand",
                   "deadline", "timed out", "timeout", "429", "resource_exhausted")
TRANSIENT_ROUNDS = 4          # full passes over the model list before giving up
TRANSIENT_SLEEP_SEC = 20      # grows each round: 20s, 40s, 60s


def _is_transient(msg: str) -> bool:
    low = msg.lower()
    return any(k in low for k in _TRANSIENT_KEYS)


def gemini_generate(client, contents, config=None):
    """generate_content with automatic fallback + retry across models.

    - 404 / retired model            -> try next model
    - 429 with a DAILY quota message -> mark that model exhausted, try next model
    - 503 / overloaded / per-minute 429 / timeouts
                                     -> try next model; after a full pass, wait and retry
    - all models out of daily quota  -> GeminiQuotaExhausted (callers should stop, not retry)
    """
    global _WORKING_MODEL
    key = _CLIENT_KEYS.get(id(client), "")
    dead = _EXHAUSTED_MODELS.setdefault(key, set())
    last = None
    for rnd in range(TRANSIENT_ROUNDS):
        candidates = [m for m in GEMINI_MODELS if m not in dead]
        if _WORKING_MODEL in candidates:  # known-good model first
            candidates.remove(_WORKING_MODEL)
            candidates.insert(0, _WORKING_MODEL)
        if not candidates:
            raise GeminiQuotaExhausted(
                f"Gemini {key_label(key) if key else 'key'}: all models out of daily quota/unavailable."
            ) from last
        transient_seen = False
        for m in candidates:
            try:
                kwargs = {"model": m, "contents": contents}
                if config is not None:
                    kwargs["config"] = config
                resp = client.models.generate_content(**kwargs)
                if _WORKING_MODEL != m:
                    print(f"[i] Using Gemini model: {m}", flush=True)
                _WORKING_MODEL = m
                return resp
            except Exception as e:
                last = e
                msg = str(e)
                if "404" in msg or "NOT_FOUND" in msg or "no longer available" in msg:
                    print(f"[!] Model {m} unavailable - trying next", flush=True)
                    dead.add(m)
                    continue
                if _is_bad_key(msg):
                    print(f"[!] {key_label(key) if key else 'key'} rejected by Gemini (invalid/blocked) - rotating", flush=True)
                    dead.update(GEMINI_MODELS)
                    raise GeminiQuotaExhausted(f"Gemini {key_label(key) if key else 'key'} invalid/blocked.") from e
                if _is_daily_quota(msg):
                    print(f"[!] Model {m} daily quota exhausted - trying next model", flush=True)
                    dead.add(m)
                    continue
                if _is_transient(msg):
                    transient_seen = True
                    print(f"[!] Model {m} temporarily unavailable ({msg[:60]}...) - trying next", flush=True)
                    continue
                raise
        if not transient_seen:
            continue
        if rnd < TRANSIENT_ROUNDS - 1:
            wait = TRANSIENT_SLEEP_SEC * (rnd + 1)
            print(f"[i] Gemini busy - waiting {wait}s then retrying (round {rnd + 2}/{TRANSIENT_ROUNDS})", flush=True)
            time.sleep(wait)
    raise last if last else RuntimeError("no Gemini model available")


def _sample_transcript_for_ai(transcript: list[dict], max_chars: int = MAX_TRANSCRIPT_CHARS_FOR_AI) -> str:
    if not transcript:
        return ""
    total_duration = transcript[-1]["end"] - transcript[0]["start"]
    if total_duration < 90 * 60:
        lines = [f"{s['start']:.1f}-{s['end']:.1f}: {s['text']}" for s in transcript]
        return "\n".join(lines)[:max_chars]

    window = WINDOW_SECONDS
    n_windows = min(WINDOWS_TO_SAMPLE, max(4, int(total_duration // window)))
    step = total_duration / n_windows
    start0 = transcript[0]["start"]
    selected: list[dict] = []
    for i in range(n_windows):
        w_start = start0 + i * step
        w_end = w_start + window
        for s in transcript:
            if s["end"] < w_start:
                continue
            if s["start"] > w_end:
                break
            selected.append(s)
    seen = set()
    unique = []
    for s in selected:
        key = (round(s["start"], 1), s["text"][:40])
        if key not in seen:
            seen.add(key)
            unique.append(s)
    lines = [f"{s['start']:.1f}-{s['end']:.1f}: {s['text']}" for s in unique]
    text = "\n".join(lines)
    if len(text) > max_chars:
        half = max_chars // 2
        text = text[:half] + "\n...[middle truncated]...\n" + text[-half:]
    return text


def pick_best_clips(transcript: list[dict], video_title: str = "stream", max_clips: int = 10,
                    streamer: str = "") -> list[dict]:
    if not transcript:
        return []
    sampled = _sample_transcript_for_ai(transcript)
    if not sampled.strip():
        return []
    duration_h = (transcript[-1]["end"] - transcript[0]["start"]) / 3600.0
    print(f"[i] Transcript spans ~{duration_h:.1f}h, sending sampled text ({len(sampled)} chars) to AI")

    prompt = f"""You are an expert GTA 6 / Grand Theft Auto VI YouTube Shorts editor and viral clipper.
From this transcript of a stream{(" by " + streamer) if streamer else ""} titled "{video_title}" (approx {duration_h:.1f} hours long), pick the {max_clips} BEST moments for viral YouTube Shorts.

Prioritize:
1. Police chases / wanted chaos
2. Epic crashes, stunts, ragdoll fails
3. Funny NPC / unexpected reactions
4. Heist / mission tension with payoff
5. Streamer reaction + action
6. Discovery / "wait you can do that" moments
7. Skillful driving/shooting

Rules:
- Each clip MUST be 30–40 seconds long (end - start between 30 and 40). Clear HOOK in first 1–2s and PAYOFF before end.
- Start a second or two BEFORE the key action so the setup is understood; end right after the payoff/reaction.
- Open on action/reaction — never quiet driving or menus.
- Spread across the stream (early/mid/late).
- Titles search-friendly, prefer starting with "GTA 6".
- AVOID story spoilers (major plot twists, mission endings, character deaths, cutscene content). If a moment contains one, set "spoiler": true.
- Return ONLY a valid JSON array. Each item has:
  "start", "end" (seconds), "title", "reason",
  "hook": on-screen text, max 6 words, a curiosity line (NOT the streamer's words),
  "style": one of "voiceover" | "text_story" | "comparison",
     (voiceover = narrator sets up the moment; text_story = on-screen text tells the story;
      comparison = frame it as GTA 6 vs GTA 5 / expectation vs reality),
  "voiceover": 1-2 short sentences (max 20 words) a narrator says BEFORE the action to set up why it matters,
     in {VOICEOVER_LANGUAGE}. Add real commentary, never just describe what is visible,
  "spoiler": true or false.

Transcript (sampled):
{sampled}
"""
    ai_clips = _ask_for_clips(prompt, transcript, max_clips)
    if ai_clips:
        return ai_clips[:max_clips]
    if os.getenv("CLIP_HEURISTIC_FALLBACK", "1") != "0":
        print("[!] AI gave no usable clips - falling back to keyword/energy based picking "
              "(set CLIP_HEURISTIC_FALLBACK=0 to disable)", flush=True)
        return heuristic_clips(transcript, max_clips)
    return []


# --------------------------------------------------------------------------- parsing helpers

def parse_json_array(text: str) -> list:
    """Parse a JSON array from model output. Handles ```fences```, {"clips": [...]}, and truncated output."""
    text = (text or "").strip()
    if not text:
        return []
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    return v
            return []
        if isinstance(data, list):
            return data
    except Exception:
        pass
    start = text.find("[")
    if start == -1:
        return []
    end = text.rfind("]")
    try:
        if end > start:
            return json.loads(text[start:end + 1])
    except Exception:
        pass
    cut = text.rfind("}")
    while cut > start:            # truncated output: keep every complete {...} object
        try:
            return json.loads(text[start:cut + 1] + "]")
        except Exception:
            cut = text.rfind("}", start, cut)
    return []


def to_seconds(v) -> float | None:
    """123, "123.5", "12:34", "1:02:03" -> seconds."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip().lower().rstrip("s").strip()
    if not t:
        return None
    try:
        if ":" in t:
            parts = [float(p) for p in t.split(":")]
            sec = 0.0
            for p in parts:
                sec = sec * 60 + p
            return sec
        return float(t)
    except ValueError:
        return None


def _normalize_clip(c) -> dict | None:
    if not isinstance(c, dict):
        return None
    def first(*keys):
        for k in keys:
            if k in c and c[k] not in (None, ""):
                return c[k]
        return None
    st = to_seconds(first("start", "start_time", "start_seconds", "begin"))
    en = to_seconds(first("end", "end_time", "end_seconds", "stop"))
    if en is None and st is not None:
        d = to_seconds(first("duration", "length"))
        en = st + d if d else None
    if st is None or en is None or en <= st:
        return None
    out = dict(c)
    out["start"], out["end"] = st, en
    return out


def _overlap_ratio(a: dict, b: dict) -> float:
    inter = min(a["end"], b["end"]) - max(a["start"], b["start"])
    return max(0.0, inter) / max(1.0, min(a["end"] - a["start"], b["end"] - b["start"]))


def _clean_clips(items: list, transcript: list[dict], max_clips: int) -> list[dict]:
    t0, t1 = transcript[0]["start"], transcript[-1]["end"]
    norm = [n for n in (_normalize_clip(c) for c in items) if n]
    print(f"[i] AI returned {len(items)} item(s), {len(norm)} had valid start/end", flush=True)
    inside = []
    for c in norm:
        if c["end"] < t0 - 5 or c["start"] > t1 + 5:
            continue
        c["start"] = max(c["start"], t0)
        c["end"] = min(c["end"], t1)
        inside.append(c)
    if len(inside) < len(norm):
        print(f"[i] Dropped {len(norm) - len(inside)} clip(s) outside the transcript range", flush=True)
    uniq: list[dict] = []
    for c in inside:
        if all(_overlap_ratio(c, u) < 0.3 for u in uniq):
            uniq.append(c)
    # spoilers: drop them only if enough clean clips remain (the AI over-flags this a lot)
    clean = [c for c in uniq if not c.get("spoiler")]
    spoilers = len(uniq) - len(clean)
    if spoilers:
        if len(clean) >= min(3, max_clips):
            print(f"[i] Dropped {spoilers} clip(s) flagged as story spoilers", flush=True)
            uniq = clean
        else:
            print(f"[i] {spoilers} clip(s) flagged as spoilers but too few others - keeping them", flush=True)
    return uniq


def _ask_for_clips(prompt: str, transcript: list[dict], max_clips: int) -> list[dict]:
    keys = get_api_keys()
    if not keys:
        print("[!] GEMINI_API_KEY / GEMINI_API_KEYS missing - cannot select clips")
        return []
    try:
        from google.genai import types as _t
        cfg = _t.GenerateContentConfig(response_mime_type="application/json")
    except Exception:
        cfg = None

    for attempt in (1, 2):
        text, last_err = "", None
        key = next_usable_key() or keys[0]
        tried: set[str] = set()
        while key and key not in tried:
            tried.add(key)
            try:
                client = make_client(key)
                resp = gemini_generate(client, prompt, config=cfg)
                text = (resp.text or "").strip()
                break
            except GeminiQuotaExhausted as e:
                last_err = e
                nxt = next_usable_key(key)
                print(f"[!] {e}" + (f" Switching to {key_label(nxt)}" if nxt else ""), flush=True)
                key = nxt
            except Exception as e:
                last_err = e
                nxt = next_usable_key(key)
                if nxt and nxt not in tried:
                    print(f"[!] {key_label(key)} failed ({type(e).__name__}: {str(e)[:120]}) - trying {key_label(nxt)}", flush=True)
                    key = nxt
                    continue
                break
        if not text:
            print(f"[!] Gemini API failure (attempt {attempt}): {type(last_err).__name__}: {str(last_err)[:300]}", flush=True)
            return []
        items = parse_json_array(text)
        if not items:
            print(f"[!] Could not parse clips from Gemini (attempt {attempt}). Raw response head: {text[:300]!r}", flush=True)
            prompt += "\n\nIMPORTANT: reply with ONLY a JSON array, no prose, no markdown. Keep each item short."
            continue
        clips = _clean_clips(items, transcript, max_clips)
        if clips:
            return clips
        prompt += "\n\nIMPORTANT: use numeric seconds for start/end that exist in the transcript above."
    return []


# --------------------------------------------------------------------------- no-AI fallback

_HYPE = ("bro", "no way", "oh my god", "omg", "wait", "yo ", "bruh", "crazy", "insane", "holy",
         "help", "run", "police", "cops", "crash", "jump", "let's go", "lets go", "ahh", "aah",
         "dude", "sheesh", "what the", "are you serious", "legend", "dead", "shoot", "chase")


def heuristic_clips(transcript: list[dict], max_clips: int, length: float = 36.0) -> list[dict]:
    """Pick the most 'energetic' windows using hype words, !, ?, sound notes and speech density."""
    if not transcript:
        return []
    t0, t1 = transcript[0]["start"], transcript[-1]["end"]
    step = 5
    nbins = int((t1 - t0) // step) + 2
    bins = [0.0] * nbins
    for s in transcript:
        txt = (s.get("text") or "").lower()
        score = sum(txt.count(h) for h in _HYPE) * 2 + txt.count("!") * 2 + txt.count("?") \
            + (3 if "[" in txt else 0) + len(txt.split()) / 20
        bins[min(nbins - 1, int((s["start"] - t0) // step))] += score
    w = int(length // step)
    scored = []
    for i in range(0, max(1, nbins - w)):
        scored.append((sum(bins[i:i + w]), t0 + i * step))
    scored.sort(reverse=True)
    picked: list[dict] = []
    floor = max(3.0, 0.25 * scored[0][0]) if scored else 0
    for sc, st in scored:
        if sc < floor or len(picked) >= max_clips:
            break
        start = max(t0, st - 2)
        end = min(t1, start + length)
        if end - start < 25:
            continue
        if all(abs(start - p["start"]) >= length for p in picked):
            picked.append({
                "start": start, "end": end,
                "title": f"Wild Moment {len(picked) + 1}",
                "hook": "WAIT FOR IT", "style": "text_story",
                "reason": f"heuristic score {sc:.0f}", "spoiler": False,
            })
    print(f"[i] Heuristic picker chose {len(picked)} clip(s)", flush=True)
    return picked


VOICEOVER_LANGUAGE = os.getenv("VOICEOVER_LANGUAGE", "English")
