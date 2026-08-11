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
import io
import json
import time
import urllib.parse
import traceback
from pathlib import Path
from datetime import datetime, timedelta, timezone
import requests
from PIL import Image, ImageDraw, ImageFont
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google import genai
import config

APPLY_CHANGES = os.getenv("CHANNEL_MANAGER_APPLY_CHANGES", "false").lower() == "true"

_LOG_FILE = config.ASSETS_DIR / "published_log.json"
_ACTIONS_LOG_FILE = config.ASSETS_DIR / "channel_manager_actions.json"


# How far below the channel's own median a video has to fall to count as
# "underperforming" (0.4 = less than 40% of the median views).
_UNDERPERFORM_RATIO = 0.4
_MIN_AGE_HOURS_FOR_REVIEW = 48
_MAX_AGE_DAYS_FOR_REVIEW = 21
_MAX_EDITS_PER_RUN = 3  # a safety cap so one bad run can't rewrite the whole channel

from thumbnail_generator import generate_thumbnail


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
        disclaimer = (
            "This video is for educational and informational purposes only and does not "
            "constitute financial advice. Always do your own research or consult a licensed "
            "financial advisor before making financial decisions."
        )
        if disclaimer not in new_description:
            new_description = f"{new_description}\n\n{disclaimer}"
        new_tags = new_meta.get("tags", old_tags) or old_tags

        thumbnail_path = config.OUTPUT_DIR / f"thumb_{video_id}.jpg"
        thumbnail_ready = generate_thumbnail(new_title, thumbnail_path, seed_extra=video_id)

        _log_action({
            "type": "rewrite_metadata", "video_id": video_id,
            "old_title": old_title, "new_title": new_title,
            "old_tags": old_tags, "new_tags": new_tags,
            "new_thumbnail_generated": thumbnail_ready,
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

            if thumbnail_ready:
                try:
                    youtube.thumbnails().set(
                        videoId=video_id,
                        media_body=MediaFileUpload(str(thumbnail_path))
                    ).execute()
                    print(f"[✓] Updated thumbnail for {video_id}.")
                except Exception as e:
                    print(f"[!] Could not set thumbnail for {video_id} (often means the channel "
                          f"isn't phone-verified yet — check youtube.com/verify): {e}")
        else:
            print(f"[i] (dry run) Would rewrite {video_id} -> title: '{new_title}', "
                  f"tags: {new_tags}, new thumbnail: {'ready' if thumbnail_ready else 'failed to generate'}. "
                  f"Set CHANNEL_MANAGER_APPLY_CHANGES=true to actually apply this.")
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


_RETENTION_INSIGHTS_FILE = config.ASSETS_DIR / "retention_insights.json"


def _parse_iso8601_duration_seconds(duration: str) -> float:
    """Parses YouTube's ISO 8601 video duration format (e.g. 'PT45S',
    'PT1M5S') into seconds. Returns 0 if it can't be parsed."""
    import re
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
    researcher.py reads and folds into the script prompt — so future
    scripts can keep the pacing tight before that point instead of us
    just guessing.

    Best-effort: any failure here just means no retention guidance gets
    added, nothing more.
    """
    try:
        now = datetime.now(timezone.utc)
        dropoff_points = []

        # Sample a handful of recent public videos — enough for a rough
        # signal without burning excessive API quota every run.
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

                # Find the single biggest drop between consecutive points.
                biggest_drop, drop_at_ratio = 0, None
                for i in range(1, len(rows)):
                    prev_ratio = float(rows[i - 1][1])
                    curr_ratio = float(rows[i][1])
                    drop = prev_ratio - curr_ratio
                    if drop > biggest_drop:
                        biggest_drop = drop
                        drop_at_ratio = float(rows[i][0])

                if drop_at_ratio is not None and biggest_drop > 0.05:  # ignore noise
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

        _analyze_retention_dropoff(youtube, analytics, video_ids)
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
