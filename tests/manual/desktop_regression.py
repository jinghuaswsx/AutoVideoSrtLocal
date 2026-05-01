"""
Desktop regression test against the test deployment (:8080).

确认 PC 视图（≥ 1024px）在引入 mobile 改动后没有任何视觉回归——
hamburger / backdrop / mobile-brand 应该全部 display: none，
sidebar 220px 固定可见，main-wrap margin-left: 220px。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright


PAGES = [
    ("login", "/login"),
    ("medias", "/medias/"),
    ("pushes", "/pushes"),
    ("order_analytics", "/order-analytics"),
    ("tasks", "/tasks/"),
    ("multi_translate", "/multi-translate"),
    ("ai_billing", "/admin/ai-usage"),
]

ARTIFACTS = Path(__file__).parent / "desktop_regression_artifacts"
ARTIFACTS.mkdir(parents=True, exist_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://172.30.254.14:8080")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--pwd", default="709709@")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--timeout", type=int, default=30000)
    args = parser.parse_args()

    report = {
        "started_at": datetime.utcnow().isoformat() + "Z",
        "base": args.base,
        "viewport": f"{args.width}x{args.height}",
        "results": [],
        "console_errors": [],
        "regression_checks": [],
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": args.width, "height": args.height},
            device_scale_factor=1,
        )
        page = context.new_page()
        page.set_default_timeout(args.timeout)

        def on_console(msg):
            if msg.type == "error":
                report["console_errors"].append({
                    "page": page.url,
                    "type": msg.type,
                    "text": msg.text,
                })
        page.on("console", on_console)

        # ---- Login ----
        print(f"[i] login at {args.base}/login (PC {args.width}x{args.height})")
        page.goto(args.base + "/login", wait_until="domcontentloaded")
        page.screenshot(path=str(ARTIFACTS / "00_login.png"), full_page=False)
        page.fill('input[name="username"]', args.user)
        page.fill('input[name="password"]', args.pwd)
        page.click('button[type="submit"]')
        page.wait_for_load_state("domcontentloaded")
        if "login" in page.url:
            print(f"[!] login failed: {page.url}", file=sys.stderr)
            return 1
        report["results"].append({"name": "login", "status": "ok"})

        # ---- Walk pages with regression checks ----
        for idx, (name, path) in enumerate(PAGES, start=1):
            url = args.base + path
            print(f"[i] {idx:02d} → {name} ({url})")
            try:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)

                # Regression check: mobile DOM must be hidden on PC
                if path != "/login":
                    sidebar_toggle_visible = page.evaluate("""
                        () => {
                            const el = document.querySelector('#sidebarToggle');
                            if (!el) return null;
                            const cs = getComputedStyle(el);
                            return cs.display !== 'none';
                        }
                    """)
                    mobile_brand_visible = page.evaluate("""
                        () => {
                            const el = document.querySelector('.topbar-mobile-brand');
                            if (!el) return null;
                            const cs = getComputedStyle(el);
                            return cs.display !== 'none';
                        }
                    """)
                    backdrop_display = page.evaluate("""
                        () => {
                            const el = document.querySelector('#sidebarBackdrop');
                            if (!el) return null;
                            const cs = getComputedStyle(el);
                            return cs.display;
                        }
                    """)
                    sidebar_left = page.evaluate("""
                        () => {
                            const el = document.querySelector('#appSidebar');
                            if (!el) return null;
                            const r = el.getBoundingClientRect();
                            return { left: r.left, width: r.width };
                        }
                    """)
                    main_wrap_margin = page.evaluate("""
                        () => {
                            const el = document.querySelector('.main-wrap');
                            if (!el) return null;
                            const cs = getComputedStyle(el);
                            return cs.marginLeft;
                        }
                    """)
                    topbar_title_visible = page.evaluate("""
                        () => {
                            const el = document.querySelector('.topbar > .topbar-title');
                            if (!el) return null;
                            const cs = getComputedStyle(el);
                            return cs.display !== 'none';
                        }
                    """)

                    expected = {
                        "sidebarToggle hidden": sidebar_toggle_visible is False,
                        "topbar-mobile-brand hidden": mobile_brand_visible is False,
                        "sidebarBackdrop display=none": backdrop_display == "none",
                        "sidebar at left=0": sidebar_left and abs(sidebar_left["left"]) < 1,
                        "sidebar width=220": sidebar_left and abs(sidebar_left["width"] - 220) < 2,
                        "main-wrap marginLeft=220px": main_wrap_margin == "220px",
                        "topbar-title visible": topbar_title_visible is True,
                    }
                    fails = [k for k, v in expected.items() if not v]
                    report["regression_checks"].append({
                        "page": name,
                        "url": url,
                        "checks": expected,
                        "fails": fails,
                        "raw": {
                            "sidebarToggle_visible": sidebar_toggle_visible,
                            "mobile_brand_visible": mobile_brand_visible,
                            "backdrop_display": backdrop_display,
                            "sidebar_rect": sidebar_left,
                            "main_wrap_margin": main_wrap_margin,
                            "topbar_title_visible": topbar_title_visible,
                        },
                    })
                    if fails:
                        print(f"   [!] regression fails: {fails}", file=sys.stderr)
                    else:
                        print(f"   [OK] PC layout intact")

                page.screenshot(path=str(ARTIFACTS / f"{idx:02d}_{name}.png"), full_page=False)
                report["results"].append({"name": name, "status": "ok", "url": url})
            except Exception as e:
                print(f"   [!] error: {e}", file=sys.stderr)
                report["results"].append({"name": name, "status": "fail", "url": url, "error": str(e)})

        browser.close()

    report["finished_at"] = datetime.utcnow().isoformat() + "Z"
    with (ARTIFACTS / "report.json").open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    fails = [r for r in report["results"] if r.get("status") != "ok"]
    regr_fails = [c for c in report["regression_checks"] if c["fails"]]
    print()
    print(f"[i] done. {len(report['results'])} pages, {len(fails)} pageload fails, {len(regr_fails)} regression fails, {len(report['console_errors'])} console errors")
    if regr_fails:
        for c in regr_fails:
            print(f"    - {c['page']}: {c['fails']}")
    return 0 if not fails and not regr_fails else 1


if __name__ == "__main__":
    sys.exit(main())
