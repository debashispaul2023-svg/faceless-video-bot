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

def schedule_video_upload(video_path: Path, metadata: dict, target_hour_est: int = 12):
    """Uploads video to YouTube and sets schedule time for peak EST hours."""
    print("[+] Initiating YouTube OAuth authentication...")
    youtube = authenticate_youtube()

    # Calculate target hour in EST (US Audience)
    est_tz = pytz.timezone('US/Eastern')
    now = datetime.datetime.now(est_tz)

    scheduled_time = now.replace(hour=target_hour_est, minute=0, second=0, microsecond=0)
    if scheduled_time <= now:
        scheduled_time += datetime.timedelta(days=1)

    publish_at_iso = scheduled_time.isoformat()
    clean_tags = [t.replace("#", "") for t in metadata.get("hashtags", ["shorts", "viral"])]

    body = {
        "snippet": {
            "title": metadata.get("title", "Automated Viral Short")[:100],
            "description": f"{metadata.get('description', '')}\n\n#shorts",
            "tags": clean_tags,
            "categoryId": "24"  # Entertainment
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
