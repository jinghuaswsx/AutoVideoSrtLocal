"""Spike 3: Playwright launch + stealth init script 隐藏自动化指纹。

只忽略 --enable-automation 等 automation flag，保留 Playwright 的 CDP pipe。
在 page 加载前注入脚本改写 navigator.webdriver / chrome.runtime / permissions 等，
让 Shopify App Bridge 检测不到 automation。
"""
from __future__ import annotations
import time
from playwright.sync_api import sync_playwright


STEALTH_INIT = r"""
// Hide webdriver
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// Ensure window.chrome exists with basic runtime (real Chrome has this; --enable-automation sometimes clears it)
if (!window.chrome) window.chrome = {};
if (!window.chrome.runtime) window.chrome.runtime = { id: undefined };

// Fix plugins length (real Chrome has >0; headless has 0)
try {
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5],
    });
} catch(e) {}

// Fix languages
try {
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
    });
} catch(e) {}

// Fix permissions.query for notifications
if (navigator.permissions && navigator.permissions.query) {
    const origQuery = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (p) => (
        p && p.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : origQuery(p)
    );
}
"""


def main() -> None:
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=r"C:\chrome-shopify-image",
            channel="chrome",
            headless=False,
            no_viewport=True,
            # 只忽略 automation 相关默认 flag，保留 Playwright 的 CDP pipe 等必需项
            ignore_default_args=[
                "--enable-automation",
                "--disable-blink-features=AutomationControlled",
            ],
            args=[
                "--no-first-run",
                "--no-default-browser-check",
                "--window-position=2560,0",
                "--window-size=1440,2512",
                "--proxy-server=http://127.0.0.1:7890",
                "--proxy-bypass-list=127.0.0.1;localhost;172.30.254.14;<local>",
            ],
        )
        # 所有 new document 都会预执行这段
        ctx.add_init_script(STEALTH_INIT)

        page = ctx.new_page()
        page.goto(
            "https://admin.shopify.com/store/0ixug9-pv/apps/ez-product-image-translate/product/8552296546477",
            wait_until="commit",
            timeout=30000,
        )
        print("[spike3] navigated (stealth init applied)")
        success = False
        for t in range(10):
            time.sleep(3)
            info = page.evaluate(
                """
                (() => {
                    const f = document.querySelector('iframe');
                    if (!f) return {status:'no_iframe_el'};
                    if (!f.contentDocument) return {status:'no_contentDoc'};
                    return {
                        doc_html_len: f.contentDocument.documentElement.outerHTML.length,
                        imgs_actual: f.contentDocument.querySelectorAll('img.actual-image').length,
                        imgs_all: f.contentDocument.querySelectorAll('img').length,
                        body_head: (f.contentDocument.body && f.contentDocument.body.innerText || '').slice(0, 160),
                    };
                })()
                """
            )
            print(f"[spike3 {(t+1)*3}s]", info)
            if info and info.get("doc_html_len", 0) > 500 and info.get("imgs_actual", 0) > 0:
                success = True
                print("*** iframe LOADED with real content ***")
                break
        try:
            page.screenshot(path="tmp_probe/ez_pipe_stealth.png", full_page=True)
        except Exception:
            pass
        print(f"[spike3] done success={success}")
        time.sleep(2)
        ctx.close()


if __name__ == "__main__":
    main()
