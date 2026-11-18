"""Find the next stream/VOD to clip from a list of channels (no manual URL needed).

Config (env, all optional):
  CLIP_CHANNELS        comma-separated channel handles/URLs. Default: @IShowSpeed,@MrBeast
  CLIP_TITLE_KEYWORDS  comma-separated, a video title must contain one (case-insensitive).
                       Default "gta". Set to "" to accept every stream.
  CLIP_MIN_MINUTES     ignore anything shorter (default 20)
  CLIP_MAX_AGE_VIDEOS  how many recent streams to look at per channel (default 15)

Processed video ids are remembered in assets/processed_streams.json (the workflow
commits that file back to the repo so the bot never clips the same stream twice).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import yt_dlp

from clip_core import _ydl_opts, run_extraction

STATE_FILE = Path(__file__).resolve().parent / "assets" / "processed_streams.json"

DEFAULT_CHANNELS = "@IShowSpeed,@MrBeast"


def _channels() -> list[str]:
    raw = os.getenv("CLIP_CHANNELS", DEFAULT_CHANNELS)
    return [c.strip() for c in raw.split(",") if c.strip()]


def _keywords() -> list[str]:
    raw = os.getenv("CLIP_TITLE_KEYWORDS", "gta")
    return [k.strip().lower() for k in raw.split(",") if k.strip()]


def _tab_urls(channel: str) -> list[str]:
    """Streams tab first (live VODs), then regular uploads."""
    if channel.startswith("http"):
        base = channel.rstrip("/")
    else:
        base = f"https://www.youtube.com/{channel if channel.startswith('@') else '@' + channel}"
    return [f"{base}/streams", f"{base}/videos"]


def load_processed() -> set[str]:
    try:
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def mark_processed(video_id: str) -> None:
    ids = load_processed()
    ids.add(video_id)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(sorted(ids), indent=2), encoding="utf-8")


def _list_tab(url: str) -> list[dict]:
    n = int(os.getenv("CLIP_MAX_AGE_VIDEOS", "15"))

    def _do():
        opts = _ydl_opts(skip_download=True, extract_flat=True, playlistend=n)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return [e for e in (info.get("entries") or []) if e and e.get("id")]

    try:
        return run_extraction(url, _do, f"list {url}")
    except Exception as e:
        print(f"[i] Could not list {url}: {type(e).__name__}: {str(e)[:120]}")
        return []


def find_next_stream() -> dict | None:
    """Return {'id','url','title','channel'} for the newest unprocessed matching stream."""
    done = load_processed()
    kws = _keywords()
    min_sec = int(os.getenv("CLIP_MIN_MINUTES", "20")) * 60
    for channel in _channels():
        for tab in _tab_urls(channel):
            for e in _list_tab(tab):
                vid = e["id"]
                title = e.get("title") or ""
                if vid in done:
                    continue
                if e.get("live_status") in ("is_live", "is_upcoming"):
                    continue
                dur = e.get("duration")
                if dur is not None and dur < min_sec:
                    continue
                if kws and not any(k in title.lower() for k in kws):
                    continue
                print(f"[+] Next stream: {title!r} ({vid}) from {channel}")
                return {
                    "id": vid,
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "title": title,
                    "channel": channel.lstrip("@").split("/")[-1],
                }
    print("[i] No new matching stream found (all processed, or no title matched the keywords)")
    return None
