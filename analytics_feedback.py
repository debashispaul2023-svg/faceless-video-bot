"""
Analytics-driven scripting feedback loop.

REQUIRES A ONE-TIME SETUP STEP before this does anything useful:
  1. Enable "YouTube Analytics API" in the same Google Cloud project as your
     other YouTube API (APIs & Services > Library > search "YouTube Analytics
     API" > Enable).
  2. Your YOUTUBE_REFRESH_TOKEN must include the yt-analytics.readonly scope.
     The existing token (authorized with only youtube/youtube.upload) will NOT
     work for this — you need to redo the OAuth Playground authorization step
     with BOTH scopes ticked:
       - https://www.googleapis.com/auth/youtube
       - https://www.googleapis.com/auth/yt-analytics.readonly
     then update the YOUTUBE_REFRESH_TOKEN GitHub secret with the new token.

Until that's done, every function in this file just logs a message and does
nothing harmful — it will NEVER crash or block video generation/upload.
"""

import os
import json
import traceback
from pathlib import Path
from datetime import datetime, timedelta, timezone
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import config

_LOG_FILE = config.ASSETS_DIR / "published_log.json"
_SCORES_FILE = config.ASSETS_DIR / "category_scores.json"



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


def log_published_video(video_id: str, category: str, title: str):
    """
    Call this right after a successful upload so we have a record of which
    topic category each video came from — needed later to correlate
    performance back to a category.
    """
    if not video_id:
        return
    try:
        entries = []
        if _LOG_FILE.is_file():
            entries = json.loads(_LOG_FILE.read_text())
        entries.append({
            "video_id": video_id,
            "category": category or "unknown",
            "title": title,
            "published_at": datetime.now(timezone.utc).isoformat(),
        })
        entries = entries[-200:]  # keep the log file bounded
        _LOG_FILE.write_text(json.dumps(entries, indent=2))
    except Exception as e:
        print(f"[!] Could not log published video for analytics: {e}")


def update_category_weights():
    """
    Best-effort: looks at videos published 20+ hours ago (so YouTube has had
    time to gather real numbers), pulls their views + average-view-percentage
    from the YouTube Analytics API, and writes a per-category score file that
    researcher.py reads to weight which category gets picked next run.

    Any failure here (scope not granted yet, API not enabled, no data yet,
    quota issue, etc.) is caught and simply skipped.
    """
    try:
        if not _LOG_FILE.is_file():
            print("[i] Analytics feedback: no published-video log yet, skipping.")
            return

        entries = json.loads(_LOG_FILE.read_text())
        if not entries:
            return

        now = datetime.now(timezone.utc)
        cutoff_old = now - timedelta(days=7)
        cutoff_recent = now - timedelta(hours=20)

        candidates = [
            e for e in entries
            if cutoff_old < datetime.fromisoformat(e["published_at"]) < cutoff_recent
        ]
        if not candidates:
            print("[i] Analytics feedback: no videos old enough yet to have meaningful stats.")
            return

        creds = _authenticate()
        analytics = build("youtubeAnalytics", "v2", credentials=creds)

        scores, counts = {}, {}
        for entry in candidates:
            try:
                resp = analytics.reports().query(
                    ids="channel==MINE",
                    startDate="2020-01-01",
                    endDate=now.date().isoformat(),
                    metrics="views,averageViewPercentage",
                    filters=f"video=={entry['video_id']}",
                ).execute()
                rows = resp.get("rows", [])
                if not rows:
                    continue
                views, avg_pct = rows[0][0], rows[0][1]
                category = entry.get("category", "unknown")
                # Retention matters more than raw views for a young channel.
                score = (avg_pct or 0) * 2 + min(views or 0, 1000) / 100
                scores[category] = scores.get(category, 0) + score
                counts[category] = counts.get(category, 0) + 1
            except Exception:
                continue  # one bad lookup shouldn't sink the whole update

        if not scores:
            print("[i] Analytics feedback: API reachable but no usable rows returned yet.")
            return

        averaged = {cat: round(scores[cat] / counts[cat], 2) for cat in scores}
        _SCORES_FILE.write_text(json.dumps(averaged, indent=2))
        print(f"[+] Updated category performance scores: {averaged}")

    except KeyError as e:
        print(f"[i] Analytics feedback skipped — missing env var {e}. "
              f"(YOUTUBE_REFRESH_TOKEN / CLIENT_ID / CLIENT_SECRET must be set.)")
    except Exception as e:
        print(f"[i] Analytics feedback skipped (likely needs the one-time re-auth with "
              f"yt-analytics.readonly scope — see the top of analytics_feedback.py): {e}")
        traceback.print_exc()
