"""
AI Channel Manager.

Runs as a SEPARATE, once-a-day check (not on every video-generation run —
see the workflow note at the bottom of this file) and does three things:

  1. Detects "stuck" schedules: a video that was scheduled to go public at
     some past time but is STILL sitting private (a scheduling failure on
     YouTube's side, or a bug in our own code) — and publishes it
     immediately so it doesn't sit invisible forever.

  2. Detects underperforming videos (published 48h+ ago, views well below
     the channel's own recent median) and uses Gemini to rewrite a punchier
     title/description, then updates it on YouTube.

  3. Checks whether the pipeline has gone quiet (no successful upload in the
     last ~36 hours) and prints a clear warning in the Actions log — this is
     as close to "keep the channel from going dormant" as code run once a
     day CAN safely do; it cannot fix a broken Actions schedule by itself,
     but it makes the problem visible instead of silent.

═══════════════════════════════════════════════════════════════════════
SAFETY: DRY-RUN BY DEFAULT
═══════════════════════════════════════════════════════════════════════
This script only LOGS what it would change unless the environment variable
CHANNEL_MANAGER_APPLY_CHANGES is set to "true". Live-editing a real
channel's titles/descriptions is not something that should run unattended
and unreviewed on day one — watch a few dry-run logs first, then flip it on.

Requires the SAME extra OAuth scope as analytics_feedback.py
(yt-analytics.readonly) — see that file's docstring for the one-time setup.
"""

import os
import json
import traceback
from pathlib import Path
from datetime import datetime, timedelta, timezone
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from google import genai
import config

APPLY_CHANGES = os.getenv("CHANNEL_MANAGER_APPLY_CHANGES", "false").lower() == "true"

_LOG_FILE = config.ASSETS_DIR / "published_log.json"
_ACTIONS_LOG_FILE = config.ASSETS_DIR / "channel_manager_actions.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

# How far below the channel's own median a video has to fall to count as
# "underperforming" (0.4 = less than 40% of the median views).
_UNDERPERFORM_RATIO = 0.4
_MIN_AGE_HOURS_FOR_REVIEW = 48
_MAX_AGE_DAYS_FOR_REVIEW = 21
_MAX_EDITS_PER_RUN = 3  # a safety cap so one bad run can't rewrite the whole channel


def _authenticate():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
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


def _fix_stuck_schedules(youtube, video_ids: list):
    """Publishes any video whose scheduled publishAt has already passed but
    is still sitting private — a self-heal for scheduling failures."""
    now = datetime.now(timezone.utc)
    fixed = 0

    for video_id in video_ids:
        try:
            resp = youtube.videos().list(part="status", id=video_id).execute()
            items = resp.get("items", [])
            if not items:
                continue
            status = items[0]["status"]
            publish_at = status.get("publishAt")
            if status.get("privacyStatus") != "private" or not publish_at:
                continue

            scheduled = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
            if scheduled < now:
                print(f"[!] Video {video_id} was scheduled for {publish_at} but is still "
                      f"private — this looks like a stuck/failed schedule.")
                _log_action({"type": "fix_stuck_schedule", "video_id": video_id,
                             "was_scheduled_for": publish_at})
                if APPLY_CHANGES:
                    youtube.videos().update(
                        part="status",
                        body={"id": video_id, "status": {"privacyStatus": "public"}}
                    ).execute()
                    print(f"[✓] Published {video_id} immediately to fix the stuck schedule.")
                    fixed += 1
                else:
                    print(f"[i] (dry run) Would publish {video_id} immediately. Set "
                          f"CHANNEL_MANAGER_APPLY_CHANGES=true to actually apply this.")
        except Exception as e:
            print(f"[!] Could not check/fix schedule for {video_id}: {e}")

    return fixed


def _rewrite_underperformer(youtube, gemini_client, video_id: str, old_title: str,
                             old_description: str, old_tags: list):
    """Asks Gemini for a punchier title/description/tags and applies all three."""
    try:
        prompt = (
            "You are a YouTube Shorts optimization expert for a US personal-finance channel. "
            "This video is underperforming. Rewrite its title, description, AND tags to be "
            "punchier and more discoverable WITHOUT being clickbait-misleading and WITHOUT "
            "making any financial promises or recommending specific investments (keep it "
            "educational, compliant, general). Tags should be 8-15 short, relevant keywords "
            "(no # symbols). Return ONLY valid JSON: "
            '{"title": "...", "description": "...", "tags": ["...", "..."]}\n\n'
            f"Current title: {old_title}\n"
            f"Current description: {old_description}\n"
            f"Current tags: {', '.join(old_tags) if old_tags else '(none)'}"
        )
        response = gemini_client.models.generate_content(model="gemini-3.5-flash", contents=prompt)
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
        new_meta = json.loads(raw)
        new_title = new_meta.get("title", old_title)[:100]
        new_description = new_meta.get("description", old_description)
        new_tags = new_meta.get("tags", old_tags) or old_tags

        _log_action({
            "type": "rewrite_metadata", "video_id": video_id,
            "old_title": old_title, "new_title": new_title,
            "old_tags": old_tags, "new_tags": new_tags,
        })

        if APPLY_CHANGES:
            youtube.videos().update(
                part="snippet",
                body={
                    "id": video_id,
                    "snippet": {
                        "title": new_title,
                        "description": new_description,
                        "tags": new_tags,
                        "categoryId": "27",  # Education
                    },
                },
            ).execute()
            print(f"[✓] Updated title/description/tags for underperforming video {video_id}: '{new_title}'")
        else:
            print(f"[i] (dry run) Would rewrite {video_id} -> title: '{new_title}', "
                  f"tags: {new_tags}. Set CHANNEL_MANAGER_APPLY_CHANGES=true to actually apply this.")
        return True
    except Exception as e:
        print(f"[!] Could not rewrite metadata for {video_id}: {e}")
        traceback.print_exc()
        return False


