from __future__ import annotations

from pathlib import Path

import requests


def _safe_filename(filename: str, fallback: str) -> str:
    normalized = (filename or "").strip().replace("\\", "_").replace("/", "_")
    return normalized or fallback


def download_images(
    items: list[dict],
    output_dir: Path,
    *,
    retries: int = 1,
    status_cb=None,
) -> list[dict]:
    downloaded: list[dict] = []
    total = len(items)
    for index, item in enumerate(items, start=1):
        item_id = str(item.get("id") or f"image-{index}")
        filename = _safe_filename(str(item.get("filename") or ""), f"{item_id}.jpg")
        output_path = output_dir / filename
        last_exc: Exception | None = None
        if status_cb is not None:
            status_cb(f"正在下载图片 {index}/{total}: {filename}")

        for _attempt in range(retries + 1):
            try:
                response = requests.get(str(item.get("url") or ""), timeout=30)
                response.raise_for_status()
                output_path.write_bytes(response.content)
                downloaded.append({**item, "local_path": str(output_path)})
                last_exc = None
                break
            except requests.RequestException as exc:
                last_exc = exc

        if last_exc is not None:
            raise RuntimeError(f"failed to download {filename}: {last_exc}") from last_exc

    return downloaded
