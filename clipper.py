#!/usr/bin/env python3
"""YouTube stream/VOD clipper for faceless-video-bot.

Paste a YouTube URL → download transcript → AI picks best moments →
ffmpeg edit (9:16 + zoom + captions) → schedule 1 short/day on YouTube.

When YouTube shows bot verification, emails the link and waits/retries
so the job can continue after manual verification if the block lifts.
"""
from __future__ import annotations

import json
import os
import smtplib
import subprocess
import time
from email.mime.text import MIMEText
from pathlib import Path

import yt_dlp
from google import genai

import config
from youtube_uploader import find_next_available_slots, schedule_video_upload


YDL_BASE = {
    "quiet": True,
    "no_warnings": False,
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "web", "ios", "mweb"],
            "skip": ["dash", "hls"],
        }
    },
    "retries": 5,
    "fragment_retries": 5,
    "sleep_interval": 1,
    "max_sleep_interval": 5,
}

# How long the bot waits and retries after sending the verification email
WAIT_MINUTES = int(os.getenv("BOT_WAIT_MINUTES", "8"))
RETRY_INTERVAL_SEC = 60


def is_bot_error(err: Exception | str) -> bool:
    text = str(err).lower()
    return any(k in text for k in [
        "sign in to confirm",
        "not a bot",
        "bot detection",
        "confirm you’re not a bot",
        "confirm you're not a bot",
        "http error 429",
        "too many requests",
    ])


def send_bot_alert_email(video_url: str, error_msg: str = "") -> bool:
    """Email the video URL when YouTube requires manual bot verification."""
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    to_email = os.getenv("ALERT_EMAIL", "")

    if not (smtp_user and smtp_pass and to_email):
        print("[!] Bot verification needed, but SMTP credentials not set. Cannot send email.")
        print(f"[!] Video URL: {video_url}")
        return False

    # Clean URL for display (remove tracking params if any)
    clean_url = video_url.split("?")[0] if "?" in video_url else video_url

    subject = "[Clip Bot] VERIFY THIS LINK — bot is waiting"
    body = f"""YouTube blocked the clipper. Bot is WAITING for you.

================================================
  OPEN THIS LINK TO VERIFY:
  {clean_url}
================================================

Steps:
1. Tap / open the link above
2. Sign in if YouTube asks
3. Complete the "not a bot" check
4. The bot will automatically retry for {WAIT_MINUTES} minutes

Do not click any other links. Only the link between the lines is the verification link.
"""
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_email

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, [to_email], msg.as_string())
        print(f"[+] Bot verification alert emailed to {to_email}")
        return True
    except Exception as e:
        print(f"[!] Failed to send email: {e}")
        print(f"[!] Video URL: {video_url}")
        return False


def wait_and_retry_on_bot(url: str, attempt_fn, description: str = "operation"):
    """
    Try `attempt_fn()`. If bot error, email once, then wait and retry for WAIT_MINUTES.
    Returns the successful result or raises the last error.
    """
    emailed = False
    deadline = time.time() + WAIT_MINUTES * 60
    last_err = None

    while True:
        try:
            return attempt_fn()
        except Exception as e:
            last_err = e
            if not is_bot_error(e):
                raise
            print(f"[!] Bot check during {description}: {e}")
            if not emailed:
                send_bot_alert_email(url, str(e))
                emailed = True
                print(f"[+] Bot is waiting up to {WAIT_MINUTES} minutes for verification...")
            if time.time() >= deadline:
                print("[!] Wait time expired. Still blocked.")
                raise
            print(f"[i] Retrying {description} in {RETRY_INTERVAL_SEC}s...")
            time.sleep(RETRY_INTERVAL_SEC)


