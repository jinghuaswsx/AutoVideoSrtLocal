from appcore import tasks


class FakeCursor:
    def __init__(self):
        self.executed = []
        self.lastrowid = None
        self.rowcount = 0
        self._next_id = 100
        self._fetchall = []
        self._fetchone = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, args=None):
        self.executed.append((sql, args))
        if sql.startswith("INSERT INTO tasks"):
            self.lastrowid = self._next_id
            self._next_id += 1
            self.rowcount = 1
        elif sql.startswith("UPDATE tasks SET status=%s, updated_at=NOW()"):
            self.rowcount = 2
        elif sql.startswith("UPDATE tasks SET status=%s, last_reason=NULL"):
            self.rowcount = 1
        elif "SELECT id FROM tasks WHERE parent_task_id" in sql:
            self._fetchall = [{"id": 201}, {"id": 202}]
        else:
            self.rowcount = 1

    def fetchall(self):
        return list(self._fetchall)

    def fetchone(self):
        return self._fetchone


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_obj = cursor
        self.committed = False
        self.closed = False

    def begin(self):
        pass

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def test_create_parent_task_emits_pending_and_child_notifications(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    calls = []
    fake_notifications = type(
        "FakeNotifications",
        (),
        {
            "notify_parent_assigned": staticmethod(
                lambda cur, *, task_id, assignee_id, product_name: calls.append(
                    ("parent_assigned", task_id, assignee_id, product_name)
                )
            ),
            "notify_child_blocked": staticmethod(
                lambda cur, *, task_id, assignee_id, product_name, country_code: calls.append(
                    ("child_blocked", task_id, assignee_id, product_name, country_code)
                )
            ),
        },
    )

    monkeypatch.setattr(tasks, "get_conn", lambda: conn)
    monkeypatch.setattr(tasks, "_product_name_for_notification", lambda cur, product_id: "保温杯")
    monkeypatch.setattr(tasks, "notifications_svc", fake_notifications, raising=False)

    parent_id = tasks.create_parent_task(
        media_product_id=7,
        media_item_id=8,
        countries=["DE", "FR"],
        translator_id=9,
        raw_processor_id=6,
        created_by=1,
    )

    assert parent_id == 100
    parent_insert_sql, parent_insert_args = cursor.executed[0]
    assert "assignee_id" in parent_insert_sql
    assert "claimed_at" in parent_insert_sql
    assert parent_insert_args == (7, 8, 6, tasks.PARENT_RAW_IN_PROGRESS, 1)
    assert calls == [
        ("parent_assigned", 100, 6, "保温杯"),
        ("child_blocked", 101, 9, "保温杯", "DE"),
        ("child_blocked", 102, 9, "保温杯", "FR"),
    ]
    assert conn.committed is True


def test_create_parent_task_emits_child_notifications_per_language_assignee(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    calls = []
    fake_notifications = type(
        "FakeNotifications",
        (),
        {
            "notify_parent_assigned": staticmethod(
                lambda cur, *, task_id, assignee_id, product_name: calls.append(
                    ("parent_assigned", task_id, assignee_id, product_name)
                )
            ),
            "notify_child_blocked": staticmethod(
                lambda cur, *, task_id, assignee_id, product_name, country_code: calls.append(
                    ("child_blocked", task_id, assignee_id, product_name, country_code)
                )
            ),
        },
    )

    monkeypatch.setattr(tasks, "get_conn", lambda: conn)
    monkeypatch.setattr(tasks, "_product_name_for_notification", lambda cur, product_id: "保温杯")
    monkeypatch.setattr(tasks, "notifications_svc", fake_notifications, raising=False)

    parent_id = tasks.create_parent_task(
        media_product_id=7,
        media_item_id=8,
        countries=["DE", "FR"],
        language_assignments={"DE": 9, "FR": 10},
        raw_processor_id=88,
        created_by=1,
    )

    assert parent_id == 100
    assert calls == [
        ("parent_assigned", 100, 88, "保温杯"),
        ("child_blocked", 101, 9, "保温杯", "DE"),
        ("child_blocked", 102, 10, "保温杯", "FR"),
    ]
    assert conn.committed is True


def test_approve_raw_notifies_children_after_unblock(monkeypatch):
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    calls = []
    fake_notifications = type(
        "FakeNotifications",
        (),
        {
            "notify_child_assigned": staticmethod(
                lambda cur, *, task_id, product_name: calls.append(
                    ("child_assigned", task_id, product_name)
                )
            ),
        },
    )

    monkeypatch.setattr(tasks, "get_conn", lambda: conn)
    monkeypatch.setattr(
        "appcore.task_raw_source_bridge.ensure_raw_source_for_parent_task",
        lambda **kwargs: {"created": True, "raw_source_id": 55},
    )
    monkeypatch.setattr(tasks, "_product_name_for_notification", lambda cur, product_id: "折叠灯")
    monkeypatch.setattr(tasks, "_task_product_id_for_notification", lambda cur, task_id: 7)
    monkeypatch.setattr(tasks, "notifications_svc", fake_notifications, raising=False)

    tasks.approve_raw(task_id=100, actor_user_id=1)

    assert calls == [
        ("child_assigned", 201, "折叠灯"),
        ("child_assigned", 202, "折叠灯"),
    ]
    assert conn.committed is True
