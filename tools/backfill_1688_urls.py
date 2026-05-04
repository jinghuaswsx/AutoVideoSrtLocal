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
    """Split Chinese product name into searchable keywords, longest first.

    Includes n-gram substrings (2-char min) so that e.g. 全自动水枪 and
    ARP9电动水枪 can match on the shared bigram 水枪.
    """
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
    # Also extract CJK bigrams from each part for fuzzy matching
    result = set()
    for p in parts:
        p = p.strip()
        if len(p) >= 2:
            result.add(p)
            # Generate 2-gram and 3-gram substrings for CJK-heavy strings
            if len(p) >= 3:
                for i in range(len(p) - 1):
                    result.add(p[i:i + 2])
                for i in range(len(p) - 2):
                    result.add(p[i:i + 3])
    # Return unique, longest first
    result = sorted(result, key=len, reverse=True)
    if not result and len(name) >= 2:
        result = [name.strip()]
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="match products and print would-be updates, but do not write to the DB",
    )
    args = parser.parse_args()
    dry_run = bool(args.dry_run)

    # 1. Pull ALL supply pairing records (waiting + paired) in one shot.
    # Empty status hits all rows (~378 on MKTT); status="2" alone is only the
    # 11 user-confirmed pairings, which is ~3% of the actual 1688-linkable
    # surface.
    print("[1] Pulling ALL supply pairing records (waiting + paired)...")
    all_paired: list[dict] = []
    try:
        result = supply_pairing.search_supply_pairing("", status="", page_size=100)
        all_paired = result.get("items") or []
        print(f"  pulled: {len(all_paired)} items")
    except Exception as exc:
        print(f"  ERROR {exc}")

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
    # candidate update with priority: lower number = higher priority
    # 0=exact_sku, 1=partial_sku, 2=keyword, 3=rev_kw
    candidates: list[dict] = []  # {pid, name, url, method, priority}

    for prod in products:
        pid = prod["id"]
        name = prod["name"]
        skus = prod["skus"]
        best_url = None
        match_method = ""
        priority = None

        def _is_1688(url: str | None) -> bool:
            return bool(url and "1688.com" in url)

        # Method A: Exact SKU match (1688 URLs only for purchase_1688_url)
        for sku in skus:
            if sku in sku_index:
                for item in sku_index[sku]:
                    url = supply_pairing.extract_1688_url(item)
                    if _is_1688(url):
                        best_url = url
                        match_method = f"exact_sku={sku}"
                        priority = 0
                        break
                if best_url:
                    break

        # Method B: Substring SKU match (1688 URLs only)
        if not best_url and skus:
            for db_sku in skus:
                for paired_sku, items in sku_index.items():
                    if db_sku in paired_sku or paired_sku in db_sku:
                        for item in items:
                            url = supply_pairing.extract_1688_url(item)
                            if _is_1688(url):
                                best_url = url
                                match_method = f"partial_sku={db_sku}≈{paired_sku}"
                                priority = 1
                                break
                        if best_url:
                            break
                if best_url:
                    break

        # Method C: Local product name keyword → dianxiaomi name (1688 URLs only)
        if not best_url and name:
            keywords = _product_keywords(name)
            for kw in keywords:
                if len(kw) < 2:
                    continue
                for item in all_paired:
                    paired_name = str(item.get("name") or "")
                    if kw in paired_name:
                        url = supply_pairing.extract_1688_url(item)
                        if _is_1688(url):
                            best_url = url
                            match_method = f"keyword={kw}"
                            priority = 2
                            break
                if best_url:
                    break

        # Method D: Dianxiaomi name keyword → local product name (1688 URLs only)
        if not best_url and name:
            for item in all_paired:
                paired_name = str(item.get("name") or "")
                if not paired_name:
                    continue
                paired_kws = _product_keywords(paired_name)
                for kw in paired_kws:
                    if len(kw) < 2:
                        continue
                    if kw in name:
                        url = supply_pairing.extract_1688_url(item)
                        if _is_1688(url):
                            best_url = url
                            match_method = f"rev_kw={kw}"
                            priority = 3
                            break
                if best_url:
                    break

        if best_url:
            candidates.append({
                "pid": pid, "name": name, "url": best_url,
                "method": match_method, "priority": priority,
            })
            matched += 1
        else:
            no_match += 1
            # Print first few no-match cases for debugging
            if no_match <= 10:
                sku_preview = ", ".join(list(skus)[:3])
                print(f"  [no_match] #{pid} {name[:40]} (SKUs: {sku_preview})")

    # 3.5. Deduplicate by URL: same 1688 URL can only be assigned to ONE
    # local product (otherwise keyword fan-out yields obvious mis-matches
    # like "证书套装" and "钢针套装" sharing the same offer). Tie-break
    # by priority: exact_sku wins over keyword.
    candidates.sort(key=lambda c: (c["priority"], c["pid"]))
    seen_urls: set[str] = set()
    updates: list[tuple[str, int]] = []
    dropped: list[dict] = []
    for c in candidates:
        if c["url"] in seen_urls:
            dropped.append(c)
            continue
        seen_urls.add(c["url"])
        updates.append((c["url"], c["pid"]))
        print(f"  [{c['method']}] #{c['pid']} {c['name'][:30]} -> {c['url'][:80]}")

    if dropped:
        print(f"\n[3.5] dropped {len(dropped)} fan-out duplicates (URL already taken):")
        for d in dropped:
            print(f"    [{d['method']}] #{d['pid']} {d['name'][:30]} (URL {d['url'][:60]} reserved by higher-priority match)")

    # 4. Apply updates
    if dry_run:
        print(f"\n[4] DRY RUN: would apply {len(updates)} updates (no DB write)")
    else:
        print(f"\n[4] Applying {len(updates)} updates...")
        for url, pid in updates:
            execute("UPDATE media_products SET purchase_1688_url = %s WHERE id = %s",
                    (url, pid))

    print(f"\n=== Summary ===")
    print(f"Total paired records : {len(all_paired)}")
    print(f"Products scanned     : {len(products)}")
    print(f"Products matched     : {matched}")
    print(f"Products no match    : {no_match}")
    label = "Products would update" if dry_run else "Products updated"
    print(f"{label:<21}: {len(set(p for _, p in updates))}")


if __name__ == "__main__":
    main()
