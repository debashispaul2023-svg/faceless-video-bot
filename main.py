import sys
import time
from researcher import discover_trending_topic
from script_generator import generate_viral_script
from tts_engine import generate_voiceover
from visual_generator import generate_visual_assets
from video_renderer import render_final_video
from youtube_uploader import schedule_video_upload

def generate_single_short(video_index: int, auto_upload: bool = False):
    divider = "=" * 20
    print(f"\n{divider} GENERATING SHORT #{video_index + 1} {divider}")

    # Step 1: Research Unique Trending Topic
    topic = discover_trending_topic()

    # Step 2: Generate Script & Visual Prompts
    script_data = generate_viral_script(topic)

    # Step 3: Text-To-Speech Synthesis
    audio_path = generate_voiceover(
        script_data["narration_text"], 
        output_filename=f"voiceover_{video_index}.mp3"
    )

    # Step 4: AI Image Generation
    images = generate_visual_assets(script_data["visual_scenes"])

    if not images:
        print(f"[!] Critical Error: Failed to generate images for Short #{video_index + 1}.")
        return

    # Step 5: Video Assembly & Subtitle Rendering
    final_video = render_final_video(
        image_paths=images,
        audio_path=audio_path,
        narration_text=script_data["narration_text"],
        output_filename=f"final_short_{video_index + 1}.mp4"
    )

    print(f"[✓] Short #{video_index + 1} successfully created at: {final_video}")

    # Step 6: Automatic YouTube Scheduling
    if auto_upload:
        schedule_hours = [6, 12, 18] # US Prime Hours: 6 AM, 12 PM, 6 PM EST
        target_hour = schedule_hours[video_index % len(schedule_hours)]
        schedule_video_upload(final_video, script_data, target_hour_est=target_hour)

def main(total_videos: int = 3, auto_upload: bool = False):
    print("=" * 60)
    print(f"🎬 STARTING AUTOMATION PIPELINE FOR {total_videos} SHORTS")
    print("=" * 60)

    for i in range(total_videos):
        generate_single_short(video_index=i, auto_upload=auto_upload)
        if i < total_videos - 1:
            print("\n[⏳] Waiting 20 seconds before starting next video...")
            time.sleep(20)

    print("\n🎉 ALL SHORTS GENERATED SUCCESSFULLY!")

if __name__ == "__main__":
    main(total_videos=3, auto_upload=False)
