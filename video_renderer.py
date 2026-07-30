import os
from pathlib import Path
from moviepy import VideoFileClip, AudioFileClip, ImageClip, TextClip, CompositeVideoClip, concatenate_videoclips
import config

def render_final_video(image_paths: list, audio_path: Path, narration_text: str, output_filename: str) -> Path:
    """Renders final video with audio and basic formatting."""
    print("[+] Assembling video with MoviePy...")

    try:
        audio_clip = AudioFileClip(str(audio_path))
        duration_per_image = audio_clip.duration / len(image_paths)

        clips = []
        for img_path in image_paths:
            img_clip = ImageClip(str(img_path)).with_duration(duration_per_image)
            clips.append(img_clip)

        video_clip = concatenate_videoclips(clips, method="compose")
        final_clip = video_clip.with_audio(audio_clip)

        output_path = config.OUTPUT_DIR / output_filename
        final_clip.write_videofile(
            str(output_path),
            fps=24,
            codec="libx264",
            audio_codec="aac"
        )

        audio_clip.close()
        final_clip.close()

        print(f"[✓] Render completed: {output_path}")
        return output_path

    except Exception as e:
        print(f"[!] Error in video rendering (fallback version): {e}")
        try:
            audio_clip = AudioFileClip(str(audio_path))
            duration_per_image = audio_clip.duration / len(image_paths)
            clips = [ImageClip(str(img)).set_duration(duration_per_image) for img in image_paths]
            video = concatenate_videoclips(clips, method="compose").set_audio(audio_clip)
            output_path = config.OUTPUT_DIR / output_filename
            video.write_videofile(str(output_path), fps=24, codec="libx264", audio_codec="aac")
            return output_path
        except Exception as fallback_err:
            print(f"[!] Critical rendering failure: {fallback_err}")
            raise fallback_err
