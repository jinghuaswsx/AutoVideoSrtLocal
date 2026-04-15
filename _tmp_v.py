from playwright.sync_api import sync_playwright
import json
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(viewport={"width":1440,"height":900})
    page = ctx.new_page()
    page.goto("http://14.103.220.208:8888/login", wait_until="networkidle")
    page.fill('input[name="username"]',"admin")
    page.fill('input[name="password"]',"709709@")
    page.click('button[type="submit"]')
    page.wait_for_url(lambda u:"/login" not in u, timeout=10000)
    page.goto("http://14.103.220.208:8888/medias/", wait_until="networkidle")
    page.wait_for_selector("#grid tbody tr")
    page.screenshot(path="_tmp_m.png", clip={"x":0,"y":0,"width":1440,"height":500})
    print(json.dumps(page.evaluate("""()=>{
      const tr=document.querySelector('#grid tbody tr');
      return {rowH:Math.round(tr.getBoundingClientRect().height),
        tds:[...tr.querySelectorAll('td')].map((t,i)=>({i,w:Math.round(t.getBoundingClientRect().width)}))};
    }"""), ensure_ascii=False))
    b.close()
