"""
Mobile responsive smoke test against the test deployment (:8080).

模拟 iPhone 15 Pro Max（430×932 CSS px, DPR 3）走遍所有侧栏页面，
对每页：
- 截图 (mobile_smoke_artifacts/<idx>_<name>.png)
- 截图打开抽屉一次后 (mobile_smoke_artifacts/<idx>_<name>_drawer.png)
- 自动检查指标：整页是否横滚、顶栏高度、hamburger 可见、抽屉能开、
  抽样输入框字号、抽样按钮高度
- 抓 console.error

输出 JSON 报告到 mobile_smoke_artifacts/report.json，并在终端
打印每页的检查结果表。

用法：
  python tests/manual/mobile_smoke.py [--base http://172.30.254.14:8080]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright


PAGES = [
    ("login", "/login", False),                     # 第三个字段：是否需要登录
    ("medias", "/medias/", True),
    ("mk_selection", "/medias/mk-selection", True),
    ("pushes", "/pushes", True),
    ("order_analytics", "/order-analytics", True),
    ("multi_translate", "/multi-translate", True),
    ("title_translate", "/title-translate", True),
    ("image_translate", "/image-translate", True),
    ("subtitle_removal", "/subtitle-removal", True),
    ("tasks", "/tasks/", True),
    ("tools", "/tools/", True),
    ("raw_video_pool", "/raw-video-pool/", True),
    ("bulk_translate_admin", "/admin/bulk-translate/tasks", True),
    ("settings", "/settings", True),
    ("admin_ai_billing", "/admin/ai-usage", True),
    ("user_settings", "/user-settings", True),
    ("admin_users", "/admin/users", True),
    ("admin_settings", "/admin/settings", True),
    ("projects", "/projects", True),
    ("av_sync", "/video-translate-av-sync", True),
    ("productivity_stats", "/productivity-stats/", True),
    ("scheduled_tasks", "/scheduled-tasks", True),
    ("voice_library", "/voice-library", True),
    ("prompt_library", "/prompt-library", True),
    ("copywriting", "/copywriting", True),
    ("text_translate", "/text-translate", True),
    ("video_creation", "/video-creation", True),
    ("video_review", "/video-review", True),
    ("link_check", "/link-check", True),
    ("omni_translate", "/omni-translate", True),
]

ARTIFACTS = Path(__file__).parent / "mobile_smoke_artifacts"
ARTIFACTS.mkdir(parents=True, exist_ok=True)


def collect_metrics(page) -> dict:
    """跑一段 evaluate 收集这一页的可用性指标。"""
    return page.evaluate(r"""
        () => {
            const data = {};
            data.viewport = { w: window.innerWidth, h: window.innerHeight };
            data.documentScrollWidth = document.documentElement.scrollWidth;
            data.bodyScrollWidth = document.body.scrollWidth;
            data.horizontalOverflow = data.bodyScrollWidth > window.innerWidth + 1;

            const topbar = document.querySelector('.topbar');
            data.topbarHeight = topbar ? topbar.getBoundingClientRect().height : null;

            const hamburger = document.querySelector('#sidebarToggle');
            if (hamburger) {
                const cs = getComputedStyle(hamburger);
                const r = hamburger.getBoundingClientRect();
                data.hamburger = {
                    display: cs.display,
                    width: r.width,
                    height: r.height,
                    visible: cs.display !== 'none' && r.width >= 32 && r.height >= 32,
                };
            } else {
                data.hamburger = null;
            }

            const sidebar = document.querySelector('#appSidebar');
            if (sidebar) {
                const r = sidebar.getBoundingClientRect();
                const cs = getComputedStyle(sidebar);
                data.sidebar = {
                    transform: cs.transform,
                    left: r.left,
                    width: r.width,
                    inViewport: r.right > 0 && r.left < window.innerWidth,
                };
            }

            // 抽样输入框字号
            const inputs = Array.from(document.querySelectorAll(
                'input[type=text], input[type=password], input[type=search], input[type=number], input[type=date], input:not([type]), select, textarea'
            )).slice(0, 10);
            data.inputFontSizes = inputs.map(el => parseFloat(getComputedStyle(el).fontSize) || 0);
            data.minInputFontSize = data.inputFontSizes.length ? Math.min(...data.inputFontSizes) : null;

            // 抽样"主操作按钮"高度（不含行内紧凑按钮如 .btn-mini / .product-copy-btn 等）
            const mainButtons = Array.from(document.querySelectorAll(
                '.btn-primary, .oc-btn.primary, .tc-btn--primary, button[type="submit"], .filter-actions button'
            )).filter(el => {
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            });
            data.mainButtonHeights = mainButtons.map(el => el.getBoundingClientRect().height);
            data.minMainButtonHeight = data.mainButtonHeights.length ? Math.min(...data.mainButtonHeights) : null;

            // 主内容区高度
            const main = document.querySelector('.main-content');
            data.mainContentHeight = main ? main.getBoundingClientRect().height : null;
            data.mainContentChildren = main ? main.children.length : 0;

            // 当前页面 URL
            data.url = location.href;

            return data;
        }
    """)


def evaluate(metrics: dict) -> dict:
    """给一页打分：horizontalOverflow=fatal, hamburger missing=fatal, 其他 warn"""
    issues_fatal = []
    issues_warn = []

    if metrics.get("horizontalOverflow"):
        issues_fatal.append(
            f"整页横向溢出 (body.scrollWidth={metrics['bodyScrollWidth']} > viewport={metrics['viewport']['w']})"
        )

    h = metrics.get("hamburger")
    is_login = "/login" in (metrics.get("url") or "")
    if not is_login:
        if not h or not h.get("visible"):
            issues_fatal.append(f"hamburger 按钮不可见 ({h})")
        else:
            if h["height"] < 36 or h["width"] < 36:
                issues_warn.append(f"hamburger 触控热区偏小 ({h['width']}×{h['height']})")

    tb = metrics.get("topbarHeight")
    if not is_login and tb is not None:
        # 52px ± 一些（safe-area 可能加几像素）
        if tb < 48 or tb > 96:
            issues_warn.append(f"顶栏高度异常 ({tb}px，期望 ~52)")

    mfs = metrics.get("minInputFontSize")
    if mfs is not None and mfs > 0 and mfs < 16:
        issues_warn.append(f"输入框字号 {mfs}px 小于 16，iOS Safari 可能自动缩放")

    mbh = metrics.get("minMainButtonHeight")
    if mbh is not None and mbh > 0 and mbh < 36:
        issues_warn.append(f"主操作按钮最小高度 {mbh:.0f}px (期望 ≥36)")

    mc = metrics.get("mainContentHeight")
    if mc is not None and mc < 100:
        issues_warn.append(f"主内容区高度 {mc:.0f}px 偏低，可能是空白页")

    if issues_fatal:
        verdict = "FATAL"
    elif issues_warn:
        verdict = "WARN"
    else:
        verdict = "OK"

    return {
        "verdict": verdict,
        "fatal": issues_fatal,
        "warn": issues_warn,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://172.30.254.14:8080")
    parser.add_argument("--user", default=os.environ.get("AUTOVIDEOSRT_SMOKE_USER", "admin"))
    parser.add_argument("--pwd", default=os.environ.get("AUTOVIDEOSRT_SMOKE_PASSWORD", ""))
    parser.add_argument("--device", default="iPhone 15 Pro Max")
    parser.add_argument("--timeout", type=int, default=30000)
    parser.add_argument("--only", default="", help="逗号分隔，只跑指定 name 的页面")
    args = parser.parse_args()
    if not args.pwd:
        print("[!] set AUTOVIDEOSRT_SMOKE_PASSWORD or pass --pwd", file=sys.stderr)
        return 2

    only = set(s.strip() for s in args.only.split(",") if s.strip())
    pages = [p for p in PAGES if not only or p[0] in only]

    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "base": args.base,
        "device": args.device,
        "results": [],
        "console_errors": [],
    }

    with sync_playwright() as p:
        device = p.devices.get(args.device)
        if not device:
            print(f"[!] device descriptor not found: {args.device}", file=sys.stderr)
            return 2

        browser = p.chromium.launch(headless=True)
        context = browser.new_context(**device)
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

        # ---- 1. Login ----
        print(f"[i] login at {args.base}/login")
        page.goto(args.base + "/login", wait_until="domcontentloaded")
        page.fill('input[name="username"]', args.user)
        page.fill('input[name="password"]', args.pwd)
        page.click('button[type="submit"]')
        page.wait_for_load_state("domcontentloaded")
        if "login" in page.url:
            print(f"[!] login failed; current url: {page.url}", file=sys.stderr)
            return 1

        # ---- 2. Walk pages ----
        for idx, (name, path, _need_login) in enumerate(pages, start=1):
            url = args.base + path
            print(f"[{idx:02d}] → {name} ({path}) ...", end=" ", flush=True)
            entry = {"name": name, "url": url, "status": "ok"}
            try:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(1500)

                # 截图主页面
                page.screenshot(path=str(ARTIFACTS / f"{idx:02d}_{name}.png"), full_page=False)

                # 收集指标
                m = collect_metrics(page)
                entry["metrics"] = m
                entry["evaluation"] = evaluate(m)

                # 尝试打开抽屉
                if path != "/login":
                    drawer_state = "未尝试"
                    try:
                        page.click("#sidebarToggle", timeout=1500)
                        page.wait_for_timeout(400)
                        # 验证 sidebar 是否真的滑入了
                        sb = page.evaluate(
                            "() => { const s = document.querySelector('#appSidebar'); return s ? getComputedStyle(s).transform : null; }"
                        )
                        drawer_open = bool(sb and "matrix" in sb and "-280" not in sb and "translateX(-100%)" not in sb)
                        # 上面 transform 比较糙，更可靠：检查 body class
                        body_open = page.evaluate(
                            "() => document.body.classList.contains('sidebar-open')"
                        )
                        page.screenshot(path=str(ARTIFACTS / f"{idx:02d}_{name}_drawer.png"), full_page=False)
                        if body_open:
                            drawer_state = "OK"
                        else:
                            drawer_state = "FAIL: body 没加 .sidebar-open"
                            entry["evaluation"]["warn"].append("hamburger 点击未触发 sidebar-open")
                            if entry["evaluation"]["verdict"] == "OK":
                                entry["evaluation"]["verdict"] = "WARN"
                        # Esc 关
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(300)
                    except Exception as e:
                        drawer_state = f"FAIL: {e.__class__.__name__}"
                        entry["evaluation"]["warn"].append(f"无法打开抽屉: {e}")
                        if entry["evaluation"]["verdict"] == "OK":
                            entry["evaluation"]["verdict"] = "WARN"
                    entry["drawer"] = drawer_state

                e = entry["evaluation"]
                tag = e["verdict"]
                msg = ""
                if e["fatal"]:
                    msg = " | " + " ; ".join(e["fatal"])
                if e["warn"]:
                    msg += " | warn: " + " ; ".join(e["warn"])
                print(f"[{tag}]{msg}")
            except Exception as ex:
                print(f"[ERR] {ex.__class__.__name__}: {ex}")
                entry["status"] = "fail"
                entry["error"] = str(ex)
                entry["traceback"] = traceback.format_exc()
                try:
                    page.screenshot(path=str(ARTIFACTS / f"{idx:02d}_{name}_ERROR.png"), full_page=False)
                except Exception:
                    pass

            report["results"].append(entry)

        browser.close()

    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    with (ARTIFACTS / "report.json").open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # 终端总结
    print()
    print("=" * 80)
    print(f"汇总：{len(report['results'])} 页 / console errors {len(report['console_errors'])}")
    print("=" * 80)
    summary = {"OK": 0, "WARN": 0, "FATAL": 0, "ERROR": 0}
    for r in report["results"]:
        if r.get("status") != "ok":
            summary["ERROR"] += 1
            continue
        v = r.get("evaluation", {}).get("verdict", "ERROR")
        summary[v] = summary.get(v, 0) + 1
    print(f"  OK: {summary['OK']}  WARN: {summary['WARN']}  FATAL: {summary['FATAL']}  ERROR: {summary['ERROR']}")
    print()
    for r in report["results"]:
        v = r.get("evaluation", {}).get("verdict", "ERROR")
        if v in ("FATAL", "WARN", "ERROR"):
            print(f"  [{v}] {r['name']}")
            for x in r.get("evaluation", {}).get("fatal", []):
                print(f"        FATAL: {x}")
            for x in r.get("evaluation", {}).get("warn", []):
                print(f"        warn:  {x}")
            if r.get("error"):
                print(f"        error: {r['error']}")

    return 0 if summary["FATAL"] == 0 and summary["ERROR"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
