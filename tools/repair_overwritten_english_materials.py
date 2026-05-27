from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

# Ensure ROOT is in python path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import OUTPUT_DIR
from appcore import local_media_storage, object_keys
from appcore.db import execute, query_all, query_one


def _resolve_media_item_path(object_key: str) -> Path:
    try:
        if local_media_storage.exists(object_key):
            return local_media_storage.safe_local_path_for(object_key)
    except Exception:
        pass
    upload_dir = os.environ.get("UPLOAD_DIR") or "/data/autovideosrt-test/uploads"
    return Path(upload_dir) / str(object_key or "")


def main():
    parser = argparse.ArgumentParser(description="Repair English media items that were overwritten by subtitle-removal results.")
    parser.add_argument("--dry-run", action="store_true", help="Dry run only (no physical copy or database updates)")
    parser.add_argument("--confirm", action="store_true", help="Explicitly confirm execution")
    args = parser.parse_args()

    if not args.dry_run and not args.confirm:
        print("Error: You must specify --dry-run or --confirm.")
        sys.exit(1)

    print("=" * 60)
    print(f"Starting English Material Repair Script (Dry Run: {args.dry_run})")
    print("=" * 60)

    # Find raw_niuma_done events which indicate subtitle removal finished successfully.
    events = query_all(
        "SELECT task_id, payload_json, actor_user_id, created_at "
        "FROM task_events "
        "WHERE event_type = 'raw_niuma_done' "
        "ORDER BY id ASC"
    )

    if not events:
        print("No 'raw_niuma_done' events found. Nothing to repair.")
        return

    print(f"Found {len(events)} niuma success events to scan.")

    repaired_count = 0
    skipped_count = 0

    for event in events:
        task_id = int(event["task_id"])
        raw_payload = event["payload_json"]
        
        event_payload = {}
        if isinstance(raw_payload, str):
            try:
                event_payload = json.loads(raw_payload)
            except Exception:
                pass
        elif isinstance(raw_payload, dict):
            event_payload = raw_payload

        subtitle_task_id = event_payload.get("subtitle_task_id")
        if not subtitle_task_id:
            print(f"[Skip] Event for Task {task_id} has no subtitle_task_id in payload.")
            skipped_count += 1
            continue

        # Fetch parent task media_item info
        payload = query_one(
            "SELECT t.id AS task_id, t.media_product_id, t.created_by, t.media_item_id, "
            "       i.id AS item_id, i.user_id AS item_user_id, i.filename, i.object_key "
            "FROM tasks t "
            "JOIN media_items i ON i.id = t.media_item_id "
            "WHERE t.id = %s AND t.parent_task_id IS NULL AND i.deleted_at IS NULL",
            (task_id,),
        )
        if not payload:
            print(f"[Skip] Parent Task {task_id} or active media_item not found.")
            skipped_count += 1
            continue

        item_id = int(payload["item_id"])
        product_id = int(payload.get("media_product_id") or 0)
        filename = os.path.basename(str(payload.get("filename") or "source.mp4"))
        item_user_id = payload.get("item_user_id")
        created_by = payload.get("created_by")
        user_id = int(item_user_id or created_by or 0)
        object_key = payload.get("object_key")

        if product_id <= 0 or not filename or user_id <= 0 or not object_key:
            print(f"[Skip] Invalid product/user info for Task {task_id} (Product: {product_id}, User: {user_id}, File: {filename}).")
            skipped_count += 1
            continue

        # If it already has result_object_key in payload, it was already repaired or processed with new code.
        if "result_object_key" in event_payload:
            print(f"[Info] Task {task_id} (Product {product_id}) already has result_object_key in event payload. Skipping.")
            skipped_count += 1
            continue

        # Find backup video path
        task_dir = Path(OUTPUT_DIR) / "task_center_raw" / subtitle_task_id
        if not task_dir.is_dir():
            print(f"[Skip] Backup task directory not found: {task_dir}")
            skipped_count += 1
            continue

        source_files = list(task_dir.glob("source.*"))
        video_source_files = [f for f in source_files if f.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.gif', '.cover', '.json')]
        if not video_source_files:
            print(f"[Skip] No backup source video found in {task_dir}")
            skipped_count += 1
            continue

        backup_video_path = video_source_files[0]

        # Overwritten English media file path (currently contains subtitle-removed video)
        media_item_path = _resolve_media_item_path(object_key)
        if not media_item_path.is_file():
            print(f"[Skip] Overwritten English media item path not found or not a file: {media_item_path}")
            skipped_count += 1
            continue

        # Build new correct raw source key for the subtitle-removed video
        raw_video_key = object_keys.build_media_raw_source_key(
            user_id,
            product_id,
            kind="video",
            filename=filename,
            exact_filename=True,
        )

        print(f"Found task {task_id} to REPAIR:")
        print(f"  - Product ID: {product_id}")
        print(f"  - Subtitle Task ID: {subtitle_task_id}")
        print(f"  - Backup Source: {backup_video_path}")
        print(f"  - Overwritten Target: {media_item_path}")
        print(f"  - New Raw Source Key: {raw_video_key}")

        if args.dry_run:
            print("  -> [Dry Run] Would transfer overwritten target to raw source key, restore backup video, and update DB.")
            repaired_count += 1
            continue

        # Executing actual repair
        print("  -> Executing repair...")
        try:
            # 1. Relocate currently overwritten file (niuma result) to dedicated raw source storage key
            if not local_media_storage.exists(raw_video_key):
                with media_item_path.open("rb") as stream:
                    local_media_storage.write_stream(raw_video_key, stream)
                print(f"     [OK] Copied overwritten file to raw source storage key: {raw_video_key}")
            else:
                print(f"     [Info] Raw source key {raw_video_key} already exists in storage. Skipping physical copy.")

            # 2. Restore backup video with subtitles to English media item path
            shutil.copyfile(backup_video_path, media_item_path)
            new_size = media_item_path.stat().st_size
            print(f"     [OK] Restored backup video to English media file: {media_item_path} (new size: {new_size})")

            # 3. Database updates
            # 3.1 Update media_items file_size
            execute(
                "UPDATE media_items SET file_size = %s WHERE id = %s",
                (new_size, item_id),
            )
            print("     [OK] Updated media_items.file_size in database.")

            # 3.2 Update media_raw_sources to point to new raw_video_key and adjust metadata
            raw_source = query_one(
                "SELECT id FROM media_raw_sources WHERE product_id = %s AND video_object_key = %s AND deleted_at IS NULL",
                (product_id, object_key),
            )
            if raw_source:
                raw_source_id = int(raw_source["id"])
                from appcore.task_raw_source_bridge import _safe_probe_video
                niuma_resolved_path = _resolve_media_item_path(raw_video_key)
                media_info = _safe_probe_video(niuma_resolved_path)
                
                raw_size = niuma_resolved_path.stat().st_size if niuma_resolved_path.exists() else new_size
                duration = media_info.get("duration") or None
                width = media_info.get("width") or None
                height = media_info.get("height") or None

                execute(
                    "UPDATE media_raw_sources "
                    "SET video_object_key = %s, file_size = %s, duration_seconds = %s, width = %s, height = %s "
                    "WHERE id = %s",
                    (raw_video_key, raw_size, duration, width, height, raw_source_id),
                )
                print(f"     [OK] Updated media_raw_sources (ID: {raw_source_id}) to point to {raw_video_key}.")
            else:
                print("     [Info] No matching media_raw_sources record found.")

            # 3.3 Update task_events payload_json to append result_object_key for future consistency
            event_payload["result_object_key"] = raw_video_key
            execute(
                "UPDATE task_events SET payload_json = %s "
                "WHERE task_id = %s AND event_type = 'raw_niuma_done' AND payload_json LIKE %s",
                (json.dumps(event_payload, ensure_ascii=False), task_id, f"%{subtitle_task_id}%"),
            )
            print("     [OK] Updated task_events raw_niuma_done payload with result_object_key.")
            
            repaired_count += 1
            print("  -> [REPAIR COMPLETE]")
        except Exception as exc:
            print(f"  -> [ERROR] Failed to repair Task {task_id}: {exc}")

    print("=" * 60)
    print(f"Scan complete. Repaired: {repaired_count}, Skipped: {skipped_count}.")
    print("=" * 60)


if __name__ == "__main__":
    main()
