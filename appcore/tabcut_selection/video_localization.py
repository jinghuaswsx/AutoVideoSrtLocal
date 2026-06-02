from __future__ import annotations

import logging
import os
import subprocess
import time
from typing import Any

import config
from appcore.tabcut_selection import store
from pipeline.ffutil import get_media_duration

log = logging.getLogger(__name__)


def download_tiktok_video(video_id: str, author_name: str, tk_video_url: str | None = None) -> tuple[bool, str, str]:
    """
    Downloads a TikTok video via yt-dlp.
    Returns: (success, local_video_path, error_message)
    """
    # 1. Prepare target paths
    video_dir = os.path.join(config.OUTPUT_DIR, "tabcut", "videos")
    os.makedirs(video_dir, exist_ok=True)

    filename = f"{video_id}.mp4"
    local_abs_path = os.path.join(video_dir, filename)
    local_rel_path = os.path.join("tabcut", "videos", filename).replace("\\", "/")

    # 2. Get URL to download
    url = tk_video_url
    if not url or not url.startswith("http"):
        # Synthesize fallback url
        if author_name:
            url = f"https://www.tiktok.com/@{author_name}/video/{video_id}"
        else:
            url = f"https://www.tiktok.com/video/{video_id}"

    # 3. Detect yt-dlp path
    yt_dlp_bin = "/opt/autovideosrt/venv/bin/yt-dlp"
    if not os.path.exists(yt_dlp_bin):
        yt_dlp_bin = "yt-dlp"

    # 4. Perform download
    cmd = [
        yt_dlp_bin,
        "--no-check-certificate",
        "-o", local_abs_path,
        url
    ]
    log.info("Starting download of video %s via: %s", video_id, " ".join(cmd))

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if res.returncode == 0 and os.path.exists(local_abs_path) and os.path.getsize(local_abs_path) > 0:
            log.info("Successfully downloaded video %s to %s", video_id, local_abs_path)
            return True, local_rel_path, ""
        else:
            stderr_msg = res.stderr or res.stdout or "Unknown yt-dlp failure"
            log.error("Failed to download video %s: %s", video_id, stderr_msg)
            return False, "", stderr_msg[:1000]
    except subprocess.TimeoutExpired:
        log.error("Download of video %s timed out", video_id)
        return False, "", "Timeout expired during download"
    except Exception as e:
        log.error("Exception during download of video %s: %s", video_id, e, exc_info=True)
        return False, "", str(e)


def extract_video_cover(local_rel_video_path: str, video_id: str) -> str | None:
    """
    Extracts the first frame of the downloaded video as cover.
    Returns the relative path to the cover, or None if failed.
    """
    video_abs_path = os.path.join(config.OUTPUT_DIR, local_rel_video_path)
    if not os.path.exists(video_abs_path):
        return None

    cover_dir = os.path.join(config.OUTPUT_DIR, "tabcut", "video_covers")
    os.makedirs(cover_dir, exist_ok=True)

    filename = f"{video_id}.jpg"
    cover_abs_path = os.path.join(cover_dir, filename)
    cover_rel_path = os.path.join("tabcut", "video_covers", filename).replace("\\", "/")

    cmd = ["ffmpeg", "-y", "-i", video_abs_path, "-vframes", "1", "-f", "image2", cover_abs_path]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=30)
        if res.returncode == 0 and os.path.exists(cover_abs_path) and os.path.getsize(cover_abs_path) > 0:
            log.info("Successfully extracted cover for video %s to %s", video_id, cover_abs_path)
            return cover_rel_path
    except Exception as e:
        log.error("Failed to extract cover for video %s: %s", video_id, e)
    return None


def localize_video(video_item: dict, max_attempts: int = 5) -> dict[str, Any]:
    """
    Downloads a single video item, updates duration, cover, and DB status.
    """
    video_id = str(video_item["video_id"])
    author_name = video_item.get("author_name") or ""
    tk_video_url = video_item.get("tk_video_url")

    # Mark as downloading
    store.mark_local_video_downloading(video_id)

    # 1. Download
    success, local_video_path, error_msg = download_tiktok_video(video_id, author_name, tk_video_url)

    if not success:
        store.finish_local_video_download_failure(video_id, error_msg, max_attempts=max_attempts)
        return {"video_id": video_id, "status": "failed", "error": error_msg}

    # 2. Probe duration
    abs_video_path = os.path.join(config.OUTPUT_DIR, local_video_path)
    duration = get_media_duration(abs_video_path)

    # 3. Extract cover
    local_cover_path = extract_video_cover(local_video_path, video_id)
    if not local_cover_path:
        log.warning("Could not extract cover for video %s, fallback to original", video_id)
        local_cover_path = ""

    # 4. Save to DB
    store.finish_local_video_download_success(
        video_id,
        local_video_path=local_video_path,
        local_video_duration_seconds=duration,
        local_video_cover_path=local_cover_path or ""
    )
    return {
        "video_id": video_id,
        "status": "success",
        "local_video_path": local_video_path,
        "duration": duration,
        "local_cover_path": local_cover_path
    }


def run_localization_round(limit: int = 20, max_attempts: int = 5) -> dict[str, Any]:
    """
    Main entry point for localizing a batch of videos.
    """
    # 0. Clean up stale running local videos
    try:
        store.reset_stale_running_local_videos()
    except Exception:
        log.error("Failed to reset stale running videos", exc_info=True)

    # 1. Find candidates
    candidates = store.next_pending_local_videos(limit=limit, max_attempts=max_attempts)
    if not candidates:
        log.info("No pending Tabcut videos found for localization")
        return {"scanned": 0, "success": 0, "failed": 0, "results": []}

    log.info("Found %d pending Tabcut videos to localize", len(candidates))

    results = []
    success_count = 0
    failed_count = 0

    for idx, item in enumerate(candidates):
        video_id = str(item["video_id"])

        # Rate limit delay: 30 seconds before downloading (except the first one)
        if idx > 0:
            log.info("Sleeping 30 seconds for rate limit...")
            time.sleep(30)

        try:
            res = localize_video(item, max_attempts=max_attempts)
            results.append(res)
            if res["status"] == "success":
                success_count += 1
            else:
                failed_count += 1
        except Exception as e:
            log.error("Failed processing video %s: %s", video_id, e, exc_info=True)
            failed_count += 1
            results.append({"video_id": video_id, "status": "failed", "error": str(e)})

    return {
        "scanned": len(candidates),
        "success": success_count,
        "failed": failed_count,
        "results": results
    }
