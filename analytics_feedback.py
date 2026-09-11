"""
Analytics-driven scripting feedback loop.

REQUIRES A ONE-TIME SETUP STEP before this does anything useful:
  1. Enable "YouTube Analytics API" in the same Google Cloud project as your
     other YouTube API (APIs & Services > Library > search "YouTube Analytics
     API" > Enable).
  2. Your YOUTUBE_REFRESH_TOKEN must include the yt-analytics.readonly scope
     (see the OAuth Playground setup already covered earlier in this project).

Until that's done, every function in this file just logs a message and does
nothing harmful — it will NEVER crash or block video generation/upload.

This file drives TWO feedback signals that researcher.py reads back into its
prompt:
  - assets/category_scores.json         -> which TOPIC CATEGORY performs best
    (read by _choose_category())
  - assets/top_performing_patterns.json -> the structural DNA (hook technique
    + topic angle) of this channel's actual top 3 videos, used for the 80/20
    exploit/explore reinforcement clause (read by generate_script())
Both come from the SAME per-video Analytics API pass in
update_category_weights(), so this doesn't double the API quota used.
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
_TOP_PATTERNS_FILE = config.ASSETS_DIR / "top_performing_patterns.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def _authenticate():
    # Deliberately NOT passing scopes= here — see the note in
    # youtube_uploader.py's authenticate_youtube() for why (refreshing with
    # a scope list narrower/wider than what was actually granted at consent
    # time causes a Google 'invalid_scope' error).
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    creds.refresh(Request())
    return creds


def _write_version_scores(averaged_versions: dict, version_counts: dict):
    """
    Merges prompt-version performance into the SAME top_performing_patterns.json
    file get_top_performing_patterns() already writes (as a "version_scores"
    key alongside its existing "patterns" key) — deliberately not a separate
    file, just one more section of the one analytics-summary file this
    project already has.
    """
    try:
        existing = {}
        if _TOP_PATTERNS_FILE.is_file():
            existing = json.loads(_TOP_PATTERNS_FILE.read_text())
        existing["version_scores"] = averaged_versions
        existing["version_sample_sizes"] = version_counts
        existing["version_scores_updated_at"] = datetime.now(timezone.utc).isoformat()
        _TOP_PATTERNS_FILE.write_text(json.dumps(existing, indent=2))
    except Exception as e:
        print(f"[!] Could not write version scores: {e}")


def log_published_video(video_id: str, category: str, title: str, hook_technique: str = None, prompt_version: str = None):
    """
    Call this right after a successful upload so we have a record of which
    topic category, hook technique, AND prompt version each video came
    from — needed later to correlate performance back to all three, and to
    reconstruct each video's "structural DNA" for the top-performer
    analysis below.
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
            "hook_technique": hook_technique or "unspecified",
            "prompt_version": prompt_version or "unknown",
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
    from the YouTube Analytics API, and writes assets/category_scores.json
    (per topic category — read by researcher.py's _choose_category()).

    Also triggers get_top_performing_patterns() at the end, from the same
    general dataset, to refresh the structural-DNA reinforcement signal.

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
        # No upper bound here (deliberately unlike some other checks in
        # this project that only look at 'recent' videos) — YouTube
        # Analytics data for a video published within the last ~2 days can
        # still be incomplete/unsettled and come back as all-zero, which
        # was silently making every category and prompt-version score
        # exactly 0.0 even though real, later-settled data existed for the
        # same videos. Category/version performance isn't a "what's
        # trending right now" signal, so there's no reason to exclude
        # older, fully-settled videos from it.
        cutoff_recent = now - timedelta(hours=20)

        candidates = [
            e for e in entries
            if datetime.fromisoformat(e["published_at"]) < cutoff_recent
        ]
        if not candidates:
            print("[i] Analytics feedback: no videos old enough yet to have meaningful stats.")
        else:
            creds = _authenticate()
            analytics = build("youtubeAnalytics", "v2", credentials=creds)

            category_scores, category_counts = {}, {}
            version_scores, version_counts = {}, {}

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

                    # Retention matters more than raw views for a young channel.
                    score = (avg_pct or 0) * 2 + min(views or 0, 1000) / 100

                    category = entry.get("category", "unknown")
                    category_scores[category] = category_scores.get(category, 0) + score
                    category_counts[category] = category_counts.get(category, 0) + 1

                    # Same score, grouped by prompt_version instead — lets you
                    # see "is the current researcher.py prompt actually doing
                    # better than the last one?" with real data, from the
                    # exact same API calls (no extra quota spent).
                    version = entry.get("prompt_version", "unknown")
                    version_scores[version] = version_scores.get(version, 0) + score
                    version_counts[version] = version_counts.get(version, 0) + 1
                except Exception:
                    continue  # one bad lookup shouldn't sink the whole update

            if category_scores:
                averaged_categories = {cat: round(category_scores[cat] / category_counts[cat], 2) for cat in category_scores}
                _SCORES_FILE.write_text(json.dumps(averaged_categories, indent=2))
                print(f"[+] Updated category performance scores: {averaged_categories}")
            else:
                print("[i] Analytics feedback: API reachable but no usable rows returned yet for categories.")

            if version_scores:
                averaged_versions = {v: round(version_scores[v] / version_counts[v], 2) for v in version_scores}
                _write_version_scores(averaged_versions, version_counts)
                print(f"[+] Prompt version performance so far: {averaged_versions} "
                      f"(sample sizes: {version_counts})")

    except KeyError as e:
        print(f"[i] Analytics feedback skipped — missing env var {e}. "
              f"(YOUTUBE_REFRESH_TOKEN / CLIENT_ID / CLIENT_SECRET must be set.)")
        return
    except Exception as e:
        print(f"[i] Analytics feedback skipped (likely needs the one-time re-auth with "
              f"yt-analytics.readonly scope — see the top of this file): {e}")
        traceback.print_exc()
        return

    # Refresh the top-performer reinforcement signal from the same pass.
    try:
        get_top_performing_patterns(limit=3)
    except Exception as e:
        print(f"[i] Top-performer pattern analysis skipped (non-fatal): {e}")
        traceback.print_exc()


def get_top_performing_patterns(limit: int = 3) -> list:
    """
    Identifies this channel's top-performing published videos and extracts
    their "structural DNA" for the reinforcement-learning prompt in
    researcher.py (see the 80/20 exploit/explore reinforcement_clause in
    generate_script()).

    Ranking is a composite of:
      - retention (average-view-percentage) — weighted heaviest, since a
        young channel's raw view counts are noisy and easy to game with a
        single lucky/paid spike
      - engagement rate ((likes + comments) / views)
      - view velocity (views per hour since publish, capped so one viral
        outlier can't completely dominate the ranking)

    Extracted DNA per video: title, category, hook_technique, and a short
    synthesized `structure_note` built from those two — deliberately NOT the
    literal script text, so researcher.py's prompt never has an actual
    finished script to copy from (only the pattern that made it work).

    Writes the result to assets/top_performing_patterns.json (consumed by
    researcher.py) AND returns the same list, per the function's own
    contract. Returns [] on any failure or if there isn't enough data yet —
    NEVER raises, and researcher.py already treats an empty/missing file as
    "no reinforcement signal yet, use the default prompt."
    """
    try:
        if not _LOG_FILE.is_file():
            return []
        entries = json.loads(_LOG_FILE.read_text())
        if not entries:
            return []

        now = datetime.now(timezone.utc)
        candidates = [
            e for e in entries
            if (now - datetime.fromisoformat(e["published_at"])).total_seconds() > 20 * 3600
        ]
        if len(candidates) < 3:
            print("[i] Top-performer analysis: fewer than 3 videos old enough yet — skipping "
                  "(default prompt will be used until there's enough data).")
            return []

        creds = _authenticate()
        analytics = build("youtubeAnalytics", "v2", credentials=creds)
        youtube = build("youtube", "v3", credentials=creds)

        scored = []
        for entry in candidates:
            try:
                video_id = entry["video_id"]

                analytics_resp = analytics.reports().query(
                    ids="channel==MINE",
                    startDate="2020-01-01",
                    endDate=now.date().isoformat(),
                    metrics="views,averageViewPercentage",
                    filters=f"video=={video_id}",
                ).execute()
                rows = analytics_resp.get("rows", [])
                if not rows:
                    continue
                views, avg_pct = rows[0][0] or 0, rows[0][1] or 0

                likes, comments = 0, 0
                try:
                    stats_resp = youtube.videos().list(part="statistics", id=video_id).execute()
                    items = stats_resp.get("items", [])
                    if items:
                        stats = items[0].get("statistics", {})
                        likes = int(stats.get("likeCount", 0))
                        comments = int(stats.get("commentCount", 0))
                except Exception:
                    pass  # engagement is a nice-to-have signal, not required

                published_at = datetime.fromisoformat(entry["published_at"])
                hours_live = max((now - published_at).total_seconds() / 3600, 1)
                view_velocity = views / hours_live
                engagement_rate = (likes + comments) / max(views, 1)

                # Retention weighted heaviest; engagement second; velocity
                # capped at 50 so one viral outlier can't swamp the ranking.
                score = (avg_pct * 2) + (engagement_rate * 100) + min(view_velocity, 50)

                category = entry.get("category", "unknown")
                hook_technique = entry.get("hook_technique", "unspecified")

                scored.append({
                    "video_id": video_id,
                    "title": entry.get("title", ""),
                    "category": category,
                    "hook_technique": hook_technique,
                    "structure_note": f"hook style: {hook_technique}; topic angle: {category}",
                    "score": round(score, 2),
                    "views": views,
                    "average_view_percentage": round(avg_pct, 2),
                    "engagement_rate": round(engagement_rate, 4),
                })
            except Exception:
                continue  # one bad video shouldn't sink the whole analysis

        if not scored:
            print("[i] Top-performer analysis: API reachable but no usable rows returned yet.")
            return []

        scored.sort(key=lambda x: x["score"], reverse=True)
        top = scored[:limit]

        # Merge into the existing file rather than overwrite it — this same
        # file also carries "version_scores" (written by
        # update_category_weights() via _write_version_scores()), and each
        # writer should only touch its own key.
        existing = {}
        if _TOP_PATTERNS_FILE.is_file():
            try:
                existing = json.loads(_TOP_PATTERNS_FILE.read_text())
            except Exception:
                existing = {}
        existing["patterns"] = top
        existing["updated_at"] = now.isoformat()
        _TOP_PATTERNS_FILE.write_text(json.dumps(existing, indent=2))
        print(f"[+] Identified {len(top)} top-performing pattern(s): "
              f"{[p['title'] for p in top]}")
        return top

    except KeyError as e:
        print(f"[i] Top-performer analysis skipped — missing env var {e}.")
        return []
    except Exception as e:
        print(f"[i] Top-performer analysis skipped (likely needs the one-time re-auth with "
              f"yt-analytics.readonly scope): {e}")
        traceback.print_exc()
        return []
