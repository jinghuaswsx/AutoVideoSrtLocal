"""wedev_sync.py — 一键从 Chrome 抓 wedev Cookie/Bearer 并上传到主项目。

流程：
  1. 默认浏览器打开 https://os.wedev.vip/ 让你登录
  2. 回车继续
  3. 读 Chrome 里 os.wedev.vip 的 cookies + 提取 JWT（AutoPush/backend/browser_auth.py 实现）
  4. 读 Chrome 里主项目 host 的 session cookie（用于鉴权主项目 API）
  5. POST 到 <主项目>/pushes/api/push-credentials

依赖：
  - Windows + Chrome（macOS/Linux 未适配，会退化为"请手动粘贴"模式）
  - Python 3.10+，requests；Windows 需要 pywin32（DPAPI）、cryptography（AES-GCM）

用法：
  python tools/wedev_sync.py
  python tools/wedev_sync.py --project-url http://172.30.254.14 --wedev-url https://os.wedev.vip
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any

import requests


DEFAULT_PROJECT_URL = "http://172.30.254.14"
DEFAULT_WEDEV_URL = "https://os.wedev.vip"


def _import_browser_auth():
    """延迟导入 AutoPush 的 browser_auth 模块（共享 cookies 提取逻辑）。"""
    repo_root = Path(__file__).resolve().parents[1]
    autopush_backend = repo_root / "AutoPush" / "backend"
    if str(autopush_backend) not in sys.path:
        sys.path.insert(0, str(autopush_backend))
    import browser_auth  # type: ignore
    return browser_auth


def _load_wedev_credentials(wedev_url: str) -> dict[str, str]:
    """返回 {cookie, authorization}，从 Chrome 读取。"""
    browser_auth = _import_browser_auth()
    headers = browser_auth.resolve_chrome_auth_headers(wedev_url)
    return {
        "cookie": headers.get("Cookie", ""),
        "authorization": headers.get("Authorization", ""),
    }


def _load_project_session_cookie(project_url: str) -> str:
    """从 Chrome 读取主项目 host 的全部 cookies，拼 cookie header。"""
    browser_auth = _import_browser_auth()
    host = urllib.parse.urlparse(project_url).hostname or ""
    user_data_dir = browser_auth._chrome_user_data_dir()  # noqa: SLF001
    if not user_data_dir:
        return ""
    try:
        cookies = browser_auth._load_best_profile_cookies(user_data_dir, host)  # noqa: SLF001
    except Exception as exc:
        print(f"[warn] 读取主项目 cookies 失败：{exc}")
        return ""
    return browser_auth._build_cookie_header(cookies)  # noqa: SLF001


def _post_credentials(
    project_url: str,
    session_cookie: str,
    payload: dict[str, Any],
) -> tuple[int, str]:
    url = project_url.rstrip("/") + "/pushes/api/push-credentials"
    headers = {"Content-Type": "application/json"}
    if session_cookie:
        headers["Cookie"] = session_cookie
    resp = requests.post(url, json=payload, headers=headers, timeout=15)
    return resp.status_code, resp.text


def main() -> int:
    parser = argparse.ArgumentParser(description="同步 wedev 凭据到主项目")
    parser.add_argument("--project-url", default=DEFAULT_PROJECT_URL,
                        help=f"主项目 URL（默认 {DEFAULT_PROJECT_URL}）")
    parser.add_argument("--wedev-url", default=DEFAULT_WEDEV_URL,
                        help=f"wedev 登录页 URL（默认 {DEFAULT_WEDEV_URL}）")
    parser.add_argument("--no-browser", action="store_true",
                        help="跳过打开浏览器步骤")
    args = parser.parse_args()

    print("=" * 60)
    print("wedev 凭据同步工具")
    print("=" * 60)

    if not args.no_browser:
        print(f"\n1) 打开浏览器登录 wedev：{args.wedev_url}")
        webbrowser.open(args.wedev_url)
        print("   完成登录后，回到这里按 Enter 继续…")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            return 1
    else:
        print("\n1) 已跳过浏览器环节（--no-browser）。")

    print("\n2) 从 Chrome 读取 wedev 凭据…")
    wedev = _load_wedev_credentials(args.wedev_url)
    cookie = wedev["cookie"]
    authorization = wedev["authorization"]
    if not cookie and not authorization:
        print("   [FAIL] 未从 Chrome 读到 wedev 的 Cookie 或 Bearer。")
        print("         请确认浏览器是 Chrome、已登录 wedev、且本脚本在你的 Windows 账户下运行。")
        return 2
    masked = lambda s: f"{s[:6]}…{s[-4:]} (len={len(s)})" if s and len(s) > 12 else ("*" * len(s) if s else "-")
    print(f"   Authorization: {masked(authorization)}")
    print(f"   Cookie:        {masked(cookie)}")

    print(f"\n3) 从 Chrome 读取主项目 session cookie（{args.project_url}）…")
    session_cookie = _load_project_session_cookie(args.project_url)
    if not session_cookie:
        print("   [WARN] 主项目 session cookie 为空。")
        print("         请先在 Chrome 里登录一次主项目（使用管理员账号），然后重跑。")
        print("         或手动复制 cookie，使用 --project-cookie 选项（暂未实现，后续可加）。")
        return 3
    print(f"   session: {masked(session_cookie)}")

    print(f"\n4) POST {args.project_url}/pushes/api/push-credentials …")
    payload = {
        "push_localized_texts_base_url": args.wedev_url,
        "push_localized_texts_authorization": authorization,
        "push_localized_texts_cookie": cookie,
    }
    status, body = _post_credentials(args.project_url, session_cookie, payload)
    print(f"   HTTP {status}")
    try:
        body_data = json.loads(body)
        print(f"   {json.dumps(body_data, ensure_ascii=False, indent=2)}")
    except ValueError:
        print(f"   {body[:400]}")

    if status == 200:
        print("\n✓ 同步完成。可去 /settings?tab=push 查看状态。")
        return 0
    if status in (401, 403):
        print("\n[FAIL] 主项目认证失败（非 admin 或 session 失效）。")
        print("      请用管理员账号在 Chrome 里重新登录主项目，然后重跑。")
        return 4
    print(f"\n[FAIL] 主项目返回 HTTP {status}。")
    return 5


if __name__ == "__main__":
    sys.exit(main())
