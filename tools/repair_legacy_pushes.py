import json
import os
import urllib.parse
import re
from appcore.db import query, execute, query_one
from appcore import local_media_storage

# Target IP address provided by the user for legacy server migration
NEW_SERVER_IP = "172.16.254.106"
OLD_SERVER_IP = "172.30.254.14"

def get_absolute_media_url(object_key: str | None) -> str:
    if not object_key:
        return ""
    key = str(object_key).strip().replace("\\", "/")
    if key.startswith("/"):
        key = key.lstrip("/")
    encoded = urllib.parse.quote(key, safe="/")
    return f"http://{NEW_SERVER_IP}/medias/obj/{encoded}"

def get_core_keywords(name: str) -> str:
    if not name:
        return ""
    # Strip language suffixes like (意大利语), (法语)
    cleaned = re.sub(r'[\(\[（【].*?[\)\]）】]', '', name)
    # Strip dates like 2026.05.09- or 20260509
    cleaned = re.sub(r'^\d{4}[\.\-_]?\d{2}[\.\-_]?\d{2}[\-_]?', '', cleaned)
    # Strip translation suffixes
    cleaned = re.sub(r'-(原素材|补充素材|小语种翻译素材|指派|草稿|整理).*$', '', cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace(".mp4", "").replace(".png", "").replace(".jpg", "").strip()
    return cleaned

def main():
    print(f"Starting legacy pushes repair. Target IP: {NEW_SERVER_IP} (migrating from {OLD_SERVER_IP})...")
    
    # 1. Query all successful pushes in media_push_logs
    logs = query(
        "SELECT l.id, l.item_id, l.request_payload "
        "FROM media_push_logs l "
        "WHERE l.status = 'success'"
    )
    
    print(f"Loaded {len(logs)} successful push logs to analyze.")
    repaired_count = 0
    
    for row in logs:
        log_id = row["id"]
        item_id = row["item_id"]
        
        try:
            payload = json.loads(row["request_payload"]) if row.get("request_payload") else {}
        except Exception as e:
            print(f"Log {log_id}: Skip due to JSON load error: {e}")
            continue
            
        videos = payload.get("videos", [])
        if not videos:
            continue
            
        video_snap = videos[0]
        original_name = video_snap.get("name") or ""
        original_url = video_snap.get("url") or ""
        original_cover = video_snap.get("image_url") or ""
        
        # Fallback: if name is missing from payload, query the original item's filename
        if not original_name:
            item_info = query_one("SELECT filename, display_name FROM media_items WHERE id = %s", (item_id,))
            if item_info:
                original_name = item_info.get("filename") or item_info.get("display_name") or ""
                
        if not original_name:
            print(f"Log {log_id}: Missing original filename, skip.")
            continue
            
        # 2. Find product_id and language of this pushed item
        item_info = query_one(
            "SELECT product_id, lang, filename, display_name FROM media_items WHERE id = %s", 
            (item_id,)
        )
        
        if not item_info:
            print(f"Log {log_id}: media_item {item_id} not found in DB, skip.")
            continue
            
        product_id = item_info["product_id"]
        lang = item_info["lang"]
        
        # 3. Pull all media_items under the same product and language
        candidates = query(
            "SELECT id, filename, display_name, object_key, cover_object_key, file_size "
            "FROM media_items "
            "WHERE product_id = %s AND lang = %s AND deleted_at IS NULL",
            (product_id, lang)
        )
        
        matched_item = None
        original_core = get_core_keywords(original_name)
        
        # Match strategy 1: Perfect name match + file exists physically
        for cand in candidates:
            cand_name = cand["filename"] or cand["display_name"] or ""
            if not cand_name:
                continue
            obj_key = cand["object_key"]
            if not obj_key or not local_media_storage.exists(obj_key):
                continue
                
            if cand_name == original_name:
                matched_item = cand
                break
        
        # Match strategy 2: Core keywords match + file exists physically
        if not matched_item:
            for cand in candidates:
                cand_name = cand["filename"] or cand["display_name"] or ""
                if not cand_name:
                    continue
                obj_key = cand["object_key"]
                if not obj_key or not local_media_storage.exists(obj_key):
                    continue
                    
                cand_core = get_core_keywords(cand_name)
                if original_core and cand_core and (original_core in cand_core or cand_core in original_core):
                    matched_item = cand
                    break
                    
        # Match strategy 3: Size proximity match + file exists physically
        if not matched_item and candidates:
            for cand in candidates:
                obj_key = cand["object_key"]
                if not obj_key or not local_media_storage.exists(obj_key):
                    continue
                cand_size = cand["file_size"] or 0
                orig_size = video_snap.get("size") or 0
                if orig_size > 0 and cand_size > 0 and abs(cand_size - orig_size) / orig_size < 0.1:
                    matched_item = cand
                    break
                    
        # 4. Rewrite absolute paths and update DB if matched
        if matched_item:
            new_obj = matched_item["object_key"]
            new_cover_obj = matched_item["cover_object_key"]
            
            new_video_url = get_absolute_media_url(new_obj)
            new_cover_url = get_absolute_media_url(new_cover_obj) if new_cover_obj else ""
            
            url_changed = False
            
            # Clean URLs in payload if they are obsolete or different
            if original_url != new_video_url:
                video_snap["url"] = new_video_url
                url_changed = True
                
            if new_cover_url and original_cover != new_cover_url:
                video_snap["image_url"] = new_cover_url
                url_changed = True
                
            if url_changed:
                # Align other metadata
                if matched_item.get("file_size"):
                    video_snap["size"] = int(matched_item["file_size"])
                video_snap["name"] = matched_item["filename"] or video_snap.get("name")
                
                payload["videos"][0] = video_snap
                new_payload_json = json.dumps(payload, ensure_ascii=False)
                
                execute(
                    "UPDATE media_push_logs SET request_payload = %s WHERE id = %s",
                    (new_payload_json, log_id)
                )
                print(f"Log {log_id}: Repaired from library matching! Name: '{video_snap['name']}' -> '{new_video_url}'")
                repaired_count += 1
        else:
            # Fallback: if no candidates matched but original URL contains the old IP, perform direct IP replacement
            url_changed = False
            
            if OLD_SERVER_IP in original_url:
                video_snap["url"] = original_url.replace(OLD_SERVER_IP, NEW_SERVER_IP)
                url_changed = True
                
            if OLD_SERVER_IP in original_cover:
                video_snap["image_url"] = original_cover.replace(OLD_SERVER_IP, NEW_SERVER_IP)
                url_changed = True
                
            # If not old IP, check if any absolute HTTP URL points to public media, and align it to NEW_SERVER_IP
            if not url_changed and original_url.startswith(("http://", "https://")) and "/medias/obj/" in original_url:
                idx = original_url.find("/medias/obj/")
                new_url = f"http://{NEW_SERVER_IP}{original_url[idx:]}"
                if original_url != new_url:
                    video_snap["url"] = new_url
                    url_changed = True
                
                if original_cover.startswith(("http://", "https://")) and "/medias/obj/" in original_cover:
                    idx_c = original_cover.find("/medias/obj/")
                    new_cover = f"http://{NEW_SERVER_IP}{original_cover[idx_c:]}"
                    if original_cover != new_cover:
                        video_snap["image_url"] = new_cover
                        url_changed = True
            
            if url_changed:
                payload["videos"][0] = video_snap
                new_payload_json = json.dumps(payload, ensure_ascii=False)
                
                execute(
                    "UPDATE media_push_logs SET request_payload = %s WHERE id = %s",
                    (new_payload_json, log_id)
                )
                print(f"Log {log_id}: Repaired via direct IP migration: '{original_url}' -> '{video_snap['url']}'")
                repaired_count += 1
            else:
                print(f"Log {log_id}: No matching physical file found for '{original_name}' and URL is already clean, skip.")
            
    print(f"\nLegacy pushes repair completed. Total repaired/migrated logs: {repaired_count}")

if __name__ == "__main__":
    main()
