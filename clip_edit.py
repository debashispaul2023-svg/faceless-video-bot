from __future__ import annotations

from pathlib import Path
import config
import os
import random
import requests
import subprocess


CLIP_VOICE_ID = os.getenv("CLIP_VOICE_ID") or config.CHARACTER_ELEVENLABS_VOICES.get(
    "narrator", "ErXwobaYiN019PkySvjV"
)


STYLES = ("voiceover", "text_story", "comparison")


def make_voiceover(text: str, out_path: Path) -> Path | None:
    """Generate a narrator line with ElevenLabs. Rotates keys; returns None on failure."""
    text = (text or "").strip()
    keys = list(getattr(config, "ELEVENLABS_API_KEYS", []) or [])
    if not text or not keys:
        if text and not keys:
            print("[i] ELEVENLABS_API_KEYS not set - skipping voiceover")
        return None
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{CLIP_VOICE_ID}"
    payload = {
        "text": text[:400],
        "model_id": config.ELEVENLABS_MODEL_ID,
        "voice_settings": {"stability": 0.4, "similarity_boost": 0.75, "style": 0.4,
                           "use_speaker_boost": True},
    }
    for k in keys:
        try:
            r = requests.post(url, headers={"xi-api-key": k, "Content-Type": "application/json"},
                              json=payload, params={"output_format": "mp3_44100_128"}, timeout=60)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:150]}")
            out_path.write_bytes(r.content)
            return out_path
        except Exception as e:
            print(f"[!] ElevenLabs key failed: {e}")
    return None


def assign_styles(clips: list[dict]) -> None:
    """Respect the AI's style but never allow the same style 3x in a row."""
    history: list[str] = []
    for c in clips:
        st = c.get("style") if c.get("style") in STYLES else random.choice(STYLES)
        if len(history) >= 2 and history[-1] == history[-2] == st:
            st = random.choice([x for x in STYLES if x != st])
        c["style"] = st
        history.append(st)


def _escape_drawtext(text: str) -> str:
    text = (text or "").replace("\n", " ").strip()
    if len(text) > 55:
        text = text[:52].rsplit(" ", 1)[0] + "…"
    for ch in ("\\", ":", "'", "%"):
        text = text.replace(ch, "\\" + ch)
    return text


def _ass_time(t: float) -> str:
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
    header = """[Script Info]
Title: Short captions
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
        per = max(8, int((dur / len(words)) * 100))
        karaoke = "".join(f"{{\\k{per}}}{w} " for w in words).strip()
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{karaoke}\n"
        )
    ass_path.write_text("".join(lines), encoding="utf-8")


def _find_whoosh() -> Path | None:
    root = Path(__file__).resolve().parent / "assets" / "sfx"
    if not root.is_dir():
        return None
    for name in ("Rake Swing Whoosh Hall.mp3", "whoosh.mp3", "impact.mp3"):
        p = root / name
        if p.is_file() and p.stat().st_size > 1000:
            return p
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


def _apply_loop_crossfade(path: Path, duration: float, xfade_d: float = 0.35) -> None:
    if duration <= xfade_d * 3:
        return
    tmp = path.with_name(path.stem + "_loop" + path.suffix)
    offset = duration - xfade_d
    fc = (
        f"[0:v]split=2[v0][v1];"
        f"[v0]trim=0:{offset},setpts=PTS-STARTPTS[main];"
        f"[v1]trim={offset}:{duration},setpts=PTS-STARTPTS[end];"
        f"[main][end]xfade=transition=fade:duration={xfade_d}:offset={offset - xfade_d}[vout]"
    )
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(path), "-filter_complex", fc,
             "-map", "[vout]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "23", "-c:a", "aac", str(tmp)],
            check=True, capture_output=True,
        )
        tmp.replace(path)
    except Exception as e:
        print(f"[i] loop crossfade skipped: {e}")
        tmp.unlink(missing_ok=True)


def _edit_clip_fallback(src: Path, dest: Path, captions: list[dict]) -> bool:
    """Simple 9:16 + basic subtitles if advanced filters fail."""
    srt = dest.with_suffix(".srt")
    with srt.open("w", encoding="utf-8") as f:
        for i, seg in enumerate(captions, 1):
            def fmt(t):
                h, m, s = int(t//3600), int((t%3600)//60), int(t%60)
                ms = int((t%1)*1000)
                return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
            f.write(f"{i}\n{fmt(seg['start'])} --> {fmt(seg['end'])}\n{seg['text']}\n\n")
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vf", f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,subtitles={srt}:force_style='Fontsize=28,PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=2'",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return dest.exists()
    except Exception as e:
        print(f"[!] Fallback edit failed: {e}")
        return False


def edit_clip(src: Path, dest: Path, captions: list[dict], zoom: bool = True,
              hook_text: str = "", voiceover_path: Path | None = None,
              style: str = "text_story") -> bool:
    """9:16 + optional zoom + word-highlight captions + hook + voiceover mix."""
    ass_path = dest.with_suffix(".ass")
    _write_word_highlight_ass(captions, ass_path)

    # Base video filters
    vf_parts = []
    if zoom:
        vf_parts.append(
            "zoompan=z='min(zoom+0.0015,1.15)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920"
        )
    else:
        vf_parts.append("scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920")
    vf_parts.append(f"subtitles={ass_path}")

    # Hook text overlay (first 2.5s)
    if hook_text:
        esc = _escape_drawtext(hook_text)
        vf_parts.append(
            f"drawtext=text='{esc}':fontsize=42:fontcolor=white:borderw=3:bordercolor=black:"
            f"x=(w-text_w)/2:y=120:enable='lt(t,2.5)'"
        )

    vf = ",".join(vf_parts)

    inputs = ["-i", str(src)]
    audio_map = "0:a"
    filter_complex = None

    if voiceover_path and voiceover_path.is_file():
        inputs += ["-i", str(voiceover_path)]
        # Mix voiceover over original (ducked)
        filter_complex = (
            "[1:a]volume=1.4[vo];"
            "[0:a]volume=0.35[bg];"
            "[bg][vo]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        audio_map = "[aout]"

    cmd = ["ffmpeg", "-y"] + inputs
    if filter_complex:
        cmd += ["-filter_complex", filter_complex, "-map", "0:v", "-map", audio_map]
    else:
        cmd += ["-map", "0:v", "-map", "0:a?"]
    cmd += [
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        str(dest),
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True)
        if dest.exists():
            # Optional loop polish
            dur = _probe_duration(dest)
            if dur > 8:
                _apply_loop_crossfade(dest, dur)
            return True
        return False
    except subprocess.CalledProcessError as e:
        print(f"[!] Edit failed: {(e.stderr or b'').decode(errors='ignore')[-500:]}")
        return _edit_clip_fallback(src, dest, captions)
    except Exception as e:
        print(f"[!] Edit failed: {e}")
        return _edit_clip_fallback(src, dest, captions)
