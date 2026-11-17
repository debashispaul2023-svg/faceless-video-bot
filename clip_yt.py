from __future__ import annotations

from google import genai
from pathlib import Path
import json
import os
import subprocess
import time
import yt_dlp
from clip_ai import gemini_generate
from clip_core import ExtractionAuthError, ExtractionNetworkError, ExtractionRateLimitError, _ydl_opts, extract_id_from_url, run_extraction


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


AUDIO_CHUNK_SEC = 600


MAX_AUDIO_MINUTES = int(os.getenv("MAX_AUDIO_MINUTES", "240"))


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

    print(f"[i] Audio downloaded ({src.stat().st_size / 1e6:.1f} MB) - splitting into chunks...", flush=True)
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
    print(f"[i] Split into {len(chunks)} chunks", flush=True)
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
