from __future__ import annotations

from pathlib import Path

import requests

from tools.shopify_image_localizer import cancellation


def _safe_filename(filename: str, fallback: str) -> str:
    normalized = (filename or "").strip().replace("\\", "_").replace("/", "_")
    return normalized or fallback


def download_images(
    items: list[dict],
    output_dir: Path,
    *,
    retries: int = 1,
    status_cb=None,
    cancel_token: cancellation.CancellationToken | None = None,
) -> list[dict]:
    downloaded: list[dict] = []
    total = len(items)
    for index, item in enumerate(items, start=1):
        cancellation.throw_if_cancelled(cancel_token)
        item_id = str(item.get("id") or f"image-{index}")
        filename = _safe_filename(str(item.get("filename") or ""), f"{item_id}.jpg")
        output_path = output_dir / filename
        last_exc: Exception | None = None
        if status_cb is not None:
            status_cb(f"正在下载图片 {index}/{total}: {filename}")

        for _attempt in range(retries + 1):
            cancellation.throw_if_cancelled(cancel_token)
            try:
                response = requests.get(str(item.get("url") or ""), timeout=30)
                response.raise_for_status()
                output_path.write_bytes(response.content)
                downloaded.append({**item, "local_path": str(output_path)})
                last_exc = None
                break
            except requests.RequestException as exc:
                last_exc = exc
            cancellation.throw_if_cancelled(cancel_token)

        if last_exc is not None:
            raise RuntimeError(f"failed to download {filename}: {last_exc}") from last_exc

    return downloaded
