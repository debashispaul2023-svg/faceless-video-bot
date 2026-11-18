from __future__ import annotations

from pathlib import Path
import config
import os
import random
import requests
import shutil
import subprocess

from clip_captions import words_from_segments, words_from_whisper, write_karaoke_ass


CLIP_VOICE_ID = os.getenv("CLIP_VOICE_ID") or config.ELEVENLABS_NARRATOR_VOICE


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


# --------------------------------------------------------------------------- settings

LAYOUT = os.getenv("CLIP_LAYOUT", "blur").strip().lower()      # blur | crop | facecam


def _parse_facecam() -> tuple[float, float, float, float]:
    """CLIP_FACECAM="x,y,w,h" as fractions of the source frame (default: top-right corner)."""
    try:
        x, y, w, h = (float(v) for v in os.getenv("CLIP_FACECAM", "0.74,0,0.26,0.26").split(","))
        if w > 0 and h > 0:
            return x, y, w, h
    except Exception:
        pass
    return 0.74, 0.0, 0.26, 0.26


FACECAM_BOX = _parse_facecam()
# Rule A: reset audio PTS to 0 and resample in sync (facecam layout only).
AUDIO_SYNC = "asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0" if LAYOUT == "facecam" else ""
ZOOM_AMOUNT = float(os.getenv("CLIP_ZOOM_AMOUNT", "0.14"))      # 0.14 = zoom up to 114%
ZOOM_PERIOD = float(os.getenv("CLIP_ZOOM_PERIOD", "5"))         # seconds per zoom-in + zoom-out
USE_SFX = os.getenv("CLIP_SFX", "1") != "0"
OUT_W, OUT_H = 1080, 1920


def _probe_size(path: Path) -> tuple[int, int]:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
            capture_output=True, text=True, check=True,
        )
        w, h = r.stdout.strip().split("x")
        return int(w), int(h)
    except Exception:
        return 1920, 1080


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


def _has_audio(path: Path) -> bool:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
             "stream=index", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, check=True,
        )
        return bool(r.stdout.strip())
    except Exception:
        return False


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


def _ff_path(p: Path) -> str:
    """Escape a path for use inside an ffmpeg filter argument."""
    s = str(p).replace("\\", "/")
    for ch in (":", "'", ",", "[", "]", ";"):
        s = s.replace(ch, "\\" + ch)
    return s


def _zoom_expr() -> str:
    """Smooth zoom-in then zoom-out, repeating every ZOOM_PERIOD seconds."""
    return f"(1+{ZOOM_AMOUNT}*(0.5-0.5*cos(2*PI*t/{ZOOM_PERIOD})))"


def _video_graph(src_w: int, src_h: int, ass_path: Path) -> str:
    """filter_complex fragment that turns input 0 into [vout] (1080x1920, zoom + captions)."""
    z = _zoom_expr()
    ass = _ff_path(ass_path)
    if LAYOUT == "facecam":
        # Facecam enlarged on top (620x400), 3:4 gameplay below (1080x1440 at y=400),
        # blurred + darkened gameplay fills the whole 1080x1920 canvas.
        fx, fy, fw, fh = FACECAM_BOX
        return (
            f"[0:v]fps=30,split=3[b][g][f];"
            f"[b]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W}:{OUT_H},boxblur=25:2,eq=brightness=-0.15[bg];"
            f"[g]crop=ih*3/4:ih,scale={OUT_W}:1440,setsar=1[gp];"
            f"[f]crop=iw*{fw}:ih*{fh}:iw*{fx}:ih*{fy},"
            f"scale=620:400:force_original_aspect_ratio=increase,crop=620:400[fc];"
            f"[bg][gp]overlay=0:400[t1];"
            f"[t1][fc]overlay=(W-w)/2:0[t2];"
            f"[t2]setpts=PTS-STARTPTS,ass='{ass}'[vout]"
        )
    if LAYOUT == "crop":
        # Fullscreen: fill 9:16 with the centre of the gameplay, zoom in/out on top.
        return (
            f"[0:v]fps=30,scale=-2:{OUT_H},"
            f"scale=w='trunc(iw*{z}/2)*2':h=-2:eval=frame,"
            f"crop={OUT_W}:{OUT_H}:(iw-{OUT_W})/2:(ih-{OUT_H})/2,setsar=1[v0];"
            f"[v0]ass='{ass}'[vout]"
        )
    # blur layout: whole 16:9 gameplay in the middle, blurred copy fills the 9:16 canvas.
    fg_h = int(round(OUT_W * src_h / src_w / 2)) * 2
    return (
        f"[0:v]fps=30,split=2[a][b];"
        f"[a]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
        f"crop={OUT_W}:{OUT_H},boxblur=28:4,eq=brightness=-0.18:saturation=1.15[bg];"
        f"[b]scale={OUT_W}:{fg_h},"
        f"scale=w='trunc({OUT_W}*{z}/2)*2':h=-2:eval=frame,"
        f"crop={OUT_W}:{fg_h}:(iw-{OUT_W})/2:(ih-{fg_h})/2,setsar=1[fg];"
        f"[bg][fg]overlay=0:(H-h)/2[v0];"
        f"[v0]ass='{ass}'[vout]"
    )


