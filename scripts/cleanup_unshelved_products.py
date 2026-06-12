#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Cleanup script for unshelved products, tasks, and media assets.
Designed to run on the production server or test environment.
"""

import os
import sys
import argparse

# Inject the parent directory into sys.path to make appcore importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from appcore import db
from appcore import tos_clients
from config import OUTPUT_DIR

def get_unshelved_products():
    """Query all products with listing_status = '下架'."""
    sql = "SELECT id, product_code, name, deleted_at FROM media_products WHERE listing_status = '下架'"
    return db.query(sql)

def collect_assets_for_product(product_id):
    """Collect all file paths, TOS object keys, item IDs, and task IDs for a product."""
    tos_keys = set()
    local_paths = set()
    
    # 1. media_product_covers
    covers = db.query("SELECT object_key FROM media_product_covers WHERE product_id = %s", (product_id,))
    for c in covers:
        if c.get("object_key"):
            tos_keys.add(c["object_key"])
            
    # 2. media_product_detail_images
    details = db.query("SELECT object_key FROM media_product_detail_images WHERE product_id = %s", (product_id,))
    for d in details:
        if d.get("object_key"):
            tos_keys.add(d["object_key"])
            
    # 3. media_raw_sources
    raw_sources = db.query("SELECT video_object_key, cover_object_key FROM media_raw_sources WHERE product_id = %s", (product_id,))
    for r in raw_sources:
        if r.get("video_object_key"):
            tos_keys.add(r["video_object_key"])
        if r.get("cover_object_key"):
            tos_keys.add(r["cover_object_key"])
            
    # 4. media_items
    items = db.query("SELECT id, object_key, thumbnail_path FROM media_items WHERE product_id = %s", (product_id,))
    item_ids = [it["id"] for it in items]
    for it in items:
        if it.get("object_key"):
            tos_keys.add(it["object_key"])
        if it.get("thumbnail_path"):
            tpath = it["thumbnail_path"]
            if not os.path.isabs(tpath):
                tpath = os.path.join(OUTPUT_DIR, tpath)
            local_paths.add(tpath)
            
    # 5. media_item_versions
    versions = db.query("SELECT object_key, cover_object_key FROM media_item_versions WHERE product_id = %s", (product_id,))
    for v in versions:
        if v.get("object_key"):
            tos_keys.add(v["object_key"])
        if v.get("cover_object_key"):
            tos_keys.add(v["cover_object_key"])
            
    # 6. tasks
    tasks = db.query("SELECT id FROM tasks WHERE media_product_id = %s", (product_id,))
    task_ids = [t["id"] for t in tasks]
    
    return {
        "tos_keys": sorted(list(tos_keys)),
        "local_paths": sorted(list(local_paths)),
        "item_ids": item_ids,
        "task_ids": task_ids
    }

def count_table_rows(product_id, item_ids, task_ids):
    """Count rows to be deleted or updated in various tables for summary/dry-run."""
    counts = {}
    
    # helper for simple product_id count
    def get_count(table, column="product_id"):
        res = db.query_one(f"SELECT COUNT(*) as c FROM {table} WHERE {column} = %s", (product_id,))
        return res["c"] if res else 0

    counts["media_products"] = get_count("media_products", "id")
    counts["media_copywritings"] = get_count("media_copywritings")
    counts["media_product_covers"] = get_count("media_product_covers")
    counts["media_product_detail_images"] = get_count("media_product_detail_images")
    counts["media_product_shopify_ids"] = get_count("media_product_shopify_ids")
    counts["media_product_skus"] = get_count("media_product_skus")
    counts["media_product_stability_snapshots"] = get_count("media_product_stability_snapshots")
    counts["media_product_ad_summary_cache"] = get_count("media_product_ad_summary_cache")
    counts["media_product_lang_ad_summary_cache"] = get_count("media_product_lang_ad_summary_cache")
    counts["media_raw_sources"] = get_count("media_raw_sources")
    counts["media_item_versions"] = get_count("media_item_versions")
    counts["media_items"] = get_count("media_items")
    counts["media_push_quality_checks"] = get_count("media_push_quality_checks")
    counts["campaign_product_overrides"] = get_count("campaign_product_overrides")
    counts["product_title_cache"] = get_count("product_title_cache")
    
    # strategist/analysis
    counts["ai_material_strategist_product_results"] = get_count("ai_material_strategist_product_results")
    counts["ad_material_ai_analysis_product_results"] = get_count("ad_material_ai_analysis_product_results")

    # items dependent
    if item_ids:
        placeholders = ",".join(["%s"] * len(item_ids))
        res = db.query_one(f"SELECT COUNT(*) as c FROM media_push_logs WHERE item_id IN ({placeholders})", tuple(item_ids))
        counts["media_push_logs"] = res["c"] if res else 0
        
        res = db.query_one(f"SELECT COUNT(*) as c FROM media_push_readiness_overrides WHERE media_item_id IN ({placeholders})", tuple(item_ids))
        counts["media_push_readiness_overrides"] = res["c"] if res else 0
    else:
        counts["media_push_logs"] = 0
        counts["media_push_readiness_overrides"] = 0

    # tasks dependent
    counts["tasks"] = get_count("tasks", "media_product_id")
    if task_ids:
        placeholders = ",".join(["%s"] * len(task_ids))
        res = db.query_one(f"SELECT COUNT(*) as c FROM task_events WHERE task_id IN ({placeholders})", tuple(task_ids))
        counts["task_events"] = res["c"] if res else 0
    else:
        counts["task_events"] = 0

    # updates (to set to NULL)
    counts["dianxiaomi_rankings (update to NULL)"] = get_count("dianxiaomi_rankings", "media_product_id")
    counts["shopify_orders (update to NULL)"] = get_count("shopify_orders")
    counts["dianxiaomi_order_lines (update to NULL)"] = get_count("dianxiaomi_order_lines")

    return counts

def cleanup_product_db_records(product_id, item_ids, task_ids):
    """Physically delete or disconnect product records in a transaction."""
    conn = db.get_conn()
    try:
        conn.begin()
        with conn.cursor() as cur:
            # 1. task_events
            if task_ids:
                placeholders = ",".join(["%s"] * len(task_ids))
                cur.execute(f"DELETE FROM task_events WHERE task_id IN ({placeholders})", tuple(task_ids))
            
            # 2. tasks
            cur.execute("DELETE FROM tasks WHERE media_product_id = %s", (product_id,))
            
            # 3. media_push_logs & readiness overrides
            if item_ids:
                placeholders = ",".join(["%s"] * len(item_ids))
                cur.execute(f"DELETE FROM media_push_logs WHERE item_id IN ({placeholders})", tuple(item_ids))
                cur.execute(f"DELETE FROM media_push_readiness_overrides WHERE media_item_id IN ({placeholders})", tuple(item_ids))
                
            # 4. media_push_quality_checks
            cur.execute("DELETE FROM media_push_quality_checks WHERE product_id = %s", (product_id,))
            
            # 5. media_product_skus
            cur.execute("DELETE FROM media_product_skus WHERE product_id = %s", (product_id,))
            
            # 6. media_product_stability_snapshots
            cur.execute("DELETE FROM media_product_stability_snapshots WHERE product_id = %s", (product_id,))
            
            # 7. media_product_ad_summary_cache
            cur.execute("DELETE FROM media_product_ad_summary_cache WHERE product_id = %s", (product_id,))
            
            # 8. media_product_lang_ad_summary_cache
            cur.execute("DELETE FROM media_product_lang_ad_summary_cache WHERE product_id = %s", (product_id,))
            
            # 9. media_copywritings
            cur.execute("DELETE FROM media_copywritings WHERE product_id = %s", (product_id,))
            
            # 10. media_product_covers
            cur.execute("DELETE FROM media_product_covers WHERE product_id = %s", (product_id,))
            
            # 11. media_product_detail_images
            cur.execute("DELETE FROM media_product_detail_images WHERE product_id = %s", (product_id,))
            
            # 12. media_product_shopify_ids
            cur.execute("DELETE FROM media_product_shopify_ids WHERE product_id = %s", (product_id,))
            
            # 13. campaign_product_overrides
            cur.execute("DELETE FROM campaign_product_overrides WHERE product_id = %s", (product_id,))

            # 14. product_title_cache
            cur.execute("DELETE FROM product_title_cache WHERE product_id = %s", (product_id,))

            # 15. ai_material_strategist_product_results
            cur.execute("DELETE FROM ai_material_strategist_product_results WHERE product_id = %s", (product_id,))

            # 16. ad_material_ai_analysis_product_results
            cur.execute("DELETE FROM ad_material_ai_analysis_product_results WHERE product_id = %s", (product_id,))

            # 17. media_raw_sources
            cur.execute("DELETE FROM media_raw_sources WHERE product_id = %s", (product_id,))
            
            # 18. media_item_versions
            cur.execute("DELETE FROM media_item_versions WHERE product_id = %s", (product_id,))
            
            # 19. media_items
            cur.execute("DELETE FROM media_items WHERE product_id = %s", (product_id,))
            
            # 20. Disconnect (set NULL) history links
            cur.execute("UPDATE dianxiaomi_rankings SET media_product_id = NULL WHERE media_product_id = %s", (product_id,))
            cur.execute("UPDATE shopify_orders SET product_id = NULL WHERE product_id = %s", (product_id,))
            cur.execute("UPDATE dianxiaomi_order_lines SET product_id = NULL WHERE product_id = %s", (product_id,))

            # 21. media_products itself
            cur.execute("DELETE FROM media_products WHERE id = %s", (product_id,))
            
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def main():
    parser = argparse.ArgumentParser(description="Clean up unshelved products, tasks, and media assets.")
    parser.add_argument("--run", action="store_true", help="Perform the actual hard deletion in the database and TOS.")
    args = parser.parse_args()
    
    print("==================================================")
    print("         DATA CLEANUP: UNSHELVED PRODUCTS         ")
    print("==================================================")
    
    products = get_unshelved_products()
    if not products:
        print("No unshelved products (listing_status = '下架') found.")
        return
        
    print(f"Found {len(products)} unshelved product(s):")
    for p in products:
        print(f" - ID: {p['id']}, Code: {p['product_code']}, Name: {p['name']}, SoftDeleted: {p.get('deleted_at') is not None}")
    
    print("\nCollecting associated asset details...")
    
    all_tos_keys = []
    all_local_paths = []
    all_counts = {}
    
    product_assets = {}
    for p in products:
        pid = p["id"]
        assets = collect_assets_for_product(pid)
        product_assets[pid] = assets
        
        all_tos_keys.extend(assets["tos_keys"])
        all_local_paths.extend(assets["local_paths"])
        
        counts = count_table_rows(pid, assets["item_ids"], assets["task_ids"])
        for tbl, val in counts.items():
            all_counts[tbl] = all_counts.get(tbl, 0) + val
            
    print(f"\nTOS Storage Objects to delete: {len(all_tos_keys)}")
    for key in all_tos_keys:
        print(f" - TOS: {key}")
        
    print(f"\nLocal Thumbnail files to delete: {len(all_local_paths)}")
    for path in all_local_paths:
        print(f" - Local: {path}")
        
    print("\nDatabase records to delete / disconnect:")
    for tbl, cnt in sorted(all_counts.items()):
        if cnt > 0:
            print(f" - {tbl}: {cnt} row(s)")
            
    print("\n==================================================")
    if not args.run:
        print("DRY-RUN MODE: No files or database records have been deleted.")
        print("To execute the actual deletion, run with the '--run' argument.")
        print("Example: python3 scripts/cleanup_unshelved_products.py --run")
        print("==================================================")
        return
        
    print("RUN MODE: Ready to perform HARD DELETE.")
    print("==================================================")
    
    # Double check confirmation
    try:
        confirm = input("Are you absolutely sure you want to perform HARD DELETE? This is irreversible! (yes/no): ")
    except KeyboardInterrupt:
        print("\nCancelled.")
        return
        
    if confirm.lower() != "yes":
        print("Aborted.")
        return
        
    print("\nStarting cleanup...")
    
    for p in products:
        pid = p["id"]
        pname = p["name"]
        assets = product_assets[pid]
        
        print(f"\n[Product {pid}: {pname}]")
        
        # 1. Delete TOS files
        print(f" - Deleting {len(assets['tos_keys'])} files from TOS...")
        for key in assets["tos_keys"]:
            try:
                # Use delete_media_object which defaults to TOS_MEDIA_BUCKET
                tos_clients.delete_media_object(key)
                print(f"   [Deleted TOS] {key}")
            except Exception as e:
                print(f"   [Error TOS] Failed to delete {key}: {e}")
                
        # 2. Delete Local files
        print(f" - Deleting {len(assets['local_paths'])} local files...")
        for path in assets["local_paths"]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                    print(f"   [Deleted Local] {path}")
                except Exception as e:
                    print(f"   [Error Local] Failed to delete {path}: {e}")
            else:
                print(f"   [Skip Local] File does not exist: {path}")
                
        # 3. Delete DB records
        print(" - Deleting database records...")
        try:
            cleanup_product_db_records(pid, assets["item_ids"], assets["task_ids"])
            print("   [DB Success] Physically deleted from database.")
        except Exception as e:
            print(f"   [DB Error] Failed to delete records: {e}")
            print("   Transaction rolled back.")
            
    print("\nCleanup finished successfully!")

if __name__ == "__main__":
    main()
