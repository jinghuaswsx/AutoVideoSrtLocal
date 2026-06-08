from __future__ import annotations


def test_list_translators_filters_active_users_with_translate_permission(monkeypatch):
    from appcore import users

    calls = []

    def fake_query(sql, args=()):
        calls.append((sql, args))
        return [
            {"id": 1, "username": "alice", "role": "user", "permissions": '{"can_translate": true}'},
            {"id": 2, "username": "bob", "role": "admin", "permissions": {"can_translate": True}},
            {"id": 3, "username": "carol", "role": "user", "permissions": '{"can_translate": false}'},
            {"id": 4, "username": "dan", "role": "user", "permissions": ""},
            {"id": 5, "username": "admin", "role": "superadmin", "permissions": "{}"},
        ]

    monkeypatch.setattr(users, "query", fake_query)

    assert users.list_translators() == [
        {"id": 1, "username": "alice"},
        {"id": 2, "username": "bob"},
        {"id": 5, "username": "admin"},
    ]
    assert calls == [
        (
            "SELECT id, username, role, permissions FROM users WHERE is_active=1 ORDER BY username ASC",
            (),
        )
    ]


def test_list_translation_work_users_requires_translate_and_work_scope(monkeypatch):
    from appcore import users

    def fake_query(sql, args=()):
        return [
            {
                "id": 4,
                "username": "admin",
                "display_name": "蔡靖华",
                "role": "superadmin",
                "permissions": "{}",
            },
            {
                "id": 1,
                "username": "zhou",
                "display_name": "周干琴",
                "role": "user",
                "permissions": '{"can_translate": true, "work_scope_translation": true}',
            },
            {
                "id": 2,
                "username": "translate-only",
                "display_name": "翻译但非范围",
                "role": "user",
                "permissions": '{"can_translate": true, "work_scope_translation": false}',
            },
            {
                "id": 3,
                "username": "scope-only",
                "display_name": "范围但非翻译",
                "role": "user",
                "permissions": '{"can_translate": false, "work_scope_translation": true}',
            },
        ]

    monkeypatch.setattr(users, "_user_display_name_expr", lambda: "username", raising=False)
    monkeypatch.setattr(users, "query", fake_query)
    monkeypatch.setattr(
        "appcore.tasks.get_active_pending_push_task_ids",
        lambda: set()
    )

    assert users.list_translation_work_users() == [
        {
            "id": 4,
            "username": "admin",
            "display_name": "蔡靖华",
            "todo_count": 0,
            "urgent_count": 0,
            "completed_today_count": 0,
        },
        {
            "id": 1,
            "username": "zhou",
            "display_name": "周干琴",
            "todo_count": 0,
            "urgent_count": 0,
            "completed_today_count": 0,
        },
    ]


def test_ensure_translation_work_user_accepts_valid_user(monkeypatch):
    from appcore import users

    monkeypatch.setattr(users, "_user_display_name_expr", lambda: "username", raising=False)
    monkeypatch.setattr(
        users,
        "query_one",
        lambda sql, args: {
            "id": 5,
            "username": "worker",
            "display_name": "顾倩",
            "role": "user",
            "permissions": '{"can_translate": true, "work_scope_translation": true}',
            "is_active": 1,
        },
    )

    assert users.ensure_translation_work_user(5)["username"] == "worker"


def test_ensure_translation_work_user_accepts_active_superadmin(monkeypatch):
    from appcore import users

    monkeypatch.setattr(users, "_user_display_name_expr", lambda: "username", raising=False)
    monkeypatch.setattr(
        users,
        "query_one",
        lambda sql, args: {
            "id": 33,
            "username": "admin",
            "display_name": "蔡靖华",
            "role": "superadmin",
            "permissions": "{}",
            "is_active": 1,
        },
    )

    assert users.ensure_translation_work_user(33)["display_name"] == "蔡靖华"


