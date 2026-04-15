"""快速截图验证 /medias/ 布局"""
import sys
from playwright.sync_api import sync_playwright

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
    page.screenshot(path="_tmp_medias.png", full_page=True)

    # collect layout info
    info = page.evaluate("""() => {
      const grid = document.getElementById('grid');
      const table = grid && grid.querySelector('table');
      const g = grid && window.getComputedStyle(grid);
      const t = table && table.getBoundingClientRect();
      const ths = table ? Array.from(table.querySelectorAll('thead th')).map(h => ({text: h.textContent.trim(), w: h.getBoundingClientRect().width})) : [];
      const firstRow = table && table.querySelector('tbody tr');
      const rowHeight = firstRow ? firstRow.getBoundingClientRect().height : null;
      return {
        grid_display: g && g.display,
        grid_width: grid && grid.getBoundingClientRect().width,
        table_width: t && t.width,
        table_height: t && t.height,
        viewport: {w: window.innerWidth, h: window.innerHeight},
        thead: ths,
        row_height: rowHeight,
      };
    }""")
    print("LAYOUT INFO:")
    import json
    print(json.dumps(info, ensure_ascii=False, indent=2))
    browser.close()
print("screenshot saved to _tmp_medias.png")
