from __future__ import annotations

from google import genai
from pathlib import Path
import json
import os
import subprocess
import time
import yt_dlp
from clip_ai import (
    GeminiQuotaExhausted, gemini_generate, get_api_keys, key_label, make_client,
    next_usable_key, wait_file_active,
)
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


AUDIO_CHUNK_SEC = 1200  # fewer, longer chunks = fewer API calls (free tier is request-limited)


MAX_AUDIO_MINUTES = int(os.getenv("MAX_AUDIO_MINUTES", "240"))


def _parse_json_array(text: str) -> list:
    """Parse a JSON array from model output; salvage complete items if it was cut off."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    start = text.find("[")
    if start == -1:
        return []
    end = text.rfind("]")
    try:
        return json.loads(text[start:end + 1]) if end > start else json.loads(text[start:])
    except Exception:
        pass
    # truncated output: keep everything up to the last complete {...} object
    cut = text.rfind("}")
    while cut > start:
        try:
            return json.loads(text[start:cut + 1] + "]")
        except Exception:
            cut = text.rfind("}", start, cut)
    return []


def transcribe_audio_with_gemini(url: str, workdir: Path) -> list[dict]:
    """Download low-bitrate audio, split into chunks, transcribe each with Gemini."""
    keys = get_api_keys()
    if not keys:
        print("[!] GEMINI_API_KEY / GEMINI_API_KEYS missing - cannot transcribe audio")
        return []
    print(f"[i] {len(keys)} Gemini key(s) configured", flush=True)

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
    cur_key = next_usable_key() or keys[0]
    client = make_client(cur_key)
    segments: list[dict] = []

    prompt = (
        "Transcribe this audio (a GTA gameplay stream; speech may be Hindi, English or mixed). "
        "Return ONLY a JSON array. Each item: {\"start\": seconds_from_start_of_this_audio (number), "
        "\"end\": seconds (number), \"text\": \"what was said\"}. "
        "Use segments of roughly 5-15 seconds. Also include short notes in [brackets] for loud "
        "events (crash, siren, gunfire, shouting). Keep original language; Hindi in Roman script is fine."
    )
    try:
        from google.genai import types as _gtypes
        json_cfg = _gtypes.GenerateContentConfig(response_mime_type="application/json")
    except Exception:
        json_cfg = None
    for idx, chunk in enumerate(chunks):
        offset = idx * AUDIO_CHUNK_SEC
        for attempt in range(3 + len(keys)):  # extra attempts so a key switch is not "wasted"
            try:
                print(f"[i] chunk {idx + 1}/{len(chunks)} attempt {attempt + 1}: uploading...", flush=True)
                up = client.files.upload(file=str(chunk))
                wait_file_active(client, up)
                print(f"[i] chunk {idx + 1}/{len(chunks)}: transcribing...", flush=True)
                resp = gemini_generate(client, [prompt, up], config=json_cfg)
                try:
                    client.files.delete(name=up.name)
                except Exception:
                    pass
                items = _parse_json_array(resp.text)
                if not items:
                    snippet = (resp.text or "")[:200].replace("\n", " ")
                    print(f"[!] chunk {idx + 1}/{len(chunks)} attempt {attempt + 1}: empty/unparseable "
                          f"response: {snippet!r}", flush=True)
                    if attempt < 2:
                        time.sleep(5)
                        continue
                for it in items:
                    txt = str(it.get("text", "")).strip()
                    if txt:
                        segments.append({
                            "start": offset + float(it.get("start", 0)),
                            "end": offset + float(it.get("end", it.get("start", 0))),
                            "text": txt,
                        })
                break
            except GeminiQuotaExhausted as e:
                nxt = next_usable_key(cur_key)
                if nxt:
                    print(f"[!] {e} Switching to {key_label(nxt)} and retrying chunk", flush=True)
                    cur_key = nxt
                    client = make_client(cur_key)
                    continue
                print(f"[!] {e} No other Gemini key left.", flush=True)
                for c in chunks[idx:]:
                    c.unlink(missing_ok=True)
                print(f"[!] Stopping transcription early - partial transcript: {len(segments)} segments", flush=True)
                return segments
            except Exception as e:
                print(f"[!] chunk {idx + 1}/{len(chunks)} attempt {attempt + 1} failed: {e}")
                time.sleep(10 * (attempt + 1))
        print(f"[i] chunk {idx + 1}/{len(chunks)} done - {len(segments)} segments so far", flush=True)
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
