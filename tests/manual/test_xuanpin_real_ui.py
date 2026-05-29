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

def run_real_ui_test():
    safe_print("[UI Test] Starting E2E real-world test for Xuanpin duplicate task guard...")
    
    with sync_playwright() as p:
        safe_print("[UI Test] Launching Chromium browser...")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        
        # Set up route intercept to return specific mock data with existing=true flags
        # This allows us to E2E test the disabled UI render perfectly on the real server!
        def handle_languages_api(route):
            safe_print("[UI Test] Intercepted /tasks/api/languages request. Returning mock duplicate language data...")
            route.fulfill(
                status=200,
                content_type="application/json",
                body='''{
                    "languages": [
                        {"code": "DE", "name_zh": "德语", "label": "德语 (DE)", "existing": true},
                        {"code": "FR", "name_zh": "法语", "label": "法语 (FR)", "existing": false},
                        {"code": "ES", "name_zh": "西班牙语", "label": "西班牙语 (ES)", "existing": true},
                        {"code": "IT", "name_zh": "意大利语", "label": "意大利语 (IT)", "existing": false}
                    ]
                }'''
            )
            
        page.route("**/tasks/api/languages*", handle_languages_api)
        
        # 1. Access the selection center page
        target_url = f"{TEST_ENV_URL}/medias/mk-selection"
        safe_print(f"[UI Test] Navigating to {target_url}...")
        page.goto(target_url)
        
        # 2. Check if redirected to login page
        current_url = page.url
        safe_print(f"[UI Test] Current page URL: {current_url}")
        if "/auth/login" in current_url or "login" in current_url:
            safe_print("[UI Test] Login required. Filling in credentials...")
            page.fill("input[name='username']", TEST_USER)
            page.fill("input[name='password']", TEST_PASS)
            
            # Click login button
            safe_print("[UI Test] Submitting login form...")
            page.click("button[type='submit']")
            page.wait_for_load_state("networkidle")
            safe_print(f"[UI Test] Login finished. Redirected URL: {page.url}")
            
            # Ensure we are back on mk-selection
            if "/medias/mk-selection" not in page.url:
                page.goto(target_url)
                page.wait_for_load_state("networkidle")
        
        safe_print("[UI Test] Successfully loaded Selection Center Page!")
        time.sleep(3) # Wait for page script to load
        
        # 3. Force open the modal asynchronously using setTimeout in browser context
        # Using setTimeout(..., 0) prevents page.evaluate from blocking on the returned Promise!
        safe_print("[UI Test] Calling global mkiXiaoOpenModal asynchronously in browser context...")
        page.evaluate("setTimeout(() => mkiXiaoOpenModal({productId: 99, itemId: 123}), 0)")
        
        # 4. Wait for modal to pop up and languages to load
        safe_print("[UI Test] Waiting for modal #mkiXiaoModal to become visible...")
        page.wait_for_selector("#mkiXiaoModal", state="visible", timeout=10000)
        time.sleep(3) # Buffer to render elements
        
        # 5. Inspect target language pills
        safe_print("[UI Test] Inspecting target language checkboxes and styles in Chromium DOM...")
        pills = page.query_selector_all("#mkiXiaoLangs .mki-xiao-lang-pill")
        
        enabled_count = 0
        disabled_count = 0
        
        for pill in pills:
            card = pill.query_selector(".mki-xiao-lang-card")
            input_el = pill.query_selector("input")
            label_span = pill.query_selector(".mki-xiao-lang-main")
            
            code = card.get_attribute("data-mki-ai-lang-card") if card else "--"
            label = label_span.inner_text() if label_span else "--"
            is_disabled = input_el.is_disabled() if input_el else False
            card_class = card.get_attribute("class") if card else ""
            
            status_text = "DISABLED (已有任务)" if is_disabled else "ENABLED (未创建)"
            safe_print(f"  - Language: {label} [{code}] -> Status: {status_text}, Class: '{card_class}'")
            
            if is_disabled:
                disabled_count += 1
                # Verification points:
                assert "mki-xiao-lang-card--disabled" in card_class, f"Card for {code} should have disabled class"
                assert "已有任务" in label, f"Label for {code} should contain '(已有任务)' suffix"
            else:
                enabled_count += 1
                
        safe_print(f"[UI Test] Verification complete. Total enabled: {enabled_count}, Total disabled (already created): {disabled_count}")
        
        # Assertions to ensure our implementation is perfect
        assert disabled_count == 2, "Should have exactly 2 disabled duplicate languages (DE, ES)"
        assert enabled_count == 2, "Should have exactly 2 enabled languages (FR, IT)"
        
        # 6. Click the "强制创建" button for DE
        safe_print("[UI Test] Locating '强制创建' button for DE...")
        de_card = page.query_selector("[data-mki-ai-lang-card='DE']")
        assert de_card is not None, "DE card not found"
        force_btn = de_card.query_selector(".mki-xiao-force-btn")
        assert force_btn is not None, "DE force button not found"
        
        safe_print("[UI Test] Clicking the '强制创建' button for DE...")
        force_btn.click()
        time.sleep(1) # Wait for click handler to execute
        
        # 7. Re-verify DE card state after clicking force button
        safe_print("[UI Test] Re-verifying DE card state after clicking force button...")
        de_input = de_card.query_selector("input")
        de_card_class = de_card.get_attribute("class")
        de_label_span = de_card.querySelector(".mki-xiao-lang-main") if hasattr(de_card, "querySelector") else de_card.query_selector(".mki-xiao-lang-main")
        de_label = de_label_span.inner_text() if de_label_span else ""
        
        is_disabled = de_input.is_disabled()
        is_checked = de_input.is_checked()
        
        safe_print(f"  - DE Card Class after force: '{de_card_class}'")
        safe_print(f"  - DE Input is_disabled after force: {is_disabled}")
        safe_print(f"  - DE Input is_checked after force: {is_checked}")
        safe_print(f"  - DE Label after force: '{de_label}'")
        
        assert "mki-xiao-lang-card--disabled" not in de_card_class, "DE Card should no longer have disabled class"
        assert not is_disabled, "DE input checkbox should no longer be disabled"
        assert is_checked, "DE input checkbox should be checked"
        assert "强创" in de_label or "强选" in de_label, "DE label should be updated to show force create state"
        
        safe_print("[UI Test] Force button click verification PASSED successfully!")
        
        # Close the modal
        safe_print("[UI Test] Closing modal...")
        page.click("#mkiXiaoCancel")
        time.sleep(1)
        
        browser.close()
        safe_print("[UI Test] E2E Real UI Test PASSED successfully against http://172.16.254.106:8080/ !")
        return True

if __name__ == "__main__":
    run_real_ui_test()
