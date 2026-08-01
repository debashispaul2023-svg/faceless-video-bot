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

def schedule_video_upload(video_path: Path, metadata: dict, target_hour_est: int = 12,
                           target_minute_est: int = 0, day_offset: int = 0):
    """Uploads video to YouTube and sets schedule time for peak EST/EDT hours.

    target_minute_est matters — several slots are on the half-hour (e.g.
    7:30 AM), and dropping the minutes silently shifted every such slot
    30 minutes early. Always pass both hour AND minute for the intended slot.

    day_offset lets overflow videos (4th, 5th, 6th... in a batch) roll over
    to the next day's slot instead of colliding with an earlier video that
    already claimed that same hour today.
    """
    print("[+] Initiating YouTube OAuth authentication...")
    youtube = authenticate_youtube()

    # Calculate target hour in EST (US Audience)
    est_tz = pytz.timezone('US/Eastern')
    now = datetime.datetime.now(est_tz)

    scheduled_time = (now + datetime.timedelta(days=day_offset)).replace(
        hour=target_hour_est, minute=target_minute_est, second=0, microsecond=0
    )
    # The "already passed today, push to tomorrow" safety check only makes
    # sense for same-day slots (day_offset == 0). Overflow videos already
    # have an explicit future day baked in via day_offset, so re-checking
    # against "now" here would double-shift them.
    if day_offset == 0 and scheduled_time <= now:
        scheduled_time += datetime.timedelta(days=1)

    publish_at_iso = scheduled_time.isoformat()
    clean_tags = [t.replace("#", "") for t in metadata.get("hashtags", ["shorts", "viral"])]

    body = {
        "snippet": {
            "title": metadata.get("title", "Automated Viral Short")[:100],
            "description": f"{metadata.get('description', '')}\n\n#shorts",
            "tags": clean_tags,
            "categoryId": "22"  # People & Blogs — more accurate than Entertainment for finance content
        },
        "status": {
            "privacyStatus": "private",  # Must be private to be scheduled
            "publishAt": publish_at_iso,
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    print(f"[+] Uploading to YouTube... Scheduling for: {publish_at_iso} EST")
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"[+] Upload progress: {int(status.progress() * 100)}%")

    print(f"[✓] Upload successful! Video ID: {response.get('id')}")
    return response.get('id')
