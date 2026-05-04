"""自测 AI 视频分析 Modal 的胶囊 tab + 智能 trigger + 重新评估按钮。

用 Playwright 直连线上 172.30.254.14，禁缓存重新加载，截图保存到 /tmp。
跑完用脚本 + 截图肉眼验证，不要再让用户回看。
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://172.30.254.14"
USER = "admin"
PASSWORD = "709709@"
OUT_DIR = Path("/tmp/vr_autotest")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # bypass cache to ensure new CSS/JS
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        ctx.set_extra_http_headers({"Cache-Control": "no-cache, no-store",
                                    "Pragma": "no-cache"})
        page = ctx.new_page()

        # 1) login
        page.goto(f"{BASE}/login")
        page.wait_for_load_state("networkidle")
        page.fill("input[name='username']", USER)
        page.fill("input[name='password']", PASSWORD)
        page.click("button[type='submit']")
        page.wait_for_load_state("networkidle")
        print("[step] logged in, url =", page.url)

        # 2) jump to medias
        page.goto(f"{BASE}/medias/", wait_until="networkidle")
        page.screenshot(path=str(OUT_DIR / "01_medias_list.png"), full_page=False)
        print("[step] medias list rendered")

        # 3) open the spray-can product (用户报问题就是这个产品)
        page.goto(f"{BASE}/medias/spray-can-trigger-handle-rjc",
                  wait_until="networkidle")
        page.wait_for_timeout(1200)  # 等 medias.js 初始化卡片
        page.screenshot(path=str(OUT_DIR / "02_product_edit.png"), full_page=False)
        print("[step] product edit page open, url =", page.url)

        # 4) find AI 视频分析 button and click
        vr_btn = page.locator('[data-act="vr-run"]').first
        if not vr_btn.count():
            print("[error] no vr-run button found")
            return 3
        vr_btn.scroll_into_view_if_needed()
        page.screenshot(path=str(OUT_DIR / "03_before_click.png"))
        vr_btn.click()
        # wait for modal
        page.wait_for_selector("#vrModal:not(.hidden)", timeout=10000)
        page.wait_for_timeout(800)  # 等 _renderModal 跑完
        page.screenshot(path=str(OUT_DIR / "04_modal_request_tab.png"), full_page=False)
        print("[step] Modal opened, screenshot 04 done")

        # 5) inspect tab styles via getComputedStyle
        active_tab = page.locator("#vrModal .vr-tab.active").first
        active_bg = active_tab.evaluate(
            "el => getComputedStyle(el).backgroundColor"
        )
        active_color = active_tab.evaluate(
            "el => getComputedStyle(el).color"
        )
        active_radius = active_tab.evaluate(
            "el => getComputedStyle(el).borderRadius"
        )
        print(f"[check] .vr-tab.active bg={active_bg} color={active_color} "
              f"radius={active_radius}")

        inactive_tab = page.locator("#vrModal .vr-tab:not(.active)").first
        inactive_bg = inactive_tab.evaluate(
            "el => getComputedStyle(el).backgroundColor"
        )
        print(f"[check] .vr-tab inactive bg={inactive_bg}")

        # 6) click 结果 tab
        result_tab = page.locator('#vrModal .vr-tab[data-tab="result"]').first
        result_tab.click()
        page.wait_for_timeout(300)
        page.screenshot(path=str(OUT_DIR / "05_modal_result_tab.png"), full_page=False)
        print("[step] switched to result tab")

        # 7) check rerun button visibility
        rerun = page.locator('#vrModal [data-act="rerun"]').first
        rerun_hidden = rerun.evaluate("el => el.hidden")
        rerun_visible_css = rerun.is_visible()
        print(f"[check] 重新评估 hidden_attr={rerun_hidden} visible={rerun_visible_css}")

        # 8) close modal
        page.locator('#vrModal .vr-modal-close').click()
        page.wait_for_timeout(300)

        browser.close()

    print("\n=== screenshots ===")
    for f in sorted(OUT_DIR.glob("*.png")):
        print(f"  {f}")

    # rough pass criteria
    fails = []
    if "rgb(255, 255, 255)" not in active_color and "white" not in active_color.lower():
        # accent_fg 是 oklch(99% 0 0) 折算 ~ rgb(252,252,252) 也算白
        if "252" not in active_color and "255" not in active_color:
            fails.append(f"active tab text not white: {active_color}")
    if active_radius == "0px" or "px" in active_radius and int(float(active_radius.replace("px","").split()[0])) < 50:
        # radius-full = 9999px
        fails.append(f"active tab not pill: radius={active_radius}")
    if fails:
        print("\n!! REGRESSIONS:")
        for f in fails:
            print(" -", f)
        return 1
    print("\n=== visual checks PASSED ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
