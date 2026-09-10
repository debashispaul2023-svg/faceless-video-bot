"""
Auto-Improver — closed-loop quality improvement for the faceless video bot.

Cycle (runs once per day, after analytics):
  1. ANALYZE   recent published videos (views, retention, engagement)
  2. DIAGNOSE  top problems (weak hook, caption style, pacing, visuals…)
  3. RESEARCH  what specific change would fix each problem
  4. FIX       write concrete rules/params into assets/active_improvements.json
  5. REPEAT    next day's generation reads those improvements

researcher.py and video_renderer.py read active_improvements.json and apply
the current rules. History is kept so the loop can see what was already tried.

SAFE BY DEFAULT:
  - Never blocks the main pipeline on failure
  - Only updates a small set of bounded parameters / prompt clauses
  - Keeps a rolling history; won't re-apply the same failed fix forever
"""

from __future__ import annotations

import json
import os
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google import genai
from google.genai import types

import config

_LOG_FILE = config.ASSETS_DIR / "published_log.json"
_RETENTION_FILE = config.ASSETS_DIR / "retention_insights.json"
_TOP_PATTERNS_FILE = config.ASSETS_DIR / "top_performing_patterns.json"
_IMPROVEMENTS_FILE = config.ASSETS_DIR / "active_improvements.json"
_HISTORY_FILE = config.ASSETS_DIR / "improvement_history.json"

# Defaults that match current video_renderer.py / researcher.py behavior.
# The improver only overrides keys it is confident about.
_DEFAULT_IMPROVEMENTS = {
    "version": 0,
    "updated_at": None,
    "diagnosis_summary": "baseline — no auto-improvements applied yet",
    "script_rules": [],          # list of short instruction strings injected into the prompt
    "caption": {
        "font_size": 70,
        "chunk_size": 3,
        "emphasis_font_size": 94,
        "y_ratio": 0.70,
    },
    "editing": {
        "crossfade_duration": 0.18,
        "ken_burns_zoom": 0.22,
        "punch_in_duration": 0.18,
        "punch_in_overshoot": 0.10,
    },
    "visual_rules": [],          # extra visual-direction lines for the prompt
    "problems_addressed": [],
}


def _load_json(path: Path, default):
    try:
        if path.is_file():
            return json.loads(path.read_text())
    except Exception:
        pass
    return default


def _save_json(path: Path, data):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def load_active_improvements() -> dict:
    """Public helper — other modules call this to get current tunable settings."""
    data = _load_json(_IMPROVEMENTS_FILE, None)
    if not data:
        return dict(_DEFAULT_IMPROVEMENTS)
    # Merge over defaults so missing keys never break callers
    merged = dict(_DEFAULT_IMPROVEMENTS)
    merged.update({k: v for k, v in data.items() if k in merged or k in (
        "version", "updated_at", "diagnosis_summary", "script_rules",
        "caption", "editing", "visual_rules", "problems_addressed"
    )})
    if isinstance(data.get("caption"), dict):
        merged["caption"] = {**_DEFAULT_IMPROVEMENTS["caption"], **data["caption"]}
    if isinstance(data.get("editing"), dict):
        merged["editing"] = {**_DEFAULT_IMPROVEMENTS["editing"], **data["editing"]}
    return merged


def _gather_performance_snapshot() -> dict:
    """Build a compact snapshot of recent channel performance for Gemini."""
    log = _load_json(_LOG_FILE, [])
    retention = _load_json(_RETENTION_FILE, {})
    patterns = _load_json(_TOP_PATTERNS_FILE, {})
    current = load_active_improvements()
    history = _load_json(_HISTORY_FILE, {"runs": []})

    recent = log[-12:] if log else []
    return {
        "recent_videos": [
            {
                "video_id": e.get("video_id"),
                "title": e.get("title"),
                "category": e.get("category"),
                "hook_technique": e.get("hook_technique"),
                "prompt_version": e.get("prompt_version"),
                "published_at": e.get("published_at"),
            }
            for e in recent
        ],
        "retention_insight": retention,
        "top_patterns": patterns.get("patterns", [])[:3],
        "version_scores": patterns.get("version_scores", {}),
        "current_improvements": {
            "version": current.get("version"),
            "script_rules": current.get("script_rules", []),
            "caption": current.get("caption"),
            "editing": current.get("editing"),
            "visual_rules": current.get("visual_rules", []),
            "problems_addressed": current.get("problems_addressed", []),
        },
        "recent_improvement_history": history.get("runs", [])[-5:],
    }


