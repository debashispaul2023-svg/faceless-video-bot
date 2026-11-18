"""YouTube stream/VOD clipper for faceless-video-bot (entry point).

Split into: clip_core (yt-dlp helpers), clip_ai (Gemini), clip_yt (download/transcribe), clip_edit (FFmpeg/voiceover).
"""
from __future__ import annotations
import sys

# Make CI logs show progress live (stdout is block-buffered when piped).
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

from pathlib import Path
from youtube_uploader import find_next_available_slots, schedule_video_upload
import config
import json
import os
import sys
import time
import yt_dlp
from clip_ai import pick_best_clips
from clip_core import ExtractionAuthError, ExtractionNetworkError, ExtractionRateLimitError
from clip_edit import assign_styles, edit_clip, make_voiceover
from clip_yt import download_segment, download_transcript, get_video_id, transcribe_audio_with_gemini


VOICEOVER_ENABLED = os.getenv("CLIP_VOICEOVER", "0") == "1"   # narrator needs ElevenLabs keys
TIME_BUDGET_SEC = int(os.getenv("CLIP_TIME_BUDGET_MIN", "80")) * 60   # stop making new clips after this
MIN_LEN, MAX_LEN = 30.0, 40.0                                  # target Short length (seconds)


def _fit_length(start: float, end: float) -> tuple[float, float]:
    """Force every clip into the 30-40s window (trim long ones, extend short ones)."""
    dur = end - start
    if dur > MAX_LEN + 2:
        end = start + MAX_LEN
    elif dur < MIN_LEN - 2:
        end = start + MIN_LEN + 2
    return start, end


