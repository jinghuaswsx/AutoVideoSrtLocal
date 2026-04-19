"""Tests that task_state persists to and restores from DB."""
import pytest
import pymysql

import appcore.task_state as ts
from appcore.db import execute, query_one


def _is_live_mysql_unavailable(exc: BaseException) -> bool:
    code = exc.args[0] if getattr(exc, "args", None) else None
    return code in {2002, 2003, 2005, 2013}


@pytest.fixture(autouse=True, scope="module")
def require_live_mysql():
    try:
        query_one("SELECT 1")
    except pymysql.MySQLError as exc:
        if _is_live_mysql_unavailable(exc):
            pytest.skip("requires live MySQL at configured host")
        raise


@pytest.fixture(autouse=True)
def cleanup():
    execute("DELETE FROM users WHERE username = %s", ("_test_ts_user_",))
    yield
    execute(
        "DELETE FROM projects WHERE id IN ('test_ts_001','test_ts_002','test_ts_003','test_ts_link_check')"
    )
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


def test_create_link_check_persists_to_db_with_null_expires_at(user_id, tmp_path):
    ts.create_link_check(
        "test_ts_link_check",
        task_dir=str(tmp_path),
        user_id=user_id,
        link_url="https://newjoyloo.com/fr/products/demo",
        target_language="fr",
        target_language_name="法语",
        reference_images=[],
        display_name="demo · FR",
    )

    row = query_one(
        "SELECT status, type, expires_at FROM projects WHERE id = %s",
        ("test_ts_link_check",),
    )

    assert row["type"] == "link_check"
    assert row["status"] == "queued"
    assert row["expires_at"] is None
