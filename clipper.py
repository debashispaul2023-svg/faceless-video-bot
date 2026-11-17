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
import sys
import yt_dlp
from clip_ai import pick_best_clips
from clip_core import ExtractionAuthError, ExtractionNetworkError, ExtractionRateLimitError
from clip_edit import assign_styles, edit_clip, make_voiceover
from clip_yt import download_segment, download_transcript, get_video_id, transcribe_audio_with_gemini


def process_url(url: str, max_clips: int = 10, schedule: bool = True) -> list[Path]:
    """Clip a stream/VOD into Shorts. Fail fast on auth/429. Preserve outputs on upload errors."""
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

    clips = pick_best_clips(transcript, video_title="stream", max_clips=max_clips)
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
        start, end = float(clip["start"]), float(clip["end"])
        dur = end - start
        if dur < 10 or dur > 55:
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
            }
            for s in transcript
            if s["end"] > start and s["start"] < end
        ]
        title = (clip.get("title") or f"Best Moment {i}")[:100]
        if not title.upper().startswith("GTA"):
            title = f"GTA 6 {title}"[:100]
        style = clip.get("style", "text_story")
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
        meta = {
            "title": title,
            "description": (
                f"{title}\n\nClipped from stream.\n\n"
                f"#GTA6 #GTA6Shorts #GTA6Clips #shorts #gaming #GTA"
            ),
            "hashtags": [
                "GTA6", "GTA6Shorts", "GTA6Clips", "shorts",
                "GTA", "gaming", "ViceCity", "GTA6Moments",
            ],
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


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python clipper.py <youtube_url>")
        sys.exit(1)
    out = process_url(sys.argv[1])
    sys.exit(0 if out else 1)
