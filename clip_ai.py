from __future__ import annotations

from google import genai
import json
import os
from clip_core import MAX_TRANSCRIPT_CHARS_FOR_AI, WINDOWS_TO_SAMPLE, WINDOW_SECONDS


GEMINI_MODELS = [m for m in [
    os.getenv("GEMINI_MODEL", "").strip(),
    "gemini-3.8-flash",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
] if m]


_WORKING_MODEL: str | None = None


def gemini_generate(client, contents):
    """generate_content with automatic fallback across model names on 404/not found."""
    global _WORKING_MODEL
    models = [_WORKING_MODEL] if _WORKING_MODEL else GEMINI_MODELS
    last = None
    for m in models:
        try:
            resp = client.models.generate_content(model=m, contents=contents)
            if _WORKING_MODEL != m:
                print(f"[i] Using Gemini model: {m}")
            _WORKING_MODEL = m
            return resp
        except Exception as e:
            last = e
            msg = str(e)
            if "404" in msg or "NOT_FOUND" in msg or "no longer available" in msg:
                print(f"[!] Model {m} unavailable - trying next")
                continue
            raise
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


def pick_best_clips(transcript: list[dict], video_title: str = "stream", max_clips: int = 10) -> list[dict]:
    if not transcript:
        return []
    sampled = _sample_transcript_for_ai(transcript)
    if not sampled.strip():
        return []
    duration_h = (transcript[-1]["end"] - transcript[0]["start"]) / 3600.0
    print(f"[i] Transcript spans ~{duration_h:.1f}h, sending sampled text ({len(sampled)} chars) to AI")

    prompt = f"""You are an expert GTA 6 / Grand Theft Auto VI YouTube Shorts editor and viral clipper.
From this transcript of a stream titled "{video_title}" (approx {duration_h:.1f} hours long), pick the {max_clips} BEST moments for viral YouTube Shorts.

Prioritize:
1. Police chases / wanted chaos
2. Epic crashes, stunts, ragdoll fails
3. Funny NPC / unexpected reactions
4. Heist / mission tension with payoff
5. Streamer reaction + action
6. Discovery / "wait you can do that" moments
7. Skillful driving/shooting

Rules:
- Each clip 15–45 seconds (sweet spot 25–40s). Clear HOOK in first 1–2s and PAYOFF before end.
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
     in {VO_LANG}. Add real commentary, never just describe what is visible,
  "spoiler": true or false.

Transcript (sampled):
{sampled}
""".replace("{VO_LANG}", VOICEOVER_LANGUAGE)
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        print("[!] GEMINI_API_KEY missing — cannot select clips")
        return []
    try:
        client = genai.Client(api_key=api_key)
        resp = gemini_generate(client, prompt)
        text = (resp.text or "").strip()
    except Exception as e:
        print(f"[!] Gemini API failure: {e}")
        return []

    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    try:
        clips = json.loads(text)
        valid = [c for c in clips if isinstance(c, dict) and "start" in c and "end" in c]
        safe = [c for c in valid if not c.get("spoiler")]
        if len(safe) < len(valid):
            print(f"[i] Dropped {len(valid) - len(safe)} clip(s) flagged as story spoilers")
        return safe[:max_clips]
    except Exception as e:
        print(f"[!] AI parse failed: {e}")
        print(f"[!] Raw AI response (first 400 chars): {text[:400]}")
        return []


VOICEOVER_LANGUAGE = os.getenv("VOICEOVER_LANGUAGE", "English")
