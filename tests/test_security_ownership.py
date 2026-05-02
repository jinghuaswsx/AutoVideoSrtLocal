"""安全测试：任务归属校验。

确保用户不能访问或操作其他用户的任务。
"""
import os

import pytest


@pytest.fixture
def user1_client(monkeypatch):
    """以 user_id=1 登录的 client。"""
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.scheduled_tasks.latest_failure_alert", lambda: None)
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
        task_dir = tmp_path / "task"
        project_dir = str(task_dir / "capcut")
        (task_dir / "capcut").mkdir(parents=True)

        fake_task = {
            "_user_id": 1,
            "task_dir": str(task_dir),
            "exports": {"capcut_project": project_dir},
        }
        monkeypatch.setattr("web.routes.task.store.get", lambda tid: fake_task)
        monkeypatch.setattr("web.routes.task.store.update", lambda *a, **kw: None)
        monkeypatch.setattr("web.services.task_capcut.deploy_capcut_project",
                            lambda p: str(tmp_path / "deployed"))

        resp = user1_client.post("/api/tasks/some-task-id/deploy/capcut")
        assert resp.status_code == 200


class TestAdminUserIdValidation:
    """admin.py toggle_active 应正确处理非法 user_id。"""

    @pytest.fixture
    def admin_client(self, monkeypatch):
        monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
        monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
        monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
        monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
        monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
        monkeypatch.setattr("appcore.scheduled_tasks.latest_failure_alert", lambda: None)
        fake_user = {"id": 1, "username": "admin", "role": "superadmin", "is_active": 1}
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


class TestCopywritingInputsOwnership:
    """copywriting update_inputs 应校验任务归属。"""

    def test_update_inputs_wrong_user_returns_404(self, user1_client, monkeypatch):
        """user_id=1 不能修改 user_id=2 的 copywriting 商品信息。"""
        import appcore.task_state as ts
        ts._tasks["cw-other"] = {"id": "cw-other", "_user_id": 2, "type": "copywriting"}
        monkeypatch.setattr("web.routes.copywriting.task_state", ts)

        resp = user1_client.put("/api/copywriting/cw-other/inputs",
                                json={"product_title": "hacked"})
        assert resp.status_code == 404
        ts._tasks.pop("cw-other", None)

    def test_update_inputs_own_task_succeeds(self, user1_client, monkeypatch):
        """user_id=1 可以修改自己的 copywriting 商品信息。"""
        import appcore.task_state as ts
        ts._tasks["cw-mine"] = {"id": "cw-mine", "_user_id": 1, "type": "copywriting"}
        monkeypatch.setattr("web.routes.copywriting.task_state", ts)
        # mock DB
        monkeypatch.setattr("web.routes.copywriting.get_connection",
                            lambda: _FakeConn())

        resp = user1_client.put("/api/copywriting/cw-mine/inputs",
                                json={"product_title": "legit"})
        assert resp.status_code == 200
        ts._tasks.pop("cw-mine", None)


class TestFixStepRestriction:
    """copywriting fix_step 应限制可设置的状态值。"""

    def test_fix_step_rejects_done_status(self, user1_client, monkeypatch):
        """不允许前端将步骤设为 done。"""
        import appcore.task_state as ts
        ts._tasks["cw-fs"] = {
            "id": "cw-fs", "_user_id": 1, "type": "copywriting",
            "steps": {"keyframe": "running"},
        }
        monkeypatch.setattr("web.routes.copywriting.task_state", ts)

        resp = user1_client.post("/api/copywriting/cw-fs/fix-step",
                                 json={"step": "keyframe", "status": "done"})
        assert resp.status_code == 400
        ts._tasks.pop("cw-fs", None)

    def test_fix_step_allows_pending(self, user1_client, monkeypatch):
        """允许将步骤重置为 pending。"""
        import appcore.task_state as ts
        ts._tasks["cw-fs2"] = {
            "id": "cw-fs2", "_user_id": 1, "type": "copywriting",
            "steps": {"keyframe": "error"},
        }
        monkeypatch.setattr("web.routes.copywriting.task_state", ts)

        resp = user1_client.post("/api/copywriting/cw-fs2/fix-step",
                                 json={"step": "keyframe", "status": "pending"})
        assert resp.status_code == 200
        ts._tasks.pop("cw-fs2", None)


class TestResumeOwnership:
    """task resume_from_step 应先验证归属。"""

    def test_resume_wrong_user_returns_404(self, user1_client, monkeypatch):
        """user_id=1 不能 resume user_id=2 的任务。"""
        # DB 查询返回空（不属于 user1）
        monkeypatch.setattr("web.routes.task.db_query_one", lambda *a, **kw: None)
        import appcore.task_state as ts
        ts._tasks["other-task"] = {"id": "other-task", "_user_id": 2, "status": "error"}
        monkeypatch.setattr("web.routes.task.store.get", lambda tid: ts._tasks.get(tid))

        resp = user1_client.post("/api/tasks/other-task/resume",
                                 json={"start_step": "translate"})
        assert resp.status_code == 404
        ts._tasks.pop("other-task", None)


# ── 辅助 ──

class _FakeConn:
    """最小化的假数据库连接，用于 update_inputs 测试。"""
    def cursor(self):
        return _FakeCursor()
    def commit(self):
        pass
    def close(self):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass

class _FakeCursor:
    def execute(self, *args, **kwargs):
        pass
    def fetchone(self):
        return None
    def fetchall(self):
        return []
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