def _call_gemini_diagnose_and_fix(snapshot: dict) -> dict | None:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[i] Auto-improver: GEMINI_API_KEY missing — skipped.")
        return None

    client = genai.Client(api_key=api_key)

    prompt = f"""
You are an expert YouTube Shorts growth engineer for a faceless personal-finance channel.
Your job is the closed loop: ANALYZE → FIND PROBLEMS → RESEARCH FIXES → APPLY.

CHANNEL SNAPSHOT (JSON):
{json.dumps(snapshot, indent=2)}

CONTEXT:
- Typical drop-off is around the first 6 seconds (hook/visual failure is the #1 killer).
- Many videos get very low views; swipe-away rates above 80% are common.
- Current pipeline: Gemini scripts, ElevenLabs/Edge TTS, stock+AI visuals, karaoke captions,
  Ken Burns + punch-in editing, fixed 5 PM IST daily upload.

TASK:
1. Identify the TOP 1-3 concrete problems hurting retention / views right now.
2. For each problem, propose a SPECIFIC fix that can be applied in the next generation run.
3. Only change things that are safe and bounded (see allowed fields below).
4. Do NOT repeat fixes that already appear in recent_improvement_history unless data shows they failed.

ALLOWED OUTPUT FIELDS (return ONLY valid JSON, no markdown):
{{
  "diagnosis_summary": "1-2 sentence overall diagnosis",
  "problems_addressed": ["short problem labels"],
  "script_rules": [
    "short imperative rules to inject into the scriptwriting prompt (max 5 rules, each under 25 words)"
  ],
  "visual_rules": [
    "short visual-direction rules for the HOOK and key scenes (max 4 rules)"
  ],
  "caption": {{
    "font_size": <int 60-90>,
    "chunk_size": <int 2-4>,
    "emphasis_font_size": <int 80-110>,
    "y_ratio": <float 0.62-0.75>
  }},
  "editing": {{
    "crossfade_duration": <float 0.10-0.30>,
    "ken_burns_zoom": <float 0.12-0.35>,
    "punch_in_duration": <float 0.12-0.28>,
    "punch_in_overshoot": <float 0.06-0.18>
  }},
  "reasoning": "brief why these changes should improve the 0-6s retention"
}}

Rules for good fixes:
- If drop-off is ~6s: strengthen HOOK visual + first spoken sentence (script_rules + visual_rules).
- If captions feel slow/cluttered: lower chunk_size or raise font_size slightly.
- If editing feels soft: raise punch_in_overshoot / ken_burns_zoom a bit.
- Prefer fewer, stronger rules over many weak ones.
- Keep values inside the ranges above.
"""

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                http_options=types.HttpOptions(timeout=45000)
            ),
        )
        raw = (response.text or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
        data = json.loads(raw)
        if not isinstance(data, dict):
            return None
        return data
    except Exception as e:
        print(f"[!] Auto-improver Gemini call failed: {e}")
        traceback.print_exc()
        return None


def _clamp_improvements(proposed: dict) -> dict:
    """Keep every numeric param inside safe bounds."""
    out = dict(_DEFAULT_IMPROVEMENTS)

    out["diagnosis_summary"] = str(proposed.get("diagnosis_summary") or out["diagnosis_summary"])[:400]
    out["problems_addressed"] = [
        str(p)[:80] for p in (proposed.get("problems_addressed") or [])[:5]
    ]
    out["script_rules"] = [
        str(r)[:160] for r in (proposed.get("script_rules") or [])[:5]
    ]
    out["visual_rules"] = [
        str(r)[:160] for r in (proposed.get("visual_rules") or [])[:4]
    ]

    cap = proposed.get("caption") or {}
    out["caption"] = {
        "font_size": max(60, min(90, int(cap.get("font_size", 70)))),
        "chunk_size": max(2, min(4, int(cap.get("chunk_size", 3)))),
        "emphasis_font_size": max(80, min(110, int(cap.get("emphasis_font_size", 94)))),
        "y_ratio": max(0.62, min(0.75, float(cap.get("y_ratio", 0.70)))),
    }

    ed = proposed.get("editing") or {}
    out["editing"] = {
        "crossfade_duration": max(0.10, min(0.30, float(ed.get("crossfade_duration", 0.18)))),
        "ken_burns_zoom": max(0.12, min(0.35, float(ed.get("ken_burns_zoom", 0.22)))),
        "punch_in_duration": max(0.12, min(0.28, float(ed.get("punch_in_duration", 0.18)))),
        "punch_in_overshoot": max(0.06, min(0.18, float(ed.get("punch_in_overshoot", 0.10)))),
    }
    return out


def run_auto_improve() -> None:
    """
    Main entry: analyze → diagnose → fix → persist.
    Safe no-op on any failure so it never breaks the daily pipeline.
    """
    print("=" * 60)
    print("🔧 AUTO-IMPROVER — analyze → diagnose → fix → repeat")
    print("=" * 60)

    try:
        snapshot = _gather_performance_snapshot()
        if not snapshot.get("recent_videos"):
            print("[i] Auto-improver: no published videos yet — nothing to learn from.")
            return

        print(f"[+] Snapshot ready ({len(snapshot['recent_videos'])} recent videos).")
        print("[+] Asking Gemini to diagnose problems and propose fixes...")

        proposed = _call_gemini_diagnose_and_fix(snapshot)
        if not proposed:
            print("[i] Auto-improver: no proposal returned — keeping previous improvements.")
            return

        current = load_active_improvements()
        fixed = _clamp_improvements(proposed)
        fixed["version"] = int(current.get("version") or 0) + 1
        fixed["updated_at"] = datetime.now(timezone.utc).isoformat()

        _save_json(_IMPROVEMENTS_FILE, fixed)

        history = _load_json(_HISTORY_FILE, {"runs": []})
        history.setdefault("runs", []).append({
            "version": fixed["version"],
            "updated_at": fixed["updated_at"],
            "diagnosis_summary": fixed["diagnosis_summary"],
            "problems_addressed": fixed["problems_addressed"],
            "script_rules": fixed["script_rules"],
            "visual_rules": fixed["visual_rules"],
            "caption": fixed["caption"],
            "editing": fixed["editing"],
            "reasoning": str(proposed.get("reasoning", ""))[:300],
        })
        history["runs"] = history["runs"][-30:]  # bound history
        _save_json(_HISTORY_FILE, history)

        print(f"[✓] Improvements v{fixed['version']} saved.")
        print(f"    Diagnosis: {fixed['diagnosis_summary']}")
        print(f"    Problems: {fixed['problems_addressed']}")
        print(f"    Script rules: {len(fixed['script_rules'])}")
        print(f"    Visual rules: {len(fixed['visual_rules'])}")
        print(f"    Caption: {fixed['caption']}")
        print(f"    Editing: {fixed['editing']}")
    except Exception as e:
        print(f"[!] Auto-improver failed (non-fatal): {e}")
        traceback.print_exc()

    print("=" * 60)


if __name__ == "__main__":
    run_auto_improve()
