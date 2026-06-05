import pytest
import json
from appcore import tasks

def test_unconfirm_final_push_confirmation_success(monkeypatch):
    query_one_calls = []
    executes = []
    commits = []
    closed = []

    # 1. 模拟子任务数据
    child_task = {
        "id": 45,
        "parent_task_id": 12,
        "assignee_id": 9,
        "status": tasks.CHILD_DONE,
        "media_product_id": 7,
        "media_item_id": 1,
        "country_code": "DE",
    }
    
    # 模拟父任务数据
    parent_task = {
        "id": 12,
        "status": tasks.PARENT_ALL_DONE,
    }

    # 模拟数据库查询
    def fake_query_one(sql, args):
        query_one_calls.append((sql, args))
        if "FROM tasks" in sql:
            return child_task
        return None

    class FakeCursor:
        rowcount = 1

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, args=None):
            executes.append((" ".join(str(sql).split()), args))
            self._last_sql = " ".join(str(sql).split())
            self._last_args = args

        def fetchall(self):
            if "FROM task_events" in self._last_sql:
                # 模拟 task_events，通过判断 args
                task_id = self._last_args[0] if self._last_args else None
                event_type = self._last_args[1] if self._last_args and len(self._last_args) > 1 else None
                if task_id == 45 and event_type == "manual_step_confirmed":
                    # 返回一个子任务的确认事件
                    return [{"id": 101, "payload_json": json.dumps({"key": "final_push_confirmation"})}]
                if task_id == 45 and event_type == "completed":
                    # 返回子任务的 completed 事件
                    return [{"id": 102, "payload_json": json.dumps({"reason": "final_push_confirmation"})}]
                if task_id == 12 and event_type == "completed":
                    # 返回父任务的 completed 事件
                    return [{"id": 103, "payload_json": ""}]
            if "SELECT status FROM tasks WHERE id=%s" in self._last_sql:
                return [parent_task]
            return []

        def fetchone(self):
            if "SELECT status FROM tasks WHERE id=%s" in self._last_sql:
                return parent_task
            return None

    class FakeConn:
        def begin(self):
            pass

        def cursor(self):
            return FakeCursor()

        def commit(self):
            commits.append(True)

        def rollback(self):
            raise AssertionError("rollback should not be called")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(tasks, "query_one", fake_query_one)
    monkeypatch.setattr(tasks, "get_conn", lambda: FakeConn())
    monkeypatch.setattr(
        tasks,
        "_refresh_push_status_cache_for_child_task",
        lambda task_id, row: None,
    )

    # 执行 unconfirm
    result = tasks.unconfirm_child_step(
        task_id=45,
        step_key="final_push_confirmation",
        actor_user_id=9,
        is_admin=False,
    )

    # 验证返回
    assert result == {
        "step_key": "final_push_confirmation",
        "completed": False,
        "status": tasks.CHILD_REVIEW,
    }
    assert commits == [True]
    assert closed == [True]

    # 验证 SQL 执行
    # 验证删除 manual_step_confirmed 事件
    assert any("DELETE FROM task_events WHERE id IN (%s)" in sql and args == (101,) for sql, args in executes)
    # 验证回退子任务状态为 review
    assert any("UPDATE tasks SET status=%s, completed_at=NULL, updated_at=NOW() WHERE id=%s AND status=%s" in sql and args == (tasks.CHILD_REVIEW, 45, tasks.CHILD_DONE) for sql, args in executes)
    # 验证删除子任务 completed 事件
    assert any("DELETE FROM task_events WHERE id IN (%s)" in sql and args == (102,) for sql, args in executes)
    # 验证回退父任务状态为 raw_done
    assert any("UPDATE tasks SET status=%s, completed_at=NULL, updated_at=NOW() WHERE id=%s AND status=%s" in sql and args == (tasks.PARENT_RAW_DONE, 12, tasks.PARENT_ALL_DONE) for sql, args in executes)
    # 验证删除父任务 completed 事件
    assert any("DELETE FROM task_events WHERE id IN (%s)" in sql and args == (103,) for sql, args in executes)


def test_unconfirm_fails_for_non_final_push(monkeypatch):
    # 如果不是 final_push_confirmation，应该直接报错 ValueError
    with pytest.raises(ValueError) as exc:
        tasks.unconfirm_child_step(
            task_id=45,
            step_key="translated_video",
            actor_user_id=9,
        )
    assert "only final_push_confirmation step can be unconfirmed" in str(exc.value)


def test_unconfirm_permission_denied(monkeypatch):
    # assignee 不是当前用户，且非 admin，应该抛出 PermissionError
    child_task = {
        "id": 45,
        "parent_task_id": 12,
        "assignee_id": 9,
        "status": tasks.CHILD_DONE,
        "media_product_id": 7,
        "media_item_id": 1,
        "country_code": "DE",
    }
    monkeypatch.setattr(tasks, "query_one", lambda sql, args: child_task)

    with pytest.raises(PermissionError) as exc:
        tasks.unconfirm_child_step(
            task_id=45,
            step_key="final_push_confirmation",
            actor_user_id=1,
            is_admin=False,
        )
    assert "forbidden" in str(exc.value)
