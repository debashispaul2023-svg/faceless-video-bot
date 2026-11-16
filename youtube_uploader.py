"""YouTube Data API v3 upload / schedule helpers.

OAuth (CLIENT_ID / CLIENT_SECRET / REFRESH_TOKEN) is for UPLOAD only.
It does not authenticate yt-dlp extraction.
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path

try:
    import pytz
except Exception:
    pytz = None

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
except Exception:
    Credentials = Request = build = MediaFileUpload = None  # type: ignore

try:
    from thumbnail_generator import generate_thumbnail
except Exception:
    def generate_thumbnail(*a, **k):
        return None

import config

# Least privilege sufficient for upload + schedule + list own videos
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]

DAILY_SLOTS = [(23, 0)]  # 11:00 PM IST


class YouTubeAuthError(RuntimeError):
    """OAuth refresh or credential failure."""


def _ist_now():
    if pytz is None:
        return datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
    return datetime.datetime.now(pytz.timezone("Asia/Kolkata"))


def authenticate_youtube():
    """Refresh OAuth token and return YouTube API client. Never logs secrets."""
    if Credentials is None or Request is None or build is None:
        raise YouTubeAuthError("google-api-python-client / google-auth not installed")

    client_id = os.environ.get("YOUTUBE_CLIENT_ID", "")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET", "")
    refresh_token = os.environ.get("YOUTUBE_REFRESH_TOKEN", "")
    if not (client_id and client_secret and refresh_token):
        raise YouTubeAuthError(
            "Missing YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN"
        )

    try:
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            token_uri="https://oauth2.googleapis.com/token",
        )
        creds.refresh(Request())
        return build("youtube", "v3", credentials=creds)
    except Exception as e:
        # Do not include raw credential material in message
        raise YouTubeAuthError(f"OAuth refresh failed: {type(e).__name__}") from e


def find_next_available_slots(count: int = 1):
    """Return list of (day_offset, hour, minute) for next free DAILY_SLOTS."""
    youtube = authenticate_youtube()
    now = _ist_now()
    booked = {}
    video_ids = []
    page_token = None
    for _ in range(3):
        resp = youtube.search().list(
            part="id", forMine=True, type="video", order="date",
            maxResults=50, pageToken=page_token,
        ).execute()
        video_ids += [item["id"]["videoId"] for item in resp.get("items", [])]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        if not batch:
            continue
        details = youtube.videos().list(part="status,snippet", id=",".join(batch)).execute()
        for item in details.get("items", []):
            when_str = item["status"].get("publishAt") or item["snippet"].get("publishedAt")
            if not when_str:
                continue
            when = datetime.datetime.fromisoformat(when_str.replace("Z", "+00:00"))
            if pytz is not None:
                when = when.astimezone(pytz.timezone("Asia/Kolkata"))
            booked.setdefault(when.date(), set()).add((when.hour, when.minute))

    results = []
    day_offset = 0
    while len(results) < count and day_offset < 60:
        candidate_date = (now + datetime.timedelta(days=day_offset)).date()
        taken = booked.setdefault(candidate_date, set())
        for hour, minute in DAILY_SLOTS:
            if len(results) >= count:
                break
            slot_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + datetime.timedelta(
                days=day_offset
            )
            already_passed = day_offset == 0 and slot_dt <= now
            if (hour, minute) in taken or already_passed:
                continue
            results.append((day_offset, hour, minute))
            taken.add((hour, minute))
        day_offset += 1
    print(f"[+] Next {count} available slot(s): {results}")
    return results


def schedule_video_upload(
    video_path: Path,
    metadata: dict,
    target_hour: int,
    target_minute: int,
    day_offset: int = 0,
):
    """Upload video as private + scheduled publish. Preserves file on failure."""
    if not Path(video_path).is_file():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if MediaFileUpload is None:
        raise YouTubeAuthError("MediaFileUpload unavailable")

    youtube = authenticate_youtube()
    now = _ist_now()
    scheduled_time = (now + datetime.timedelta(days=day_offset)).replace(
        hour=target_hour, minute=target_minute, second=0, microsecond=0
    )
    if day_offset == 0 and scheduled_time <= now:
        scheduled_time += datetime.timedelta(days=1)
    publish_at_iso = scheduled_time.isoformat()
    clean_tags = [t.replace("#", "") for t in metadata.get("hashtags", ["shorts", "viral"])]
    body = {
        "snippet": {
            "title": metadata.get("title", "Automated Short")[:100],
            "description": f"{metadata.get('description', '')}\n\n#shorts",
            "tags": clean_tags,
            "categoryId": "20",
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": publish_at_iso,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,
        },
    }
    print(f"[+] Scheduling for: {publish_at_iso}")
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    print("[+] Uploading to YouTube...")
    response = None
    try:
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"[+] Upload progress: {int(status.progress() * 100)}%")
    except Exception as e:
        print(f"[!] Upload failed — local file kept at {video_path}")
        raise RuntimeError(f"YouTube upload failed: {type(e).__name__}") from e

    video_id = response.get("id")
    print(f"[✓] Upload ok — id={video_id} scheduled {publish_at_iso}")
    return video_id


def build_cartoon_metadata(plan) -> dict:
    """Metadata helper for cartoon pipeline (preserved)."""
    title = getattr(plan, "youtube_title", None) or getattr(plan, "title", "Episode")
    description = getattr(plan, "description", "") or ""
    tags = list(getattr(plan, "tags", None) or ["shorts", "cartoon"])
    return {"title": title, "description": description, "hashtags": tags}


def upload_cartoon_episode(video_path, plan, thumbnail_path=None, privacy="private"):
    """Cartoon pipeline upload entry (preserved)."""
    try:
        youtube = authenticate_youtube()
    except YouTubeAuthError as e:
        print(f"[!] Upload skipped — auth error: {e}")
        return None
    meta = build_cartoon_metadata(plan)
    body = {
        "snippet": {
            "title": meta["title"][:100],
            "description": meta["description"],
            "tags": [t.replace("#", "") for t in meta["hashtags"]],
            "categoryId": "1",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": bool(getattr(config, "YOUTUBE_MADE_FOR_KIDS", False)),
        },
    }
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    try:
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"[+] Upload progress: {int(status.progress() * 100)}%")
    except Exception as e:
        print(f"[!] Cartoon upload failed — file preserved: {e}")
        return None
    vid = response.get("id")
    print(f"[✓] Cartoon upload id={vid}")
    if thumbnail_path and Path(thumbnail_path).is_file():
        try:
            youtube.thumbnails().set(
                videoId=vid, media_body=MediaFileUpload(str(thumbnail_path))
            ).execute()
        except Exception as e:
            print(f"[i] Thumbnail set skipped: {e}")
    return vid
