# scripts/backfill_localized_texts_in_push_history.py
import json
import logging
import sys
import os

# 确保能加载 appcore
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from appcore.db import query, execute
from appcore.pushes import resolve_localized_text_payload

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def backfill():
    logging.info("Start scanning push logs for localized texts backfill...")
    
    # 查找所有成功推送并且素材语言为小语种的推送记录
    rows = query(
        "SELECT l.id AS log_id, l.item_id, l.request_payload, "
        "       i.id, i.product_id, i.lang, i.display_name, i.filename "
        "FROM media_push_logs l "
        "JOIN media_items i ON i.id = l.item_id "
        "WHERE l.status = 'success' AND i.lang <> 'en' AND i.deleted_at IS NULL"
    )
    
    logging.info(f"Found {len(rows)} non-English success push logs in database.")
    
    backfill_count = 0
    skipped_count = 0
    failed_count = 0
    
    for row in rows:
        log_id = row["log_id"]
        item_id = row["item_id"]
        lang = row["lang"]
        
        payload_str = row["request_payload"]
        if not payload_str:
            skipped_count += 1
            continue
            
        try:
            payload = json.loads(payload_str)
        except Exception as e:
            logging.warning(f"Failed to parse JSON for log {log_id}: {e}")
            skipped_count += 1
            continue
            
        texts = payload.get("texts") or []
        
        # 判断当前快照是否需要回填小语种：
        # 如果 texts 列表为空，或者 texts 的第一个元素对应的 lang 是英文兜底，
        # 且我们能够查询到有效的对应小语种已录入文案，就进行回填清洗。
        is_english_snapshot = False
        if not texts:
            is_english_snapshot = True
        else:
            first_text = texts[0]
            snap_lang = str(first_text.get("lang") or "").strip().lower()
            # 若快照文案语种不匹配实际小语种，或者明确是英文
            if "en" in snap_lang or "english" in snap_lang or "英语" in snap_lang:
                is_english_snapshot = True
                
        if not is_english_snapshot:
            # 已经回填或者是匹配该小语种的，跳过
            skipped_count += 1
            continue
            
        # 组装符合 resolve_localized_text_payload 输入的 item 字典结构
        item = {
            "product_id": row["product_id"],
            "lang": lang
        }
        
        try:
            localized_text = resolve_localized_text_payload(item)
            if not localized_text:
                # 若对应的小语种文案在当时或目前不存在，保持英文兜底不破坏，优雅跳过
                skipped_count += 1
                continue
                
            # 执行文案回填
            payload["texts"] = [localized_text]
            
            # 安全写回数据库
            execute(
                "UPDATE media_push_logs SET request_payload = %s WHERE id = %s",
                (json.dumps(payload, ensure_ascii=False), log_id)
            )
            logging.info(
                f"Successfully backfilled log {log_id} (item {item_id}, lang={lang}) "
                f"with localized copywriting snapshot."
            )
            backfill_count += 1
        except Exception as e:
            logging.error(f"Failed to backfill log {log_id} (item {item_id}): {e}")
            failed_count += 1
            
    logging.info(
        f"Backfill localized texts complete! "
        f"Success: {backfill_count}, Skipped: {skipped_count}, Failed: {failed_count}."
    )

if __name__ == "__main__":
    backfill()
