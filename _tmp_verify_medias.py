"""快速截图验证 /medias/ 布局"""
from playwright.sync_api import sync_playwright
import json

URL = "http://14.103.220.208:8888"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.goto(URL + "/login", wait_until="networkidle")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "709709@")
    page.click('button[type="submit"]')
    page.wait_for_url(lambda u: "/login" not in u, timeout=10000)
    page.goto(URL + "/medias/", wait_until="networkidle")
    page.wait_for_selector("#grid table, #grid .oc-state", timeout=10000)
    # Only screenshot the visible area around table
    page.screenshot(path="_tmp_medias.png", clip={"x": 0, "y": 0, "width": 1440, "height": 700})

    info = page.evaluate("""() => {
      const tr = document.querySelector('#grid tbody tr');
      if (!tr) return null;
      const tds = Array.from(tr.querySelectorAll('td')).map((td, i) => {
        const cs = window.getComputedStyle(td);
        return {
          i,
          w: Math.round(td.getBoundingClientRect().width),
          h: Math.round(td.getBoundingClientRect().height),
          wordBreak: cs.wordBreak,
          overflow: cs.overflow,
          whiteSpace: cs.whiteSpace,
          text: (td.textContent || '').trim().substring(0, 40),
        };
      });
      return {
        trHeight: Math.round(tr.getBoundingClientRect().height),
        tds,
      };
    }""")
    print(json.dumps(info, ensure_ascii=False, indent=2))
    browser.close()
