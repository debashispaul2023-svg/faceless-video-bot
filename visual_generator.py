import requests
import urllib.parse
from pathlib import Path
import time
import random
from tenacity import retry, stop_after_attempt, wait_exponential
import config

# Pexels Free API Key (ফ্রিতে পিক্সেলস ওয়েবসাইট থেকে নেওয়া যায়)
# Key না থাকলে এটি অটোমেটিক Pollinations AI ইমেজ জেনারেট করবে
PEXELS_API_KEY = "YOUR_FREE_PEXELS_API_KEY"

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _download_file(url: str, save_path: Path):
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    with open(save_path, 'wb') as f:
        f.write(response.content)

def fetch_real_stock_image(keyword: str, save_path: Path) -> bool:
    """Fetches real-world stock photos using Pexels Free API."""
    if PEXELS_API_KEY == "YOUR_FREE_PEXELS_API_KEY":
        return False
        
    headers = {"Authorization": PEXELS_API_KEY}
    url = f"https://api.pexels.com/v1/search?query={urllib.parse.quote(keyword)}&orientation=portrait&per_page=5"
    
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if data.get("photos"):
                photo_url = data["photos"][0]["src"]["portrait"]
                _download_file(photo_url, save_path)
                print(f"[+] Downloaded REAL Stock Image for: {keyword}")
                return True
    except Exception as e:
        print(f"[!] Real stock fetch failed: {e}")
    return False

def generate_visual_assets(visual_prompts: list) -> list:
    """Generates a hybrid mix of Real World Images and AI Flux Visuals."""
    image_paths = []
    
    for idx, prompt in enumerate(visual_prompts):
        output_file = config.OUTPUT_DIR / f"frame_{idx}.jpg"
        
        # ১. প্রথমে চেষ্টা করবে আসল স্টক ইমেজ (Real World Data) খুঁজে বের করতে
        search_keyword = prompt.split("of")[-1].strip() if "of" in prompt else prompt[:30]
        success = fetch_real_stock_image(search_keyword, output_file)
        
        # ২. আসল ছবি না পাওয়া গেলে Pollinations AI (Flux) দিয়ে হাই-কোয়ালিটি ছবি বানাবে
        if not success:
            enhanced_prompt = f"{prompt}, historical photorealistic photography, 8k resolution, cinematic lighting, 9:16 vertical"
            encoded_prompt = urllib.parse.quote(enhanced_prompt)
            url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1920&model=flux&seed={int(time.time()) + idx}"
            
            print(f"[+] Generating AI Visual {idx + 1}/{len(visual_prompts)} via Pollinations Flux...")
            try:
                _download_file(url, output_file)
            except Exception as e:
                print(f"[!] Visual generation failed for frame {idx}: {e}")
                continue
                
        image_paths.append(output_file)
            
    return image_paths
