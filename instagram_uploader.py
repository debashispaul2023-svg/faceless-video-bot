"""
Instagram Reels auto-publisher (Meta Graph API).

════════════════════════════════════════════════════════════════════════
ONE-TIME SETUP REQUIRED — this file will not work until you've done this.
No public "App Review" is needed for your OWN Instagram account; adding
yourself as an Instagram Tester is enough.
════════════════════════════════════════════════════════════════════════

1. Convert your Instagram account to a Professional account:
   Instagram app > Settings > Account type and tools > Switch to
   Professional Account > choose "Creator" or "Business".

2. Link it to a Facebook Page (Instagram will prompt you for this during
   the step above, or: Instagram Settings > Account Center > link a Page).

3. Create a Meta developer app:
   - Go to developers.facebook.com > My Apps > Create App.
   - Choose type "Business" or "Other" > "Business".
   - In the app dashboard, add the "Instagram Graph API" product.

4. Add yourself as an Instagram Tester (this is what lets you publish
   WITHOUT going through Meta's public app review process):
   - App dashboard > App roles > Roles > Instagram Testers > Add Instagram
     Testers > enter your own Instagram username > Submit.
   - Open the Instagram app on your phone > Settings > Apps and Websites >
     Tester Invites > Accept the invite from your app.

5. Get a long-lived Access Token:
   - Go to developers.facebook.com/tools/explorer
   - Select your app, select your Facebook Page/User, and request these
     permissions: instagram_basic, instagram_content_publish,
     pages_show_list, pages_read_engagement.
   - Generate a token, then exchange it for a LONG-LIVED token (lasts ~60
     days) using this URL in a browser (fill in your values):
     https://graph.facebook.com/v19.0/oauth/access_token?grant_type=fb_exchange_token&client_id=YOUR_APP_ID&client_secret=YOUR_APP_SECRET&fb_exchange_token=YOUR_SHORT_LIVED_TOKEN
   - Note: this token expires roughly every 60 days — you'll need to repeat
     this exchange step periodically and update the GitHub secret below.

6. Find your Instagram Business Account ID:
   - In Graph API Explorer, run: GET /me/accounts  (find your Page's id)
   - Then run: GET /{page-id}?fields=instagram_business_account
   - The number returned is your INSTAGRAM_BUSINESS_ID.

7. Add two GitHub repo secrets:
   - INSTAGRAM_ACCESS_TOKEN  = the long-lived token from step 5
   - INSTAGRAM_BUSINESS_ID   = the ID from step 6

════════════════════════════════════════════════════════════════════════
HOW THIS FILE ACTUALLY PUBLISHES A VIDEO
════════════════════════════════════════════════════════════════════════
Instagram's API requires the video to already be sitting at a public HTTPS
URL — it will not accept raw uploaded bytes. Since our bot only has a local
.mp4 file, this module temporarily hosts it by attaching it as an asset to
a GitHub Release in this same repo (using the automatic GITHUB_TOKEN that
GitHub Actions provides — no extra secret needed for that part), grabs the
public download URL, hands that to Instagram, and polls until Instagram
finishes processing it.
"""

import os
import time
import traceback
from pathlib import Path
import requests
import config

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"


def _github_upload_asset(video_path: Path) -> str:
    """
    Uploads `video_path` as an asset on a new GitHub Release in this repo,
    and returns its public download URL. Requires the GITHUB_TOKEN and
    GITHUB_REPOSITORY environment variables, which GitHub Actions sets
    automatically for every workflow run (GITHUB_TOKEN must be explicitly
    passed into the step's `env:` block — see the note in daily_bot.yml).
    """
    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]  # e.g. "yourname/faceless-video-bot"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    tag = f"media-{int(time.time())}"
    release_resp = requests.post(
        f"https://api.github.com/repos/{repo}/releases",
        headers=headers,
        json={"tag_name": tag, "name": tag, "prerelease": True,
              "body": "Temporary hosting for an auto-generated Short, used to publish to Instagram."},
        timeout=30,
    )
    release_resp.raise_for_status()
    release = release_resp.json()

    upload_url = release["upload_url"].split("{")[0]
    with open(video_path, "rb") as f:
        asset_resp = requests.post(
            f"{upload_url}?name={video_path.name}",
            headers={**headers, "Content-Type": "video/mp4"},
            data=f,
            timeout=120,
        )
    asset_resp.raise_for_status()
    asset = asset_resp.json()
    return asset["browser_download_url"]


def upload_to_instagram(video_path: Path, metadata: dict) -> str | None:
    """
    Publishes `video_path` as an Instagram Reel. Returns the published
    media ID on success, or None if Instagram publishing isn't configured
    or fails (this NEVER raises — a missing/failed Instagram upload should
    never take down the rest of the pipeline, including the YouTube upload).
    """
    if not config.INSTAGRAM_ACCESS_TOKEN or not config.INSTAGRAM_BUSINESS_ID:
        print("[i] Instagram publishing skipped — INSTAGRAM_ACCESS_TOKEN / "
              "INSTAGRAM_BUSINESS_ID not set. See instagram_uploader.py for setup steps.")
        return None

    try:
        print("[+] Hosting video temporarily via a GitHub Release for Instagram to fetch...")
        public_url = _github_upload_asset(video_path)

        caption = metadata.get("description", metadata.get("title", ""))
        hashtags = " ".join(f"#{h.lstrip('#')}" for h in metadata.get("hashtags", []))
        full_caption = f"{caption}\n\n{hashtags}".strip()

        print("[+] Creating Instagram Reel container...")
        create_resp = requests.post(
            f"{GRAPH_API_BASE}/{config.INSTAGRAM_BUSINESS_ID}/media",
            data={
                "media_type": "REELS",
                "video_url": public_url,
                "caption": full_caption[:2200],  # Instagram's caption length limit
                "access_token": config.INSTAGRAM_ACCESS_TOKEN,
            },
            timeout=30,
        )
        create_resp.raise_for_status()
        creation_id = create_resp.json()["id"]

        print("[+] Waiting for Instagram to finish processing the video...")
        for attempt in range(20):  # up to ~5 minutes
            time.sleep(15)
            status_resp = requests.get(
                f"{GRAPH_API_BASE}/{creation_id}",
                params={"fields": "status_code", "access_token": config.INSTAGRAM_ACCESS_TOKEN},
                timeout=30,
            )
            status_resp.raise_for_status()
            status_code = status_resp.json().get("status_code")
            if status_code == "FINISHED":
                break
            if status_code == "ERROR":
                print("[!] Instagram reported an error while processing the video.")
                return None
        else:
            print("[!] Timed out waiting for Instagram to process the video.")
            return None

        print("[+] Publishing Instagram Reel...")
        publish_resp = requests.post(
            f"{GRAPH_API_BASE}/{config.INSTAGRAM_BUSINESS_ID}/media_publish",
            data={"creation_id": creation_id, "access_token": config.INSTAGRAM_ACCESS_TOKEN},
            timeout=30,
        )
        publish_resp.raise_for_status()
        media_id = publish_resp.json().get("id")
        print(f"[✓] Instagram Reel published! Media ID: {media_id}")
        return media_id

    except Exception as e:
        print(f"[!] Instagram publishing failed (non-fatal, continuing): {e}")
        traceback.print_exc()
        return None
