"""
upload_youtube.py
Automated YouTube Shorts / Video uploader using YouTube Data API v3 endpoints.
"""

import json
import os
import sys
import time
from pathlib import Path
import requests

from config import (
    FINAL_VIDEO,
    SCRIPT_JSON,
    YOUTUBE_CLIENT_ID,
    YOUTUBE_CLIENT_SECRET,
    YOUTUBE_REFRESH_TOKEN,
    logger,
)


def get_access_token() -> str:
    """Exchange YOUTUBE_REFRESH_TOKEN for a short-lived access token."""
    logger.info("Exchanging refresh token for access token...")
    url = "https://oauth2.googleapis.com/token"
    payload = {
        "client_id": YOUTUBE_CLIENT_ID,
        "client_secret": YOUTUBE_CLIENT_SECRET,
        "refresh_token": YOUTUBE_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }
    
    resp = requests.post(url, data=payload, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to refresh access token: {resp.status_code} - {resp.text}")
    
    token_data = resp.json()
    return token_data["access_token"]


def initiate_resumable_upload(access_token: str, video_path: Path, title: str, description: str) -> str:
    """Initiate a resumable upload session and return the unique Location URL."""
    logger.info("Initiating YouTube resumable upload session...")
    url = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
    file_size = video_path.stat().st_size

    # YouTube titles are limited to 100 characters
    formatted_title = title[:95] + "..." if len(title) > 100 else title
    formatted_desc = f"{description}\n\n#Shorts #Reels"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4",
        "X-Upload-Content-Length": str(file_size),
    }

    body = {
        "snippet": {
            "title": formatted_title,
            "description": formatted_desc,
            "tags": ["shorts", "reels", "ai"],
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    resp = requests.post(url, headers=headers, json=body, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to initiate resumable upload: {resp.status_code} - {resp.text}")

    upload_url = resp.headers.get("Location")
    if not upload_url:
        raise RuntimeError("YouTube API did not return a Location header for upload.")

    return upload_url


def upload_video_binary(upload_url: str, video_path: Path, max_retries: int = 3) -> str:
    """Upload the video binary stream to the unique resumable upload URI."""
    file_size = video_path.stat().st_size
    headers = {
        "Content-Type": "video/mp4",
        "Content-Length": str(file_size),
    }

    logger.info("Uploading binary video payload (%.2f MB)...", file_size / 1e6)

    for attempt in range(1, max_retries + 1):
        try:
            with open(video_path, "rb") as video_file:
                resp = requests.put(upload_url, headers=headers, data=video_file, timeout=300)

            if resp.status_code in (200, 201):
                data = resp.json()
                video_id = data.get("id")
                return video_id
            
            logger.warning("Upload attempt %d returned HTTP %d: %s", attempt, resp.status_code, resp.text)
        except requests.RequestException as exc:
            logger.warning("Upload attempt %d network exception: %s", attempt, exc)

        if attempt < max_retries:
            sleep_s = 5 * (2 ** (attempt - 1))
            time.sleep(sleep_s)

    raise RuntimeError("Failed to upload video binary payload to YouTube after multiple attempts.")


def main() -> None:
    # 1. Check credentials
    if not all([YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN]):
        raise EnvironmentError(
            "Missing one or more required YouTube environment variables: "
            "YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN"
        )

    # 2. Check source video and script metadata
    video_path = Path(FINAL_VIDEO)
    script_path = Path(SCRIPT_JSON)

    if not video_path.exists():
        raise FileNotFoundError(f"Final video file not found at: {video_path}")
    if not script_path.exists():
        raise FileNotFoundError(f"Script metadata file not found at: {script_path}")

    with script_path.open(encoding="utf-8") as fh:
        script_data = json.load(fh)

    title = script_data.get("title", "Automated Short")
    description = script_data.get("description", "")

    # 3. Execute 3-step OAuth & Resumable Upload
    access_token = get_access_token()
    upload_url = initiate_resumable_upload(access_token, video_path, title, description)
    video_id = upload_video_binary(upload_url, video_path)

    youtube_url = f"https://youtu.be/{video_id}"

    print("\n" + "=" * 60)
    print("  ✅  YouTube Short published successfully!")
    print(f"  Title      : {title}")
    print(f"  Video ID   : {video_id}")
    print(f"  YouTube URL: {youtube_url}")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.critical("YouTube upload step failed: %s", exc, exc_info=True)
        sys.exit(1)