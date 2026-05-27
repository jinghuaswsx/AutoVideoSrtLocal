#!/usr/bin/env python3
"""One-time backfill: populate NULL product_id in meta_ad_daily_ad_metrics.

Root cause: _extract_product_code_from_ad_name() extracts a truncated
product code from ad_name that doesn't match media_products. The campaign-
level table has correct product_id (matched via full campaign_name). This
script cross-references the campaign table to propagate product_id to the
ad-level table.

Strategy:
  For each ad row with NULL product_id, check if its product_code column
  is a prefix of any campaign's matched_product_code (which already has the
  correct mapping). If found, copy the product_id and matched_product_code.

Usage:
  cd /opt/autovideosrt
  venv/bin/python scripts/backfill_ad_product_id.py [--dry-run]
"""
from __future__ import annotations

import sys
sys.path.insert(0, ".")

from appcore.db import query, execute


def backfill(*, dry_run: bool = False) -> dict:
    # Step 1: Find all NULL product_id ad rows with a product_code
    null_rows = query(
        "SELECT id, product_code, ad_account_id, meta_business_date "
        "FROM meta_ad_daily_ad_metrics "
        "WHERE product_id IS NULL "
        "AND product_code IS NOT NULL AND product_code != '' "
        "AND COALESCE(spend_usd, 0) > 0"
    )
    print(f"Found {len(null_rows)} ad rows with NULL product_id and spend > 0")

    # Step 2: Build a lookup from campaign table
    # campaign rows have matched_product_code and product_id set correctly
    campaign_map: dict[tuple, dict] = {}
    campaign_rows = query(
        "SELECT DISTINCT ad_account_id, meta_business_date, "
        "       matched_product_code, product_id, campaign_name "
        "FROM meta_ad_daily_campaign_metrics "
        "WHERE product_id IS NOT NULL AND matched_product_code IS NOT NULL"
    )
    for cr in campaign_rows:
        key = (cr["ad_account_id"], str(cr["meta_business_date"]))
        if key not in campaign_map:
            campaign_map[key] = []
        campaign_map[key].append(cr)
    print(f"Loaded {len(campaign_rows)} campaign rows with product_id")

    # Step 3: Match each ad row to a campaign row
    updated = 0
    skipped = 0
    for row in null_rows:
        key = (row["ad_account_id"], str(row["meta_business_date"]))
        candidates = campaign_map.get(key, [])
        matched = None
        ad_pc = (row["product_code"] or "").strip().lower()
        if not ad_pc or len(ad_pc) < 8:
            skipped += 1
            continue

        for c in candidates:
            c_pc = (c["matched_product_code"] or "").strip().lower()
            # Check if the campaign's product_code starts with the ad's
            # truncated product_code (the ad_name prefix is often shorter)
            if c_pc.startswith(ad_pc) or c_pc.replace("-rjc", "").startswith(ad_pc):
                matched = c
                break

        if not matched:
            # Fallback: try matching campaign_name (lowercased) starts with ad_pc
            for c in candidates:
                c_name = (c["campaign_name"] or "").strip().lower()
                if c_name.startswith(ad_pc) or c_name == ad_pc:
                    matched = c
                    break

        if matched:
            if not dry_run:
                execute(
                    "UPDATE meta_ad_daily_ad_metrics "
                    "SET product_id = %s, matched_product_code = %s "
                    "WHERE id = %s",
                    (matched["product_id"], matched["matched_product_code"], row["id"])
                )
            updated += 1
        else:
            skipped += 1

    result = {"updated": updated, "skipped": skipped, "total": len(null_rows)}
    print(f"Result: {result}")
    if dry_run:
        print("(DRY RUN - no changes written)")
    return result


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    backfill(dry_run=dry_run)
