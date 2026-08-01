import time
import traceback
from researcher import generate_script
from tts_engine import generate_voiceover
from visual_generator import generate_visual_assets
from video_renderer import render_final_video
from youtube_uploader import schedule_video_upload
from instagram_uploader import upload_to_instagram
import analytics_feedback


def get_sequential_slot_est(index: int):
    """
    Maps video index sequentially to YOUR personal finance channel's time slots.
    
    3 videos per day at FIXED TIMES:
    1. 3:00 AM EDT (Slot 1)
    2. 5:00 PM EDT (Slot 2)
    3. 10:30 PM EDT (Slot 3)
    
    Videos 4-6 roll over to next day, 7-9 to day+2, etc.
    """
    slot_pattern = [
        (3, 0),     # 3:00 AM EDT
        (17, 0),    # 5:00 PM EDT
        (22, 30),   # 10:30 PM EDT
    ]

    pattern_index = index % len(slot_pattern)
    day_offset = index // len(slot_pattern)

    target_hour, target_minute = slot_pattern[pattern_index]
    return target_hour, target_minute, day_offset


def process_single_short(index: int):
    print(f"\n==================== GENERATING SHORT #{index + 1} ====================")

    script_data = generate_script()

    audio_path = generate_voiceover(script_data["script"], output_filename=f"voiceover_{index}.mp3")

    images = generate_visual_assets(script_data.get("visual_scenes", []))

    final_video_path = render_final_video(
        image_paths=images,
        audio_path=audio_path,
        narration_text=script_data["script"],
        output_filename=f"final_short_{index + 1}.mp4"
    )

    print(f"[✓] Short #{index + 1} successfully created at: {final_video_path}")

    try:
        print(f"[+] Starting YouTube upload for Short #{index + 1}...")
        target_hour, target_minute, day_offset = get_sequential_slot_est(index)
        video_id = schedule_video_upload(
            final_video_path,
            script_data,
            target_hour_est=target_hour,
            target_minute_est=target_minute,
            day_offset=day_offset
        )
        print(f"[✓] Upload process completed for Short #{index + 1} "
              f"at {target_hour}:{target_minute:02d} EDT (day +{day_offset})!")

        try:
            analytics_feedback.log_published_video(
                video_id=video_id,
                category=script_data.get("category", "unknown"),
                title=script_data.get("title", ""),
            )
        except Exception as e:
            print(f"[i] Could not log video for analytics feedback (non-fatal): {e}")

    except Exception as e:
        print(f"[!] Upload failed for Short #{index + 1}: {e}")
        traceback.print_exc()

    try:
        upload_to_instagram(final_video_path, script_data)
    except Exception as e:
        print(f"[i] Instagram upload skipped for Short #{index + 1} (non-fatal): {e}")


def main():
    print("=" * 60)
    print("🎬 STARTING AUTOMATION PIPELINE FOR 3 SHORTS")
    print("=" * 60)

    try:
        analytics_feedback.update_category_weights()
    except Exception as e:
        print(f"[i] Analytics feedback loop skipped this run (non-fatal): {e}")
        traceback.print_exc()

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
