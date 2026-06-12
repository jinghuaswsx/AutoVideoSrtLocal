import requests
import re

def main():
    session = requests.Session()
    
    # 1. GET 登录页以获得 CSRF Token
    login_url = "http://127.0.0.1/login"
    print("GETing login page to extract CSRF token...")
    try:
        r_get = session.get(login_url)
        print("GET Status:", r_get.status_code)
        
        # 寻找 HTML 中的 csrf_token input 或者 meta 标签
        # 比如：<input type="hidden" name="csrf_token" value="...">
        csrf_token = ""
        
        # 匹配 <input ... name="csrf_token" ... value="([^\"]+)"> 或相似的
        m = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', r_get.text)
        if not m:
            # 尝试另一种顺序
            m = re.search(r'value=["\']([^"\']+)["\']\s+name=["\']csrf_token["\']', r_get.text)
            
        if m:
            csrf_token = m.group(1)
            print("Extracted Form CSRF Token:", csrf_token)
        else:
            # 尝试 meta tag
            m = re.search(r'name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', r_get.text)
            if m:
                csrf_token = m.group(1)
                print("Extracted Meta CSRF Token:", csrf_token)
            else:
                print("Warning: Could not extract CSRF token from HTML!")
                
        # 2. POST 登录
        login_data = {
            "username": "admin",
            "password": "709709@"
        }
        if csrf_token:
            login_data["csrf_token"] = csrf_token
            
        headers = {
            "Referer": login_url,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        r_post = session.post(login_url, data=login_data, headers=headers)
        print("POST Status:", r_post.status_code)
        print("Current URL after POST:", r_post.url)
        
        # 验证是否登录成功 (主页通常会包含 login_required 之外的东西，或者 session cookies)
        if "login" not in r_post.url and len(session.cookies) > 0:
            print("Login success! Cookies:", session.cookies.get_dict())
        else:
            print("Login failed! HTML content contains:")
            # 打印出错提示
            error_m = re.search(r'class=["\'].*flash.*["\'][^>]*>(.*?)</div>', r_post.text)
            if error_m:
                print("Flash error:", error_m.group(1).strip())
            return
            
    except Exception as e:
        print("Login failed with exception:", e)
        return

    # 3. 登录成功后，测试图片 API
    task_id = "c7e9e58e-2f79-4d82-bd88-fd0dd42ca784"
    urls_to_test = {
        "Site Image 4 (site-4)": f"http://127.0.0.1/api/link-check/tasks/{task_id}/images/site/site-4",
        "Original Image (detail-18114)": f"http://127.0.0.1/api/link-check/tasks/{task_id}/images/original/detail-18114",
        "Reference Image (detail-18129)": f"http://127.0.0.1/api/link-check/tasks/{task_id}/images/reference/detail-18129"
    }

    for name, url in urls_to_test.items():
        print(f"\n--- Testing {name} ---")
        try:
            r = session.get(url, allow_redirects=False)
            print("Status Code:", r.status_code)
            print("Headers:", dict(r.headers))
            if r.status_code == 200:
                body_len = len(r.content)
                header_len = int(r.headers.get("Content-Length", 0))
                print(f"Content-Length Header: {header_len}")
                print(f"Actual Downloaded Body Size: {body_len} bytes")
                if body_len != header_len:
                    print("!!! WARNING: Downloaded size does NOT match Content-Length header!")
            else:
                print("Response Body (first 200 chars):", r.text[:200])
        except Exception as e:
            print("Error requesting url:", e)

if __name__ == "__main__":
    main()
