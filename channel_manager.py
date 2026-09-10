"""
AI Channel Manager — Gemini-only agent.

Runs as a SEPARATE, once-a-day check and does:

  1. Retention analysis: samples recent public videos' audience-retention
     curves to find where viewers tend to drop off, written to
     assets/retention_insights.json (read by researcher.py's prompt).

  2. Gathers a snapshot of every tracked video (status, schedule, title,
     description, tags, views, retention).

  3. Sends that snapshot to GEMINI, which proposes zero or more specific
     actions (publish a stuck video, rewrite an underperformer's metadata)
     with its reasoning.

  4. Applies approved actions (dry-run by default).

  5. Warns if the pipeline has gone quiet for 36+ hours.

═══════════════════════════════════════════════════════════════════════
SAFETY: DRY-RUN BY DEFAULT
═══════════════════════════════════════════════════════════════════════
This script only LOGS what it would change unless the environment variable
CHANNEL_MANAGER_APPLY_CHANGES is set to "true". A hard cap
(_MAX_AGENT_ACTIONS_PER_RUN) also limits how many actions can be applied
in a single run.

REQUIRES:
  - GEMINI_API_KEY        (already used elsewhere in this project)
  - YouTube OAuth secrets (same as the main pipeline)
"""

import os
import json
import re
import traceback
from pathlib import Path
from datetime import datetime, timedelta, timezone
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google import genai
import config
try:
    import auto_improver
except Exception:
    auto_improver = None
from thumbnail_generator import generate_thumbnail

APPLY_CHANGES = os.getenv("CHANNEL_MANAGER_APPLY_CHANGES", "false").lower() == "true"

_LOG_FILE = config.ASSETS_DIR / "published_log.json"
_ACTIONS_LOG_FILE = config.ASSETS_DIR / "channel_manager_actions.json"
_RETENTION_INSIGHTS_FILE = config.ASSETS_DIR / "retention_insights.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

GEMINI_MODEL = "gemini-3.5-flash"

_MIN_AGE_HOURS_FOR_REVIEW = 48
_MAX_AGE_DAYS_FOR_REVIEW = 21
_MAX_AGENT_ACTIONS_PER_RUN = 3  # safety cap so one bad run can't rewrite the whole channel, even with both models agreeing


def _authenticate():
    # Deliberately NOT passing scopes= here — see the note in
    # youtube_uploader.py's authenticate_youtube() for why.
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    creds.refresh(Request())
    return creds


def _log_action(action: dict):
    try:
        entries = []
        if _ACTIONS_LOG_FILE.is_file():
            entries = json.loads(_ACTIONS_LOG_FILE.read_text())
        action["timestamp"] = datetime.now(timezone.utc).isoformat()
        action["applied"] = APPLY_CHANGES
        entries.append(action)
        entries = entries[-300:]
        _ACTIONS_LOG_FILE.write_text(json.dumps(entries, indent=2))
    except Exception:
        pass  # logging must never break the actual check


def _check_channel_activity():
    """Warns (doesn't/can't auto-fix) if no video has been published recently
    — the most likely cause is the daily GitHub Actions run failing silently."""
    try:
        if not _LOG_FILE.is_file():
            return
        entries = json.loads(_LOG_FILE.read_text())
        if not entries:
            return
        last_published = max(datetime.fromisoformat(e["published_at"]) for e in entries)
        hours_since = (datetime.now(timezone.utc) - last_published).total_seconds() / 3600
        if hours_since > 36:
            print(f"[!!] WARNING: no video has been published in {hours_since:.0f} hours. "
                  f"Check the GitHub Actions run history for failures — the daily pipeline "
                  f"may have broken silently.")
    except Exception:
        pass


def _parse_iso8601_duration_seconds(duration: str) -> float:
    """Parses YouTube's ISO 8601 video duration format (e.g. 'PT45S',
    'PT1M5S') into seconds. Returns 0 if it can't be parsed."""
    match = re.match(r"PT(?:(\d+)M)?(?:(\d+)S)?", duration or "")
    if not match:
        return 0
    minutes = int(match.group(1) or 0)
    seconds = int(match.group(2) or 0)
    return minutes * 60 + seconds