def get_video_id(url: str) -> str:
    def _do():
        opts = {**YDL_BASE, "skip_download": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get("id") or "unknown"

    try:
        return wait_and_retry_on_bot(url, _do, "get_video_id")
    except Exception as e:
        print(f"[!] get_video_id failed: {e}")
        # fallback: extract from URL
        if "v=" in url:
            return url.split("v=")[1].split("&")[0][:11]
        if "/live/" in url:
            return url.split("/live/")[1].split("?")[0][:11]
        return "unknown"


def download_transcript(url: str, workdir: Path) -> list[dict]:
    """Get auto/manual English subtitles as list of {start, end, text}."""
    def _do():
        opts = {
            **YDL_BASE,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en", "hi"],
            "subtitlesformat": "json3",
            "outtmpl": str(workdir / "%(id)s"),
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        for p in workdir.glob("*.json3"):
            try:
                data = json.loads(p.read_text())
                events = data.get("events") or []
                segments = []
                for ev in events:
                    if "segs" not in ev:
                        continue
                    start = ev.get("tStartMs", 0) / 1000.0
                    dur = ev.get("dDurationMs", 0) / 1000.0
                    text = "".join(s.get("utf8", "") for s in ev.get("segs", [])).strip()
                    if text:
                        segments.append({"start": start, "end": start + dur, "text": text})
                return segments
            except Exception:
                continue
        return []

    try:
        return wait_and_retry_on_bot(url, _do, "download_transcript")
    except Exception as e:
        print(f"[!] Transcript download failed: {e}")
        return []


def pick_best_clips(transcript: list[dict], video_title: str = "stream", max_clips: int = 5) -> list[dict]:
    """Use Gemini to select best short moments (15-60s). Gaming-focused."""
    if not transcript:
        return []
    lines = [f"{s['start']:.1f}-{s['end']:.1f}: {s['text']}" for s in transcript[:1200]]
    prompt = f"""You are an expert BGMI / battle royale shorts editor. From this transcript of a stream titled "{video_title}", pick the {max_clips} BEST moments for viral YouTube Shorts.

Prioritize these gaming moments (in order):
1. High kill streaks / multi-kills / squad wipes
2. Clutch 1vX situations or intense final circles
3. Funny fails, rage, or unexpected moments
4. Skillful plays (headshots, good positioning, team coordination)
5. High energy reactions from the streamer

Rules:
- Each clip 15-50 seconds, self-contained with a clear hook and payoff.
- Prefer moments with clear action or strong emotion.
- Avoid long quiet/looting parts.
- Return ONLY valid JSON array of objects with keys: start (float seconds), end (float seconds), title (short punchy title under 60 chars), reason (one line why it's good).

Transcript:
{chr(10).join(lines)}
"""
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))
    resp = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
    )
    text = resp.text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    try:
        clips = json.loads(text)
        return [c for c in clips if isinstance(c, dict) and "start" in c and "end" in c][:max_clips]
    except Exception as e:
        print(f"[!] AI parse failed: {e}")
        return []


def download_segment(url: str, start: float, end: float, out_path: Path) -> bool:
    section = f"*{start}-{end}"
    def _do():
        opts = {
            **YDL_BASE,
            "format": "bestvideo[height<=1080]+bestaudio/best",
            "outtmpl": str(out_path),
            "download_sections": [section],
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        if not out_path.exists():
            raise RuntimeError("segment file not created")
        return True

    try:
        return wait_and_retry_on_bot(url, _do, "download_segment")
    except Exception as e:
        print(f"[!] Segment download failed: {e}")
        return False


def edit_clip(src: Path, dest: Path, captions: list[dict], zoom: bool = True) -> bool:
    """9:16 crop + optional zoom + burn captions with ffmpeg."""
    srt_path = dest.with_suffix(".srt")
    with srt_path.open("w") as f:
        for i, seg in enumerate(captions, 1):
            def fmt(t: float) -> str:
                h = int(t // 3600)
                m = int((t % 3600) // 60)
                s = int(t % 60)
                ms = int((t % 1) * 1000)
                return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
            f.write(f"{i}\n{fmt(seg['start'])} --> {fmt(seg['end'])}\n{seg['text']}\n\n")

    zoom_filter = "zoompan=z='min(zoom+0.0015,1.15)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920" if zoom else "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vf", f"{zoom_filter},subtitles={srt_path}:force_style='Fontsize=24,PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=2'",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return dest.exists()
    except Exception as e:
        print(f"[!] Edit failed: {e}")
        return False


def process_url(url: str, max_clips: int = 3, schedule: bool = True) -> list[Path]:
    vid = get_video_id(url)
    workdir = config.OUTPUT_DIR / "clips" / vid
    workdir.mkdir(parents=True, exist_ok=True)

    print(f"[+] Analyzing {url} (id={vid})")
    transcript = download_transcript(url, workdir)
    if not transcript:
        print("[!] No transcript available. Video may be blocked or have no captions.")
        return []

    clips = pick_best_clips(transcript, max_clips=max_clips)
    print(f"[+] AI selected {len(clips)} clips")

    produced = []
    for i, clip in enumerate(clips, 1):
        start, end = float(clip["start"]), float(clip["end"])
        if end - start < 8 or end - start > 90:
            continue
        raw = workdir / f"raw_{i}.mp4"
        final = workdir / f"short_{i}.mp4"
        if not download_segment(url, start, end, raw):
            continue
        rel_caps = [
            {"start": max(0, s["start"] - start), "end": max(0.1, s["end"] - start), "text": s["text"]}
            for s in transcript
            if s["end"] > start and s["start"] < end
        ]
        if edit_clip(raw, final, rel_caps, zoom=True):
            produced.append(final)
            meta = {
                "title": clip.get("title", f"Best Moment {i}")[:100],
                "description": f"Best moment from the stream.\n\n#shorts #bgmi #gaming",
                "hashtags": ["shorts", "bgmi", "gaming", "clip"],
            }
            (workdir / f"meta_{i}.json").write_text(json.dumps(meta, indent=2))
            if schedule:
                slots = find_next_available_slots(1)
                if slots:
                    day, hour, minute = slots[0]
                    schedule_video_upload(final, meta, hour, minute, day_offset=day)
    return produced


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python clipper.py <youtube_url>")
        sys.exit(1)
    process_url(sys.argv[1])
