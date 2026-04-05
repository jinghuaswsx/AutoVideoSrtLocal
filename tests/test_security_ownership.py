"""安全测试：任务归属校验。

确保用户不能访问或操作其他用户的任务。
"""
import os

import pytest


@pytest.fixture
def user1_client(monkeypatch):
    """以 user_id=1 登录的 client。"""
    fake_user = {"id": 1, "username": "user1", "role": "user", "is_active": 1}
    monkeypatch.setattr("web.auth.get_by_id", lambda uid: fake_user if int(uid) == 1 else None)

    from web.app import create_app
    app = create_app()
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["_user_id"] = "1"
        sess["_fresh"] = True
    return c


class TestDeployCapcutOwnership:
    """deploy_capcut 应校验任务归属（当前缺失）。"""

    def test_rejects_other_users_task(self, user1_client, monkeypatch, tmp_path):
        """user_id=1 不应该能部署 user_id=2 的任务。"""
        # 创建真实的 capcut 目录，确保不是因路径不存在而 404
        project_dir = str(tmp_path / "capcut_project")
        os.makedirs(project_dir)

        fake_task = {
            "_user_id": 2,
            "exports": {"capcut_project": project_dir},
        }
        monkeypatch.setattr("web.routes.task.store.get", lambda tid: fake_task)

        resp = user1_client.post("/api/tasks/some-task-id/deploy/capcut")
        assert resp.status_code in (403, 404), "应拒绝访问其他用户的任务"

    def test_allows_own_task(self, user1_client, monkeypatch, tmp_path):
        """user_id=1 应能部署自己的任务。"""
        project_dir = str(tmp_path / "capcut")
        (tmp_path / "capcut").mkdir()

        fake_task = {
            "_user_id": 1,
            "exports": {"capcut_project": project_dir},
        }
        monkeypatch.setattr("web.routes.task.store.get", lambda tid: fake_task)
        monkeypatch.setattr("web.routes.task.store.update", lambda *a, **kw: None)
        monkeypatch.setattr("pipeline.capcut.deploy_capcut_project",
                            lambda p: str(tmp_path / "deployed"))

        resp = user1_client.post("/api/tasks/some-task-id/deploy/capcut")
        assert resp.status_code == 200


class TestAdminUserIdValidation:
    """admin.py toggle_active 应正确处理非法 user_id。"""

    @pytest.fixture
    def admin_client(self, monkeypatch):
        fake_user = {"id": 1, "username": "admin", "role": "admin", "is_active": 1}
        monkeypatch.setattr("web.auth.get_by_id", lambda uid: fake_user if int(uid) == 1 else None)

        from web.app import create_app
        app = create_app()
        c = app.test_client()
        with c.session_transaction() as sess:
            sess["_user_id"] = "1"
            sess["_fresh"] = True
        return c

    def test_invalid_user_id_returns_400(self, admin_client, monkeypatch):
        """非数字 user_id 应返回 400 而非 500。"""
        monkeypatch.setattr("web.routes.admin.list_users", lambda: [])
        resp = admin_client.post("/admin/users",
                                 data={"action": "toggle_active", "user_id": "abc", "active": "1"})
        assert resp.status_code == 400

    def test_missing_user_id_returns_400(self, admin_client, monkeypatch):
        """缺少 user_id 应返回 400。"""
        monkeypatch.setattr("web.routes.admin.list_users", lambda: [])
        resp = admin_client.post("/admin/users",
                                 data={"action": "toggle_active", "active": "1"})
        assert resp.status_code == 400

    def test_valid_user_id_succeeds(self, admin_client, monkeypatch):
        """正常数字 user_id 应正常处理。"""
        monkeypatch.setattr("web.routes.admin.set_active", lambda uid, active: None)
        resp = admin_client.post("/admin/users",
                                 data={"action": "toggle_active", "user_id": "42", "active": "1"},
                                 follow_redirects=False)
        assert resp.status_code == 302  # redirect to users page
