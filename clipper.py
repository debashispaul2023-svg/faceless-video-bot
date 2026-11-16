#!/usr/bin/env python3
"""YouTube stream/VOD clipper for faceless-video-bot.

Paste a YouTube URL → download transcript → AI picks best moments →
ffmpeg edit (9:16 + zoom + captions) → schedule 1 short/day on YouTube.

When YouTube shows bot verification, emails the link and waits/retries
so the job can continue after manual verification if the block lifts.

Supports cookies via YOUTUBE_COOKIES_FILE (path) or YOUTUBE_COOKIES
(secret content) so GitHub Actions runners can bypass "confirm you're
not a bot" / 429 blocks.
"""
from __future__ import annotations

import json
import os
import smtplib
import subprocess
import tempfile
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
            # Prefer android/web; ios/mweb sometimes trigger harder checks
            "player_client": ["android", "web"],
            "skip": ["dash", "hls"],
        }
    },
    "retries": 8,
    "fragment_retries": 8,
    "sleep_interval": 2,
    "max_sleep_interval": 10,
    # Reduce chance of hammering the same endpoint
    "sleep_interval_requests": 1,
}

# How long the bot waits and retries after sending the verification email
WAIT_MINUTES = int(os.getenv("BOT_WAIT_MINUTES", "8"))
RETRY_INTERVAL_SEC = 60

# For very long streams (12–16h) we cannot dump the entire transcript into Gemini.
# We sample windows across the whole duration.
MAX_TRANSCRIPT_CHARS_FOR_AI = 90_000
WINDOW_SECONDS = 25 * 60          # ~25 min windows
WINDOWS_TO_SAMPLE = 12            # cover a 12–16h stream reasonably


