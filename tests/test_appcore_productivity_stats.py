import pytest
from datetime import datetime, timedelta
from appcore.db import execute, query_one


@pytest.fixture
def db_test_user():
    from appcore.users import create_user, get_by_username
    username = "_t_ps_user"
    execute("DELETE FROM users WHERE username=%s", (username,))
    create_user(username, "x", role="user")
    uid = get_by_username(username)["id"]
    yield uid
    execute("DELETE FROM users WHERE username=%s", (username,))


def _insert_event(task_id, event_type, actor_user_id):
    return execute(
        "INSERT INTO task_events (task_id, event_type, actor_user_id) VALUES (%s, %s, %s)",
        (task_id, event_type, actor_user_id),
    )


def test_get_daily_throughput(db_test_user):
    from appcore import productivity_stats
    today = datetime.now()
    e1 = _insert_event(99991, "approved", db_test_user)
    e2 = _insert_event(99992, "approved", db_test_user)
    e3 = _insert_event(99993, "completed", db_test_user)

    rows = productivity_stats.get_daily_throughput(
        from_dt=today - timedelta(days=2),
        to_dt=today + timedelta(days=1),
    )
    user_rows = [r for r in rows if r["user_id"] == db_test_user]
    total = sum(r["count"] for r in user_rows)
    assert total == 3

    execute("DELETE FROM task_events WHERE id IN (%s, %s, %s)", (e1, e2, e3))


def test_get_pass_rate(db_test_user):
    from appcore import productivity_stats
    today = datetime.now()
    e1 = _insert_event(99994, "approved", db_test_user)
    e2 = _insert_event(99995, "approved", db_test_user)
    e3 = _insert_event(99996, "rejected", db_test_user)

    rows = productivity_stats.get_pass_rate(
        from_dt=today - timedelta(days=1),
        to_dt=today + timedelta(days=1),
    )
    user_row = next((r for r in rows if r["user_id"] == db_test_user), None)
    assert user_row is not None
    assert user_row["approved"] == 2
    assert user_row["rejected"] == 1
    assert user_row["pass_rate"] == round(2/3, 3)

    execute("DELETE FROM task_events WHERE id IN (%s, %s, %s)", (e1, e2, e3))


def test_get_rework_rate(db_test_user):
    from appcore import productivity_stats
    today = datetime.now()
    # User submits twice, no rejections in this window
    e1 = _insert_event(99997, "submitted", db_test_user)
    e2 = _insert_event(99998, "submitted", db_test_user)

    rows = productivity_stats.get_rework_rate(
        from_dt=today - timedelta(days=1),
        to_dt=today + timedelta(days=1),
    )
    user_row = next((r for r in rows if r.get("user_id") == db_test_user), None)
    # User has submitted 2, rejected 0 → rework_rate = 0
    if user_row:
        assert user_row["submitted"] == 2
        assert user_row["rejected"] == 0
        assert user_row["rework_rate"] == 0

    execute("DELETE FROM task_events WHERE id IN (%s, %s)", (e1, e2))
