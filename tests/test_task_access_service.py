from __future__ import annotations


class FakeTaskStore:
    def __init__(self, tasks):
        self.tasks = tasks

    def get(self, task_id):
        return self.tasks.get(task_id)


def test_get_user_task_returns_owned_task():
    from web.services.task_access import get_user_task

    task = {"id": "task-1", "_user_id": 42}
    store = FakeTaskStore({"task-1": task})

    assert get_user_task("task-1", user_id=42, task_store=store) is task


def test_get_user_task_rejects_missing_or_unowned_task():
    from web.services.task_access import get_user_task

    store = FakeTaskStore({"task-1": {"id": "task-1", "_user_id": 42}})

    assert get_user_task("missing", user_id=42, task_store=store) is None
    assert get_user_task("task-1", user_id=7, task_store=store) is None


def test_is_admin_user_reads_flask_login_user_shape():
    from types import SimpleNamespace

    from web.services.task_access import is_admin_user

    assert is_admin_user(SimpleNamespace(is_admin=True)) is True
    assert is_admin_user(SimpleNamespace(is_admin=False)) is False
    assert is_admin_user(SimpleNamespace()) is False


def test_optional_user_id_reads_authenticated_flask_login_user():
    from types import SimpleNamespace

    from web.services.task_access import optional_user_id

    assert optional_user_id(SimpleNamespace(id=42, is_authenticated=True)) == 42
    assert optional_user_id(SimpleNamespace(id=42, is_authenticated=False)) is None
    assert optional_user_id(SimpleNamespace(id=42)) is None