def test_ensure_translation_work_user_rejects_missing_scope(monkeypatch):
    from appcore import users

    monkeypatch.setattr(users, "_user_display_name_expr", lambda: "username", raising=False)
    monkeypatch.setattr(
        users,
        "query_one",
        lambda sql, args: {
            "id": 5,
            "username": "worker",
            "display_name": "顾倩",
            "role": "user",
            "permissions": '{"can_translate": true, "work_scope_translation": false}',
            "is_active": 1,
        },
    )

    try:
        users.ensure_translation_work_user(5)
    except ValueError as exc:
        assert "翻译工作范围" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_list_translation_work_users_counts_blocked_tasks(monkeypatch):
    from appcore import users

    queries = []

    def fake_query(sql, args=()):
        queries.append((sql, args))
        if "FROM tasks" in sql:
            # First call: counts query
            return [
                {"assignee_id": 1, "todo_count": 5, "urgent_count": 2, "completed_today_count": 3},
                {"assignee_id": 2, "todo_count": 0, "urgent_count": 0, "completed_today_count": 0},
            ]
        else:
            # Second call: users query
            return [
                {
                    "id": 1,
                    "username": "zhou",
                    "display_name": "周干琴",
                    "role": "user",
                    "permissions": '{"can_translate": true, "work_scope_translation": true}',
                },
                {
                    "id": 2,
                    "username": "wang",
                    "display_name": "王健",
                    "role": "user",
                    "permissions": '{"can_translate": true, "work_scope_translation": true}',
                },
            ]

    monkeypatch.setattr(users, "_user_display_name_expr", lambda: "username", raising=False)
    monkeypatch.setattr(users, "query", fake_query)
    monkeypatch.setattr(
        "appcore.tasks.get_active_pending_push_task_ids",
        lambda: set()
    )

    res = users.list_translation_work_users()
    assert len(res) == 2
    assert res[0]["todo_count"] == 5
    assert res[0]["urgent_count"] == 2
    assert res[0]["completed_today_count"] == 3
    assert res[1]["todo_count"] == 0

    # Verify that the query SQL statement contains 'blocked'
    counts_sql = queries[0][0]
    assert "'blocked'" in counts_sql
    assert "status IN ('pending', 'raw_in_progress', 'raw_review')" in counts_sql
    assert "status IN ('blocked', 'assigned', 'review')" in counts_sql


def test_list_translation_work_users_excludes_pending_push(monkeypatch):
    from appcore import users

    queries = []

    def fake_query(sql, args=()):
        queries.append((sql, args))
        if "FROM tasks" in sql:
            return [
                {"assignee_id": 1, "todo_count": 5, "urgent_count": 2, "completed_today_count": 3},
            ]
        else:
            return [
                {
                    "id": 1,
                    "username": "zhou",
                    "display_name": "周干琴",
                    "role": "user",
                    "permissions": '{"can_translate": true, "work_scope_translation": true}',
                },
            ]

    monkeypatch.setattr(users, "_user_display_name_expr", lambda: "username", raising=False)
    monkeypatch.setattr(users, "query", fake_query)
    monkeypatch.setattr(
        "appcore.tasks.get_active_pending_push_task_ids",
        lambda: {999}
    )

    res = users.list_translation_work_users()
    assert len(res) == 1
    assert res[0]["todo_count"] == 5

    counts_sql = queries[0][0]
    assert "id NOT IN (999)" in counts_sql


def test_get_employee_task_stats_excludes_pending_push(monkeypatch):
    from appcore import tasks

    queries = []

    def fake_query(sql, args=()):
        queries.append((sql, args))
        return [
            {
                "assignee_id": 1,
                "employee_name": "周干琴",
                "today_completed": 0,
                "today_pending": 5,
                "urgent_pending": 2,
                "total_tasks": 10,
                "raw_tasks": 0,
                "translate_tasks": 10,
            }
        ]

    monkeypatch.setattr(tasks, "_user_display_name_expr", lambda prefix: "u.username", raising=False)
    monkeypatch.setattr(tasks, "query_all", fake_query)
    monkeypatch.setattr(
        "appcore.tasks.get_active_pending_push_task_ids",
        lambda: {999}
    )

    res = tasks.get_employee_task_stats("2026-06-08")
    assert len(res) == 1
    assert res[0]["today_pending"] == 5

    stats_sql = queries[0][0]
    assert "t.id NOT IN (999)" in stats_sql

