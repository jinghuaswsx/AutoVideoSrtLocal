"""Backfill purchase_1688_url by pulling ALL supply pairing records and matching.

Strategy:
1. Fetch ALL paired (status=2) supply pairing records from dianxiaomi
2. For each local product missing 1688 URL, match against the pulled records:
   a. Exact match on dianxiaomi_sku / xmyc_storage_skus.sku
   b. Substring match on SKU
   c. Chinese product name keyword match (longest common substring)
3. Update products where a match is found

Run on the server: venv/bin/python tools/backfill_1688_urls.py
"""
from __future__ import annotations

import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore import supply_pairing
from appcore.db import execute, query


def _product_keywords(name: str) -> list[str]:
    """Split Chinese product name into searchable keywords, longest first."""
    if not name:
        return []
    # Remove variant suffixes like color/size
    cleaned = re.sub(r'[-－]\s*(黑色|白色|红色|蓝色|绿色|黄色|粉色|灰色|棕色|紫色|银色|金色'
                     r'|卡其色|米色|浅棕|深咖|随机|英文|充电|中文|普通|豪华|'
                     r'\d+[支个件套米]|\d+\.?\d*[米mM])', '', name)
    cleaned = cleaned.strip()
    if not cleaned:
        cleaned = name.strip()
    # Split by common delimiters
    parts = re.split(r'[，,\s]+', cleaned)
    # Return unique non-empty parts, longest first
    parts = sorted(set(p for p in parts if len(p) >= 2), key=len, reverse=True)
    if not parts and len(name) >= 2:
        parts = [name.strip()]
    return parts


def main():
    # 1. Pull ALL paired records from supply pairing
    print("[1] Pulling ALL paired supply pairing records...")
    all_paired: list[dict] = []
    try:
        result = supply_pairing.search_supply_pairing("", status="2", page_size=100)
        all_paired = result.get("items") or []
        total_from_api = result.get("total", len(all_paired))
        print(f"  Pulled {len(all_paired)} items (API reports total={total_from_api})")
        # If there are more pages, we may need to paginate; but search_supply_pairing
        # with empty query should return all. If not, we rely on the function's pagination.
    except Exception as exc:
        print(f"  ERROR pulling records: {exc}")
        return

    if not all_paired:
        print("  No paired records found at all.")
        return

    # Build lookup: SKU -> list of paired items
    sku_index: dict[str, list[dict]] = defaultdict(list)
    for item in all_paired:
        sku = str(item.get("sku") or "").strip()
        sku_code = str(item.get("skuCode") or "").strip()
        if sku:
            sku_index[sku].append(item)
        if sku_code and sku_code != sku:
            sku_index[sku_code].append(item)

    print(f"  Built index with {len(sku_index)} unique SKU entries")

    # 2. Find products missing 1688 URL
    rows = query("""
        SELECT DISTINCT mp.id AS product_id, mp.name,
               ps.dianxiaomi_sku,
               (SELECT GROUP_CONCAT(DISTINCT xs.sku SEPARATOR '||')
                FROM xmyc_storage_skus xs WHERE xs.product_id = mp.id) AS xmyc_skus
        FROM media_products mp
        LEFT JOIN media_product_skus ps ON ps.product_id = mp.id
        WHERE (mp.purchase_1688_url IS NULL OR mp.purchase_1688_url = '')
        ORDER BY mp.id
    """)

    if not rows:
        print("No products to backfill.")
        return

    # Build product index
    products: list[dict] = []
    for row in rows:
        pid = int(row["product_id"])
        # Find existing entry or create new
        existing = next((p for p in products if p["id"] == pid), None)
        if existing is None:
            name = str(row.get("name") or "")
            skus = set()
            if row.get("dianxiaomi_sku"):
                skus.add(str(row["dianxiaomi_sku"]).strip())
            if row.get("xmyc_skus"):
                for s in str(row["xmyc_skus"]).split("||"):
                    s = s.strip()
                    if s:
                        skus.add(s)
            products.append({
                "id": pid,
                "name": name,
                "skus": skus,
            })
        else:
            if row.get("dianxiaomi_sku"):
                existing["skus"].add(str(row["dianxiaomi_sku"]).strip())
            if row.get("xmyc_skus"):
                for s in str(row["xmyc_skus"]).split("||"):
                    s = s.strip()
                    if s:
                        existing["skus"].add(s)

    print(f"\n[2] Matching {len(products)} products against {len(all_paired)} paired records...")

    # 3. Match products to supply pairing records
    matched = 0
    no_match = 0
    updates: list[tuple[str, int]] = []  # (url, product_id)

    for prod in products:
        pid = prod["id"]
        name = prod["name"]
        skus = prod["skus"]
        best_url = None
        match_method = ""

        # Method A: Exact SKU match
        for sku in skus:
            if sku in sku_index:
                items = sku_index[sku]
                url = supply_pairing.extract_1688_url(items[0])
                if url:
                    best_url = url
                    match_method = f"exact_sku={sku}"
                    break

        # Method B: Substring SKU match (the DB SKU might be part of the pairing SKU)
        if not best_url and skus:
            for db_sku in skus:
                for paired_sku, items in sku_index.items():
                    if db_sku in paired_sku or paired_sku in db_sku:
                        url = supply_pairing.extract_1688_url(items[0])
                        if url:
                            best_url = url
                            match_method = f"partial_sku={db_sku}≈{paired_sku}"
                            break
                if best_url:
                    break

        # Method C: Product name keyword match
        if not best_url and name:
            keywords = _product_keywords(name)
            for kw in keywords:
                if len(kw) < 2:
                    continue
                for item in all_paired:
                    paired_name = str(item.get("name") or "")
                    if kw in paired_name:
                        url = supply_pairing.extract_1688_url(item)
                        if url:
                            best_url = url
                            match_method = f"keyword={kw}"
                            break
                if best_url:
                    break

        if best_url:
            updates.append((best_url, pid))
            matched += 1
            print(f"  [{match_method}] #{pid} {name[:30]} -> {best_url[:80]}")
        else:
            no_match += 1
            # Print first few no-match cases for debugging
            if no_match <= 5:
                sku_preview = ", ".join(list(skus)[:3])
                print(f"  [no_match] #{pid} {name[:40]} (SKUs: {sku_preview})")

    # 4. Apply updates
    print(f"\n[3] Applying {len(updates)} updates...")
    for url, pid in updates:
        execute("UPDATE media_products SET purchase_1688_url = %s WHERE id = %s", (url, pid))

    print(f"\n=== Summary ===")
    print(f"Total paired records : {len(all_paired)}")
    print(f"Products scanned     : {len(products)}")
    print(f"Products matched     : {matched}")
    print(f"Products no match    : {no_match}")
    print(f"Products updated     : {len(set(p for _, p in updates))}")


if __name__ == "__main__":
    main()
