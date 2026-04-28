from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from appcore.db import execute, query


MEDIA_SUFFIXES = {
    ".aac",
    ".avi",
    ".flac",
    ".jpeg",
    ".jpg",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".png",
    ".wav",
    ".webm",
    ".webp",
}

PROXY_REQUIRED_PROVIDERS = {
    "anthropic",
    "gemini_aistudio",
    "gemini_vertex",
    "openai",
    "openrouter",
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill usage_log_payloads.request_data network media size estimates."
    )
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Recalculate existing network_estimate fields.")
    args = parser.parse_args()

    scanned = 0
    updated = 0
    skipped = 0
    missing_media = 0
    last_log_id = 0

    while True:
        remaining = args.limit - scanned if args.limit else args.batch_size
        if remaining <= 0:
            break
        batch_size = min(args.batch_size, remaining)
        rows = query(
            """
            SELECT p.log_id, p.request_data, ul.provider
            FROM usage_log_payloads p
            JOIN usage_logs ul ON ul.id = p.log_id
            WHERE p.log_id > %s AND p.request_data IS NOT NULL
            ORDER BY p.log_id ASC
            LIMIT %s
            """,
            (last_log_id, batch_size),
        )
        if not rows:
            break

        for row in rows:
            scanned += 1
            last_log_id = int(row["log_id"])
            request_data = _loads(row["request_data"])
            next_data, changed, missing_count = enrich_request_data(
                request_data,
                provider=row.get("provider") or "",
                force=args.force,
            )
            missing_media += missing_count
            if not changed:
                skipped += 1
                continue
            updated += 1
            if args.dry_run:
                continue
            execute(
                "UPDATE usage_log_payloads SET request_data = %s WHERE log_id = %s",
                (json.dumps(next_data, ensure_ascii=False), last_log_id),
            )

    mode = "DRY RUN" if args.dry_run else "UPDATED"
    print(
        f"{mode}: scanned={scanned} updated={updated} skipped={skipped} "
        f"missing_media={missing_media}"
    )


def enrich_request_data(data: Any, *, provider: str, force: bool = False) -> tuple[Any, bool, int]:
    if not isinstance(data, dict):
        return data, False, 0

    changed = False
    enriched = dict(data)

    if force or "network_route_intent" not in enriched:
        enriched["network_route_intent"] = _network_route_intent(provider)
        changed = True

    if "source_image_bytes" in enriched and (force or "estimated_base64_payload_bytes" not in enriched):
        source_bytes = _as_positive_int(enriched.get("source_image_bytes"))
        if source_bytes is not None:
            enriched["estimated_base64_payload_bytes"] = _base64_size(source_bytes)
            changed = True

    missing_media = 0
    if force or "network_estimate" not in enriched:
        paths = _extract_media_paths(enriched)
        if paths:
            estimate = _media_network_estimate(paths)
            missing_media = sum(1 for item in estimate["media"] if item["bytes"] is None)
            if any(item["bytes"] is not None for item in estimate["media"]):
                enriched["network_estimate"] = estimate
                changed = True

    return enriched, changed, missing_media


def _loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _extract_media_paths(value: Any) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for child in node.values():
                visit(child)
            return
        if isinstance(node, list):
            for child in node:
                visit(child)
            return
        if not isinstance(node, str) or not _looks_like_media_path(node):
            return
        normalized = str(Path(node))
        if normalized in seen:
            return
        seen.add(normalized)
        found.append(normalized)

    visit(value)
    return found


def _looks_like_media_path(value: str) -> bool:
    text = value.strip()
    if not text or text.startswith(("http://", "https://", "data:")):
        return False
    path = Path(text)
    return path.suffix.lower() in MEDIA_SUFFIXES and (path.is_absolute() or "/" in text or "\\" in text)


def _media_network_estimate(media_paths: list[str]) -> dict:
    items: list[dict[str, Any]] = []
    total_bytes = 0
    estimated_payload_bytes = 0
    for raw_path in media_paths:
        path = Path(raw_path)
        size = None
        try:
            size = path.stat().st_size
        except OSError:
            size = None
        if size is not None:
            total_bytes += int(size)
            estimated_payload_bytes += _base64_size(int(size))
        items.append({
            "path": str(path),
            "bytes": size,
            "estimated_base64_payload_bytes": _base64_size(int(size)) if size is not None else None,
        })
    return {
        "media": items,
        "total_media_bytes": total_bytes,
        "estimated_base64_payload_bytes": estimated_payload_bytes,
    }


def _network_route_intent(provider: str) -> str:
    value = (provider or "").strip().lower()
    if value in PROXY_REQUIRED_PROVIDERS:
        return "proxy_required"
    if value.startswith("doubao"):
        return "direct_preferred"
    return "unknown"


def _base64_size(size: int) -> int:
    return ((int(size) + 2) // 3) * 4


def _as_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


if __name__ == "__main__":
    main()
