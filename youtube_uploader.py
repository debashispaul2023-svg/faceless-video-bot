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

def _get_next_available_slot(youtube, target_hour_est: int, target_minute_est: int, day_offset: int = 0):
    """
    ইউটিউব থেকে বর্তমান শিডিউল চেক করে। যদি নির্দিষ্ট স্লটটি আগে থেকেই পূর্ণ থাকে,
    তবে স্বয়ংক্রিয়ভাবে পরবর্তী খালি স্লটে (পরবর্তী সময় বা অন্য তারিখে) শিফট করে দেয়।
    """
    est_tz = pytz.timezone('US/Eastern')
    now = datetime.datetime.now(est_tz)

    scheduled_time = (now + datetime.timedelta(days=day_offset)).replace(
        hour=target_hour_est, minute=target_minute_est, second=0, microsecond=0
    )
    
    if day_offset == 0 and scheduled_time <= now:
        scheduled_time += datetime.timedelta(days=1)

    try:
        request = youtube.videos().list(
            part="status",
            mine=True,
            maxResults=50
        )
        response = request.execute()
        
        booked_times = set()
        for item in response.get("items", []):
            status = item.get("status", {})
            publish_at = status.get("publishAt")
            if publish_at:
                dt = datetime.datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
                booked_times.add(dt.astimezone(est_tz).strftime("%Y-%m-%d %H:%M"))

        while scheduled_time.strftime("%Y-%m-%d %H:%M") in booked_times:
            print(f"[i] Slot {scheduled_time.strftime('%Y-%m-%d %H:%M')} EST is already full. Finding next available slot...")
            current_hour, current_minute = scheduled_time.hour, scheduled_time.minute
            
            if current_hour == 7 and current_minute == 30:
                scheduled_time = scheduled_time.replace(hour=13, minute=0)
            elif current_hour == 13 and current_minute == 0:
                scheduled_time = scheduled_time.replace(hour=17, minute=30)
            else:
                scheduled_time += datetime.timedelta(days=1)
                scheduled_time = scheduled_time.replace(hour=7, minute=30, second=0, microsecond=0)
                
    except Exception as e:
        print(f"[!] Could not check existing schedule conflicts from YouTube API: {e}")

    return scheduled_time

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

    # Get next available slot dynamically checking YouTube API conflicts
    scheduled_time = _get_next_available_slot(youtube, target_hour_est, target_minute_est, day_offset)

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
