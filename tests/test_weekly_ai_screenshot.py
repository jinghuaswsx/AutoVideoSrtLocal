import os
import time
from playwright.sync_api import sync_playwright

def test_capture_weekly_ai_screenshot():
    print("Starting Playwright screenshot capture via pytest...")
    
    # Target screenshot path in the Gemini artifact directory
    output_dir = r"C:\Users\admin\.gemini\antigravity\brain\3675f998-26c7-46a4-bb1c-b77e48649d57"
    screenshot_path = os.path.join(output_dir, "ui_changes_validation.png")
    
    with sync_playwright() as p:
        # Launch headless Chromium
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()
        
        # 1. Log in
        print("Navigating to login page...")
        page.goto("http://172.16.254.106/login")
        page.fill("input[name='username']", "admin")
        page.fill("input[name='password']", "709709@")
        
        print("Submitting login credentials...")
        page.click("button[type='submit']")
        page.wait_for_load_state("networkidle", timeout=10000)
        
        # 2. Navigate to Weekly AI report page
        target_url = "http://172.16.254.106/order-analytics/weekly-ai-analysis-view"
        print(f"Navigating to: {target_url}")
        page.goto(target_url)
        
        # Wait for data and async charts to fully load
        print("Waiting for page resources to load...")
        page.wait_for_load_state("networkidle", timeout=15000)
        time.sleep(5)  # Wait an extra 5 seconds for chart/JS rendering
        
        # 3. Inject visual highlights for changes
        print("Injecting styles to highlight changes...")
        page.evaluate("""() => {
            // Highlight the Potential Products card in red
            const cards = document.querySelectorAll('#weeklyAiStabilitySummary .oa-card');
            cards.forEach(card => {
                if (card.textContent.includes('潜力品')) {
                    card.style.outline = '4px solid red';
                    card.style.outlineOffset = '2px';
                    card.style.boxShadow = '0 0 10px rgba(255,0,0,0.5)';
                }
            });

            // Highlight Potential Products rows in red and their 100x100 images
            const rows = document.querySelectorAll('#weeklyAiStabilityBody tr');
            rows.forEach(row => {
                const typeTd = row.querySelector('td');
                if (typeTd && typeTd.textContent.includes('潜力品')) {
                    row.style.outline = '3px solid red';
                    row.style.outlineOffset = '-3px';
                    const imgFrame = row.querySelector('.oar-weekly-product-image-frame');
                    if (imgFrame) {
                        imgFrame.style.outline = '4px dashed red';
                        imgFrame.style.outlineOffset = '1px';
                    }
                }
                // Highlight Stable Products 100x100 images in blue
                if (typeTd && typeTd.textContent.includes('稳定品')) {
                    const imgFrame = row.querySelector('.oar-weekly-product-image-frame');
                    if (imgFrame) {
                        imgFrame.style.outline = '3px dashed blue';
                        imgFrame.style.outlineOffset = '1px';
                    }
                }
            });
        }""")
        
        # 4. Save the screenshot
        print(f"Saving screenshot to: {screenshot_path}")
        page.screenshot(path=screenshot_path, full_page=True)
        print("Playwright screenshot capture complete.")
        browser.close()
        
    assert os.path.exists(screenshot_path), f"Screenshot was not generated at {screenshot_path}"
