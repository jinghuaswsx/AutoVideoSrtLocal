"""Run once to create the initial admin user.

Usage:
    python db/create_admin.py                           # 交互式输入密码
    python db/create_admin.py --password 'your_pass'    # 命令行指定密码
    ADMIN_PASSWORD='your_pass' python db/create_admin.py  # 环境变量
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv; load_dotenv()
from appcore.users import create_user, get_by_username

parser = argparse.ArgumentParser(description="创建管理员用户")
parser.add_argument("--username", default="admin", help="管理员用户名（默认 admin）")
parser.add_argument("--password", default=None, help="管理员密码")
args = parser.parse_args()

username = args.username

if get_by_username(username):
    print(f"用户 '{username}' 已存在，跳过创建。")
    sys.exit(0)

password = args.password or os.getenv("ADMIN_PASSWORD") or ""

if not password:
    if not sys.stdin.isatty():
        print("未提供密码且非交互终端，跳过创建管理员。")
        print("下次部署请通过 --password 或 ADMIN_PASSWORD 环境变量提供。")
        sys.exit(0)
    password = getpass.getpass(f"请输入 {username} 的密码: ")

if not password.strip():
    print("错误：密码不能为空")
    sys.exit(1)

create_user(username, password.strip(), role="admin")
print(f"管理员用户 '{username}' 创建成功。")
