from appcore import user_notifications as notifications


class FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed = []

    def execute(self, sql, args=None):
        self.executed.append((sql, args))

    def fetchall(self):
        return self.rows


def test_notify_pending_raw_task_targets_active_raw_processors():
    cur = FakeCursor(
        [
            {"id": 1, "username": "admin", "role": "admin", "permissions": "{}"},
            {"id": 2, "username": "worker", "role": "user", "permissions": '{"can_process_raw_video": true}'},
            {"id": 3, "username": "viewer", "role": "user", "permissions": "{}"},
            {"id": 4, "username": "admin", "role": "superadmin", "permissions": "{}"},
        ]
    )

    inserted = notifications.notify_pending_raw_task(
        cur,
        task_id=42,
        product_name="保温杯",
    )

    assert inserted == 3
    insert_args = [args for sql, args in cur.executed if "INSERT INTO user_notifications" in sql]
    assert [args[0] for args in insert_args] == [1, 2, 4]
    assert all(args[1:4] == ("task", 42, "task_parent_pending") for args in insert_args)
    assert all(args[6] == "/tasks/?task_id=42" for args in insert_args)


def test_notify_child_blocked_writes_assignee_notification_only():
    cur = FakeCursor()

    inserted = notifications.notify_child_blocked(
        cur,
        task_id=77,
        assignee_id=9,
        product_name="折叠灯",
        country_code="DE",
    )

    assert inserted == 1
    insert_args = [args for sql, args in cur.executed if "INSERT INTO user_notifications" in sql]
    assert len(insert_args) == 1
    assert insert_args[0][0] == 9
    assert insert_args[0][3] == "task_child_blocked"
    assert "DE" in insert_args[0][5]


def test_user_notification_queries_are_scoped_to_current_user(monkeypatch):
    calls = []

    monkeypatch.setattr(
        notifications,
        "query_one",
        lambda sql, args: calls.append(("one", sql, args)) or {"unread_count": 5},
    )
    monkeypatch.setattr(
        notifications,
        "query_all",
        lambda sql, args: calls.append(("all", sql, args)) or [
            {
                "id": 1,
                "source_type": "task",
                "source_id": 2,
                "event_type": "task_child_assigned",
                "title": "新任务",
                "body": "请处理",
                "target_url": "/tasks/?task_id=2",
                "read_at": None,
                "created_at": None,
            }
        ],
    )
    monkeypatch.setattr(
        notifications,
        "execute",
        lambda sql, args: calls.append(("exec", sql, args)) or 1,
    )

    assert notifications.count_unread(user_id=12) == 5
    assert notifications.list_user_notifications(user_id=12, limit=10) == [
        {
            "id": 1,
            "source_type": "task",
            "source_id": 2,
            "event_type": "task_child_assigned",
            "title": "新任务",
            "body": "请处理",
            "target_url": "/tasks/?task_id=2",
            "read_at": None,
            "created_at": None,
        }
    ]
    assert notifications.mark_read(notification_id=99, user_id=12) == 1

    assert calls[0][2] == (12,)
    assert calls[1][2] == (12, 10)
    assert calls[2][2] == (99, 12)
