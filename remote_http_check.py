import requests

def main():
    # 模拟登录并测试
    session = requests.Session()
    
    # 登录接口
    login_url = "http://127.0.0.1/login"
    login_data = {
        "username": "admin",
        "password": "709709@"
    }
    
    print("Trying to login to production (port 80)...")
    try:
        # Flask 通常有 CSRF 保护，但我们需要先获取 Cookie
        r_get = session.get(login_url)
        # 很多时候我们可以直接登录，看看是否有 CSRF 限制
        # 如果需要 CSRF token，从页面中提取
        csrf_token = ""
        if 'name="csrf-token"' in r_get.text:
            idx = r_get.text.find('name="csrf-token"')
            content_part = r_get.text[idx:idx+200]
            # 简单粗暴提取
            import re
            m = re.search(r'content="([^"]+)"', content_part)
            if m:
                csrf_token = m.group(1)
        
        headers = {}
        if csrf_token:
            headers["X-CSRFToken"] = csrf_token
            login_data["csrf_token"] = csrf_token
            
        r_post = session.post(login_url, data=login_data, headers=headers)
        print("Login response status:", r_post.status_code)
        if "dashboard" in r_post.url or r_post.status_code == 200 or len(session.cookies) > 0:
            print("Login successful! Cookies:", session.cookies.get_dict())
        else:
            print("Login failed! URL redirects to:", r_post.url)
    except Exception as e:
        print("Login exception:", e)
        return

    # 测试 API 地址
    task_id = "c7e9e58e-2f79-4d82-bd88-fd0dd42ca784"
    urls_to_test = {
        "Original Image (detail-18114)": f"http://127.0.0.1/api/link-check/tasks/{task_id}/images/original/detail-18114",
        "Site Image (site-14)": f"http://127.0.0.1/api/link-check/tasks/{task_id}/images/site/site-14",
        "Reference Image (detail-18129)": f"http://127.0.0.1/api/link-check/tasks/{task_id}/images/reference/detail-18129"
    }

    for name, url in urls_to_test.items():
        print(f"\n--- Testing {name} ---")
        try:
            r = session.get(url, allow_redirects=False)
            print("Status Code:", r.status_code)
            print("Headers:", dict(r.headers))
            if r.status_code == 200:
                print("Content Length:", len(r.content))
                print("Content Preview (first 50 bytes):", r.content[:50])
            elif r.status_code in (301, 302):
                print("Redirect Location:", r.headers.get("Location"))
            else:
                print("Response Body:", r.text[:200])
        except Exception as e:
            print("Error requesting url:", e)

if __name__ == "__main__":
    main()