def process_url(url: str, max_clips: int = 10, schedule: bool = True,
                video_title: str = "stream", streamer: str = "") -> list[Path]:
    """Clip a stream/VOD into Shorts. Fail fast on auth/429. Preserve outputs on upload errors."""
    t0 = time.time()
    print(f"[+] yt-dlp version: {yt_dlp.version.__version__}")
    vid = get_video_id(url)
    workdir = config.OUTPUT_DIR / "clips" / vid
    workdir.mkdir(parents=True, exist_ok=True)
    print(f"[+] Analyzing {url} (id={vid})")

    try:
        transcript = download_transcript(url, workdir)
    except ExtractionAuthError as e:
        print(f"[!] Auth challenge — stopping extraction. {e}")
        return []
    except ExtractionRateLimitError as e:
        print(f"[!] Rate limited — stopping extraction. {e}")
        return []
    except ExtractionNetworkError as e:
        print(f"[!] Network failure after retries. {e}")
        return []
    except Exception as e:
        print(f"[!] Transcript download failed: {e}")
        return []

    if not transcript:
        print("[i] No captions - trying audio transcription fallback")
        transcript = transcribe_audio_with_gemini(url, workdir)
    if not transcript:
        print("[!] No transcript available (captions and audio fallback both failed).")
        return []

    duration_h = (transcript[-1]["end"] - transcript[0]["start"]) / 3600.0
    print(f"[+] Got {len(transcript)} segments spanning ~{duration_h:.1f} hours")

    clips = pick_best_clips(transcript, video_title=video_title, max_clips=max_clips, streamer=streamer)
    print(f"[+] AI selected {len(clips)} clips (target {max_clips})")
    if not clips:
        print("[!] No clips selected — AI or parse failure")
        return []

    slots = []
    if schedule:
        try:
            slots = find_next_available_slots(max(len(clips), max_clips))
        except Exception as e:
            print(f"[!] Could not fetch schedule slots (OAuth?): {e}")
            print("[i] Clips will still be produced; schedule skipped")

    assign_styles(clips)
    produced: list[Path] = []
    for i, clip in enumerate(clips, 1):
        if time.time() - t0 > TIME_BUDGET_SEC:
            print(f"[!] Time budget ({TIME_BUDGET_SEC // 60} min) reached - stopping with {len(produced)} Short(s) done", flush=True)
            break
        print(f"[i] --- clip {i}/{len(clips)} (elapsed {time.time() - t0:.0f}s) ---", flush=True)
        start, end = _fit_length(float(clip["start"]), float(clip["end"]))
        dur = end - start
        if dur < 20 or dur > 60:
            print(f"[i] Skipping clip {i}: duration {dur:.1f}s out of range")
            continue
        raw = workdir / f"raw_{i}.mp4"
        final = workdir / f"short_{i}.mp4"
        print(f"[+] Downloading segment {i}: {start:.1f}s → {end:.1f}s")
        if not download_segment(url, start, end, raw):
            # Auth failure on first segment should stop the batch
            continue
        rel_caps = [
            {
                "start": max(0, s["start"] - start),
                "end": max(0.1, s["end"] - start),
                "text": s["text"],
                "words": [
                    {"w": w["w"], "start": max(0, w["start"] - start), "end": max(0.1, w["end"] - start)}
                    for w in (s.get("words") or [])
                ] or None,
            }
            for s in transcript
            if s["end"] > start and s["start"] < end
        ]
        title = (clip.get("title") or f"Best Moment {i}")[:100]
        if not title.upper().startswith("GTA"):
            title = f"GTA 6 {title}"[:100]
        style = clip.get("style", "text_story")
        if style == "voiceover" and not VOICEOVER_ENABLED:
            style = "text_story"
        hook = (clip.get("hook") or title)[:60]
        vo_path = None
        if style == "voiceover":
            vo_path = make_voiceover(clip.get("voiceover", ""), workdir / f"vo_{i}.mp3")
            if vo_path is None:
                style = "text_story"  # no narrator available -> text-led style
        print(f"[i] Clip {i} style={style} voiceover={'yes' if vo_path else 'no'}")
        if not edit_clip(raw, final, rel_caps, zoom=True, hook_text=hook,
                         voiceover_path=vo_path, style=style):
            continue
        produced.append(final)
        who = streamer.replace("@", "") if streamer else ""
        tags = ["GTA6", "GTA6Shorts", "GTA6Clips", "shorts", "GTA", "gaming", "ViceCity", "GTA6Moments"]
        if who:
            tags += [who, f"{who}Clips"]
        credit = f"Clip from {who}'s stream: {url}&t={int(start)}s" if who else f"Source: {url}&t={int(start)}s"
        meta = {
            "title": title,
            "description": (
                f"{title}\n\n{credit}\n"
                f"All credit to the original creator. Clipped & edited as a highlight.\n\n"
                + " ".join(f"#{t}" for t in tags[:6])
            ),
            "hashtags": tags,
        }
        (workdir / f"meta_{i}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        if schedule and slots:
            try:
                day, hour, minute = slots.pop(0)
                print(f"[+] Scheduling clip {i} → day+{day} at {hour:02d}:{minute:02d} IST")
                schedule_video_upload(final, meta, hour, minute, day_offset=day)
            except Exception as e:
                print(f"[!] Upload/schedule failed (clip file preserved): {e}")

    print(f"[+] Produced {len(produced)} Shorts")
    return produced


QUEUE_TARGET_DAYS = int(os.getenv("CLIP_QUEUE_DAYS", "7"))   # keep this many days of Shorts scheduled ahead


def run_auto(upload: bool = True, max_clips: int | None = None) -> int:
    """Daily entry point: keep ~QUEUE_TARGET_DAYS of Shorts scheduled (1 per day).

    Returns 0 on success (including 'queue already full'), 1 on failure.
    """
    from clip_sources import find_next_stream, mark_processed

    if upload:
        try:
            from youtube_uploader import find_next_available_slots
            ahead = find_next_available_slots(1)[0][0]
        except Exception as e:
            print(f"[!] Could not read the upload queue ({e}); assuming it is empty")
            ahead = 0
        if ahead >= QUEUE_TARGET_DAYS:
            print(f"[+] Queue already has {ahead} day(s) scheduled - nothing to do today")
            return 0
        need = QUEUE_TARGET_DAYS - ahead
    else:
        need = 3
    want = max_clips or need

    stream = find_next_stream()
    if not stream:
        print("[!] Nothing to clip today (no new stream matched). Check CLIP_CHANNELS / CLIP_TITLE_KEYWORDS.")
        return 0
    out = process_url(stream["url"], max_clips=min(want, 10), schedule=upload,
                      video_title=stream["title"], streamer=stream["channel"])
    if out:
        mark_processed(stream["id"])
        return 0
    return 1


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="GTA 6 stream -> Shorts clipper")
    ap.add_argument("url", nargs="?", help="YouTube stream/VOD URL (omit with --auto)")
    ap.add_argument("--auto", action="store_true", help="pick the next stream from CLIP_CHANNELS")
    ap.add_argument("--upload", action="store_true", help="schedule uploads on YouTube (1 per day)")
    ap.add_argument("--max-clips", type=int, default=None)
    a = ap.parse_args()
    if a.auto:
        sys.exit(run_auto(upload=a.upload, max_clips=a.max_clips))
    if not a.url:
        ap.error("give a URL or use --auto")
    out = process_url(a.url, max_clips=a.max_clips or 10, schedule=a.upload)
    sys.exit(0 if out else 1)
