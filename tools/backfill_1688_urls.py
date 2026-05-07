"""Backfill purchase_1688_url by pulling ALL supply pairing records and matching.

Strategy:
1. Fetch ALL supply pairing records from dianxiaomi (waiting + paired)
2. For each local product missing 1688 URL, match against the pulled records:
   a. Exact match on dianxiaomi_sku / xmyc_storage_skus.sku
   b. Substring match on SKU
   c. Chinese product name keyword match, exported for manual review
3. Apply only exact-SKU high confidence matches automatically. Keyword matches
   are exported for review and can be imported after a human confirms them.

Run on the server:
  venv/bin/python tools/backfill_1688_urls.py --export-csv /tmp/1688_candidates.csv
  venv/bin/python tools/backfill_1688_urls.py --import-reviewed /tmp/1688_reviewed.csv
"""
from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore import supply_pairing
from appcore.db import execute, query


GENERIC_KEYWORDS = {
    "套装",
    "多功能",
    "汽车",
    "车载",
    "金属",
    "工具",
    "配件",
    "家用",
    "户外",
    "便携",
    "通用",
    "新款",
    "升级",
    "加厚",
    "防滑",
}

CSV_COLUMNS = [
    "product_id",
    "product_name",
    "candidate_url",
    "match_method",
    "confidence",
    "auto_apply",
    "paired_id",
    "paired_name",
    "matched_keyword",
    "reviewed_url",
    "review_note",
]


def normalize_1688_url(url: str | None) -> str | None:
    raw = str(url or "").strip()
    if not raw:
        return None
    if raw.startswith("//"):
        raw = f"https:{raw}"
    elif "://" not in raw and (
        raw.startswith("1688.com/") or raw.startswith("www.1688.com/")
        or raw.startswith("detail.1688.com/")
    ):
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if host == "1688.com" or host.endswith(".1688.com"):
        return raw
    return None


def is_1688_url(url: str | None) -> bool:
    return normalize_1688_url(url) is not None


def _is_generic_keyword(keyword: str) -> bool:
    kw = keyword.strip().lower()
    if not kw:
        return True
    if kw in GENERIC_KEYWORDS:
        return True
    return any(kw in stop for stop in GENERIC_KEYWORDS)


