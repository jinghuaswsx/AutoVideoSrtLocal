import sys
import time
from playwright.sync_api import sync_playwright

# 确保控制台输出使用 utf-8 编码，防止 GBK 编码报错
sys.stdout.reconfigure(encoding='utf-8')

def main():
    print("Starting Playwright diagnosis...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        # 监听控制台日志
        console_logs = []
        page.on("console", lambda msg: console_logs.append(f"[{msg.type}] {msg.text}"))

        # 监听请求失败
        failed_requests = []
        page.on("requestfailed", lambda req: failed_requests.append(f"Failed: {req.method} {req.url} - {req.failure_content or 'unknown error'}"))
        
        # 监听所有响应
        all_responses = []
        def handle_response(res):
            all_responses.append({
                "url": res.url,
                "status": res.status,
                "content_type": res.headers.get("content-type", "")
            })
        page.on("response", handle_response)

        # 1. 登录
        print("Navigating to login page...")
        page.goto("http://172.16.254.106/login")
        page.fill("input[name='username']", "admin")
        page.fill("input[name='password']", "709709@")
        
        print("Submitting login form...")
        page.click("button[type='submit']")
        
        # 等待页面加载
        page.wait_for_load_state("networkidle", timeout=10000)
        print("Login complete. Current URL:", page.url)

        # 2. 访问链接检查详情页
        target_url = "http://172.16.254.106/link-check/c7e9e58e-2f79-4d82-bd88-fd0dd42ca784"
        print(f"Navigating to link check detail page: {target_url}")
        page.goto(target_url)
        
        # 等待网络空闲
        page.wait_for_load_state("networkidle", timeout=15000)
        time.sleep(5) # 额外多等几秒

        # 3. 提取详情图 #5 的 DOM 结构
        print("\n--- Inspecting DOM for Detail Item #5 (site-14) ---")
        element_html = page.evaluate("""() => {
            const items = document.querySelectorAll('.oc-link-check-item');
            for (let item of items) {
                const urlEl = item.querySelector('.oc-link-check-item-url');
                if (urlEl && urlEl.textContent.includes('from_url_en_14')) {
                    return item.outerHTML;
                }
            }
            return "Not Found by source_url containing from_url_en_14";
        }""")
        
        # 写入本地文件以防万一，同时也打印出来
        out_file = "C:/Users/admin/.gemini/antigravity/brain/3d62eafe-b763-404f-82ce-891509c44e1d/diagnose_output.txt"
        with open(out_file, "w", encoding="utf-8") as f:
            f.write("=== DOM HTML ===\n")
            f.write(element_html)
            f.write("\n\n=== Network Responses ===\n")
            task_images_responses = [res for res in all_responses if "api/link-check" in res["url"]]
            for res in task_images_responses:
                f.write(f"URL: {res['url']}\n  Status: {res['status']}, Content-Type: {res['content_type']}\n")
            f.write("\n\n=== Failed Requests ===\n")
            for req in failed_requests:
                f.write(req + "\n")
            f.write("\n\n=== Console Logs ===\n")
            for log in console_logs:
                f.write(log + "\n")

        print("Logged HTML and logs to diagnose_output.txt")
        print(element_html)

        # 4. 打印网络请求响应状态
        print("\n--- Network Responses for Link Check Tasks ---")
        for res in task_images_responses:
            print(f"URL: {res['url']}\n  Status: {res['status']}, Content-Type: {res['content_type']}")

        # 5. 打印失败的请求
        print("\n--- Failed Requests ---")
        for req in failed_requests:
            print(req)

        # 6. 打印控制台日志
        print("\n--- Console Logs ---")
        for log in console_logs:
            print(log)

        # 7. 截图保存为 artifact
        screenshot_path = "C:/Users/admin/.gemini/antigravity/brain/3d62eafe-b763-404f-82ce-891509c44e1d/diagnose_screenshot.png"
        page.screenshot(path=screenshot_path, full_page=True)
        print(f"\nScreenshot saved to: {screenshot_path}")

        browser.close()

if __name__ == "__main__":
    main()