def _analyze_retention_dropoff(youtube, analytics, video_ids: list):
    """
    Looks at audience retention curves for recent public videos and finds
    roughly WHERE (in seconds) viewers tend to drop off the hardest. Writes
    that as a plain-language note to assets/retention_insights.json, which
    researcher.py reads and folds into the script prompt. Best-effort: any
    failure here just means no retention guidance gets added, nothing more.
    """
    try:
        now = datetime.now(timezone.utc)
        dropoff_points = []

        checked = 0
        for video_id in video_ids:
            if checked >= 8:
                break
            try:
                details = youtube.videos().list(part="status,contentDetails", id=video_id).execute()
                items = details.get("items", [])
                if not items or items[0]["status"].get("privacyStatus") != "public":
                    continue
                duration_s = _parse_iso8601_duration_seconds(items[0]["contentDetails"].get("duration", ""))
                if duration_s <= 0:
                    continue

                resp = analytics.reports().query(
                    ids="channel==MINE",
                    startDate="2020-01-01",
                    endDate=now.date().isoformat(),
                    metrics="audienceWatchRatio",
                    dimensions="elapsedVideoTimeRatio",
                    filters=f"video=={video_id}",
                ).execute()
                rows = resp.get("rows", [])
                if len(rows) < 3:
                    continue
                checked += 1

                biggest_drop, drop_at_ratio = 0, None
                for i in range(1, len(rows)):
                    prev_ratio = float(rows[i - 1][1])
                    curr_ratio = float(rows[i][1])
                    drop = prev_ratio - curr_ratio
                    if drop > biggest_drop:
                        biggest_drop = drop
                        drop_at_ratio = float(rows[i][0])

                if drop_at_ratio is not None and biggest_drop > 0.05:
                    dropoff_points.append(drop_at_ratio * duration_s)
            except Exception:
                continue

        if not dropoff_points:
            print("[i] Retention analysis: not enough data yet for a reliable signal.")
            return

        avg_dropoff_second = sum(dropoff_points) / len(dropoff_points)
        insight = {
            "typical_dropoff_second": round(avg_dropoff_second, 1),
            "sample_size": len(dropoff_points),
            "updated_at": now.isoformat(),
        }
        _RETENTION_INSIGHTS_FILE.write_text(json.dumps(insight, indent=2))
        print(f"[+] Retention insight: viewers tend to drop off hardest around "
              f"{avg_dropoff_second:.1f}s into the video (from {len(dropoff_points)} videos sampled).")

    except Exception as e:
        print(f"[i] Retention analysis skipped (non-fatal): {e}")
        traceback.print_exc()


# ─────────────────────────────────────────────────────────────────────────
# Gemini-only agent
# ─────────────────────────────────────────────────────────────────────────

def _gather_channel_snapshot(youtube, analytics, video_ids: list) -> list:
    """
    Collects one compact data snapshot per tracked video — status,
    schedule, title, description, tags, age, views, retention — gathered
    ONCE here so neither model needs its own YouTube API access; they only
    ever see this snapshot.
    """
    now = datetime.now(timezone.utc)
    snapshot = []

    for video_id in video_ids[:30]:  # cap for API quota + prompt size
        try:
            resp = youtube.videos().list(part="snippet,status", id=video_id).execute()
            items = resp.get("items", [])
            if not items:
                continue
            snippet, status = items[0]["snippet"], items[0]["status"]

            published_at = snippet.get("publishedAt")
            age_hours = None
            if published_at:
                age_hours = round((now - datetime.fromisoformat(published_at.replace("Z", "+00:00"))).total_seconds() / 3600, 1)

            entry = {
                "video_id": video_id,
                "title": snippet.get("title", ""),
                "description": snippet.get("description", "")[:300],
                "tags": snippet.get("tags", []),
                "privacy_status": status.get("privacyStatus"),
                "publish_at": status.get("publishAt"),
                "published_at": published_at,
                "age_hours": age_hours,
                "views": None,
                "average_view_percentage": None,
            }

            if status.get("privacyStatus") == "public":
                try:
                    a_resp = analytics.reports().query(
                        ids="channel==MINE", startDate="2020-01-01",
                        endDate=now.date().isoformat(),
                        metrics="views,averageViewPercentage",
                        filters=f"video=={video_id}",
                    ).execute()
                    rows = a_resp.get("rows", [])
                    if rows:
                        entry["views"] = rows[0][0]
                        entry["average_view_percentage"] = rows[0][1]
                except Exception:
                    pass

            snapshot.append(entry)
        except Exception:
            continue

    return snapshot


