import os
from pathlib import Path
from moviepy import (
    VideoFileClip, AudioFileClip, ImageClip, TextClip,
    CompositeVideoClip, concatenate_videoclips
)
import config


def _build_captions(narration_text: str, total_duration: float):
    """Builds word-chunked, Hormozi-style bold captions synced to audio duration."""
    words = narration_text.split()
    if not words:
        return []

    chunk_size = 3
    num_chunks = max(1, len(words) // chunk_size)
    chunk_duration = total_duration / num_chunks

    sub_clips = []
    for i in range(0, len(words), chunk_size):
        chunk_text = " ".join(words[i:i + chunk_size]).upper()
        start_time = (i // chunk_size) * chunk_duration

        txt_clip = (
            TextClip(
                text=chunk_text,
                font_size=70,
                color='yellow',
                stroke_color='black',
                stroke_width=4,
                method='caption',
                size=(config.VIDEO_WIDTH - 120, None)
            )
            .with_position(('center', 1350))
            .with_start(start_time)
            .with_duration(chunk_duration)
        )
        sub_clips.append(txt_clip)

    return sub_clips


def render_final_video(image_paths: list, audio_path: Path, narration_text: str, output_filename: str) -> Path:
    """Renders final video with audio, images, and styled captions."""
    print("[+] Assembling video with MoviePy...")

    audio_clip = AudioFileClip(str(audio_path))
    duration_per_image = audio_clip.duration / len(image_paths)

    clips = [ImageClip(str(img)).with_duration(duration_per_image) for img in image_paths]
    video_clip = concatenate_videoclips(clips, method="compose")
    video_with_audio = video_clip.with_audio(audio_clip)

    try:
        captions = _build_captions(narration_text, audio_clip.duration)
        if captions:
            final_clip = CompositeVideoClip([video_with_audio] + captions)
        else:
            final_clip = video_with_audio
    except Exception as e:
        print(f"[!] Caption rendering failed, continuing without captions: {e}")
        final_clip = video_with_audio

    output_path = config.OUTPUT_DIR / output_filename
    try:
        final_clip.write_videofile(
            str(output_path),
            fps=24,
            codec="libx264",
            audio_codec="aac"
        )
    except Exception as e:
        print(f"[!] Render with captions failed ({e}), retrying without captions...")
        video_with_audio.write_videofile(
            str(output_path),
            fps=24,
            codec="libx264",
            audio_codec="aac"
        )

    audio_clip.close()
    final_clip.close()

    print(f"[✓] Render completed: {output_path}")
    return output_path
