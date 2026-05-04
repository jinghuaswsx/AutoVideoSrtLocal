"""Backfill purchase_1688_url for all products missing it.

For each product with a dianxiaomi_sku but no 1688 URL, search the supply
pairing API and update the product if a matching 1688 link is found.

Run on the server: venv/bin/python tools/backfill_1688_urls.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore import supply_pairing
from appcore.db import execute, query


def main():
    # 1. Find all products missing 1688 URL that have a dianxiaomi_sku
    rows = query("""
        SELECT DISTINCT mp.id AS product_id, ps.dianxiaomi_sku, mp.name
        FROM media_products mp
        JOIN media_product_skus ps ON ps.product_id = mp.id
        WHERE (mp.purchase_1688_url IS NULL OR mp.purchase_1688_url = '')
          AND ps.dianxiaomi_sku IS NOT NULL
          AND ps.dianxiaomi_sku != ''
        ORDER BY mp.id
    """)

    if not rows:
        print("No products to backfill.")
        return

    # Group by SKU to deduplicate CDP searches
    sku_to_products: dict[str, list[dict]] = {}
    for row in rows:
        sku = str(row["dianxiaomi_sku"]).strip()
        if sku not in sku_to_products:
            sku_to_products[sku] = []
        sku_to_products[sku].append(row)

    unique_skus = list(sku_to_products.keys())
    total_products = len(rows)
    print(f"Found {total_products} products across {len(unique_skus)} unique SKUs to backfill.")

    # 2. Search each unique SKU via supply pairing
    sku_url_map: dict[str, str] = {}  # sku -> 1688 URL
    matched = 0
    failed = 0
    empty = 0

    for i, sku in enumerate(unique_skus, 1):
        pids = [str(r["product_id"]) for r in sku_to_products[sku]]
        print(f"\n[{i}/{len(unique_skus)}] Searching SKU={sku} (products: {', '.join(pids)})...")
        try:
            result = supply_pairing.search_supply_pairing(sku, status="0")
            items = result.get("items") or []
            if items:
                url = supply_pairing.extract_1688_url(items[0])
                if url:
                    sku_url_map[sku] = url
                    name = items[0].get("name") or ""
                    print(f"  -> FOUND: {url} ({name})")
                    matched += 1
                else:
                    print(f"  -> No sourceUrl in result (found {len(items)} items)")
                    empty += 1
            else:
                print(f"  -> No supply pairing match for this SKU")
                empty += 1
        except Exception as exc:
            print(f"  -> ERROR: {exc}")
            failed += 1

    # 3. Update products
    updated = 0
    for sku, url in sku_url_map.items():
        for row in sku_to_products[sku]:
            pid = int(row["product_id"])
            try:
                execute(
                    "UPDATE media_products SET purchase_1688_url = %s WHERE id = %s",
                    (url, pid),
                )
                updated += 1
            except Exception as exc:
                print(f"  DB update error for product {pid}: {exc}")

    print(f"\n=== Summary ===")
    print(f"Products scanned : {total_products}")
    print(f"Unique SKUs      : {len(unique_skus)}")
    print(f"SKU matched      : {matched}")
    print(f"SKU no match     : {empty}")
    print(f"SKU error        : {failed}")
    print(f"Products updated : {updated}")
    print(f"Still NULL       : {total_products - updated}")


if __name__ == "__main__":
    main()
