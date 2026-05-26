# scripts/backfill_push_history.py
import json
import logging
import sys
import os
from datetime import datetime

# 确保能加载 appcore
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from appcore.db import query, execute, query_one
from appcore.pushes import build_item_payload

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def backfill():
    # 查找所有 pushed_at IS NOT NULL 并且没有成功 push_log 的 items
    items = query(
        "SELECT i.id, i.product_id, i.pushed_at, i.display_name, i.filename, i.file_size, i.object_key, i.cover_object_key, i.lang "
        "FROM media_items i "
        "LEFT JOIN media_push_logs l ON l.item_id = i.id AND l.status = 'success' "
        "WHERE i.pushed_at IS NOT NULL AND l.id IS NULL AND i.deleted_at IS NULL"
    )
    
    logging.info(f"Found {len(items)} items to backfill push logs.")
    
    success_count = 0
    for item in items:
        item_id = item["id"]
        product_id = item["product_id"]
        pushed_at = item["pushed_at"]
        
        product = query_one("SELECT * FROM media_products WHERE id = %s", (product_id,))
        if not product:
            logging.warning(f"Product {product_id} not found for item {item_id}, skipping.")
            continue
            
        try:
            # 组装 payload 快照
            payload = build_item_payload(item, product)
        except Exception as e:
            # 有时可能产品已下架等，我们做一个宽松的容错，尽量组装出 payload
            logging.warning(f"build_item_payload failed for item {item_id}: {e}. Using fallback payload.")
            video = {
                "name": item.get("display_name") or item.get("filename") or "",
                "size": int(item.get("file_size") or 0),
                "width": 1080,
                "height": 1920,
                "url": f"https://ad-kaogujia-video.tos-cn-shanghai.volces.com/{item.get('object_key') or ''}",
                "image_url": f"https://ad-kaogujia-video.tos-cn-shanghai.volces.com/{item.get('cover_object_key') or ''}",
            }
            payload = {
                "mode": "create",
                "product_name": product.get("name") or "",
                "texts": [],
                "product_links": [],
                "videos": [video],
                "source": 0,
                "level": int(product.get("importance") or 3),
                "author": "蔡靖华",
                "push_admin": "蔡靖华",
                "roas": 1.6,
                "platforms": ["tiktok"],
                "selling_point": product.get("selling_points") or "",
                "tags": [],
            }
            
        # 插入成功快照，指定 created_at 为 pushed_at
        try:
            log_id = execute(
                "INSERT INTO media_push_logs "
                "(item_id, operator_user_id, status, request_payload, response_body, created_at) "
                "VALUES (%s, %s, 'success', %s, %s, %s)",
                (item_id, 1, json.dumps(payload, ensure_ascii=False), "Backfilled from historic pushed_at", pushed_at),
            )
            # 更新最新 push_id
            execute(
                "UPDATE media_items SET latest_push_id = %s WHERE id = %s",
                (log_id, item_id)
            )
            success_count += 1
            logging.info(f"Successfully backfilled push log for item {item_id}, log_id={log_id}")
        except Exception as e:
            logging.error(f"Failed to insert push log for item {item_id}: {e}")
            
    logging.info(f"Backfill finished. Total backfilled: {success_count}/{len(items)}")

if __name__ == "__main__":
    backfill()
