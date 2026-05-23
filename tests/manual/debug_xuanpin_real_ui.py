import time
import sys
from playwright.sync_api import sync_playwright

TEST_ENV_URL = "http://172.16.254.106:8080"
TEST_USER = "admin"
TEST_PASS = "709709@"

def safe_print(msg):
    try:
        sys.stdout.buffer.write((msg + "\n").encode("utf-8"))
        sys.stdout.flush()
    except Exception:
        print(msg.encode("ascii", "ignore").decode("ascii"))

def debug():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        
        target_url = f"{TEST_ENV_URL}/medias/mk-selection"
        safe_print(f"[Debug] Navigating to {target_url}...")
        page.goto(target_url)
        
        if "/auth/login" in page.url or "login" in page.url:
            safe_print("[Debug] Logging in...")
            page.fill("input[name='username']", TEST_USER)
            page.fill("input[name='password']", TEST_PASS)
            page.click("button[type='submit']")
            page.wait_for_load_state("networkidle")
            
            if "/medias/mk-selection" not in page.url:
                page.goto(target_url)
                page.wait_for_load_state("networkidle")
                
        safe_print(f"[Debug] Page loaded. URL: {page.url}")
        time.sleep(5) # Wait for async cards to load
        
        # Get count of items
        cards = page.query_selector_all(".mki-card")
        safe_print(f"[Debug] Found {len(cards)} material cards (.mki-card)")
        
        buttons = page.query_selector_all("button")
        safe_print(f"[Debug] Found {len(buttons)} total buttons on the page")
        
        for i, btn in enumerate(buttons):
            cls = btn.get_attribute("class") or ""
            title = btn.get_attribute("title") or ""
            text = btn.inner_text() or ""
            is_dis = btn.is_disabled()
            
            # Print only relevant buttons
            if "mki-btn" in cls or "xiao" in cls or "join" in cls or "import" in cls or text or title:
                safe_print(f"  Button {i}: text='{text}', title='{title}', class='{cls}', disabled={is_dis}")
                
        browser.close()

if __name__ == "__main__":
    debug()