def _product_keywords(name: str) -> list[str]:
    """Split Chinese product name into searchable keywords, longest first.

    Chinese runs get 2/3-gram substrings so e.g. 全自动水枪 and ARP9电动水枪
    can match on 水枪. ASCII words stay whole; generating ``su`` / ``pe`` from
    ``super`` caused unsafe false positives in the 2026-05-07 dry run.
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
    tokens = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", cleaned)
    result: dict[str, int] = {}

    def _add_keyword(keyword: str) -> None:
        if keyword not in result:
            result[keyword] = len(result)

    for token in tokens:
        token = token.strip()
        if not token:
            continue
        if re.fullmatch(r"[A-Za-z0-9]+", token):
            if len(token) >= 3:
                _add_keyword(token.lower())
            continue
        if len(token) >= 2 and not _is_generic_keyword(token):
            _add_keyword(token)
        if len(token) >= 3:
            for size in (3, 2):
                for i in range(len(token) - size + 1):
                    piece = token[i:i + size]
                    if not _is_generic_keyword(piece):
                        _add_keyword(piece)
    # Return unique, longest first
    return sorted(result, key=lambda kw: (-len(kw), result[kw]))


def _paired_sku_values(item: dict[str, Any]) -> list[str]:
    values = []
    for key in ("sku", "skuCode"):
        value = str(item.get(key) or "").strip()
        if value and value not in values:
            values.append(value)
    return values


def build_sku_index(all_paired: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    sku_index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in all_paired:
        for sku in _paired_sku_values(item):
            sku_index[sku].append(item)
    return sku_index


def _candidate(
    *,
    prod: dict[str, Any],
    item: dict[str, Any],
    url: str,
    method: str,
    priority: int,
    confidence: str,
    auto_apply: bool,
    matched_keyword: str = "",
    paired_sku: str = "",
) -> dict[str, Any]:
    return {
        "pid": int(prod["id"]),
        "name": str(prod.get("name") or ""),
        "url": url,
        "method": method,
        "priority": priority,
        "confidence": confidence,
        "auto_apply": auto_apply,
        "paired_id": str(item.get("id") or ""),
        "paired_name": str(item.get("name") or ""),
        "matched_keyword": matched_keyword,
        "paired_sku": paired_sku,
    }


def build_match_candidates(
    products: list[dict[str, Any]],
    all_paired: list[dict[str, Any]],
    *,
    extract_1688_url_fn: Callable[[dict[str, Any]], str | None] = supply_pairing.extract_1688_url,
) -> list[dict[str, Any]]:
    sku_index = build_sku_index(all_paired)
    candidates: list[dict[str, Any]] = []

    for prod in products:
        name = str(prod.get("name") or "")
        skus = {str(s).strip() for s in (prod.get("skus") or set()) if str(s).strip()}
        best: dict[str, Any] | None = None

        # Method A: exact SKU match. This is the only auto-apply path.
        for sku in sorted(skus):
            for item in sku_index.get(sku, []):
                url = normalize_1688_url(extract_1688_url_fn(item))
                if url:
                    best = _candidate(
                        prod=prod,
                        item=item,
                        url=url,
                        method=f"exact_sku={sku}",
                        priority=0,
                        confidence="high",
                        auto_apply=True,
                        paired_sku=sku,
                    )
                    break
            if best:
                break

        # Method B: substring SKU match. Export for review.
        if not best and skus:
            for db_sku in sorted(skus):
                for paired_sku, items in sku_index.items():
                    if db_sku == paired_sku:
                        continue
                    if db_sku in paired_sku or paired_sku in db_sku:
                        for item in items:
                            url = normalize_1688_url(extract_1688_url_fn(item))
                            if url:
                                best = _candidate(
                                    prod=prod,
                                    item=item,
                                    url=url,
                                    method=f"partial_sku={db_sku}≈{paired_sku}",
                                    priority=1,
                                    confidence="review",
                                    auto_apply=False,
                                    paired_sku=paired_sku,
                                )
                                break
                        if best:
                            break
                if best:
                    break

        # Method C: local product name keyword -> dianxiaomi name.
        if not best and name:
            for kw in _product_keywords(name):
                for item in all_paired:
                    paired_name = str(item.get("name") or "")
                    if kw in paired_name.lower() or kw in paired_name:
                        url = normalize_1688_url(extract_1688_url_fn(item))
                        if url:
                            best = _candidate(
                                prod=prod,
                                item=item,
                                url=url,
                                method=f"keyword={kw}",
                                priority=2,
                                confidence="review",
                                auto_apply=False,
                                matched_keyword=kw,
                            )
                            break
                if best:
                    break

        # Method D: dianxiaomi name keyword -> local product name.
        if not best and name:
            lower_name = name.lower()
            for item in all_paired:
                paired_name = str(item.get("name") or "")
                if not paired_name:
                    continue
                for kw in _product_keywords(paired_name):
                    if kw in lower_name or kw in name:
                        url = normalize_1688_url(extract_1688_url_fn(item))
                        if url:
                            best = _candidate(
                                prod=prod,
                                item=item,
                                url=url,
                                method=f"rev_kw={kw}",
                                priority=3,
                                confidence="review",
                                auto_apply=False,
                                matched_keyword=kw,
                            )
                            break
                if best:
                    break

        if best:
            candidates.append(best)

    return candidates


def deduplicate_candidates(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ordered = sorted(candidates, key=lambda c: (c["priority"], c["pid"]))
    seen_urls: set[str] = set()
    selected: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for candidate in ordered:
        url = candidate["url"]
        if url in seen_urls:
            dropped.append(candidate)
            continue
        seen_urls.add(url)
        selected.append(candidate)
    return selected, dropped


def export_candidates_csv(candidates: list[dict[str, Any]], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for candidate in candidates:
            writer.writerow({
                "product_id": candidate["pid"],
                "product_name": candidate["name"],
                "candidate_url": candidate["url"],
                "match_method": candidate["method"],
                "confidence": candidate["confidence"],
                "auto_apply": "true" if candidate["auto_apply"] else "false",
                "paired_id": candidate.get("paired_id", ""),
                "paired_name": candidate.get("paired_name", ""),
                "matched_keyword": candidate.get("matched_keyword", ""),
                "reviewed_url": "",
                "review_note": "",
            })


def import_reviewed_csv(
    input_path: str | Path,
    *,
    execute_fn: Callable[[str, tuple], int] = execute,
) -> dict[str, int]:
    summary = {
        "rows": 0,
        "updated": 0,
        "skipped_blank": 0,
        "skipped_invalid": 0,
    }
    with Path(input_path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            summary["rows"] += 1
            reviewed_url = (row.get("reviewed_url") or "").strip()
            if not reviewed_url:
                summary["skipped_blank"] += 1
                continue
            url = normalize_1688_url(reviewed_url)
            if not url:
                summary["skipped_invalid"] += 1
                continue
            try:
                pid = int(row.get("product_id") or row.get("pid") or "")
            except ValueError:
                summary["skipped_invalid"] += 1
                continue
            changed = execute_fn(
                """
                UPDATE media_products
                SET purchase_1688_url = %s
                WHERE id = %s
                  AND (purchase_1688_url IS NULL OR purchase_1688_url = '')
                """,
                (url, pid),
            )
            if changed:
                summary["updated"] += 1
    return summary


def load_products_missing_1688() -> list[dict[str, Any]]:
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

    products: list[dict[str, Any]] = []
    by_id: dict[int, dict[str, Any]] = {}
    for row in rows:
        pid = int(row["product_id"])
        product = by_id.get(pid)
        if product is None:
            product = {
                "id": pid,
                "name": str(row.get("name") or ""),
                "skus": set(),
            }
            by_id[pid] = product
            products.append(product)
        if row.get("dianxiaomi_sku"):
            product["skus"].add(str(row["dianxiaomi_sku"]).strip())
        if row.get("xmyc_skus"):
            for sku in str(row["xmyc_skus"]).split("||"):
                sku = sku.strip()
                if sku:
                    product["skus"].add(sku)
    return products


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="match products and print would-be updates, but do not write to the DB",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write high-confidence exact-SKU matches to the DB",
    )
    parser.add_argument(
        "--export-csv",
        help="write all selected candidates to a CSV for manual review",
    )
    parser.add_argument(
        "--import-reviewed",
        help="import a reviewed CSV and update reviewed_url values only",
    )
    args = parser.parse_args()

    if args.import_reviewed:
        summary = import_reviewed_csv(args.import_reviewed)
        print("[import-reviewed] summary:")
        for key, value in summary.items():
            print(f"  {key}: {value}")
        return

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

    sku_index = build_sku_index(all_paired)
    print(f"  Built index with {len(sku_index)} unique SKU entries")

    # 2. Find products missing 1688 URL
    products = load_products_missing_1688()
    if not products:
        print("No products to backfill.")
        return

    print(f"\n[2] Matching {len(products)} products against {len(all_paired)} paired records...")

    candidates = build_match_candidates(products, all_paired)
    matched_ids = {c["pid"] for c in candidates}
    no_match_products = [p for p in products if p["id"] not in matched_ids]
    for prod in no_match_products[:10]:
        sku_preview = ", ".join(list(prod["skus"])[:3])
        print(f"  [no_match] #{prod['id']} {prod['name'][:40]} (SKUs: {sku_preview})")

    # 3.5. Deduplicate by URL: same 1688 URL can only be assigned to ONE
    # local product (otherwise keyword fan-out yields obvious mis-matches
    # like "证书套装" and "钢针套装" sharing the same offer). Tie-break
    # by priority: exact_sku wins over keyword.
    selected, dropped = deduplicate_candidates(candidates)
    auto_updates = [c for c in selected if c["auto_apply"]]
    review_candidates = [c for c in selected if not c["auto_apply"]]
    for c in selected:
        marker = "auto" if c["auto_apply"] else "review"
        print(f"  [{marker}:{c['method']}] #{c['pid']} {c['name'][:30]} -> {c['url'][:80]}")

    if dropped:
        print(f"\n[3.5] dropped {len(dropped)} fan-out duplicates (URL already taken):")
        for d in dropped:
            print(f"    [{d['method']}] #{d['pid']} {d['name'][:30]} (URL {d['url'][:60]} reserved by higher-priority match)")

    if args.export_csv:
        export_candidates_csv(selected, args.export_csv)
        print(f"\n[3.6] exported {len(selected)} candidates to {args.export_csv}")

    # 4. Apply updates
    if args.apply and not args.dry_run:
        print(f"\n[4] Applying {len(auto_updates)} high-confidence updates...")
        for c in auto_updates:
            execute(
                """
                UPDATE media_products
                SET purchase_1688_url = %s
                WHERE id = %s
                  AND (purchase_1688_url IS NULL OR purchase_1688_url = '')
                """,
                (c["url"], c["pid"]),
            )
    else:
        print(
            f"\n[4] REVIEW MODE: no DB write. "
            f"Would auto-apply {len(auto_updates)} high-confidence exact-SKU updates."
        )
        if review_candidates:
            print(f"    {len(review_candidates)} keyword/partial candidates need CSV review.")

    print(f"\n=== Summary ===")
    print(f"Total paired records : {len(all_paired)}")
    print(f"Products scanned     : {len(products)}")
    print(f"Products matched     : {len(matched_ids)}")
    print(f"Products no match    : {len(no_match_products)}")
    print(f"Auto-apply candidates: {len(auto_updates)}")
    print(f"Review candidates    : {len(review_candidates)}")
    label = "Products updated" if args.apply and not args.dry_run else "Products would auto-update"
    print(f"{label:<27}: {len(auto_updates)}")


if __name__ == "__main__":
    main()
