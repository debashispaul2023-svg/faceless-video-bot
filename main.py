import time
import traceback
from researcher import generate_script
from tts_engine import generate_voiceover
from visual_generator import generate_visual_assets
from video_renderer import render_final_video
from youtube_uploader import schedule_video_upload, find_next_available_slots
from instagram_uploader import upload_to_instagram
import analytics_feedback


# Daily posting time used only as a fallback if the channel slot lookup fails.
# Change these if you want a different default (hour is 24h IST).
DEFAULT_DAY_OFFSET = 0
DEFAULT_TARGET_HOUR = 17      # 5:00 PM IST
DEFAULT_TARGET_MINUTE = 0


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
    print("🎬 STARTING AUTOMATION PIPELINE FOR 1 DAILY SHORT")
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
    # anything — if the chosen slot is already booked, it lands on the next
    # day that still has room.
    print("\n[+] Checking the channel for the next available upload slot...")
    try:
        slots = find_next_available_slots(count=1)
        if len(slots) < 1:
            raise RuntimeError(f"Only found {len(slots)} free slot(s), expected 1.")
    except Exception as e:
        print(f"[!] Could not determine available slot from the channel ({e}). "
              f"Falling back to today's fixed slot "
              f"{DEFAULT_TARGET_HOUR}:{DEFAULT_TARGET_MINUTE:02d} IST "
              f"(day_offset={DEFAULT_DAY_OFFSET}) — this may collide with "
              f"something already scheduled, so check YouTube Studio afterward.")
        traceback.print_exc()
        slots = [(DEFAULT_DAY_OFFSET, DEFAULT_TARGET_HOUR, DEFAULT_TARGET_MINUTE)]

    successes = 0
    day_offset, target_hour, target_minute = slots[0]
    try:
        process_single_short(0, day_offset, target_hour, target_minute)
        successes += 1
    except Exception as e:
        # A rendering/upload crash must never take the whole run down
        # silently — each video already has its own internal fallbacks
        # (script, ElevenLabs, stock/AI visuals).
        print(f"\n[!] Short #1 failed completely and was skipped: {e}")
        traceback.print_exc()

    print("\n" + "=" * 60)
    print(f"🎉 FINISHED: {successes}/1 short generated and uploaded successfully.")
    print("=" * 60)


if __name__ == "__main__":
    main()
