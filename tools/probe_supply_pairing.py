"""V4: Deep-dive into data.page structure for the supply pairing API:
POST /api/dxmAlibabaProductPair/alibabaProductPairPageList.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main():
    from playwright.sync_api import sync_playwright

    cdp_url = "http://127.0.0.1:9222"

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        existing = [pg for pg in ctx.pages if "dianxiaomi.com" in (pg.url or "")]
        page = existing[0] if existing else ctx.new_page()

        print("[1] Navigating to supply/pairing page...")
        page.goto("https://www.dianxiaomi.com/web/supply/pairing", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        API_URL = "https://www.dianxiaomi.com/api/dxmAlibabaProductPair/alibabaProductPairPageList.json"

        def fetch_api(payload: dict) -> dict:
            result = page.evaluate(
                """
                async ({ url, payload }) => {
                  const body = new URLSearchParams();
                  for (const [k, v] of Object.entries(payload)) body.append(k, String(v));
                  const r = await fetch(url, {
                    method: "POST",
                    headers: {
                      "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                      "X-Requested-With": "XMLHttpRequest",
                    },
                    credentials: "include",
                    body: body.toString(),
                  });
                  return { ok: r.ok, status: r.status, text: await r.text() };
                }
                """,
                {"url": API_URL, "payload": payload},
            )
            if result.get("ok") and result.get("status") == 200:
                return json.loads(result["text"])
            return {}

        # 1. Get status=2 (已配对) with pageSize=1 to see full item structure
        print("\n[2] === status=2 (已配对), pageSize=1 (full item structure) ===")
        data = fetch_api({"pageNo": "1", "pageSize": "1", "status": "2",
                          "searchType": "1", "searchValue": "", "searchMode": "1"})
        page_data = (data.get("data") or {}).get("page") or {}
        print(f"page keys: {list(page_data.keys())}")
        for k, v in page_data.items():
            if k not in ("list", "rows", "data", "result", "records"):
                print(f"  page.{k}: {v}")
        items = (page_data.get("list") or page_data.get("rows") or page_data.get("data")
                 or page_data.get("result") or page_data.get("records") or [])
        print(f"  items count: {len(items)}")
        if items:
            item = items[0]
            print(f"  Item keys ({len(item)} total): {list(item.keys())}")
            # Print all fields
            for k, v in item.items():
                if isinstance(v, str) and len(v) > 200:
                    v = v[:200] + "..."
                print(f"    {k}: {v}")

        # 2. Try with status=0 (全部) to see more items
        print("\n[3] === status=0 (全部), pageSize=2 ===")
        data = fetch_api({"pageNo": "1", "pageSize": "2", "status": "0",
                          "searchType": "1", "searchValue": "", "searchMode": "1"})
        page_data = (data.get("data") or {}).get("page") or {}
        items = (page_data.get("list") or page_data.get("rows") or page_data.get("data")
                 or page_data.get("result") or page_data.get("records") or [])
        print(f"  items count: {len(items)}")
        for i, item in enumerate(items):
            print(f"  --- Item {i} ---")
            for k, v in item.items():
                if isinstance(v, str) and len(v) > 200:
                    v = v[:200] + "..."
                print(f"    {k}: {v}")

        # 3. Try status=0 with Chinese keyword search
        print("\n[4] === status=0, searchType=2, searchValue='女装' ===")
        data = fetch_api({"pageNo": "1", "pageSize": "2", "status": "0",
                          "searchType": "2", "searchValue": "女装", "searchMode": "1"})
        page_data = (data.get("data") or {}).get("page") or {}
        items = (page_data.get("list") or page_data.get("rows") or page_data.get("data")
                 or page_data.get("result") or page_data.get("records") or [])
        print(f"  items count: {len(items)}")
        if items:
            item = items[0]
            for k, v in item.items():
                if isinstance(v, str) and len(v) > 200:
                    v = v[:200] + "..."
                print(f"    {k}: {v}")
        else:
            print("  No items found with this search.")


if __name__ == "__main__":
    main()
