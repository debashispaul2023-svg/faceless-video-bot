import os
import datetime
import pytz
from pathlib import Path
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from thumbnail_generator import generate_thumbnail
import config

SCOPES = ["https://www.googleapis.com/auth/youtube"]

# The ONLY time a video is ever allowed to publish. Nothing in this
# file ever schedules outside this exact clock time (IST).
# One high-quality video per day at peak evening engagement window.
DAILY_SLOTS = [(17, 0)]  # 5:00 PM IST


def find_next_available_slots(count: int = 1):
    """
    Looks at what's ALREADY scheduled/published on this channel and returns
    the next `count` FREE slots as (day_offset, hour, minute) tuples —
    filling any gaps in today first, then tomorrow, then the day after, etc.
    A day only gets skipped once its fixed slot is taken.

    This is a one-time, up-front lookahead (called once per run, before any
    uploading starts) — NOT a per-upload dynamic override. Once a slot is
    chosen here, schedule_video_upload() below uses it exactly as given, no
    further checking or second-guessing.
    """
    youtube = authenticate_youtube()
    ist_tz = pytz.timezone('Asia/Kolkata')
    now = datetime.datetime.now(ist_tz)

    booked = {}
    video_ids = []
    page_token = None
    for _ in range(3):  # a handful of pages is plenty for a 1-a-day channel
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
    while len(results) < count and day_offset < 60:  # 60-day safety valve
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
    # Deliberately NOT passing scopes= here: the refresh token already
    # carries its actual granted scope server-side. Explicitly claiming a
    # scope list on refresh can trigger 'invalid_scope' if it doesn't match
    # EXACTLY what the token was issued with — omitting it lets Google just
    # honor whatever was actually granted.
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
    """
    Uploads video to YouTube and schedules it for EXACTLY the IST time
    main.py hands over — hour, minute, and day_offset. This function does
    NOT look at YouTube, does NOT check what's already scheduled, and does
    NOT decide anything on its own. main.py is the single source of truth
    for scheduling (via find_next_available_slots). No dynamic/API-based
    slot-hunting logic lives here — that was the source of videos landing
    at random times, and it has been removed entirely on purpose. Keep it
    that way.

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
            "description": (
                f"{metadata.get('description', '')}\n\n"
                f"This video is for educational and informational purposes only and does not "
                f"constitute financial advice. Always do your own research or consult a licensed "
                f"financial advisor before making financial decisions.\n\n#shorts"
            ),
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

    video_id = response.get('id')
    print(f"[✓] Upload successful! Video ID: {video_id} — scheduled for {publish_at_iso} IST")

    # Give this video its own unique thumbnail instead of relying on
    # YouTube's auto-picked frame — auto-picked frames kept converging on
    # similar-looking stock footage across different videos, making many
    # of them look identical in the Content list. Never let a thumbnail
    # failure affect the upload itself, which has already succeeded above.
    try:
        thumb_path = config.OUTPUT_DIR / f"thumb_{video_id}.jpg"
        if generate_thumbnail(metadata.get("title", "Money Mastery Shorts"), thumb_path, seed_extra=video_id):
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumb_path))
            ).execute()
            print(f"[✓] Custom thumbnail set for {video_id}.")
    except Exception as e:
        print(f"[!] Could not set thumbnail for {video_id} (often means the channel isn't "
              f"phone-verified yet — check youtube.com/verify): {e}")

    return video_id
