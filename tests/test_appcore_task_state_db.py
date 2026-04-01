"""Tests that task_state persists to and restores from DB."""
import pytest
import appcore.task_state as ts
from appcore.db import execute, query_one


@pytest.fixture(autouse=True)
def cleanup():
    execute("DELETE FROM users WHERE username = %s", ("_test_ts_user_",))
    yield
    execute("DELETE FROM projects WHERE id IN ('test_ts_001','test_ts_002','test_ts_003')")
    execute("DELETE FROM users WHERE username = %s", ("_test_ts_user_",))


@pytest.fixture
def user_id():
    from appcore.users import create_user
    return create_user("_test_ts_user_", "x")


def test_create_persists_to_db(user_id, tmp_path):
    task_id = "test_ts_001"
    ts.create(task_id, "/tmp/v.mp4", str(tmp_path), "v.mp4", user_id=user_id)
    row = query_one("SELECT * FROM projects WHERE id = %s", (task_id,))
    assert row is not None
    assert row["user_id"] == user_id
    assert row["status"] == "uploaded"
    assert row["expires_at"] is not None


def test_get_falls_back_to_db(user_id, tmp_path):
    task_id = "test_ts_002"
    ts.create(task_id, "/tmp/v.mp4", str(tmp_path), "v.mp4", user_id=user_id)
    # Remove from memory
    from appcore.task_state import _tasks
    _tasks.pop(task_id, None)
    # Should restore from DB
    task = ts.get(task_id)
    assert task is not None
    assert task["id"] == task_id


def test_set_step_updates_db(user_id, tmp_path):
    task_id = "test_ts_003"
    ts.create(task_id, "/tmp/v.mp4", str(tmp_path), "v.mp4", user_id=user_id)
    ts.set_step(task_id, "extract", "done")
    row = query_one("SELECT state_json FROM projects WHERE id = %s", (task_id,))
    import json
    state = json.loads(row["state_json"])
    assert state["steps"]["extract"] == "done"