def _gemini_propose_actions(snapshot: list) -> list:
    """
    First opinion: Gemini reviews the whole channel snapshot and proposes
    zero or more specific actions, each with its reasoning. This is a
    PROPOSAL only — Gemini never applies anything itself, and every
    proposals are applied directly (with safety caps).
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[i] GEMINI_API_KEY not set — skipping the propose step.")
        return []

    try:
        client = genai.Client(api_key=api_key)
        prompt = (
            "You are one of two AI managers for a personal-finance YouTube Shorts "
            "channel (a second, independent reviewer will check your proposals "
            "before anything is applied — so be honest and specific, not just "
            "agreeable). Review this snapshot of the channel's videos and propose "
            "the specific actions worth taking right now. Only propose an action "
            "when there's a clear, well-justified reason from the actual data — "
            "proposing NOTHING is a perfectly good outcome if nothing needs fixing.\n\n"
            "Consider only these two situations:\n"
            "1. A video with privacy_status 'private' whose publish_at time is "
            "already in the past (a scheduling failure — should be published "
            "immediately).\n"
            "2. A public video, 48+ hours old, with views clearly and "
            "significantly below the rest of the channel (a candidate for a "
            "title/description/tag rewrite to improve discoverability). If you "
            "propose this, write the actual new_title, new_description, and "
            "new_tags — punchier and more discoverable, but NEVER clickbait-"
            "misleading and NEVER implying a guaranteed return or specific "
            "financial promise (this is regulated YMYL content).\n\n"
            "Propose at most 3 actions total.\n\n"
            f"CHANNEL SNAPSHOT:\n{json.dumps(snapshot, indent=2)}\n\n"
            "Return ONLY a valid JSON array (an empty array [] is a fine answer) "
            "of action objects, each with exactly this shape:\n"
            '{"type": "publish_stuck_video", "video_id": "...", "reasoning": "..."}\n'
            "or\n"
            '{"type": "rewrite_metadata", "video_id": "...", "new_title": "...", '
            '"new_description": "...", "new_tags": ["..."], "reasoning": "..."}'
        )
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        raw = (response.text or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
        actions = json.loads(raw)
        return actions if isinstance(actions, list) else []
    except Exception as e:
        print(f"[!] Gemini proposal step failed (non-fatal, no actions this run): {e}")
        traceback.print_exc()
        return []



def _execute_action(youtube, action: dict) -> bool:
    """Applies ONE action that Gemini proposed.
    Respects APPLY_CHANGES (dry-run by default) exactly like the rest of
    this pipeline."""
    action_type = action.get("type")
    video_id = action.get("video_id")
    if not video_id:
        return False

    if action_type == "publish_stuck_video":
        _log_action({"type": "publish_stuck_video", "video_id": video_id,
                      "reasoning": action.get("reasoning", "")})
        if APPLY_CHANGES:
            youtube.videos().update(
                part="status", body={"id": video_id, "status": {"privacyStatus": "public"}}
            ).execute()
            print(f"[✓] Published stuck video {video_id}.")
        else:
            print(f"[i] (dry run) Would publish stuck video {video_id}. "
                  f"Set CHANNEL_MANAGER_APPLY_CHANGES=true to actually apply this.")
        return True

    if action_type == "rewrite_metadata":
        new_title = (action.get("new_title") or "")[:100]
        new_description = action.get("new_description") or ""
        new_tags = action.get("new_tags") or []
        if not new_title:
            return False

        disclaimer = (
            "This video is for educational and informational purposes only and does not "
            "constitute financial advice. Always do your own research or consult a licensed "
            "financial advisor before making financial decisions."
        )
        if disclaimer not in new_description:
            new_description = f"{new_description}\n\n{disclaimer}"

        thumbnail_path = config.OUTPUT_DIR / f"thumb_{video_id}.jpg"
        thumbnail_ready = generate_thumbnail(new_title, thumbnail_path, seed_extra=video_id)

        _log_action({"type": "rewrite_metadata", "video_id": video_id, "new_title": new_title,
                      "new_tags": new_tags, "reasoning": action.get("reasoning", ""),
                      "new_thumbnail_generated": thumbnail_ready})

        if APPLY_CHANGES:
            youtube.videos().update(
                part="snippet",
                body={"id": video_id, "snippet": {
                    "title": new_title, "description": new_description,
                    "tags": new_tags, "categoryId": "27",  # Education
                }},
            ).execute()
            print(f"[✓] Rewrote metadata for {video_id}: '{new_title}'")
            if thumbnail_ready:
                try:
                    youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumbnail_path))).execute()
                    print(f"[✓] Updated thumbnail for {video_id}.")
                except Exception as e:
                    print(f"[!] Could not set thumbnail for {video_id} (often means the channel "
                          f"isn't phone-verified yet — check youtube.com/verify): {e}")
        else:
            print(f"[i] (dry run) Would rewrite {video_id} -> title: '{new_title}', tags: {new_tags}. "
                  f"Set CHANNEL_MANAGER_APPLY_CHANGES=true to actually apply this.")
        return True

    return False


def run_channel_manager():
    print("=" * 60)
    print(f"🤖 AI CHANNEL MANAGER (Gemini-only) — "
          f"{'LIVE (will apply changes)' if APPLY_CHANGES else 'DRY RUN (logging only)'}")
    print("=" * 60)

    _check_channel_activity()

    try:
        creds = _authenticate()
        youtube = build("youtube", "v3", credentials=creds)
        analytics = build("youtubeAnalytics", "v2", credentials=creds)

        if not _LOG_FILE.is_file():
            print("[i] No published-video log yet — nothing to manage.")
            return
        entries = json.loads(_LOG_FILE.read_text())
        video_ids = [e["video_id"] for e in entries if e.get("video_id")]
        if not video_ids:
            return

        _analyze_retention_dropoff(youtube, analytics, video_ids)

        print("\n[+] Gathering channel snapshot for the agent...")
        snapshot = _gather_channel_snapshot(youtube, analytics, video_ids)
        if not snapshot:
            print("[i] No video data available yet.")
            return

        print("[+] Asking Gemini to propose actions...")
        proposed = _gemini_propose_actions(snapshot)
        if not proposed:
            print("[i] Gemini proposed no actions this run.")
            print("=" * 60)
            return
        print(f"[+] Gemini proposed {len(proposed)} action(s).")

        action_cap = _MAX_AGENT_ACTIONS_PER_RUN
        applied = 0
        for action in proposed:
            video_id = action.get("video_id")
            if applied >= action_cap:
                print(f"[i] Reached the {action_cap}-action safety cap for this run, "
                      f"skipping the rest.")
                break
            print(f"[+] Gemini proposed: {action.get('type')} on {video_id} "
                  f"— {action.get('reasoning', '')[:120]}")
            if _execute_action(youtube, action):
                applied += 1

        print(f"\n[✓] Applied {applied} action(s) this run.")

    except KeyError as e:
        print(f"[i] Channel manager skipped — missing env var {e}.")
    except Exception as e:
        print(f"[i] Channel manager skipped (likely needs the yt-analytics.readonly "
              f"re-auth — see analytics_feedback.py): {e}")
        traceback.print_exc()

    # Also run the closed-loop quality improver here (has analytics access).
    try:
        if auto_improver:
            auto_improver.run_auto_improve()
    except Exception as e:
        print(f"[i] Auto-improver skipped (non-fatal): {e}")

    print("=" * 60)


if __name__ == "__main__":
    run_channel_manager()
