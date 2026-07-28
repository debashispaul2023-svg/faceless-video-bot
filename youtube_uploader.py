import datetime
import pytz
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import config

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

def authenticate_youtube():
    flow = InstalledAppFlow.from_client_secrets_file(
        str(config.CLIENT_SECRET_FILE), SCOPES
    )
    credentials = flow.run_local_server(port=0)
    return build("youtube", "v3", credentials=credentials)

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
