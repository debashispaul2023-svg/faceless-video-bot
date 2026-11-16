import os
import datetime
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
    Credentials = Request = build = MediaFileUpload = None
from thumbnail_generator import generate_thumbnail
import config

SCOPES = ["https://www.googleapis.com/auth/youtube"]

DAILY_SLOTS = [(17, 0)]  # 5:00 PM IST

def find_next_available_slots(count: int = 1):
    youtube = authenticate_youtube()
    ist_tz = pytz.timezone('Asia/Kolkata')
    now = datetime.datetime.now(ist_tz)
    booked = {}
    video_ids = []
    page_token = None
    for _ in range(3):
        resp = youtube.search().list(
            part="id", forMine=True, type="video", order="date",
            maxResults=50, pageToken=page_token
        ).execute()
        video_ids += [item["id"]["videoId"] for item in resp.get("items", [])]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        if not batch:
            continue
        details = youtube.videos().list(part="status,snippet", id=",".join(batch)).execute()
        for item in details.get("items", []):
            when_str = item["status"].get("publishAt") or item["snippet"].get("publishedAt")
            if not when_str:
                continue
            when = datetime.datetime.fromisoformat(when_str.replace("Z", "+00:00")).astimezone(ist_tz)
            booked.setdefault(when.date(), set()).add((when.hour, when.minute))
    results = []
    day_offset = 0
    while len(results) < count and day_offset < 60:
        candidate_date = (now + datetime.timedelta(days=day_offset)).date()
        taken = booked.setdefault(candidate_date, set())
        for hour, minute in DAILY_SLOTS:
            if len(results) >= count:
                break
            slot_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + datetime.timedelta(days=day_offset)
            already_passed_today = day_offset == 0 and slot_dt <= now
            if (hour, minute) in taken or already_passed_today:
                continue
            results.append((day_offset, hour, minute))
            taken.add((hour, minute))
        day_offset += 1
    print(f"[+] Next {count} available slot(s): {results}")
    return results

def authenticate_youtube():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    creds.refresh(Request())
    return build("youtube", "v3", credentials=creds)

def schedule_video_upload(video_path: Path, metadata: dict, target_hour: int,
                           target_minute: int, day_offset: int = 0):
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
            "categoryId": "20",  # Gaming
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": publish_at_iso,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True
        }
    }
    print(f"[+] Scheduling for EXACTLY: {publish_at_iso} IST")
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    print(f"[+] Uploading to YouTube...")
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"[+] Upload progress: {int(status.progress() * 100)}%")
    video_id = response.get('id')
    print(f"[✓] Upload successful! Video ID: {video_id} — scheduled for {publish_at_iso} IST")
    return video_id
