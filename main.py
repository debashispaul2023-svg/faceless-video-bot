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

        # Best-effort: remember which category AND which hook technique this
        # video used, so the analytics feedback loop can later correlate
        # performance back to both (see analytics_feedback.update_category_weights()).
        try:
            analytics_feedback.log_published_video(
                video_id=video_id,
                category=script_data.get("category", "unknown"),
                title=script_data.get("title", ""),
                hook_technique=script_data.get("hook_technique", "unspecified"),
                prompt_version=script_data.get("prompt_version", "unknown"),
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
    print("🎬 STARTING AUTOMATION PIPELINE FOR 1 SHORT")
    print("=" * 60)

    # Best-effort: pull in performance data from previously published videos
    # (if the analytics API is set up) so today's topic AND hook-style choices
    # lean toward what's actually working. Safe no-op until that one-time
    # setup is done — see the top of analytics_feedback.py.
    try:
        analytics_feedback.update_category_weights()
    except Exception as e:
        print(f"[i] Analytics feedback loop skipped this run (non-fatal): {e}")
        traceback.print_exc()

    # Figure out where today's single video belongs BEFORE generating
    # anything — prefers the fixed daily slot (5:00 PM IST by default).
    # If that slot is already taken, it rolls forward to the next free day.
    print("\n[+] Checking the channel for the next available upload day/slot...")
    try:
        slots = find_next_available_slots(count=1)
        if len(slots) < 1:
            raise RuntimeError(f"Only found {len(slots)} free slot(s), expected 1.")
    except Exception as e:
        print(f"[!] Could not determine available slots from the channel ({e}). "
              f"Falling back to today's fixed 5:00 PM IST slot (day_offset=0) — this may "
              f"collide with something already scheduled today, so check YouTube "
              f"Studio afterward.")
        traceback.print_exc()
        slots = [(0, 17, 0)]

    day_offset, target_hour, target_minute = slots[0]
    process_single_short(0, day_offset, target_hour, target_minute)

    print("\n" + "=" * 60)
    print("🎉 SHORT GENERATED AND UPLOADED SUCCESSFULLY!")
    print("=" * 60)


if __name__ == "__main__":
    main()
