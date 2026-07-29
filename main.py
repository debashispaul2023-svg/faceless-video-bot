import time
import os
from pathlib import Path
from researcher import generate_script
from voiceover import generate_voiceover
from visualizer import generate_visuals
from video_editor import assemble_video
from youtube_uploader import schedule_video_upload
import config

def process_single_short(index: int):
    print(f"\n==================== GENERATING SHORT #{index + 1} ====================")
    
    # 1. স্ক্রিপ্ট জেনারেট
    script_data = generate_script()

    # 2. ভয়েসওভার
    audio_path = config.OUTPUT_DIR / f"voiceover_{index}.mp3"
    generate_voiceover(script_data["script"], audio_path)

    # 3. ছবি জেনারেট
    images = generate_visuals(script_data.get("visual_prompts", []), index)

    # 4. ভিডিও এডিটিং
    final_video_path = config.OUTPUT_DIR / f"final_short_{index + 1}.mp4"
    assemble_video(audio_path, images, script_data["script"], final_video_path)
    
    print(f"[✓] Short #{index + 1} successfully created at: {final_video_path}")

    # 5. ইউটিউবে আপলোড (এই অংশটিই মিসিং ছিল)
    try:
        print(f"[+] Starting YouTube upload for Short #{index + 1}...")
        # ৩ ঘণ্টা পর পর শিডিউল আপলোড
        target_hour = (12 + (index * 4)) % 24  
        schedule_video_upload(final_video_path, script_data, target_hour_est=target_hour)
        print(f"[✓] Upload process completed for Short #{index + 1}!")
    except Exception as e:
        print(f"[!] Upload failed for Short #{index + 1}: {e}")

def main():
    print("============================================================")
    print("🎬 STARTING AUTOMATION PIPELINE FOR 3 SHORTS")
    print("============================================================")

    for i in range(3):
        process_single_short(i)
        if i < 2:
            print("\n[⏳] Waiting 20 seconds before starting next video...")
            time.sleep(20)

    print("\n============================================================")
    print("🎉 ALL SHORTS GENERATED AND UPLOADED SUCCESSFULLY!")
    print("============================================================")

if __name__ == "__main__":
    main()
