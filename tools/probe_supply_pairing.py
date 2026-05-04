"""Probe: capture the EXACT API request triggered by clicking the "已配对" tab.

The supply/pairing page defaults to "未配对" (unpaired). Clicking "已配对"
triggers a page-driven API call. This script intercepts it so we can replicate
the exact endpoint + payload in our module.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlencode

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

        # ---------- Step 1: Capture ALL XHR requests ----------
        captured: list[dict] = []

        def on_request(request):
            if request.resource_type == "xhr" and request.method == "POST":
                captured.append({
                    "url": request.url,
                    "method": request.method,
                    "post_data": request.post_data,
                })

        page.on("request", on_request)

        print("[1] Navigating to supply/pairing page...")
        page.goto("https://www.dianxiaomi.com/web/supply/pairing",
                  wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(4000)

        print(f"  Initial XHR POST requests: {len(captured)}")
        for req in captured:
            pd = req.get("post_data") or ""
            print(f"  {req['method']} {req['url']}")
            if pd:
                print(f"    post_data: {pd[:400]}")

        # ---------- Step 2: Click "已配对" tab and capture ----------
        captured.clear()

        print("\n[2] Clicking '已配对' tab...")
        clicked = page.evaluate("""
            () => {
                const tabs = document.querySelectorAll('.ant-tabs-tab');
                for (const tab of tabs) {
                    if ((tab.textContent || '').includes('已配对')) {
                        tab.click();
                        return {method: 'ant-tabs-tab', text: tab.textContent.trim()};
                    }
                }
                const divs = document.querySelectorAll('div');
                for (const div of divs) {
                    const t = (div.textContent || '').trim();
                    if (t === '已配对' && div.children.length <= 2 && div.offsetParent !== null) {
                        div.click();
                        return {method: 'div-exact', class: div.className};
                    }
                }
                return {method: 'not-found'};
            }
        """)
        print(f"  Result: {clicked}")
        page.wait_for_timeout(4000)

        print(f"\n[3] Requests after clicking tab: {len(captured)}")
        for req in captured:
            pd = req.get("post_data") or ""
            print(f"  {req['method']} {req['url']}")
            if pd:
                parsed = parse_qs(pd)
                flat = {k: v[0] for k, v in parsed.items()}
                print(f"    params: {json.dumps(flat, ensure_ascii=False)}")

        # ---------- Step 3: Direct API test with various params ----------
        print("\n[4] Direct API tests:")
        api_url = "https://www.dianxiaomi.com/api/dxmAlibabaProductPair/alibabaProductPairPageList.json"

        test_params = [
            {"pageNo": "1", "pageSize": "100", "status": "2", "searchType": "1", "searchValue": "", "searchMode": "1"},
            {"pageNo": "1", "pageSize": "100", "status": "1", "searchType": "1", "searchValue": "", "searchMode": "1"},
            {"pageNo": "1", "pageSize": "100", "status": "0", "searchType": "1", "searchValue": "", "searchMode": "1"},
        ]

        for params in test_params:
            body_str = urlencode(params)
            try:
                result = page.evaluate(
                    f"""
                    async () => {{
                      const r = await fetch("{api_url}", {{
                        method: "POST",
                        headers: {{
                          "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                          "X-Requested-With": "XMLHttpRequest",
                        }},
                        credentials: "include",
                        body: "{body_str}",
                      }});
                      const text = await r.text();
                      return {{ ok: r.ok, status: r.status, text }};
                    }}
                    """
                )
                status = result.get("status", 0)
                text = result.get("text", "")
                if status == 200:
                    data = json.loads(text)
                    page_data = (data.get("data") or {}).get("page") or {}
                    total = page_data.get("totalSize", "?")
                    items = page_data.get("list") or []
                    print(f"  status={params['status']}: totalSize={total} items_page={len(items)}")
                    if items:
                        print(f"    first: sku={items[0].get('sku')} name={items[0].get('name')}")
                    # Also show the first few items' SKU + name for status=2 and status=1
                    if params['status'] in ('1', '2'):
                        for item in items[:3]:
                            print(f"      sku={item.get('sku')} name={item.get('name')}")
            except Exception as exc:
                print(f"  status={params['status']}: ERROR {exc}")


if __name__ == "__main__":
    main()
