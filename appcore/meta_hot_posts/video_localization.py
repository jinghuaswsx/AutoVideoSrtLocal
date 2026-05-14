from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from config import OUTPUT_DIR
from appcore.meta_hot_posts import store

MIN_DOWNLOAD_DELAY_SECONDS = 30.0
DEFAULT_CACHE_SUBDIR = Path("meta_hot_posts") / "videos"

RunFn = Callable[..., Any]
SleepFn = Callable[[float], None]


def default_cache_root(*, output_dir: str | Path = OUTPUT_DIR) -> Path:
    return Path(output_dir) / DEFAULT_CACHE_SUBDIR


def _safe_post_id(row: Mapping[str, Any]) -> str:
    raw = str(row.get("id") or row.get("wedev_post_id") or "").strip()
    if not raw:
        raise ValueError("missing hot post id")
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._") or "post"


def _relative_to_output(path: Path, *, output_dir: str | Path) -> str:
    rel = path.resolve().relative_to(Path(output_dir).resolve())
    return rel.as_posix()


def resolve_local_video_path(
    relative_path: str | None,
    *,
    output_dir: str | Path = OUTPUT_DIR,
) -> Path | None:
    raw = str(relative_path or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        return None
    root = Path(output_dir).resolve()
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def download_with_ytdlp(
    row: Mapping[str, Any],
    *,
    cache_root: str | Path | None = None,
    output_dir: str | Path = OUTPUT_DIR,
    run_fn: RunFn = subprocess.run,
    which_fn: Callable[[str], str | None] = shutil.which,
    timeout_seconds: int = 600,
) -> str:
    url = str(row.get("video_url") or "").strip()
    if not url:
        raise ValueError("video_url is empty")
    executable = which_fn("yt-dlp")
    if not executable:
        raise RuntimeError("yt-dlp is not installed")

    output_root = Path(output_dir)
    root = Path(cache_root) if cache_root is not None else default_cache_root(output_dir=output_root)
    root.mkdir(parents=True, exist_ok=True)
    stem = f"meta_hot_post_{_safe_post_id(row)}"
    output_template = root / f"{stem}.%(ext)s"
    command = [
        executable,
        "--no-playlist",
        "--no-part",
        "--restrict-filenames",
        "-f",
        "mp4/best",
        "-o",
        str(output_template),
        url,
    ]
    completed = run_fn(
        command,
        timeout=int(timeout_seconds),
        capture_output=True,
        text=True,
    )
    if getattr(completed, "returncode", 1) != 0:
        stderr = str(getattr(completed, "stderr", "") or "").strip()
        stdout = str(getattr(completed, "stdout", "") or "").strip()
        raise RuntimeError((stderr or stdout or "yt-dlp failed")[:1000])

    matches = [
        item
        for item in root.glob(f"{stem}.*")
        if item.is_file() and item.suffix.lower() not in {".part", ".ytdl"}
    ]
    if not matches:
        raise RuntimeError("yt-dlp completed but no video file was created")
    video_path = max(matches, key=lambda item: item.stat().st_mtime)
    if video_path.stat().st_size <= 0:
        raise RuntimeError("downloaded video file is empty")
    return _relative_to_output(video_path, output_dir=output_root)


def _delay_seconds(value: float | int | str | None) -> float:
    try:
        parsed = float(value if value is not None else MIN_DOWNLOAD_DELAY_SECONDS)
    except (TypeError, ValueError):
        parsed = MIN_DOWNLOAD_DELAY_SECONDS
    return max(MIN_DOWNLOAD_DELAY_SECONDS, parsed)


def download_hot_post_videos(
    *,
    limit: int = 20,
    max_attempts: int = 3,
    min_delay_seconds: float | int | str | None = MIN_DOWNLOAD_DELAY_SECONDS,
    cache_root: str | Path | None = None,
    output_dir: str | Path = OUTPUT_DIR,
    download_fn: Callable[..., str] = download_with_ytdlp,
    sleep_fn: SleepFn | None = None,
) -> dict[str, int]:
    rows = store.next_pending_local_videos(limit=limit, max_attempts=max_attempts)
    total = len(rows)
    summary = {"scanned": 0, "downloaded": 0, "failed": 0}
    delay = _delay_seconds(min_delay_seconds)
    root = Path(cache_root) if cache_root is not None else default_cache_root(output_dir=output_dir)
    sleeper = sleep_fn or time.sleep

    for index, row in enumerate(rows):
        post_id = int(row["id"])
        summary["scanned"] += 1
        store.mark_local_video_downloading(post_id)
        try:
            local_path = download_fn(row, cache_root=root, output_dir=output_dir)
        except Exception as exc:
            store.finish_local_video_download(
                post_id,
                local_video_path=None,
                error_message=str(exc)[:1000],
            )
            summary["failed"] += 1
        else:
            store.finish_local_video_download(
                post_id,
                local_video_path=local_path,
                error_message=None,
            )
            summary["downloaded"] += 1
        if index < total - 1:
            sleeper(delay)
    return summary
