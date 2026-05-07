from __future__ import annotations

import os

import requests


def download_generated_video_result(
    video_url: str | None,
    task_dir: str,
    *,
    filename: str = "generated_video.mp4",
    timeout: int | float = 120,
) -> str | None:
    if not video_url:
        return None

    local_video_path = os.path.join(task_dir, filename)
    response = requests.get(video_url, timeout=timeout)
    response.raise_for_status()
    with open(local_video_path, "wb") as file_obj:
        file_obj.write(response.content)
    return local_video_path
