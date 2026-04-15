from playwright.sync_api import sync_playwright
import json
URL = "http://14.103.220.208:8888"
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    page.goto(URL + "/login", wait_until="networkidle")
    page.fill('input[name="username"]', "admin")
    page.fill('input[name="password"]', "709709@")
    page.click('button[type="submit"]')
    page.wait_for_url(lambda u: "/login" not in u, timeout=10000)
    page.goto(URL + "/prompt-library/", wait_until="networkidle")
    page.wait_for_selector("#grid tbody tr", timeout=10000)
    page.screenshot(path="_tmp_pl.png", clip={"x":0,"y":0,"width":1440,"height":900})
    info = page.evaluate("""() => {
      const rows = Array.from(document.querySelectorAll('#grid tbody tr'));
      return rows.map((r,i) => {
        const ct = r.querySelector('.content-cell .ct');
        return {
          i,
          rowH: Math.round(r.getBoundingClientRect().height),
          ctH: ct ? Math.round(ct.getBoundingClientRect().height) : null,
          ctTextLen: ct ? ct.textContent.length : null,
        };
      });
    }""")
    print(json.dumps(info, ensure_ascii=False, indent=2))
    b.close()
