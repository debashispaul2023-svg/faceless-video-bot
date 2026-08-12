import time
import traceback
from researcher import generate_script
from tts_engine import generate_voiceover
from visual_generator import generate_visual_assets
from video_renderer import render_final_video
from youtube_uploader import schedule_video_upload, find_next_available_slots
from instagram_uploader import upload_to_instagram
import analytics_feedback


def process_single_short(index: int, day_offset: int, target_hour: int, target_minute: int):
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
        print(f"[+] Starting YouTube upload for Short #{index + 1} "
              f"-> targeting IST {target_hour}:{target_minute:02d} (day +{day_offset})...")
        video_id = schedule_video_upload(
            final_video_path,
            script_data,
            target_hour=target_hour,
            target_minute=target_minute,
            day_offset=day_offset
        )
        print(f"[✓] Upload process completed for Short #{index + 1}!")

        # Best-effort: remember which category this video came from, so the
        # analytics feedback loop can later correlate performance to topic.
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

    # Instagram Reels — completely separate from the YouTube upload above,
    # so a failure here never affects YouTube (or vice versa).
    try:
        upload_to_instagram(final_video_path, script_data)
    except Exception as e:
        print(f"[i] Instagram upload skipped for Short #{index + 1} (non-fatal): {e}")


def main():
    print("=" * 60)
    print("🎬 STARTING AUTOMATION PIPELINE FOR 3 SHORTS")
    print("=" * 60)

    # Best-effort: pull in performance data from previously published videos
    # (if the analytics API is set up) so today's topic choices lean toward
    # what's actually working. Safe no-op until that one-time setup is done —
    # see the top of analytics_feedback.py.
    try:
        analytics_feedback.update_category_weights()
    except Exception as e:
        print(f"[i] Analytics feedback loop skipped this run (non-fatal): {e}")
        traceback.print_exc()

    # Figure out where today's batch of 3 actually belongs BEFORE generating
    # anything — if the channel is already fully booked (3 videos) through
    # a future date, these 3 new videos automatically land on the next day
    # that still has room, always at the exact 3:00 AM / 5:00 PM / 10:30 PM
    # IST slots and never anywhere else.
    print("\n[+] Checking the channel for the next available upload day/slots...")
    try:
        slots = find_next_available_slots(count=3)
        if len(slots) < 3:
            raise RuntimeError(f"Only found {len(slots)} free slot(s), expected 3.")
    except Exception as e:
        print(f"[!] Could not determine available slots from the channel ({e}). "
              f"Falling back to today's 3 fixed slots (day_offset=0) — this may "
              f"collide with something already scheduled today, so check YouTube "
              f"Studio afterward.")
        traceback.print_exc()
        slots = [(0, 3, 0), (0, 17, 0), (0, 22, 30)]

    for i in range(3):
        day_offset, target_hour, target_minute = slots[i]
        process_single_short(i, day_offset, target_hour, target_minute)
        if i < 2:
            print("\n[⏳] Waiting 20 seconds before starting next video...")
            time.sleep(20)

    print("\n" + "=" * 60)
    print("🎉 ALL SHORTS GENERATED AND UPLOADED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    main()
