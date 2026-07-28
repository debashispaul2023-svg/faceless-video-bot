from pathlib import Path
from moviepy.editor import (
    AudioFileClip, ImageClip, concatenate_videoclips, TextClip, CompositeVideoClip
)
import config

def render_final_video(image_paths: list, audio_path: Path, narration_text: str, output_filename: str = "final_short.mp4") -> Path:
    """Assembles image sequence, overlays audio, and generates styled subtitles."""
    print("[+] Rendering Video and layering assets with MoviePy...")
    output_path = config.OUTPUT_DIR / output_filename
    
    audio_clip = AudioFileClip(str(audio_path))
    total_duration = audio_clip.duration
    
    # Calculate duration per image
    num_images = len(image_paths)
    img_duration = total_duration / num_images if num_images > 0 else total_duration
    
    # Create Image Clips
    clips = [ImageClip(str(img)).set_duration(img_duration) for img in image_paths]
    video = concatenate_videoclips(clips, method="compose").set_audio(audio_clip)
    
    # Dynamic Word-by-Word Subtitle Generator (Alex Hormozi Style)
    try:
        words = narration_text.split()
        chunk_size = 3
        sub_clips = []
        num_chunks = max(1, len(words) // chunk_size)
        chunk_duration = total_duration / num_chunks
        
        for i in range(0, len(words), chunk_size):
            chunk_text = " ".join(words[i:i + chunk_size]).upper()
            start_time = (i // chunk_size) * chunk_duration
            
            txt_clip = (
                TextClip(
                    chunk_text,
                    fontsize=70,
                    color='yellow',
                    font='Impact',
                    stroke_color='black',
                    stroke_width=4,
                    method='caption',
                    size=(config.VIDEO_WIDTH - 120, None)
                )
                .set_position(('center', 1350))
                .set_start(start_time)
                .set_duration(chunk_duration)
            )
            sub_clips.append(txt_clip)
            
        final_video = CompositeVideoClip([video] + sub_clips)
    except Exception as e:
        print(f"[!] Animated subtitles fallback triggered (ImageMagick missing or error): {e}")
        final_video = video

    print(f"[+] Exporting MP4 to {output_path}...")
    final_video.write_videofile(
        str(output_path),
        fps=config.FPS,
        codec='libx264',
        audio_codec='aac',
        preset='ultrafast'
    )
    
    audio_clip.close()
    final_video.close()
    return output_path
