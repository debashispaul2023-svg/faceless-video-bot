from __future__ import annotations

from email.mime.text import MIMEText
from pathlib import Path
import os
import smtplib
import tempfile
import time


YDL_BASE = {
    "quiet": True,
    "noprogress": True,  # no per-percent download lines in CI logs
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
    "socket_timeout": 30,
    "retries": 3,
    "fragment_retries": 3,
    "sleep_interval": 1,
    "max_sleep_interval": 5,
    "sleep_interval_requests": 1,
}


NETWORK_RETRIES = 2


NETWORK_BACKOFF_SEC = 3


MAX_TRANSCRIPT_CHARS_FOR_AI = 90_000


WINDOW_SECONDS = 25 * 60


WINDOWS_TO_SAMPLE = 12


_DIAGNOSTIC_EMAIL_SENT = False


class ExtractionAuthError(RuntimeError):
    """YouTube requires sign-in / bot check — not recoverable in CI without valid cookies."""


class ExtractionRateLimitError(RuntimeError):
    """HTTP 429 from YouTube — stop hammering."""


class ExtractionNetworkError(RuntimeError):
    """Transient network failure — limited retries allowed."""


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


# Set to True after the pinned player clients fail: lets yt-dlp use its own
# up-to-date default client list (maintainers keep those current).
_USE_DEFAULT_CLIENTS = False


def _ydl_opts(**extra) -> dict:
    opts = {**YDL_BASE, **extra}
    # Metadata/caption lookups must not die just because no media format is offered.
    if extra.get("skip_download"):
        opts["ignore_no_formats_error"] = True
    if _USE_DEFAULT_CLIENTS:
        ea = {k: dict(v) for k, v in (opts.get("extractor_args") or {}).items()}
        ea.get("youtube", {}).pop("player_client", None)
        opts["extractor_args"] = ea
    cookiefile = _resolve_cookiefile()
    if cookiefile:
        opts["cookiefile"] = cookiefile
        # Do not print path content or cookie values
        print("[i] YouTube cookiefile is configured (contents not logged)")
    else:
        print("[i] No YouTube cookiefile — public extracts only; restricted videos may fail")
    return opts


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


def send_bot_alert_email(video_url: str, error_msg: str = "") -> bool:
    return send_diagnostic_email(video_url, error_msg, kind="auth")


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
            msg = str(e).lower()
            if (not _USE_DEFAULT_CLIENTS and attempt < NETWORK_RETRIES and any(
                k in msg for k in ("requested format is not available",
                                   "only images are available",
                                   "failed to extract any player response")
            )):
                globals()["_USE_DEFAULT_CLIENTS"] = True
                print("[i] Retrying with yt-dlp default player clients")
                continue
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


def extract_id_from_url(url: str) -> str:
    if "v=" in url:
        return url.split("v=")[1].split("&")[0][:11]
    if "/live/" in url:
        return url.split("/live/")[1].split("?")[0][:11]
    if "youtu.be/" in url:
        return url.split("youtu.be/")[1].split("?")[0][:11]
    return "unknown"
