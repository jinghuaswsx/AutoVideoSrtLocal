"""Run once to create the initial admin user."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv; load_dotenv()
from appcore.users import create_user, get_by_username

username = "admin"
password = "709709@"
if get_by_username(username):
    print(f"User '{username}' already exists.")
else:
    create_user(username, password, role="admin")
    print(f"Admin user '{username}' created. Password: {password}")
