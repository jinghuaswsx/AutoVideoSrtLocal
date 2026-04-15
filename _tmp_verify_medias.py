from playwright.sync_api import sync_playwright
import json
URL = "http://14.103.220.208:8888"
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    for vw in (1440, 1200, 1000):
        ctx = b.new_context(viewport={"width": vw, "height": 800})
        page = ctx.new_page()
        page.goto(URL + "/login", wait_until="networkidle")
        page.fill('input[name="username"]', "admin")
        page.fill('input[name="password"]', "709709@")
        page.click('button[type="submit"]')
        page.wait_for_url(lambda u: "/login" not in u, timeout=10000)
        page.goto(URL + "/medias/", wait_until="networkidle")
        page.wait_for_selector("#grid tbody tr", timeout=10000)
        page.screenshot(path=f"_tmp_medias_{vw}.png", clip={"x": 0, "y": 0, "width": vw, "height": 500})
        info = page.evaluate("""() => {
          const tr = document.querySelector('#grid tbody tr');
          const tds = Array.from(tr.querySelectorAll('td')).map((td,i)=>({i, w:Math.round(td.getBoundingClientRect().width)}));
          return {trH: Math.round(tr.getBoundingClientRect().height), tds};
        }""")
        print(f"=== VW {vw} ===")
        print(json.dumps(info, ensure_ascii=False))
        ctx.close()
    b.close()
