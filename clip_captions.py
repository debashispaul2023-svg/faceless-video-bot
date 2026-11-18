"""Karaoke-style captions for Shorts.

- Word-level timing: faster-whisper (best) -> YouTube json3 word offsets -> even split.
- Output is an ASS file: 1-3 words on screen at a time, the word being spoken is
  highlighted (yellow) and "pops" (scale up/down), the rest are white.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

FONT = os.getenv("CLIP_FONT", "DejaVu Sans")
FONT_SIZE = int(os.getenv("CLIP_FONT_SIZE", "92"))
WORDS_PER_CHUNK = int(os.getenv("CLIP_WORDS_PER_CHUNK", "3"))
MAX_CHARS_PER_CHUNK = 20

# ASS colours are &HAABBGGRR
WHITE = "&H00FFFFFF&"
HIGHLIGHT = "&H0000F0FF&"   # yellow-ish
HOOK_COLOR = "&H0000F0FF&"

_BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)")


def _clean(word: str) -> str:
    word = word.replace("{", "").replace("}", "").replace("\\", "").strip()
    return word


# --------------------------------------------------------------------------- word timing

_WHISPER = None
_WHISPER_FAILED = False


def _whisper_model():
    global _WHISPER, _WHISPER_FAILED
    if _WHISPER is not None or _WHISPER_FAILED:
        return _WHISPER
    if os.getenv("CLIP_WHISPER", "1") == "0":
        _WHISPER_FAILED = True
        return None
    try:
        from faster_whisper import WhisperModel
        name = os.getenv("CLIP_WHISPER_MODEL", "base")
        print(f"[i] Loading faster-whisper model '{name}' for word-level captions", flush=True)
        _WHISPER = WhisperModel(name, device="cpu", compute_type="int8")
    except Exception as e:
        print(f"[i] faster-whisper unavailable ({type(e).__name__}) - using caption timing fallback")
        _WHISPER_FAILED = True
    return _WHISPER


def words_from_whisper(video: Path) -> list[dict]:
    """Transcribe the clip's own audio with word timestamps. [] on any failure."""
    model = _whisper_model()
    if model is None:
        return []
    wav = video.with_suffix(".16k.wav")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1",
             "-ar", "16000", str(wav)],
            check=True, capture_output=True,
        )
        lang = os.getenv("CLIP_LANGUAGE", "").strip() or None
        segments, _info = model.transcribe(
            str(wav), word_timestamps=True, vad_filter=True, language=lang,
            condition_on_previous_text=False,
        )
        words: list[dict] = []
        for seg in segments:
            for w in (seg.words or []):
                txt = _clean(w.word)
                if txt:
                    words.append({"w": txt, "start": float(w.start), "end": float(w.end)})
        return words
    except Exception as e:
        print(f"[i] whisper word timing failed: {type(e).__name__}: {e}")
        return []
    finally:
        wav.unlink(missing_ok=True)


def words_from_segments(captions: list[dict]) -> list[dict]:
    """Fallback: use per-word offsets from YouTube json3 if present, else split evenly."""
    words: list[dict] = []
    for seg in captions:
        text = _BRACKETS.sub(" ", seg.get("text") or "").strip()
        s, e = float(seg["start"]), float(seg["end"])
        yt_words = seg.get("words")
        if yt_words:
            for i, w in enumerate(yt_words):
                txt = _clean(w["w"])
                if not txt:
                    continue
                ws = max(s, float(w["start"]))
                we = float(w.get("end") or (yt_words[i + 1]["start"] if i + 1 < len(yt_words) else e))
                words.append({"w": txt, "start": ws, "end": max(ws + 0.08, we)})
            continue
        toks = [_clean(t) for t in text.split()]
        toks = [t for t in toks if t]
        if not toks:
            continue
        per = max(0.12, (e - s) / len(toks))
        for i, t in enumerate(toks):
            words.append({"w": t, "start": s + i * per, "end": s + (i + 1) * per})
    return words


# --------------------------------------------------------------------------- ASS

def _ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _chunks(words: list[dict]) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        too_long = sum(len(x["w"]) + 1 for x in cur) + len(w["w"]) > MAX_CHARS_PER_CHUNK
        gap = cur and (w["start"] - cur[-1]["end"] > 0.6)
        ends_sentence = cur and cur[-1]["w"].endswith((".", "?", "!"))
        if cur and (len(cur) >= WORDS_PER_CHUNK or too_long or gap or ends_sentence):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def _wrap_hook(text: str, width: int = 18) -> str:
    words = text.upper().split()
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\\N".join(lines[:3])


def write_karaoke_ass(words: list[dict], ass_path: Path, hook_text: str = "",
                      layout: str = "blur", clip_duration: float | None = None) -> int:
    """Write the ASS file. Returns number of caption events written."""
    if layout == "blur":
        cap_margin_v, hook_margin_v = 360, 330
    else:  # crop (fullscreen gameplay)
        cap_margin_v, hook_margin_v = 480, 260

    header = f"""[Script Info]
Title: Short captions
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{FONT},{FONT_SIZE},{WHITE},{WHITE},&H00000000&,&H64000000&,-1,0,0,0,100,100,0,0,1,7,3,2,50,50,{cap_margin_v},1
Style: Hook,{FONT},74,{HOOK_COLOR},{HOOK_COLOR},&H00000000&,&H64000000&,-1,0,0,0,100,100,0,0,1,7,3,8,60,60,{hook_margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    out = [header]
    n_events = 0

    if hook_text.strip():
        end = 2.8
        out.append(
            f"Dialogue: 1,{_ass_time(0)},{_ass_time(end)},Hook,,0,0,0,,"
            f"{{\\fad(120,250)\\fscx60\\fscy60\\t(0,180,\\fscx100\\fscy100)}}{_wrap_hook(hook_text)}\n"
        )

    words = [w for w in words if w["end"] > w["start"] or w["end"] == w["start"]]
    words.sort(key=lambda w: w["start"])
    chunks = _chunks(words)
    for ci, chunk in enumerate(chunks):
        chunk_end = chunk[-1]["end"]
        nxt_start = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else None
        if nxt_start is not None and 0 <= nxt_start - chunk_end < 0.35:
            chunk_end = nxt_start  # no flicker between consecutive chunks
        for wi, w in enumerate(chunk):
            w_start = w["start"]
            w_end = chunk[wi + 1]["start"] if wi + 1 < len(chunk) else chunk_end
            if w_end - w_start < 0.06:
                w_end = w_start + 0.06
            if clip_duration and w_start >= clip_duration:
                continue
            parts = []
            for j, x in enumerate(chunk):
                txt = x["w"].upper()
                if j == wi:
                    parts.append(
                        f"{{\\c{HIGHLIGHT}\\fscx118\\fscy118\\t(0,90,\\fscx100\\fscy100)}}{txt}{{\\r}}"
                    )
                else:
                    parts.append(f"{{\\c{WHITE}}}{txt}")
            out.append(
                f"Dialogue: 0,{_ass_time(w_start)},{_ass_time(w_end)},Cap,,0,0,0,,{' '.join(parts)}\n"
            )
            n_events += 1
    ass_path.write_text("".join(out), encoding="utf-8")
    return n_events
