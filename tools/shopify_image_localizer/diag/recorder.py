from __future__ import annotations

"""
录制用户在 Shopify 后台的真实操作，用于反推自动化脚本。

工作方式：
  - 通过 CDP 连接到本机 Chrome（7777 端口）
  - 给所有 frame 注入 JS，劫持 click/change/submit/fetch/XHR 事件
  - Python 侧通过 expose_binding 接收事件，同时也监听网络请求
  - 所有事件实时落盘到 recorder.jsonl 和 recorder.log
  - 用户按 Ctrl+C 停止录制

Usage:
    python -m tools.shopify_image_localizer.diag.recorder --out tmp_probe/record
"""

import argparse
import json
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from tools.shopify_image_localizer.browser import session


INIT_SCRIPT = r"""
(() => {
  if (window.__shopifyRecorderInstalled__) return;
  window.__shopifyRecorderInstalled__ = true;

  const describe = (el) => {
    if (!el || !el.tagName) return null;
    const attr = (k) => (el.getAttribute && el.getAttribute(k)) || '';
    const cls = (el.className && typeof el.className === 'string') ? el.className : '';
    let rect = null;
    try {
      const r = el.getBoundingClientRect();
      rect = { x: r.x|0, y: r.y|0, w: r.width|0, h: r.height|0 };
    } catch(e) {}
    // 尽量求一个独特的 css selector
    const parts = [];
    let cur = el;
    for (let i = 0; i < 5 && cur && cur.tagName; i++) {
      let p = cur.tagName.toLowerCase();
      if (cur.id) { p += '#' + cur.id; parts.unshift(p); break; }
      const cc = (cur.className && typeof cur.className === 'string') ? cur.className.trim().split(/\s+/).slice(0,2).join('.') : '';
      if (cc) p += '.' + cc;
      const testId = cur.getAttribute && cur.getAttribute('data-testid');
      if (testId) p += '[data-testid=\"' + testId + '\"]';
      const aria = cur.getAttribute && cur.getAttribute('aria-label');
      if (aria) p += '[aria-label=\"' + aria.substring(0,40) + '\"]';
      parts.unshift(p);
      cur = cur.parentElement;
    }
    return {
      tag: el.tagName,
      id: el.id || '',
      class: cls.slice(0, 300),
      aria_label: attr('aria-label'),
      data_testid: attr('data-testid'),
      role: attr('role'),
      name: attr('name'),
      type: attr('type'),
      href: attr('href'),
      text: ((el.innerText || el.value || '') + '').slice(0, 200),
      selector: parts.join(' > '),
      rect: rect,
    };
  };

  const send = (type, detail) => {
    try {
      if (window.__shopifyRecord) {
        window.__shopifyRecord({ type, detail, url: location.href, ts: Date.now() });
      }
    } catch(e) {}
  };

  document.addEventListener('click', (e) => {
    send('click', describe(e.target));
  }, true);
  document.addEventListener('submit', (e) => {
    send('submit', describe(e.target));
  }, true);
  document.addEventListener('change', (e) => {
    send('change', {
      target: describe(e.target),
      value: (e.target && typeof e.target.value === 'string') ? e.target.value.slice(0, 200) : null,
      files: (e.target && e.target.files) ? Array.from(e.target.files).map(f => ({ name: f.name, size: f.size, type: f.type })) : null,
    });
  }, true);
  // keydown 只录 Enter/Escape/Tab，避免漏掉密码
  document.addEventListener('keydown', (e) => {
    if (['Enter', 'Escape', 'Tab'].includes(e.key)) {
      send('keydown', { key: e.key, target: describe(e.target) });
    }
  }, true);

  // 劫持 fetch
  const origFetch = window.fetch;
  window.fetch = async function(...args) {
    try {
      const req = args[0];
      const url = typeof req === 'string' ? req : (req && req.url) || '';
      let method = 'GET';
      if (args[1] && args[1].method) method = args[1].method;
      else if (req && typeof req !== 'string' && req.method) method = req.method;
      if (method !== 'GET' && method !== 'HEAD') {
        let body = null;
        try {
          if (args[1] && args[1].body) {
            body = (typeof args[1].body === 'string') ? args[1].body.slice(0, 800) : '[non-string]';
          }
        } catch(e) {}
        send('fetch', { url, method, body });
      }
    } catch(e) {}
    return origFetch.apply(this, args);
  };

  // 劫持 XHR
  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url) {
    this.__recMethod = method;
    this.__recUrl = url;
    return origOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function(body) {
    try {
      if (this.__recMethod && this.__recMethod.toUpperCase() !== 'GET') {
        let bodyStr = null;
        if (body) {
          if (typeof body === 'string') bodyStr = body.slice(0, 800);
          else if (body instanceof FormData) bodyStr = '[FormData]';
          else bodyStr = '[binary]';
        }
        send('xhr', { url: this.__recUrl, method: this.__recMethod, body: bodyStr });
      }
    } catch(e) {}
    return origSend.apply(this, arguments);
  };

  send('recorder_attached', { title: document.title });
})();
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-data-dir", default=r"C:\chrome-shopify-image")
    parser.add_argument("--port", type=int, default=session.DEFAULT_CDP_PORT)
    parser.add_argument("--out", default="tmp_probe/record")
    parser.add_argument("--reset-chrome", action="store_true",
                        help="强制杀掉并用新窗口参数重启 Chrome（登录态保留）")
    parser.add_argument("--open-url", action="append", default=[],
                        help="recorder 启动后自动打开这个 URL（可重复指定）")
    args = parser.parse_args()

    out_dir = Path(args.out).resolve() / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / "events.jsonl"
    summary_path = out_dir / "summary.txt"

    print(f"[rec] 录制输出目录：{out_dir}")

    if args.reset_chrome:
        print("[rec] 按要求强制重启 Chrome（副屏 9:16 竖屏）")
        session.restart_chrome_fresh(args.user_data_dir, port=args.port)
    else:
        session.ensure_chrome_running(args.user_data_dir, port=args.port)

    events_fh = events_path.open("w", encoding="utf-8")
    counter = {"n": 0}

    def _write_event(ev: dict) -> None:
        events_fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
        events_fh.flush()
        counter["n"] += 1
        src = ev.get("source", "")
        t = ev.get("type", "")
        detail = ev.get("detail", {}) or {}
        summary = ""
        if isinstance(detail, dict):
            summary = detail.get("selector") or detail.get("url") or detail.get("text") or ""
            if isinstance(summary, str):
                summary = summary[:120]
        print(f"[rec #{counter['n']}] {src} {t}: {summary}")

    stop_flag = {"stop": False}

    def _sig(signum, frame):
        stop_flag["stop"] = True
        print("\n[rec] 收到中断信号，正在保存并退出")

    signal.signal(signal.SIGINT, _sig)

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{args.port}")
        context = browser.contexts[0] if browser.contexts else browser.new_context()

        # expose_binding 会在每个 frame 里注入一个全局函数
        def _on_record(src, event) -> None:
            _write_event({
                "source": "js",
                "frame_url": (src.get("frame") and getattr(src["frame"], "url", "")) or "",
                "ts_server": time.time(),
                **(event or {}),
            })
        try:
            context.expose_binding("__shopifyRecord", _on_record)
        except Exception as exc:
            print(f"[rec] expose_binding 失败（可能已绑定过）：{exc}")

        context.add_init_script(INIT_SCRIPT)

        def _inject_now(page) -> None:
            """对已加载的 page + 它所有 frame 立即注入一次脚本。"""
            for fr in page.frames:
                try:
                    fr.evaluate(INIT_SCRIPT)
                except Exception:
                    pass

        def _on_page(page) -> None:
            _write_event({
                "source": "ctx",
                "type": "page_opened",
                "detail": {"url": page.url},
                "ts_server": time.time(),
            })
            try:
                page.add_init_script(INIT_SCRIPT)
            except Exception:
                pass
            # 对已加载 document 立即注入（add_init_script 对已存在 document 不生效）
            _inject_now(page)

            def _on_request(req) -> None:
                method = getattr(req, "method", "GET")
                if method in ("POST", "PUT", "PATCH", "DELETE"):
                    try:
                        post_data = req.post_data or ""
                    except Exception:
                        post_data = ""
                    _write_event({
                        "source": "net",
                        "type": "request",
                        "detail": {
                            "method": method,
                            "url": req.url,
                            "body": post_data[:800] if isinstance(post_data, str) else "[bin]",
                            "resource_type": getattr(req, "resource_type", ""),
                        },
                        "ts_server": time.time(),
                    })

            def _on_frame(frame) -> None:
                _write_event({
                    "source": "ctx",
                    "type": "framenavigated",
                    "detail": {"frame_url": frame.url, "page_url": page.url},
                    "ts_server": time.time(),
                })
                # navigate 后重新注入（add_init_script 已经处理新 document，
                # 但时序上 evaluate 一次双保险）
                try:
                    frame.evaluate(INIT_SCRIPT)
                except Exception:
                    pass

            page.on("request", _on_request)
            page.on("framenavigated", _on_frame)

        for p in context.pages:
            _on_page(p)
        context.on("page", _on_page)

        # 按请求打开 URL（放在 init_script + on_page 都挂好之后，确保新 tab 被注入）
        for url in args.open_url or []:
            try:
                p = context.new_page()
                p.goto(url, wait_until="commit", timeout=15000)
                print(f"[rec] opened tab: {url}")
            except Exception as exc:
                print(f"[rec] open tab failed: {url} :: {exc}")

        print("[rec] 录制开始，请在浏览器里操作。按 Ctrl+C 结束。")
        print("[rec] 建议：打开 EZ Product Image / Translate and Adapt，完整做一次 de 图片翻译")

        try:
            while not stop_flag["stop"]:
                time.sleep(0.5)
        finally:
            events_fh.close()
            try:
                browser.close()  # 只断 CDP，Chrome 留着
            except Exception:
                pass
            summary_path.write_text(
                f"Captured {counter['n']} events\n"
                f"Events file: {events_path}\n",
                encoding="utf-8",
            )
            print(f"[rec] 完成：{counter['n']} 个事件已落盘到 {events_path}")


if __name__ == "__main__":
    main()
