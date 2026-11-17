#!/usr/bin/env python3
"""YouTube stream/VOD clipper for faceless-video-bot.

Flow: URL → transcript → AI picks moments → FFmpeg edit → optional schedule.

Extraction (yt-dlp) is separate from upload OAuth.
Cookies (optional) help some restricted extracts; they are never logged.
Bot challenges and HTTP 429 fail fast — no multi-minute wait loops.
"""
from __future__ import annotations

import json
import base64
import os
import random
import smtplib
import subprocess
import tempfile
import time
from email.mime.text import MIMEText
from pathlib import Path

import requests
import yt_dlp
from google import genai

import config
from youtube_uploader import find_next_available_slots, schedule_video_upload


# ---------------------------------------------------------------------------
# yt-dlp base options (no secrets)
# ---------------------------------------------------------------------------
YDL_BASE = {
    "quiet": True,
    "no_warnings": False,
    "extractor_args": {
        "youtube": {
            # "android" now needs a PO token and is blocked from datacenter IPs.
            # With cookies, tv/web_safari/mweb are the clients that still work.
            "player_client": ["tv", "web_safari", "mweb"],
        }
    },
    # Let yt-dlp fetch its JS challenge solver (Deno is installed in the workflow)
    "remote_components": ["ejs:github"],
    "retries": 3,
    "fragment_retries": 3,
    "sleep_interval": 1,
    "max_sleep_interval": 5,
    "sleep_interval_requests": 1,
}

# Network-only retries (NOT used for bot/429)
NETWORK_RETRIES = 2
NETWORK_BACKOFF_SEC = 3

# Long-stream AI sampling
MAX_TRANSCRIPT_CHARS_FOR_AI = 90_000
WINDOW_SECONDS = 25 * 60
WINDOWS_TO_SAMPLE = 12

# One diagnostic email per process/run
_DIAGNOSTIC_EMAIL_SENT = False


class ExtractionAuthError(RuntimeError):
    """YouTube requires sign-in / bot check — not recoverable in CI without valid cookies."""


class ExtractionRateLimitError(RuntimeError):
    """HTTP 429 from YouTube — stop hammering."""


class ExtractionNetworkError(RuntimeError):
    """Transient network failure — limited retries allowed."""


# ---------------------------------------------------------------------------
# Cookie handling (never log contents)
# ---------------------------------------------------------------------------
def _resolve_cookiefile() -> str | None:
    """Return path to a Netscape cookies file, or None. Does not print secrets."""
    path = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()
    if path and Path(path).is_file():
        return path

    content = os.getenv("YOUTUBE_COOKIES", "").strip()
    if not content:
        return None

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
        # Do not print path content or cookie values
        print("[i] YouTube cookiefile is configured (contents not logged)")
    else:
        print("[i] No YouTube cookiefile — public extracts only; restricted videos may fail")
    return opts


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------
def classify_extraction_error(err: Exception | str) -> str:
    text = str(err).lower()
    if any(k in text for k in (
        "sign in to confirm",
        "not a bot",
        "bot detection",
        "confirm you're not a bot",
        "confirm you’re not a bot",
        "confirm you are not a bot",
        "login required",
    )):
        return "auth"
    if any(k in text for k in (
        "http error 429", "too many requests", "rate limit",
        "failed to extract any player response", "http error 403",
    )):
        return "rate_limit"
    if any(k in text for k in (
        "timed out", "timeout", "connection reset", "temporary failure",
        "network is unreachable", "name or service not known",
        "ssl", "errno 104", "errno 110",
    )):
        return "network"
    return "other"


def is_bot_error(err: Exception | str) -> bool:
    return classify_extraction_error(err) in ("auth", "rate_limit")