# For a brand-new channel with too few videos to compute a reliable
# median, fall back to this absolute "clearly not getting watched" bar.
_ABSOLUTE_LOW_VIEW_FALLBACK = 15


def _check_underperformers(youtube, analytics, video_ids: list):
    """Finds videos that are old enough to judge and clearly underperforming
    (either well below the channel's own median, or below an absolute
    low-view bar when there aren't enough videos yet for a fair median),
    and rewrites up to _MAX_EDITS_PER_RUN of them."""
    now = datetime.now(timezone.utc)
    stats = []

    for video_id in video_ids:
        try:
            resp = youtube.videos().list(part="snippet,status", id=video_id).execute()
            items = resp.get("items", [])
            if not items:
                continue
            snippet = items[0]["snippet"]
            status = items[0]["status"]
            if status.get("privacyStatus") != "public":
                continue

            published = datetime.fromisoformat(snippet["publishedAt"].replace("Z", "+00:00"))
            age_hours = (now - published).total_seconds() / 3600
            if age_hours < _MIN_AGE_HOURS_FOR_REVIEW or age_hours > _MAX_AGE_DAYS_FOR_REVIEW * 24:
                continue

            analytics_resp = analytics.reports().query(
                ids="channel==MINE",
                startDate="2020-01-01",
                endDate=now.date().isoformat(),
                metrics="views",
                filters=f"video=={video_id}",
            ).execute()
            rows = analytics_resp.get("rows", [])
            views = rows[0][0] if rows else 0

            stats.append({
                "video_id": video_id, "views": views,
                "title": snippet.get("title", ""),
                "description": snippet.get("description", ""),
                "tags": snippet.get("tags", []),
            })
        except Exception as e:
            print(f"[!] Could not fetch stats for {video_id}: {e}")

    if not stats:
        print("[i] No eligible videos yet (nothing public & 48h+ old) to review.")
        return

    if len(stats) < 3:
        # Not enough videos for a fair median — use the absolute low-view
        # bar instead of giving up entirely.
        print(f"[i] Only {len(stats)} eligible video(s) so far — using an absolute "
              f"low-view bar ({_ABSOLUTE_LOW_VIEW_FALLBACK}) instead of a channel median.")
        underperformers = [s for s in stats if s["views"] < _ABSOLUTE_LOW_VIEW_FALLBACK]
    else:
        view_counts = sorted(s["views"] for s in stats)
        median_views = view_counts[len(view_counts) // 2]
        threshold = max(median_views * _UNDERPERFORM_RATIO, _ABSOLUTE_LOW_VIEW_FALLBACK)
        print(f"[i] Channel median views (eligible videos): {median_views}, "
              f"underperform threshold: {threshold:.0f}.")
        underperformers = [s for s in stats if s["views"] < threshold]

    if not underperformers:
        print("[i] No clearly underperforming videos found this run.")
        return

    print(f"[i] Found {len(underperformers)} underperformer(s), rewriting up to "
          f"{_MAX_EDITS_PER_RUN} of them.")

    gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    for entry in underperformers[:_MAX_EDITS_PER_RUN]:
        _rewrite_underperformer(youtube, gemini_client, entry["video_id"],
                                 entry["title"], entry["description"], entry["tags"])


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


def run_channel_manager():
    print("=" * 60)
    print(f"🤖 AI CHANNEL MANAGER — {'LIVE (will apply changes)' if APPLY_CHANGES else 'DRY RUN (logging only)'}")
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

        fixed = _fix_stuck_schedules(youtube, video_ids)
        if fixed:
            print(f"[✓] Fixed {fixed} stuck schedule(s).")

        _check_underperformers(youtube, analytics, video_ids)

    except KeyError as e:
        print(f"[i] Channel manager skipped — missing env var {e}.")
    except Exception as e:
        print(f"[i] Channel manager skipped (likely needs the yt-analytics.readonly "
              f"re-auth — see analytics_feedback.py): {e}")
        traceback.print_exc()

    print("=" * 60)


if __name__ == "__main__":
    run_channel_manager()
