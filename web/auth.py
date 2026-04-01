from __future__ import annotations
from flask_login import LoginManager, UserMixin
from appcore.users import get_by_id

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "请先登录"


class User(UserMixin):
    def __init__(self, row: dict):
        self.id = row["id"]
        self.username = row["username"]
        self.role = row["role"]
        self.is_active_flag = bool(row["is_active"])

    @property
    def is_active(self):
        return self.is_active_flag


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    row = get_by_id(int(user_id))
    if row and row["is_active"]:
        return User(row)
    return None


def admin_required(f):
    """Decorator: require admin role. Use after @login_required."""
    from functools import wraps
    from flask import abort
    from flask_login import current_user
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != "admin":
            abort(403)
        return f(*args, **kwargs)
    return decorated