# ---------------------------------------------------------------------------
# Diagnostic email (max one per process)
# ---------------------------------------------------------------------------
def send_diagnostic_email(video_url: str, error_msg: str, kind: str = "extraction") -> bool:
    """Send at most one diagnostic alert per workflow run. No secrets in body."""
    global _DIAGNOSTIC_EMAIL_SENT
    if _DIAGNOSTIC_EMAIL_SENT:
        print("[i] Diagnostic email already sent this run — skipping duplicate")
        return False

    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    to_email = os.getenv("ALERT_EMAIL", "")

    if not (smtp_user and smtp_pass and to_email):
        print("[!] SMTP not configured — diagnostic email skipped")
        print(f"[!] Error kind={kind} url={video_url}")
        return False

    run_url = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    repo = os.getenv("GITHUB_REPOSITORY", "")
    run_id = os.getenv("GITHUB_RUN_ID", "")
    actions_url = f"{run_url}/{repo}/actions/runs/{run_id}" if repo and run_id else "(run URL unavailable)"

    # Sanitize error — never include cookie-like blobs
    safe_err = str(error_msg)[:800]
    for bad in ("cookie", "password", "token", "secret", "refresh"):
        if bad in safe_err.lower():
            safe_err = "[redacted error detail]"
            break

    subject = f"[Clip Bot] Extraction failed ({kind})"
    body = f"""Clip bot extraction failed in GitHub Actions.

Video URL: {video_url}
Error type: {kind}
Error (truncated): {safe_err}

GitHub Actions run:
{actions_url}

Notes:
- Opening the video in your phone/browser does NOT authenticate the Actions runner.
- OAuth Client ID / refresh token are for UPLOAD only; they do not fix yt-dlp extraction.
- Optional: set secret YOUTUBE_COOKIES (Netscape cookies.txt) for restricted extracts.
- Cookies expire; refresh them if extracts start failing again.
- HTTP 429 / bot challenges: the job stops immediately (no multi-minute retry loop).

This is a single diagnostic message for this run.
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
        _DIAGNOSTIC_EMAIL_SENT = True
        print(f"[+] Diagnostic email sent to configured ALERT_EMAIL")
        return True
    except Exception as e:
        print(f"[!] Failed to send diagnostic email: {e}")
        return False


# Back-compat name used nowhere critical
def send_bot_alert_email(video_url: str, error_msg: str = "") -> bool:
    return send_diagnostic_email(video_url, error_msg, kind="auth")


# ---------------------------------------------------------------------------
# Retry: network only; auth/429 fail immediately
# ---------------------------------------------------------------------------
def run_extraction(url: str, attempt_fn, description: str = "operation"):
    """
    Run attempt_fn with limited network retries.
    On auth or rate-limit: one diagnostic email, then raise — no wait loop.
    """
    last_err: Exception | None = None
    for attempt in range(NETWORK_RETRIES + 1):
        try:
            return attempt_fn()
        except Exception as e:
            last_err = e
            kind = classify_extraction_error(e)
            print(f"[!] {description} failed ({kind}): {e}")

            if kind == "auth":
                send_diagnostic_email(url, str(e), kind="auth")
                raise ExtractionAuthError(str(e)) from e
            if kind == "rate_limit":
                send_diagnostic_email(url, str(e), kind="rate_limit")
                raise ExtractionRateLimitError(str(e)) from e
            if kind == "network" and attempt < NETWORK_RETRIES:
                delay = NETWORK_BACKOFF_SEC * (attempt + 1)
                print(f"[i] Network error — retry {attempt + 1}/{NETWORK_RETRIES} in {delay}s")
                time.sleep(delay)
                continue
            # other / exhausted network
            if kind == "network":
                send_diagnostic_email(url, str(e), kind="network")
                raise ExtractionNetworkError(str(e)) from e
            raise

    assert last_err is not None
    raise last_err


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------
def extract_id_from_url(url: str) -> str:
    if "v=" in url:
        return url.split("v=")[1].split("&")[0][:11]
    if "/live/" in url:
        return url.split("/live/")[1].split("?")[0][:11]
    if "youtu.be/" in url:
        return url.split("youtu.be/")[1].split("?")[0][:11]
    return "unknown"


def get_video_id(url: str) -> str:
    def _do():
        opts = _ydl_opts(skip_download=True)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get("id") or extract_id_from_url(url)

    try:
        return run_extraction(url, _do, "get_video_id")
    except (ExtractionAuthError, ExtractionRateLimitError):
        # Still return URL-parsed id for logging/workdir; caller must not pretend extract worked
        vid = extract_id_from_url(url)
        print(f"[!] Using URL-parsed id={vid}; metadata extract failed")
        return vid
    except Exception as e:
        print(f"[!] get_video_id failed: {e}")
        return extract_id_from_url(url)


def download_transcript(url: str, workdir: Path) -> list[dict]:
    """Download subtitles as list of {start, end, text}. Raises on auth/429."""

    def _pick_langs() -> list[str]:
        """Inspect available caption tracks and choose which to download."""
        with yt_dlp.YoutubeDL(_ydl_opts(skip_download=True)) as ydl:
            info = ydl.extract_info(url, download=False)
        manual = list((info.get("subtitles") or {}).keys())
        auto = list((info.get("automatic_captions") or {}).keys())
        print(f"[i] manual subs: {manual[:15]}")
        print(f"[i] auto captions: {auto[:15]} (total {len(auto)})")
        print(f"[i] video language: {info.get('language')}")
        prefs = ["en", "en-orig", "hi", "hi-orig", "bn", "bn-orig"]
        lang = info.get("language")
        if lang:
            prefs = [lang, f"{lang}-orig"] + prefs
        pool = manual + [a for a in auto if a not in manual]
        chosen = [p for p in prefs if p in pool]
        # any "-orig" track = the video's spoken language
        chosen += [a for a in auto if a.endswith("-orig") and a not in chosen]
        if not chosen and pool:
            chosen = pool[:1]
        print(f"[i] downloading caption languages: {chosen}")
        return chosen[:2]

    def _do():
        langs = _pick_langs()
        if not langs:
            print("[i] No YouTube captions available - will fall back to audio transcription")
            return []
        opts = _ydl_opts(
            skip_download=True,
            writesubtitles=True,
            writeautomaticsub=True,
            subtitleslangs=langs,
            subtitlesformat="json3",
            outtmpl=str(workdir / "%(id)s"),
        )
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        segments: list[dict] = []
        for p in sorted(workdir.glob("*.json3")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                for ev in data.get("events") or []:
                    if "segs" not in ev:
                        continue
                    start = ev.get("tStartMs", 0) / 1000.0
                    dur = ev.get("dDurationMs", 0) / 1000.0
                    text = "".join(s.get("utf8", "") for s in ev.get("segs", [])).strip()
                    if text:
                        segments.append({"start": start, "end": start + dur, "text": text})
            except Exception as e:
                print(f"[!] Failed parsing subtitle file {p.name}: {e}")
        return segments

    return run_extraction(url, _do, "download_transcript")


# ---------------------------------------------------------------------------
# Fallback: no captions -> download audio, transcribe with Gemini
# ---------------------------------------------------------------------------
AUDIO_CHUNK_SEC = 600
MAX_AUDIO_MINUTES = int(os.getenv("MAX_AUDIO_MINUTES", "240"))
# Models tried in order; a 404 (retired model) moves on to the next one.
# Override the first choice with the GEMINI_MODEL env var.
GEMINI_MODELS = [m for m in [
    os.getenv("GEMINI_MODEL", "").strip(),
    "gemini-3.8-flash",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
] if m]
_WORKING_MODEL: str | None = None


def gemini_generate(client, contents):
    """generate_content with automatic fallback across model names on 404/not found."""
    global _WORKING_MODEL
    models = [_WORKING_MODEL] if _WORKING_MODEL else GEMINI_MODELS
    last = None
    for m in models:
        try:
            resp = client.models.generate_content(model=m, contents=contents)
            if _WORKING_MODEL != m:
                print(f"[i] Using Gemini model: {m}")
            _WORKING_MODEL = m
            return resp
        except Exception as e:
            last = e
            msg = str(e)
            if "404" in msg or "NOT_FOUND" in msg or "no longer available" in msg:
                print(f"[!] Model {m} unavailable - trying next")
                continue
            raise
    raise last if last else RuntimeError("no Gemini model available")


def _parse_json_array(text: str) -> list:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        return []
    return json.loads(text[start:end + 1])


def transcribe_audio_with_gemini(url: str, workdir: Path) -> list[dict]:
    """Download low-bitrate audio, split into chunks, transcribe each with Gemini."""
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        print("[!] GEMINI_API_KEY missing - cannot transcribe audio")
        return []

    src_tmpl = workdir / "audio_src"

    def _dl():
        opts = _ydl_opts(format="bestaudio/best", outtmpl=str(src_tmpl) + ".%(ext)s")
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

    try:
        print("[i] Downloading audio for transcription...")
        run_extraction(url, _dl, "download_audio")
    except Exception as e:
        print(f"[!] Audio download failed: {e}")
        return []

    srcs = [p for p in workdir.glob("audio_src.*")]
    if not srcs:
        print("[!] Audio file not found after download")
        return []
    src = srcs[0]

    for old in workdir.glob("chunk_*.mp3"):
        old.unlink()
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
             "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k",
             "-t", str(MAX_AUDIO_MINUTES * 60),
             "-f", "segment", "-segment_time", str(AUDIO_CHUNK_SEC),
             str(workdir / "chunk_%03d.mp3")],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"[!] ffmpeg split failed: {(e.stderr or '')[-400:]}")
        return []
    finally:
        try:
            src.unlink()
        except OSError:
            pass

    chunks = sorted(workdir.glob("chunk_*.mp3"))
    print(f"[i] Transcribing {len(chunks)} audio chunks with Gemini")
    client = genai.Client(api_key=api_key)
    segments: list[dict] = []

    prompt = (
        "Transcribe this audio (a GTA gameplay stream; speech may be Hindi, English or mixed). "
        "Return ONLY a JSON array. Each item: {\"start\": seconds_from_start_of_this_audio (number), "
        "\"end\": seconds (number), \"text\": \"what was said\"}. "
        "Use segments of roughly 5-15 seconds. Also include short notes in [brackets] for loud "
        "events (crash, siren, gunfire, shouting). Keep original language; Hindi in Roman script is fine."
    )
    for idx, chunk in enumerate(chunks):
        offset = idx * AUDIO_CHUNK_SEC
        for attempt in range(3):
            try:
                up = client.files.upload(file=str(chunk))
                resp = gemini_generate(client, [prompt, up])
                try:
                    client.files.delete(name=up.name)
                except Exception:
                    pass
                for it in _parse_json_array(resp.text):
                    txt = str(it.get("text", "")).strip()
                    if txt:
                        segments.append({
                            "start": offset + float(it.get("start", 0)),
                            "end": offset + float(it.get("end", it.get("start", 0))),
                            "text": txt,
                        })
                break
            except Exception as e:
                print(f"[!] chunk {idx + 1}/{len(chunks)} attempt {attempt + 1} failed: {e}")
                time.sleep(10 * (attempt + 1))
        chunk.unlink(missing_ok=True)
        time.sleep(2)

    print(f"[+] Audio transcription produced {len(segments)} segments")
    return segments



def download_segment(url: str, start: float, end: float, out_path: Path) -> bool:
    section = f"*{start}-{end}"

    def _do():
        opts = _ydl_opts(
            format="bestvideo[height<=1080]+bestaudio/best",
            outtmpl=str(out_path.with_suffix("")),
            download_sections=[section],
            merge_output_format="mp4",
        )
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        if out_path.exists():
            return True
        for cand in out_path.parent.glob(out_path.stem + ".*"):
            if cand.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}:
                cand.rename(out_path)
                return True
        raise RuntimeError("segment file not created") 

    try:
        return run_extraction(url, _do, "download_segment")
    except (ExtractionAuthError, ExtractionRateLimitError, ExtractionNetworkError) as e:
        print(f"[!] Segment download stopped: {e}")
        return False
    except Exception as e:
        print(f"[!] Segment download failed: {e}")
        return False


# ---------------------------------------------------------------------------
# AI clip selection
# ---------------------------------------------------------------------------
def _sample_transcript_for_ai(transcript: list[dict], max_chars: int = MAX_TRANSCRIPT_CHARS_FOR_AI) -> str:
    if not transcript:
        return ""
    total_duration = transcript[-1]["end"] - transcript[0]["start"]
    if total_duration < 90 * 60:
        lines = [f"{s['start']:.1f}-{s['end']:.1f}: {s['text']}" for s in transcript]
        return "\n".join(lines)[:max_chars]

    window = WINDOW_SECONDS
    n_windows = min(WINDOWS_TO_SAMPLE, max(4, int(total_duration // window)))
    step = total_duration / n_windows
    start0 = transcript[0]["start"]
    selected: list[dict] = []
    for i in range(n_windows):
        w_start = start0 + i * step
        w_end = w_start + window
        fo
