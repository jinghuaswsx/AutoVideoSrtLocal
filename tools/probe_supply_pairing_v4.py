"""Probe v4: pull all status=1 records, statistics on alibabaProductId.

Key insight from v3: status=1 ('waiting list') items already have
alibabaProductId field populated by dianxiaomi's auto-matching, even when
sourceUrl is null. We can construct a 1688 URL from alibabaProductId.

Goal: enumerate all 367 status=1 items and report how many have
alibabaProductId, so we know the realistic ceiling of usable 1688 URLs.
"""
from __future__ import annotations

import json

from appcore.browser_automation_lock import browser_automation_lock

CDP_URL = "http://127.0.0.1:9222"
PAGE_URL = "https://www.dianxiaomi.com/web/supply/pairing"
API_URL = (
    "https://www.dianxiaomi.com/api/dxmAlibabaProductPair/"
    "alibabaProductPairPageList.json"
)


def _post(page, payload):
    return page.evaluate(
        """
        async ({ url, payload }) => {
          const body = new URLSearchParams();
          for (const [k, v] of Object.entries(payload)) body.append(k, String(v ?? ""));
          const r = await fetch(url, {
            method: "POST",
            headers: {
              "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
              "X-Requested-With": "XMLHttpRequest",
            },
            credentials: "include",
            body: body.toString(),
          });
          const text = await r.text();
          let parsed = null;
          try { parsed = JSON.parse(text); } catch (e) {}
          return { ok: r.ok, status: r.status, parsed, raw: text.slice(0, 600) };
        }
        """,
        {"url": API_URL, "payload": payload},
    )


def _pull_all(page, status: str, page_size: int = 100):
    out = []
    seen_ids = set()
    for page_no in range(1, 50):
        payload = {
            "pageNo": str(page_no),
            "pageSize": str(page_size),
            "status": status,
            "searchType": "1",
            "searchValue": "",
            "searchMode": "1",
        }
        r = _post(page, payload)
        parsed = r.get("parsed") or {}
        page_data = (parsed.get("data") or {}).get("page") or {}
        items = page_data.get("list") or []
        total = page_data.get("totalSize")
        new_items = [it for it in items if it.get("id") not in seen_ids]
        for it in new_items:
            seen_ids.add(it.get("id"))
        out.extend(new_items)
        print(
            f"  status={status} pageNo={page_no} returned={len(items)} "
            f"new_unique={len(new_items)} cumulative={len(out)} total={total}"
        )
        # Stop only when we got 0 items, or when cumulative >= totalSize
        if not items:
            break
        if total is not None and len(out) >= total:
            break
        if len(items) == 0:
            break
    return out


def main():
    from playwright.sync_api import sync_playwright

    with browser_automation_lock(
        task_code="supply_pairing_probe_v4",
        timeout_seconds=180,
        command="probe v4 status=1 full pull",
    ):
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_URL)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            existing = next(
                (x for x in ctx.pages if "dianxiaomi.com" in (x.url or "")), None
            )
            owns = False
            if existing is None:
                page = ctx.new_page()
                page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=30000)
                owns = True
            else:
                page = existing

            print("=" * 100)
            print("Pulling status=1 (full pagination)")
            print("=" * 100)
            status1 = _pull_all(page, "1")

            print()
            print("=" * 100)
            print("Pulling status=2 (full pagination)")
            print("=" * 100)
            status2 = _pull_all(page, "2")

            print()
            print("=" * 100)
            print("Stats")
            print("=" * 100)

            def _classify(items, label):
                with_alib_id = [it for it in items if it.get("alibabaProductId")]
                with_source = [it for it in items if it.get("sourceUrl")]
                with_1688_source = [
                    it for it in items if "1688.com" in (it.get("sourceUrl") or "")
                ]
                with_alib_list = [
                    it for it in items
                    if it.get("alibabaProductList") and len(it["alibabaProductList"]) > 0
                ]
                with_temp_pair = [
                    it for it in items
                    if it.get("tempAlibabaPairProducts")
                    and len(it["tempAlibabaPairProducts"]) > 0
                ]
                # has any 1688 signal: sourceUrl 1688 or alibabaProductId
                with_any_1688 = [
                    it for it in items
                    if "1688.com" in (it.get("sourceUrl") or "")
                    or it.get("alibabaProductId")
                ]
                print(f"\n[{label}] total={len(items)}")
                print(f"  with alibabaProductId : {len(with_alib_id)}")
                print(f"  with sourceUrl        : {len(with_source)}")
                print(f"  with 1688 sourceUrl   : {len(with_1688_source)}")
                print(f"  with alibabaProductList non-empty: {len(with_alib_list)}")
                print(f"  with tempAlibabaPairProducts non-empty: {len(with_temp_pair)}")
                print(f"  *** with ANY 1688 signal (sourceUrl or alibabaProductId): {len(with_any_1688)}")
                return with_any_1688

            sigs1 = _classify(status1, "status=1")
            sigs2 = _classify(status2, "status=2")

            # Sample 5 items with only alibabaProductId (no sourceUrl)
            print()
            print("=" * 100)
            print("Sample: items with alibabaProductId but no 1688 sourceUrl")
            print("=" * 100)
            samples = [
                it for it in status1
                if it.get("alibabaProductId")
                and "1688.com" not in (it.get("sourceUrl") or "")
            ][:5]
            for it in samples:
                print(json.dumps({
                    "name": it.get("name"),
                    "sku": it.get("sku"),
                    "skuCode": it.get("skuCode"),
                    "alibabaProductId": it.get("alibabaProductId"),
                    "sourceUrl": it.get("sourceUrl"),
                    "imgAlibaba": it.get("imgAlibaba"),
                    "supplierName": it.get("supplierName"),
                    "subject": it.get("subject"),
                    "constructed_1688_url":
                        f"https://detail.1688.com/offer/{it['alibabaProductId']}.html"
                        if it.get("alibabaProductId") else None,
                }, ensure_ascii=False))

            # write full status=1 dump for offline inspection
            with open("/tmp/dxm_status1_dump.json", "w", encoding="utf-8") as f:
                json.dump(status1, f, ensure_ascii=False)
            print(f"\nFull status=1 dump written to /tmp/dxm_status1_dump.json ({len(status1)} items)")

            with open("/tmp/dxm_status2_dump.json", "w", encoding="utf-8") as f:
                json.dump(status2, f, ensure_ascii=False)
            print(f"Full status=2 dump written to /tmp/dxm_status2_dump.json ({len(status2)} items)")

            if owns:
                try:
                    page.close()
                except Exception:
                    pass


if __name__ == "__main__":
    main()
