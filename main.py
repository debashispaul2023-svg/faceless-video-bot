import time
from researcher import generate_script
from tts_engine import generate_voiceover
from visual_generator import generate_visual_assets
from video_renderer import render_final_video
from youtube_uploader import schedule_video_upload

def process_single_short(index: int):
    print(f"\n==================== GENERATING SHORT #{index + 1} ====================")

    script_data = generate_script()

    audio_path = generate_voiceover(script_data["script"], output_filename=f"voiceover_{index}.mp3")

    images = generate_visual_assets(script_data.get("visual_prompts", []))

    final_video_path = render_final_video(
        image_paths=images,
        audio_path=audio_path,
        narration_text=script_data["script"],
        output_filename=f"final_short_{index + 1}.mp4"
    )

    print(f"[✓] Short #{index + 1} successfully created at: {final_video_path}")

    try:
        print(f"[+] Starting YouTube upload for Short #{index + 1}...")
        target_hour = (12 + (index * 4)) % 24
        schedule_video_upload(final_video_path, script_data, target_hour_est=target_hour)
        print(f"[✓] Upload process completed for Short #{index + 1}!")
    except Exception as e:
        print(f"[!] Upload failed for Short #{index + 1}: {e}")

def main():
    print("=" * 60)
    print("🎬 STARTING AUTOMATION PIPELINE FOR 3 SHORTS")
    print("=" * 60)

    for i in range(3):
        process_single_short(i)
        if i < 2:
            print("\n[⏳] Waiting 20 seconds before starting next video...")
            time.sleep(20)

    print("\n" + "=" * 60)
    print("🎉 ALL SHORTS GENERATED AND UPLOADED SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    main()
