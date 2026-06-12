import os
import sys
from pathlib import Path

# 添加当前目录以确保能 import web 和 appcore
sys.path.insert(0, str(Path(__file__).resolve().parent))

from web.app import create_app
from appcore.users import get_by_username

def main():
    app = create_app()
    app.config['WTF_CSRF_ENABLED'] = False
    app.config['TESTING'] = True

    # 获得 admin 用户的 ID
    row = get_by_username("admin")
    if not row:
        print("Error: admin user not found in DB!")
        return
        
    user_id = str(row["id"])
    print(f"Logged in user_id: {user_id}")

    with app.test_client() as client:
        # 在 session 中伪造已登录状态
        with client.session_transaction() as sess:
            sess["_user_id"] = user_id
            sess["_fresh"] = True
            sess["_id"] = "test-session-id"
            
        task_id = "c7e9e58e-2f79-4d82-bd88-fd0dd42ca784"
        
        # 1. 英语原图
        print("\n--- Requesting Original Image ---")
        r_orig = client.get(f"/api/link-check/tasks/{task_id}/images/original/detail-18114")
        print("Status:", r_orig.status_code)
        print("Content-Type:", r_orig.headers.get("Content-Type"))
        print("Content-Length:", r_orig.headers.get("Content-Length"))
        print("Body size:", len(r_orig.data))
        
        # 2. 网页实际图
        print("\n--- Requesting Site Image ---")
        r_site = client.get(f"/api/link-check/tasks/{task_id}/images/site/site-14")
        print("Status:", r_site.status_code)
        print("Content-Type:", r_site.headers.get("Content-Type"))
        print("Content-Length:", r_site.headers.get("Content-Length"))
        print("Body size:", len(r_site.data))
        
        # 3. 正常能显示的系统翻译图
        print("\n--- Requesting Reference Image ---")
        r_ref = client.get(f"/api/link-check/tasks/{task_id}/images/reference/detail-18129")
        print("Status:", r_ref.status_code)
        print("Content-Type:", r_ref.headers.get("Content-Type"))
        print("Content-Length:", r_ref.headers.get("Content-Length"))
        print("Body size:", len(r_ref.data))

if __name__ == "__main__":
    main()