def _resolve_cookiefile() -> str | None:
    """Return a path to a Netscape cookies file, or None."""
    path = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()
    if path and Path(path).is_file():
        return path

    # Secret may contain the full cookies.txt content
    content = os.getenv("YOUTUBE_COOKIES", "").strip()
    if not content:
        return None

    # Write once per process into a temp file (Actions runners are ephemeral)
    tmp = Path(tempfile.gettempdir()) / "yt_cookies_actions.txt"
    if not tmp.exists() or tmp.stat().st_size == 0:
        tmp.write_text(content, encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
    return str(tmp)


def _ydl_opts(**extra) -> dict:
    opts = {**YDL_BASE, **extra}
    cookiefile = _resolve_cookiefile()
    if cookiefile:
        opts["cookiefile"] = cookiefile
        print(f"[i] Using YouTube cookies from {cookiefile}")
    return opts


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
        "confirm you are not a bot",
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

NOTE: Verifying in your browser does NOT always unblock the GitHub Actions
IP. The proper long-term fix is to put YouTube cookies into the
YOUTUBE_COOKIES secret (see README / previous messages).

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
        opts = _ydl_opts(skip_download=True)
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
        if "youtu.be/" in url:
            return url.split("youtu.be/")[1].split("?")[0][:11]
        return "unknown"


def download_transcript(url: str, workdir: Path) -> list[dict]:
    """Get auto/manual English (or Hindi) subtitles as list of {start, end, text}."""
    def _do():
        opts = _ydl_opts(
            skip_download=True,
            writesubtitles=True,
            writeautomaticsub=True,
            subtitleslangs=["en", "hi"],
            subtitlesformat="json3",
            outtmpl=str(workdir / "%(id)s"),
        )
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        segments: list[dict] = []
        for p in sorted(workdir.glob("*.json3")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                events = data.get("events") or []
                for ev in events:
                    if "segs" not in ev:
                        continue
                    start = ev.get("tStartMs", 0) / 1000.0
                    dur = ev.get("dDurationMs", 0) / 1000.0
                    text = "".join(s.get("utf8", "") for s in ev.get("segs", [])).strip()
                    if text:
                        segments.append({"start": start, "end": start + dur, "text": text})
            except Exception as e:
                print(f"[!] Failed parsing subtitle file {p.name}: {e}")
                continue
        return segments

    try:
        return wait_and_retry_on_bot(url, _do, "download_transcript")
    except Exception as e:
        print(f"[!] Transcript download failed: {e}")
        return []


def _sample_transcript_for_ai(transcript: list[dict], max_chars: int = MAX_TRANSCRIPT_CHARS_FOR_AI) -> str:
    """
    For 12–16 hour streams the full transcript is too large for the model.
    Sample evenly spaced windows across the whole duration so we don't only
    look at the first ~20–30 minutes.
    """
    if not transcript:
        return ""

    total_duration = transcript[-1]["end"] - transcript[0]["start"]
    if total_duration < 90 * 60:  # under ~1.5h → just take everything (truncated)
        lines = [f"{s['start']:.1f}-{s['end']:.1f}: {s['text']}" for s in transcript]
        text = "\n".join(lines)
        return text[:max_chars]

    # Long stream: take WINDOWS_TO_SAMPLE windows of WINDOW_SECONDS each
    window = WINDOW_SECONDS
    n_windows = min(WINDOWS_TO_SAMPLE, max(4, int(total_duration // window)))
    step = total_duration / n_windows
    start0 = transcript[0]["start"]

    selected: list[dict] = []
    for i in range(n_windows):
        w_start = start0 + i * step
        w_end = w_start + window
        for s in transcript:
            if s["end"] < w_start:
                continue
            if s["start"] > w_end:
                break
            selected.append(s)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for s in selected:
        key = (round(s["start"], 1), s["text"][:40])
        if key not in seen:
            seen.add(key)
            unique.append(s)

    lines = [f"{s['start']:.1f}-{s['end']:.1f}: {s['text']}" for s in unique]
    text = "\n".join(lines)
    if len(text) > max_chars:
        # Keep beginning + end of the sampled text so both early and late moments remain
        half = max_chars // 2
        text = text[:half] + "\n...[middle truncated]...\n" + text[-half:]
    return text


def pick_best_clips(transcript: list[dict], video_title: str = "stream", max_clips: int = 10) -> list[dict]:
    """Use Gemini to select best short moments for GTA 6 viral Shorts.
    Handles 12–16 hour streams by sampling across the whole duration.
    Default target: 10+ clips from one long stream.
    """
    if not transcript:
        return []

    sampled = _sample_transcript_for_ai(transcript)
    if not sampled.strip():
        return []

    duration_h = (transcript[-1]["end"] - transcript[0]["start"]) / 3600.0
    print(f"[i] Transcript spans ~{duration_h:.1f}h, sending sampled text ({len(sampled)} chars) to AI")

    prompt = f"""You are an expert GTA 6 / Grand Theft Auto VI YouTube Shorts editor and viral clipper.
From this transcript of a stream titled "{video_title}" (approx {duration_h:.1f} hours long), pick the {max_clips} BEST moments for viral YouTube Shorts.

Prioritize these GTA 6 moment types (highest priority first):
1. Police chases / wanted level chaos / escapes that almost fail
2. Epic crashes, stunts, jumps, or ragdoll fails
3. Funny NPC interactions, random events, or unexpected reactions
4. Heist / mission tension with clear payoff (success or spectacular fail)
5. Streamer high-energy reaction + the action that caused it
6. "Wait you can do that?" discovery / new feature moments
7. Clean skillful plays (drifting, shooting, driving)

Hard rules for viral Shorts (2026):
- Each clip 15–45 seconds (sweet spot 25–40s). Must have a clear HOOK in the first 1–2 seconds and a PAYOFF before it ends.
- Open on the action or the reaction — never on quiet driving or loading.
- Self-contained: viewer must understand what happened without watching the full stream.
- Spread picks across the whole stream (early / mid / late), not only the beginning.
- Avoid heavy story spoilers and long cutscenes.
- Titles must be search-friendly and specific. Good examples:
  "GTA 6 police chase goes wrong in 12 seconds"
  "GTA 6 stunt jump I had no right to land"
  "GTA 6 funny NPC moment caught on stream"
  "GTA 6 crash with the worst timing"
  Bad: "OMG watch this", "Part 47", "#gta6 #viral"

Return ONLY a valid JSON array of objects with keys:
- start (float seconds)
- end (float seconds)
- title (under 70 chars, starts with "GTA 6" when possible)
- reason (one short line why it will perform)

Transcript (sampled across the stream):
{sampled}
"""
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))
    resp = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
    )
    text = (resp.text or "").strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    try:
        clips = json.loads(text)
        return [c for c in clips if isinstance(c, dict) and "start" in c and "end" in c][:max_clips]
    except Exception as e:
        print(f"[!] AI parse failed: {e}")
        print(f"[!] Raw AI response (first 500 chars): {text[:500]}")
        return []


def download_segment(url: str, start: float, end: float, out_path: Path) -> bool:
    section = f"*{start}-{end}"
    def _do():
        opts = _ydl_opts(
            format="bestvideo[height<=1080]+bestaudio/best",
            outtmpl=str(out_path.with_suffix("")),  # yt-dlp adds extension
            download_sections=[section],
            # Force merge into mp4 when possible
            merge_output_format="mp4",
        )
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        # yt-dlp may write .mp4 / .mkv / .webm depending on formats
        if out_path.exists():
            return True
        for cand in out_path.parent.glob(out_path.stem + ".*"):
            if cand.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}:
                cand.rename(out_path)
                return True
        raise RuntimeError("segment file not created")

    try:
        return wait_and_retry_on_bot(url, _do, "download_segment")
    except Exception as e:
        print(f"[!] Segment download failed: {e}")
        return False


def _escape_drawtext(text: str) -> str:
    """Escape text for ffmpeg drawtext filter."""
    text = (text or "").replace("\n", " ").strip()
    if len(text) > 55:
        text = text[:52].rsplit(" ", 1)[0] + "…"
    for ch in ("\\", ":", "'", "%"):
        text = text.replace(ch, "\\" + ch)
    return text


def _ass_time(t: float) -> str:
    """Seconds → ASS timestamp H:MM:SS.cc"""
    if t < 0:
        t = 0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int(round((t % 1) * 100))
    if cs >= 100:
        s += 1
        cs = 0
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _write_word_highlight_ass(captions: list[dict], ass_path: Path) -> None:
    """ASS karaoke-style captions: active word highlights yellow, rest white."""
    header = """[Script Info]
Title: GTA6 Short captions
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,48,&H00FFFFFF,&H0000FCFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,2,40,40,220,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for seg in captions:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start = float(seg["start"])
        end = float(seg["end"])
        dur = max(0.15, end - start)
        words = text.split()
        if not words:
            continue
        # Evenly split segment duration across words (centiseconds for \k)
        per = max(8, int((dur / len(words)) * 100))  # cs
        # Karaoke: SecondaryColour fills as \k advances → yellow highlight on active word
        karaoke = "".join(f"{{\\k{per}}}{w} " for w in words).strip()
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{karaoke}\n"
        )
    ass_path.write_text("".join(lines), encoding="utf-8")


def _find_whoosh() -> Path | None:
    """Locate whoosh/impact SFX under assets/sfx."""
    root = Path(__file__).resolve().parent / "assets" / "sfx"
    if not root.is_dir():
        return None
    for name in ("Rake Swing Whoosh Hall.mp3", "whoosh.mp3", "impact.mp3"):
        p = root / name
        if p.is_file() and p.stat().st_size > 1000:
            return p
    # any mp3/wav in folder
    for p in sorted(root.glob("*")):
        if p.suffix.lower() in {".mp3", ".wav", ".m4a", ".ogg"} and p.stat().st_size > 1000:
            return p
    return None


def _probe_duration(path: Path) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, check=True,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def edit_clip(
    src: Path,
    dest: Path,
    captions: list[dict],
    zoom: bool = True,
    hook_text: str | None = None,
    add_sfx: bool = True,
    seamless_loop: bool = True,
) -> bool:
    """High-retention Shorts edit pipeline (GTA 6 focused).

    Features:
    1. 9:16 center crop + mild push-in
    2. Hook text first 2.5s
    3. Word-by-word karaoke color highlight captions (yellow active word)
    4. Whoosh / impact SFX at start (+ soft hit near payoff)
    5. Seamless-friendly loop: soft in/out fades + short end→start crossfade tail
    """
    ass_path = dest.with_suffix(".ass")
    _write_word_highlight_ass(captions, ass_path)

    duration = _probe_duration(src)
    if duration <= 0:
        duration = 30.0

    # --- Video filters ---
    if zoom:
        vf_core = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,"
            "scale=1145:2035,"
            "crop=1080:1920"
        )
    else:
        vf_core = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"

    vfilters = [vf_core]

    if hook_text:
        safe = _escape_drawtext(hook_text)
        vfilters.append(
            f"drawtext=text='{safe}'"
            f":fontsize=42:fontcolor=white:borderw=4:bordercolor=black"
            f":fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            f":x=(w-text_w)/2:y=h*0.18"
            f":enable='lt(t,2.5)'"
        )

    # Word-highlight ASS (path may contain special chars — escape for filter)
    ass_esc = str(ass_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    vfilters.append(f"ass={ass_esc}")

    # Soft fade for loop friendliness
    fade_in = 0.12
    fade_out = 0.28
    if seamless_loop and duration > 1.5:
        vfilters.append(f"fade=t=in:st=0:d={fade_in}")
        vfilters.append(f"fade=t=out:st={max(0, duration - fade_out)}:d={fade_out}")

    vf = ",".join(vfilters)

    whoosh = _find_whoosh() if add_sfx else None
    cmd: list[str] = ["ffmpeg", "-y", "-i", str(src)]

    # --- Audio: game audio + whoosh at 0s + soft whoosh near 70% ---
    if whoosh and whoosh.is_file():
        cmd += ["-i", str(whoosh)]
        # Trim whoosh to short hits: 0–0.45s start, another hit mid-late
        # volume low so dialogue stays clear
        impact_at = max(0.5, duration * 0.72)
        afilter = (
            # [0:a] main gameplay
            f"[0:a]afade=t=in:st=0:d={fade_in},afade=t=out:st={max(0, duration - fade_out)}:d={fade_out},volume=1.0[a0];"
            # start whoosh (first 0.5s of SFX file)
            f"[1:a]atrim=0:0.55,asetpts=PTS-STARTPTS,volume=0.45,afade=t=out:st=0.4:d=0.15[w1];"
            # impact-ish hit later (reuse 0.15–0.55 of same file)
            f"[1:a]atrim=0.15:0.55,asetpts=PTS-STARTPTS,volume=0.35,adelay={int(impact_at * 1000)}|{int(impact_at * 1000)}[w2];"
            f"[a0][w1][w2]amix=inputs=3:duration=first:dropout_transition=0:normalize=0[aout]"
        )
        cmd += [
            "-filter_complex", f"[0:v]{vf}[vout];{afilter}",
            "-map", "[vout]", "-map", "[aout]",
        ]
    else:
        # video only + simple audio fade
        af = f"afade=t=in:st=0:d={fade_in},afade=t=out:st={max(0, duration - fade_out)}:d={fade_out}"
        cmd += ["-vf", vf, "-af", af]

    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-t", f"{duration:.3f}",
        str(dest),
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        if not dest.exists():
            return False

        # Optional seamless loop polish: short end→start crossfade append
        # Makes the last ~0.35s blend into the first frames so Shorts replay feels continuous
        if seamless_loop and duration > 4.0:
            _apply_loop_crossfade(dest, duration)
        return dest.exists()
    except subprocess.CalledProcessError as e:
        err = (e.stderr or "")[-600:]
        print(f"[!] Edit failed: {err}")
        return _edit_clip_fallback(src, dest, captions, hook_text)
    except Exception as e:
        print(f"[!] Edit failed: {e}")
        return _edit_clip_fallback(src, dest, captions, hook_text)


def _apply_loop_crossfade(path: Path, duration: float, xfade_d: float = 0.35) -> None:
    """Blend last xfade_d seconds into the opening so auto-replay feels seamless."""
    if duration <= xfade_d * 3:
        return
    tmp = path.with_name(path.stem + "_loop" + path.suffix)
    # Main body until (duration - xfade_d), then xfade with head of clip
    offset = duration - xfade_d
    fc = (
        f"[0:v]split=2[v0][v1];"
        f"[v0]trim=0:{offset:.3f},setpts=PTS-STARTPTS[vmain];"
        f"[v1]trim=0:{xfade_d:.3f},setpts=PTS-STARTPTS[vhead];"
        f"[vmain][vhead]xfade=transition=fade:duration={xfade_d:.3f}:offset={offset - 0.001:.3f}[vout];"
        f"[0:a]asplit=2[a0][a1];"
        f"[a0]atrim=0:{offset:.3f},asetpts=PTS-STARTPTS[amain];"
        f"[a1]atrim=0:{xfade_d:.3f},asetpts=PTS-STARTPTS[ahead];"
        f"[amain][ahead]acrossfade=d={xfade_d:.3f}[aout]"
    )
    cmd = [
        "ffmpeg", "-y", "-i", str(path),
        "-filter_complex", fc,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(tmp),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        if tmp.exists() and tmp.stat().st_size > 1000:
            tmp.replace(path)
        elif tmp.exists():
            tmp.unlink(missing_ok=True)
    except Exception as e:
        print(f"[i] Loop crossfade skipped: {e}")
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _edit_clip_fallback(
    src: Path, dest: Path, captions: list[dict], hook_text: str | None
) -> bool:
    """Simpler path if full filter graph fails."""
    ass_path = dest.with_suffix(".ass")
    if not ass_path.exists():
        _write_word_highlight_ass(captions, ass_path)
    ass_esc = str(ass_path).replace("\\", "/").replace(":", "\\:")
    vf = (
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        f"ass={ass_esc}"
    )
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-vf", vf,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-c:a", "aac", "-b:a", "128k", str(dest)],
            check=True, capture_output=True,
        )
        return dest.exists()
    except Exception as e:
        print(f"[!] Fallback edit also failed: {e}")
        return False


def process_url(url: str, max_clips: int = 10, schedule: bool = True) -> list[Path]:
    """Clip a GTA 6 (or other) stream/VOD into multiple Shorts.
    Default: aim for 10 clips, schedule 1 per day at 11 PM IST.
    """
    vid = get_video_id(url)
    workdir = config.OUTPUT_DIR / "clips" / vid
    workdir.mkdir(parents=True, exist_ok=True)

    print(f"[+] Analyzing {url} (id={vid})")
    if _resolve_cookiefile():
        print("[i] Cookies are available — bot-check risk should be much lower")
    else:
        print("[!] No YOUTUBE_COOKIES / YOUTUBE_COOKIES_FILE set. High chance of bot/429 block on GitHub Actions.")

    transcript = download_transcript(url, workdir)
    if not transcript:
        print("[!] No transcript available. Video may be blocked, have no captions, or still be live without finalized subs.")
        return []

    duration_h = (transcript[-1]["end"] - transcript[0]["start"]) / 3600.0
    print(f"[+] Got {len(transcript)} transcript segments spanning ~{duration_h:.1f} hours")

    clips = pick_best_clips(transcript, video_title="GTA 6 stream", max_clips=max_clips)
    print(f"[+] AI selected {len(clips)} clips (target was {max_clips})")

    produced = []
    # Pre-fetch enough future 11 PM slots so we can schedule one clip per day
    slots = find_next_available_slots(max(len(clips), max_clips)) if schedule else []

    for i, clip in enumerate(clips, 1):
        start, end = float(clip["start"]), float(clip["end"])
        dur = end - start
        if dur < 10 or dur > 55:
            print(f"[i] Skipping clip {i}: duration {dur:.1f}s out of viral range (10–55s)")
            continue
        raw = workdir / f"raw_{i}.mp4"
        final = workdir / f"short_{i}.mp4"
        print(f"[+] Downloading segment {i}: {start:.1f}s → {end:.1f}s ({dur:.0f}s)")
        if not download_segment(url, start, end, raw):
            continue
        rel_caps = [
            {"start": max(0, s["start"] - start), "end": max(0.1, s["end"] - start), "text": s["text"]}
            for s in transcript
            if s["end"] > start and s["start"] < end
        ]
        title = (clip.get("title") or f"GTA 6 Best Moment {i}")[:100]
        if not title.upper().startswith("GTA"):
            title = f"GTA 6 {title}"[:100]
        # Hook overlay = short version of the viral title (first 2.5s on screen)
        if edit_clip(raw, final, rel_caps, zoom=True, hook_text=title):
            produced.append(final)
            meta = {
                "title": title,
                "description": (
                    f"{title}\n\n"
                    f"Clipped from stream. Best GTA 6 moments.\n\n"
                    f"#GTA6 #GTA6Shorts #GTA6Clips #shorts #gaming #GTA"
                ),
                "hashtags": [
                    "GTA6", "GTA6Shorts", "GTA6Clips", "shorts",
                    "GTA", "gaming", "ViceCity", "GTA6Moments",
                ],
            }
            (workdir / f"meta_{i}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
            if schedule and slots:
                day, hour, minute = slots.pop(0)
                print(f"[+] Scheduling clip {i} → day+{day} at {hour:02d}:{minute:02d} IST")
                schedule_video_upload(final, meta, hour, minute, day_offset=day)
    print(f"[+] Produced {len(produced)} Shorts from this stream")
    return produced


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python clipper.py <youtube_url>")
        sys.exit(1)
    process_url(sys.argv[1])
