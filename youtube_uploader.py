import os
import datetime
import pytz
from pathlib import Path
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def authenticate_youtube():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)


def schedule_video_upload(video_path: Path, metadata: dict, target_hour: int,
                           target_minute: int, day_offset: int = 0):
    """
    Uploads video to YouTube and schedules it for EXACTLY the IST time
    main.py hands over — hour, minute, and day_offset. This function does
    NOT look at YouTube, does NOT check what's already scheduled, and does
    NOT decide anything on its own. main.py is the single source of truth
    for scheduling (index // 3 -> day_offset, index % 3 -> which of the 3
    daily slots). No dynamic/API-based slot-hunting logic lives here —
    that was the source of videos landing at random times, and it has been
    removed entirely on purpose. Keep it that way.

    The only adjustment made here is pure date arithmetic (not an API
    call): if a same-day slot (day_offset == 0) has already passed by the
    time this actually runs (e.g. the GitHub Actions cron fired late), it
    rolls forward exactly one calendar day so the video doesn't get a
    publishAt timestamp in the past. Overflow videos (day_offset >= 1)
    already carry an explicit future day from main.py and are left as-is.
    """
    youtube = authenticate_youtube()

    ist_tz = pytz.timezone('Asia/Kolkata')
    now = datetime.datetime.now(ist_tz)

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
            "categoryId": "27"  # Education
        },
        "status": {
            "privacyStatus": "private",  # Must be private to be scheduled
            "publishAt": publish_at_iso,
            "selfDeclaredMadeForKids": False,
            # Required disclosure per YouTube's policy for content that is
            # AI-generated / meaningfully altered — this whole video
            # (script, voice, visuals) is AI-produced, so this must be True.
            "containsSyntheticMedia": True
        }
    }

    print(f"[+] Scheduling for EXACTLY: {publish_at_iso} IST "
          f"(hour={target_hour}, minute={target_minute}, day_offset={day_offset})")

    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    print(f"[+] Uploading to YouTube...")
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"[+] Upload progress: {int(status.progress() * 100)}%")

    print(f"[✓] Upload successful! Video ID: {response.get('id')} — scheduled for {publish_at_iso} IST")
    return response.get('id')