def _sfx_times(duration: float) -> list[float]:
    """Whoosh on the hook and on every second zoom-in."""
    times, t = [0.0], 2 * ZOOM_PERIOD
    while t < duration - 1.5 and len(times) < 5:
        times.append(t)
        t += 2 * ZOOM_PERIOD
    return times


def _audio_graph(has_audio: bool, has_vo: bool, sfx_path: Path | None, duration: float,
                 vo_index: int, sfx_index: int) -> tuple[str, str] | None:
    """Return (filter_complex fragment, output label) or None when plain `-map 0:a` is enough."""
    chains: list[str] = []
    mix: list[str] = []
    if has_audio:
        pre = AUDIO_SYNC + "," if AUDIO_SYNC else ""
        chains.append(f"[0:a]{pre}volume={0.35 if has_vo else 1.0}[main]")
        mix.append("[main]")
    if has_vo:
        chains.append(f"[{vo_index}:a]volume=1.4[vo]")
        mix.append("[vo]")
    if sfx_path is not None:
        times = _sfx_times(duration)
        labels = "".join(f"[s{i}]" for i in range(len(times)))
        chains.append(f"[{sfx_index}:a]volume=0.55,asplit={len(times)}{labels}" if len(times) > 1
                      else f"[{sfx_index}:a]volume=0.55[s0]")
        for i, t in enumerate(times):
            ms = int(t * 1000)
            chains.append(f"[s{i}]adelay={ms}|{ms}[d{i}]")
            mix.append(f"[d{i}]")
    if not mix or (len(mix) == 1 and mix[0] == "[main]"):
        return None
    chains.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0:dropout_transition=0[aout]")
    return ";".join(chains), "[aout]"


def _video_encoder() -> list[str]:
    """NVENC when an NVIDIA GPU is usable, otherwise libx264 veryfast."""
    try:
        if shutil.which("nvidia-smi"):
            subprocess.run(["nvidia-smi"], check=True, capture_output=True)
            enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                                 capture_output=True, text=True).stdout
            if "h264_nvenc" in enc:
                return ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "21"]
    except Exception:
        pass
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"]


def _edit_clip_fallback(src: Path, dest: Path, ass_path: Path) -> bool:
    """Plain centre-crop 9:16 + the same karaoke captions, no zoom/SFX."""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
        "-vf", f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},"
               f"ass='{_ff_path(ass_path)}'",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return dest.exists()
    except subprocess.CalledProcessError as e:
        print(f"[!] Fallback edit failed: {(e.stderr or b'').decode(errors='ignore')[-400:]}")
        return False


def edit_clip(src: Path, dest: Path, captions: list[dict], zoom: bool = True,
              hook_text: str = "", voiceover_path: Path | None = None,
              style: str = "text_story") -> bool:
    """Raw 16:9 segment -> 1080x1920 Short.

    * karaoke captions (word-level; whisper -> yt word offsets -> even split)
    * smooth zoom in / zoom out loop
    * blurred-background layout (default) or fullscreen crop (CLIP_LAYOUT=crop)
    * hook text for the first ~3s, whoosh SFX on zooms, optional narrator
    """
    duration = _probe_duration(src)
    src_w, src_h = _probe_size(src)

    words = words_from_whisper(src) or words_from_segments(captions)
    ass_path = dest.with_suffix(".ass")
    n = write_karaoke_ass(words, ass_path, hook_text=hook_text, layout=LAYOUT, clip_duration=duration or None)
    print(f"[i] captions: {n} karaoke events from {len(words)} words")

    has_vo = bool(voiceover_path and Path(voiceover_path).is_file())
    has_audio = _has_audio(src)
    sfx = _find_whoosh() if USE_SFX else None

    inputs = ["-i", str(src)]
    idx = 1
    vo_index = sfx_index = -1
    if has_vo:
        inputs += ["-i", str(voiceover_path)]
        vo_index = idx
        idx += 1
    if sfx is not None:
        inputs += ["-i", str(sfx)]
        sfx_index = idx
        idx += 1

    graph = _video_graph(src_w, src_h, ass_path) if (zoom or LAYOUT == "facecam") else (
        f"[0:v]fps=30,scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
        f"crop={OUT_W}:{OUT_H},ass='{_ff_path(ass_path)}'[vout]"
    )
    audio = _audio_graph(has_audio, has_vo, sfx, duration or 40.0, vo_index, sfx_index)
    filter_complex = graph + (";" + audio[0] if audio else "")

    cmd = ["ffmpeg", "-y", "-loglevel", "error"] + inputs + [
        "-filter_complex", filter_complex, "-map", "[vout]",
    ]
    if audio:
        cmd += ["-map", audio[1]]
    elif has_audio:
        cmd += ["-map", "0:a"]
        if AUDIO_SYNC:
            cmd += ["-af", AUDIO_SYNC]
    cmd += _video_encoder() + [
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
    ]
    if duration:
        cmd += ["-t", f"{duration:.2f}"]
    cmd.append(str(dest))

    try:
        subprocess.run(cmd, check=True, capture_output=True)
        if dest.exists() and dest.stat().st_size > 10_000:
            return True
        return False
    except subprocess.CalledProcessError as e:
        print(f"[!] Edit failed, trying simple fallback: {(e.stderr or b'').decode(errors='ignore')[-600:]}")
        return _edit_clip_fallback(src, dest, ass_path)
    except Exception as e:
        print(f"[!] Edit failed: {e}")
        return _edit_clip_fallback(src, dest, ass_path)
