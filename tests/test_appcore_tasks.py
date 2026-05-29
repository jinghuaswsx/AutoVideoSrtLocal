from appcore import tasks


def test_status_constants_present():
    assert tasks.PARENT_PENDING == "pending"
    assert tasks.PARENT_RAW_IN_PROGRESS == "raw_in_progress"
    assert tasks.PARENT_RAW_REVIEW == "raw_review"
    assert tasks.PARENT_RAW_DONE == "raw_done"
    assert tasks.PARENT_ALL_DONE == "all_done"
    assert tasks.PARENT_CANCELLED == "cancelled"
    assert tasks.CHILD_BLOCKED == "blocked"
    assert tasks.CHILD_ASSIGNED == "assigned"
    assert tasks.CHILD_REVIEW == "review"
    assert tasks.CHILD_DONE == "done"
    assert tasks.CHILD_CANCELLED == "cancelled"


def test_existing_task_languages_ignore_cancelled_parent_unfinished_children(monkeypatch):
    captured = {}

    def fake_query_all(sql, args):
        captured["sql"] = " ".join(str(sql).split())
        captured["args"] = args
        return [
            {
                "country_code": "de",
                "child_status": tasks.CHILD_ASSIGNED,
                "parent_status": tasks.PARENT_RAW_DONE,
            },
            {
                "country_code": "fr",
                "child_status": tasks.CHILD_ASSIGNED,
                "parent_status": tasks.PARENT_CANCELLED,
            },
            {
                "country_code": "ja",
                "child_status": tasks.CHILD_DONE,
                "parent_status": tasks.PARENT_CANCELLED,
            },
            {
                "country_code": "nl",
                "child_status": tasks.CHILD_CANCELLED,
                "parent_status": tasks.PARENT_RAW_DONE,
            },
        ]

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    assert tasks.get_existing_task_languages_for_item(42) == ["DE", "JA"]
    assert "LEFT JOIN tasks parent ON parent.id = child.parent_task_id" in captured["sql"]
    assert captured["args"] == (42,)


def test_high_level_status_rollup():
    assert tasks.high_level_status("pending") == "in_progress"
    assert tasks.high_level_status("raw_in_progress") == "in_progress"
    assert tasks.high_level_status("raw_done") == "completed"
    assert tasks.high_level_status("review") == "in_progress"
    assert tasks.high_level_status("done") == "completed"
    assert tasks.high_level_status("all_done") == "completed"
    assert tasks.high_level_status("cancelled") == "terminated"


def test_find_target_lang_item_normalizes_country_code(monkeypatch):
    calls = []

    def fake_query_one(sql, args):
        calls.append(args)
        return {"id": 123}

    monkeypatch.setattr(tasks, "query_one", fake_query_one)

    assert tasks._find_target_lang_item(7, " DE ") == {"id": 123}
    assert calls[0] == (7, "de")


def test_infer_single_child_task_id_for_media_item_returns_unique_active_child(monkeypatch):
    captured = {}

    def fake_query_all(sql, args):
        captured["args"] = args
        return [{"id": 30}]

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    assert tasks.infer_single_child_task_id_for_media_item(599, " DE ") == 30
    assert captured["args"] == (
        599,
        "de",
        tasks.CHILD_ASSIGNED,
        tasks.CHILD_REVIEW,
        tasks.CHILD_DONE,
    )


def test_infer_single_child_task_id_for_media_item_filters_by_assignee(monkeypatch):
    captured = {}

    def fake_query_all(sql, args):
        captured["sql"] = " ".join(str(sql).split())
        captured["args"] = args
        return [{"id": 61}]

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    assert tasks.infer_single_child_task_id_for_media_item(
        599,
        " DE ",
        assignee_id=238,
    ) == 61
    assert "assignee_id=%s" in captured["sql"]
    assert captured["args"] == (
        599,
        "de",
        238,
        tasks.CHILD_ASSIGNED,
        tasks.CHILD_REVIEW,
        tasks.CHILD_DONE,
    )


def test_infer_single_child_task_id_for_media_item_ignores_ambiguous_matches(monkeypatch):
    monkeypatch.setattr(
        tasks,
        "query_all",
        lambda sql, args: [{"id": 30}, {"id": 31}],
    )

    assert tasks.infer_single_child_task_id_for_media_item(599, "de") is None


def test_infer_single_child_task_id_from_raw_source_matches_reused_parent(monkeypatch):
    captured = {}

    def fake_query_all(sql, args):
        captured["sql"] = " ".join(str(sql).split())
        captured["args"] = args
        return [
            {
                "id": 93,
                "payload_json": '{"raw_source_id":187,"media_item_id":1376}',
            },
            {
                "id": 47,
                "payload_json": '{"raw_source_id":999,"media_item_id":1376}',
            },
        ]

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    assert tasks.infer_single_child_task_id_from_raw_source(602, " ES ", 187) == 93
    assert "raw_source_reused" in captured["sql"]
    assert captured["args"] == (
        602,
        "es",
        tasks.CHILD_ASSIGNED,
        tasks.CHILD_REVIEW,
        tasks.CHILD_DONE,
    )


def test_latest_child_task_id_for_media_item_returns_latest_active_child(monkeypatch):
    captured = {}

    def fake_query_one(sql, args):
        captured["sql"] = " ".join(str(sql).split())
        captured["args"] = args
        return {"id": 99}

    monkeypatch.setattr(tasks, "query_one", fake_query_one)

    assert tasks.latest_child_task_id_for_media_item(599, " DE ") == 99
    assert "ORDER BY id DESC LIMIT 1" in captured["sql"]
    assert captured["args"] == (
        599,
        "de",
        tasks.CHILD_ASSIGNED,
        tasks.CHILD_REVIEW,
        tasks.CHILD_DONE,
    )


def test_resolve_child_task_for_media_item_upload_accepts_matching_assignee(monkeypatch):
    captured = {}

    def fake_query_one(sql, args):
        captured["args"] = args
        return {
            "id": 30,
            "assignee_id": 77,
            "status": tasks.CHILD_DONE,
            "media_product_id": 599,
            "country_code": "DE",
        }

    monkeypatch.setattr(tasks, "query_one", fake_query_one)

    assert tasks.resolve_child_task_for_media_item_upload(
        task_id="30",
        product_id=599,
        lang="de",
        actor_user_id=77,
        is_admin=False,
    ) == 30
    assert captured["args"] == (30,)


def test_resolve_child_task_for_media_item_upload_rejects_other_assignee(monkeypatch):
    monkeypatch.setattr(
        tasks,
        "query_one",
        lambda sql, args: {
            "id": 30,
            "assignee_id": 77,
            "status": tasks.CHILD_DONE,
            "media_product_id": 599,
            "country_code": "DE",
        },
    )

    with pytest.raises(PermissionError):
        tasks.resolve_child_task_for_media_item_upload(
            task_id=30,
            product_id=599,
            lang="de",
            actor_user_id=88,
            is_admin=False,
        )


def test_legacy_import_and_create_service_is_removed():
    assert not hasattr(tasks, "import_and_create_task")


def test_on_product_owner_changed_is_noop_to_preserve_task_assignees(monkeypatch):
    monkeypatch.setattr(
        tasks,
        "get_conn",
        lambda: (_ for _ in ()).throw(
            AssertionError("product owner changes must not query or mutate tasks")
        ),
    )

    assert tasks.on_product_owner_changed(
        product_id=42,
        new_user_id=7,
        actor_user_id=1,
    ) == 0


import pytest
from appcore.db import execute, query_one, query_all


@pytest.fixture
def db_user_admin():
    """Make a temporary admin user; yield id; cleanup at end."""
    from appcore.users import create_user, get_by_username
    username = "_t_tc_admin"
    execute("DELETE FROM users WHERE username=%s", (username,))
    create_user(username, "x", role="admin")
    uid = get_by_username(username)["id"]
    yield uid
    execute("DELETE FROM users WHERE username=%s", (username,))


@pytest.fixture
def db_user_translator():
    from appcore.users import create_user, get_by_username
    username = "_t_tc_tr"
    execute("DELETE FROM users WHERE username=%s", (username,))
    create_user(username, "x", role="user")
    uid = get_by_username(username)["id"]
    # 给翻译能力位
    execute(
        "UPDATE users SET permissions=JSON_SET(COALESCE(permissions, '{}'), '$.can_translate', true) WHERE id=%s",
        (uid,),
    )
    yield uid
    execute("DELETE FROM users WHERE username=%s", (username,))


@pytest.fixture
def db_user_raw_processor():
    from appcore.users import create_user, get_by_username

    username = "_t_tc_raw"
    execute("DELETE FROM users WHERE username=%s", (username,))
    create_user(username, "x", role="user")
    uid = get_by_username(username)["id"]
    execute(
        "UPDATE users SET permissions=JSON_SET(COALESCE(permissions, '{}'), '$.can_process_raw_video', true) WHERE id=%s",
        (uid,),
    )
    yield uid
    execute("DELETE FROM users WHERE username=%s", (username,))


@pytest.fixture
def db_product(db_user_admin):
    """Make a media product owned by db_user_admin."""
    # Pre-clean any leftover rows from prior failed runs (no UNIQUE on name but be safe)
    execute("DELETE FROM media_products WHERE name=%s", ("_t_tc_product",))
    # Use execute()'s return value (lastrowid) instead of LAST_INSERT_ID() —
    # the latter is per-connection and unreliable with the connection pool.
    pid = execute(
        "INSERT INTO media_products (user_id, name) VALUES (%s, %s)",
        (db_user_admin, "_t_tc_product"),
    )
    # 加一条 en item
    iid = execute(
        "INSERT INTO media_items (product_id, user_id, filename, object_key, lang) "
        "VALUES (%s, %s, %s, %s, %s)",
        (pid, db_user_admin, "x.mp4", "k/x.mp4", "en"),
    )
    yield {"product_id": pid, "item_id": iid}
    execute("DELETE FROM media_items WHERE product_id=%s", (pid,))
    execute("DELETE FROM media_products WHERE id=%s", (pid,))


def test_create_parent_task_inserts_parent_and_children(
    db_user_admin, db_user_translator, db_user_raw_processor, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE", "FR"],
        translator_id=db_user_translator,
        raw_processor_id=db_user_raw_processor,
        created_by=db_user_admin,
    )
    parent = query_one("SELECT * FROM tasks WHERE id=%s", (parent_id,))
    assert parent["parent_task_id"] is None
    assert parent["status"] == tasks.PARENT_RAW_IN_PROGRESS
    assert parent["assignee_id"] == db_user_raw_processor
    assert parent["claimed_at"] is not None
    assert parent["media_item_id"] == db_product["item_id"]

    children = query_all(
        "SELECT * FROM tasks WHERE parent_task_id=%s ORDER BY country_code",
        (parent_id,),
    )
    assert len(children) == 2
    assert {c["country_code"] for c in children} == {"DE", "FR"}
    for c in children:
        assert c["status"] == tasks.CHILD_BLOCKED
        assert c["assignee_id"] == db_user_translator
        assert c["media_item_id"] == db_product["item_id"]

    events = query_all(
        "SELECT * FROM task_events WHERE task_id IN (%s) ORDER BY id",
        (parent_id,),
    )
    assert any(e["event_type"] == "created" for e in events)

    # cleanup
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


def test_create_parent_task_supports_per_language_assignments(monkeypatch):
    from appcore import tasks

    class FakeCursor:
        def __init__(self):
            self.lastrowid = 100
            self.rowcount = 1
            self._next_id = 100
            self.executed = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, args=None):
            self.executed.append((sql, args))
            if sql.startswith("INSERT INTO tasks"):
                self.lastrowid = self._next_id
                self._next_id += 1

    class FakeConn:
        def __init__(self):
            self.cursor_obj = FakeCursor()

        def begin(self):
            pass

        def cursor(self):
            return self.cursor_obj

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    conn = FakeConn()
    monkeypatch.setattr(tasks, "get_conn", lambda: conn)
    monkeypatch.setattr(tasks, "get_existing_task_languages_for_item", lambda media_item_id: [])
    monkeypatch.setattr(tasks, "_product_name_for_notification", lambda cur, product_id: "保温杯")
    monkeypatch.setattr(
        tasks,
        "notifications_svc",
        type(
            "FakeNotifications",
            (),
            {
                "notify_parent_assigned": staticmethod(lambda *args, **kwargs: None),
                "notify_pending_raw_task": staticmethod(lambda *args, **kwargs: None),
                "notify_child_blocked": staticmethod(lambda *args, **kwargs: None),
            },
        ),
        raising=False,
    )

    parent_id = tasks.create_parent_task(
        media_product_id=7,
        media_item_id=8,
        countries=["DE", "FR"],
        language_assignments={"de": 9, "FR": 10},
        raw_processor_id=88,
        created_by=1,
    )

    assert parent_id == 100
    inserts = [
        args for sql, args in conn.cursor_obj.executed
        if sql.startswith("INSERT INTO tasks")
    ]
    assert inserts[1][3:6] == ("DE", 9, tasks.CHILD_BLOCKED)
    assert inserts[2][3:6] == ("FR", 10, tasks.CHILD_BLOCKED)


def test_create_parent_task_marks_parent_and_children_urgent(monkeypatch):
    from appcore import tasks

    class FakeCursor:
        def __init__(self):
            self.lastrowid = 100
            self._next_id = 100
            self.executed = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, args=None):
            self.executed.append((sql, args))
            if sql.startswith("INSERT INTO tasks"):
                self.lastrowid = self._next_id
                self._next_id += 1

    class FakeConn:
        def __init__(self):
            self.cursor_obj = FakeCursor()

        def begin(self):
            pass

        def cursor(self):
            return self.cursor_obj

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    conn = FakeConn()
    monkeypatch.setattr(tasks, "get_conn", lambda: conn)
    monkeypatch.setattr(tasks, "get_existing_task_languages_for_item", lambda media_item_id: [])
    monkeypatch.setattr(tasks, "_product_name_for_notification", lambda cur, product_id: "保温杯")
    monkeypatch.setattr(
        tasks,
        "notifications_svc",
        type(
            "FakeNotifications",
            (),
            {
                "notify_parent_assigned": staticmethod(lambda *args, **kwargs: None),
                "notify_pending_raw_task": staticmethod(lambda *args, **kwargs: None),
                "notify_child_blocked": staticmethod(lambda *args, **kwargs: None),
            },
        ),
        raising=False,
    )

    parent_id = tasks.create_parent_task(
        media_product_id=7,
        media_item_id=8,
        countries=["DE", "FR"],
        language_assignments={"DE": 9, "FR": 10},
        raw_processor_id=88,
        created_by=1,
        is_urgent=True,
    )

    assert parent_id == 100
    inserts = [
        (sql, args) for sql, args in conn.cursor_obj.executed
        if sql.startswith("INSERT INTO tasks")
    ]
    assert all("is_urgent" in sql for sql, _args in inserts)
    assert inserts[0][1][4] == 1
    assert inserts[1][1][6] == 1
    assert inserts[2][1][6] == 1
    event_payloads = [
        args[3] for sql, args in conn.cursor_obj.executed
        if sql.startswith("INSERT INTO task_events") and args[1] == "created"
    ]
    assert '"is_urgent": true' in event_payloads[0]


def test_create_parent_task_reuses_ready_raw_source(monkeypatch):
    from appcore import tasks

    class FakeCursor:
        def __init__(self):
            self.lastrowid = 100
            self.rowcount = 1
            self._next_id = 100
            self.executed = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, args=None):
            self.executed.append((sql, args))
            if sql.startswith("INSERT INTO tasks"):
                self.lastrowid = self._next_id
                self._next_id += 1

    class FakeConn:
        def __init__(self):
            self.cursor_obj = FakeCursor()

        def begin(self):
            pass

        def cursor(self):
            return self.cursor_obj

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    notifications = []
    conn = FakeConn()
    monkeypatch.setattr(tasks, "get_conn", lambda: conn)
    monkeypatch.setattr(tasks, "get_existing_task_languages_for_item", lambda media_item_id: [])
    monkeypatch.setattr(tasks, "_product_name_for_notification", lambda cur, product_id: "保温杯")
    monkeypatch.setattr(
        tasks,
        "notifications_svc",
        type(
            "FakeNotifications",
            (),
            {
                "notify_parent_assigned": staticmethod(lambda *args, **kwargs: notifications.append("parent_assigned")),
                "notify_pending_raw_task": staticmethod(lambda *args, **kwargs: notifications.append("pending_raw")),
                "notify_child_blocked": staticmethod(lambda *args, **kwargs: notifications.append("child_blocked")),
                "notify_child_assigned": staticmethod(lambda *args, **kwargs: notifications.append("child_assigned")),
            },
        ),
        raising=False,
    )

    parent_id = tasks.create_parent_task(
        media_product_id=7,
        media_item_id=8,
        countries=["DE", "FR"],
        language_assignments={"DE": 9, "FR": 10},
        raw_processor_id=88,
        reused_raw_source_id=301,
        created_by=1,
    )

    assert parent_id == 100
    inserts = [
        args for sql, args in conn.cursor_obj.executed
        if sql.startswith("INSERT INTO tasks")
    ]
    assert inserts[0][2:4] == (88, tasks.PARENT_RAW_DONE)
    assert inserts[1][3:6] == ("DE", 9, tasks.CHILD_ASSIGNED)
    assert inserts[2][3:6] == ("FR", 10, tasks.CHILD_ASSIGNED)
    events = [
        args[1] for sql, args in conn.cursor_obj.executed
        if sql.startswith("INSERT INTO task_events")
    ]
    assert "raw_source_reused" in events
    assert notifications == ["child_assigned", "child_assigned"]


def test_create_parent_task_rejects_empty_countries(
    db_user_admin, db_user_translator, db_user_raw_processor, db_product
):
    from appcore import tasks
    with pytest.raises(ValueError, match="countries"):
        tasks.create_parent_task(
            media_product_id=db_product["product_id"],
            media_item_id=db_product["item_id"],
            countries=[],
            translator_id=db_user_translator,
            raw_processor_id=db_user_raw_processor,
            created_by=db_user_admin,
        )


def test_create_parent_task_uppercases_countries(
    db_user_admin, db_user_translator, db_user_raw_processor, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["de", "fr"],
        translator_id=db_user_translator,
        raw_processor_id=db_user_raw_processor,
        created_by=db_user_admin,
    )
    children = query_all(
        "SELECT country_code FROM tasks WHERE parent_task_id=%s",
        (parent_id,),
    )
    assert {c["country_code"] for c in children} == {"DE", "FR"}
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


def test_create_parent_task_rejects_missing_language_assignment():
    from appcore import tasks

    with pytest.raises(ValueError, match="language_assignments"):
        tasks.create_parent_task(
            media_product_id=7,
            media_item_id=8,
            countries=["DE", "FR"],
            language_assignments={"DE": 9},
            raw_processor_id=88,
            created_by=1,
        )


def test_claim_parent_succeeds(db_user_admin, db_user_translator, db_product):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    row = query_one("SELECT * FROM tasks WHERE id=%s", (parent_id,))
    assert row["status"] == tasks.PARENT_RAW_IN_PROGRESS
    assert row["assignee_id"] == db_user_admin
    assert row["claimed_at"] is not None
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


def test_claim_parent_already_claimed_raises(
    db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    with pytest.raises(tasks.ConflictError):
        tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_translator)
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


def test_mark_uploaded_transitions_to_review(
    db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    row = query_one("SELECT * FROM tasks WHERE id=%s", (parent_id,))
    assert row["status"] == tasks.PARENT_RAW_REVIEW
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


def test_mark_uploaded_waits_for_manual_review_before_raw_source_sync(monkeypatch):
    from appcore import tasks

    sequence = []

    class FakeCursor:
        rowcount = 0
        row = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, args=()):
            if "SELECT status, assignee_id, media_item_id" in sql:
                self.row = {
                    "status": tasks.PARENT_RAW_IN_PROGRESS,
                    "assignee_id": 9,
                    "media_item_id": 11,
                }
                self.rowcount = 1
                return
            if "UPDATE tasks SET status=%s" in sql:
                sequence.append(("parent_status", args[0]))
                self.rowcount = 1
                return
            if "INSERT INTO task_events" in sql:
                sequence.append(("event", args[1]))
                self.rowcount = 1
                return
            raise AssertionError(sql)

        def fetchone(self):
            return self.row

    class FakeConnection:
        def begin(self):
            sequence.append("begin")

        def cursor(self):
            return FakeCursor()

        def commit(self):
            sequence.append("commit")

        def rollback(self):
            sequence.append("rollback")

        def close(self):
            sequence.append("close")

    monkeypatch.setattr(tasks, "get_conn", lambda: FakeConnection())
    monkeypatch.setattr(
        tasks,
        "complete_raw_parent_if_ready",
        lambda **kwargs: pytest.fail("raw source sync must wait for manual approval"),
    )

    tasks.mark_uploaded(task_id=501, actor_user_id=9)

    assert ("parent_status", tasks.PARENT_RAW_REVIEW) in sequence
    assert ("event", "raw_uploaded") in sequence


def test_mark_uploaded_requires_media_item(
    db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=None,                    # 故意不绑定
        countries=["DE"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    with pytest.raises(tasks.StateError, match="media_item"):
        tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


def test_approve_raw_requires_review_status_before_raw_source_sync(monkeypatch):
    from appcore import tasks

    monkeypatch.setattr(
        tasks,
        "query_one",
        lambda sql, args=(): {
            "id": args[0],
            "status": tasks.PARENT_RAW_IN_PROGRESS,
            "assignee_id": 11,
        },
    )
    monkeypatch.setattr(
        tasks,
        "complete_raw_parent_if_ready",
        lambda **kwargs: pytest.fail("raw source sync must not run before raw review"),
    )

    with pytest.raises(tasks.StateError, match="raw_review"):
        tasks.approve_raw(task_id=501, actor_user_id=11)


def test_approve_raw_unblocks_children(
    db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE", "FR"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.approve_raw(task_id=parent_id, actor_user_id=db_user_admin)

    parent = query_one("SELECT * FROM tasks WHERE id=%s", (parent_id,))
    assert parent["status"] == tasks.PARENT_RAW_DONE

    children = query_all(
        "SELECT * FROM tasks WHERE parent_task_id=%s", (parent_id,)
    )
    assert all(c["status"] == tasks.CHILD_ASSIGNED for c in children)

    events = query_all(
        "SELECT event_type FROM task_events "
        "WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)",
        (parent_id, parent_id),
    )
    types = [e["event_type"] for e in events]
    assert "auto_completed" in types
    assert types.count("unblocked") >= 2
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


def test_reject_raw_returns_to_in_progress_with_same_assignee(
    db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    execute(
        "UPDATE tasks SET status=%s WHERE id=%s",
        (tasks.PARENT_RAW_REVIEW, parent_id),
    )
    tasks.reject_raw(task_id=parent_id, actor_user_id=db_user_admin,
                     reason="字幕没去干净请重做一遍谢谢")
    row = query_one("SELECT * FROM tasks WHERE id=%s", (parent_id,))
    assert row["status"] == tasks.PARENT_RAW_IN_PROGRESS
    assert row["assignee_id"] == db_user_admin
    assert "字幕没去干净" in row["last_reason"]
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


def test_reject_raw_requires_min_reason(
    db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    with pytest.raises(ValueError, match="reason"):
        tasks.reject_raw(task_id=parent_id, actor_user_id=db_user_admin, reason="短")
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


def test_cancel_parent_does_not_cascade_children(
    db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE", "FR", "JA"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    # 走一遍领取父任务，再模拟 DE 子任务已完成，验证取消只影响当前父任务。
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    de_id = query_one(
        "SELECT id FROM tasks WHERE parent_task_id=%s AND country_code='DE'",
        (parent_id,),
    )["id"]
    execute("UPDATE tasks SET status='done', completed_at=NOW() WHERE id=%s", (de_id,))

    tasks.cancel_parent(task_id=parent_id, actor_user_id=db_user_admin,
                        reason="商品已下架，整体取消")

    parent = query_one("SELECT * FROM tasks WHERE id=%s", (parent_id,))
    assert parent["status"] == tasks.PARENT_CANCELLED
    assert parent["cancelled_at"] is not None

    de = query_one("SELECT * FROM tasks WHERE id=%s", (de_id,))
    assert de["status"] == tasks.CHILD_DONE     # 已 done 保留

    others = query_all(
        "SELECT * FROM tasks WHERE parent_task_id=%s AND id<>%s",
        (parent_id, de_id),
    )
    assert all(c["status"] == tasks.CHILD_BLOCKED for c in others)
    assert all(c["cancelled_at"] is None for c in others)
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


def test_cancel_parent_service_only_updates_current_parent(monkeypatch):
    import json

    from appcore import tasks

    sequence = []

    class FakeCursor:
        def __init__(self):
            self.rowcount = 1
            self.rows = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, args=()):
            normalized = " ".join(str(sql).split())
            if normalized.startswith("UPDATE tasks SET status=%s, last_reason=%s") and "parent_task_id IS NULL" in normalized:
                sequence.append(("parent_update", args))
                self.rowcount = 1
                self.rows = []
                return
            if normalized.startswith("SELECT id FROM tasks WHERE parent_task_id=%s"):
                sequence.append(("select_children", args))
                self.rows = [{"id": 101}, {"id": 102}]
                return
            if "WHERE id IN" in normalized:
                sequence.append(("child_update", args))
                self.rowcount = 2
                return
            if normalized.startswith("INSERT INTO task_events"):
                payload = json.loads(args[3]) if args[3] else None
                sequence.append(("event", args[0], args[1], payload))
                return
            sequence.append(("other", normalized, args))

        def fetchall(self):
            return list(self.rows)

    class FakeConn:
        def begin(self):
            sequence.append(("begin",))

        def cursor(self):
            return FakeCursor()

        def commit(self):
            sequence.append(("commit",))

        def rollback(self):
            sequence.append(("rollback",))

        def close(self):
            sequence.append(("close",))

    monkeypatch.setattr(tasks, "get_conn", lambda: FakeConn())

    tasks.cancel_parent(
        task_id=77,
        actor_user_id=1,
        reason="只取消当前任务不联动",
    )

    assert ("parent_update", (
        tasks.PARENT_CANCELLED,
        "只取消当前任务不联动",
        77,
        tasks.PARENT_PENDING,
        tasks.PARENT_RAW_IN_PROGRESS,
        tasks.PARENT_RAW_REVIEW,
        tasks.PARENT_RAW_DONE,
    )) in sequence
    assert not any(item[0] == "select_children" for item in sequence)
    assert not any(item[0] == "child_update" for item in sequence)
    assert ("event", 77, "cancelled", {"reason": "只取消当前任务不联动"}) in sequence


def test_submit_child_passes_with_ready(
    monkeypatch, db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.approve_raw(task_id=parent_id, actor_user_id=db_user_admin)
    child_id = query_one(
        "SELECT id FROM tasks WHERE parent_task_id=%s AND country_code='DE'",
        (parent_id,),
    )["id"]
    # Stub readiness: 假装产物齐全 + 假装目标语种 item 存在
    monkeypatch.setattr(tasks, "_find_child_task_target_lang_item",
                        lambda **kwargs: {"id": 1, "object_key": "x", "cover_object_key": "c", "lang": "de", "product_id": db_product["product_id"]})
    monkeypatch.setattr("appcore.pushes.compute_readiness",
                        lambda i, p: {"has_object": True, "has_cover": True,
                                      "has_copywriting": True, "has_push_texts": True,
                                      "is_listed": True, "lang_supported": True,
                                      "shopify_image_confirmed": True})
    monkeypatch.setattr(
        tasks,
        "_detail_images_status",
        lambda product_id, lang: {"ok": True, "required": False, "reason": ""},
    )
    monkeypatch.setattr(
        tasks,
        "_product_link_availability_status",
        lambda product_id, lang, product: {"ok": True, "required": True, "reason": ""},
    )

    tasks.submit_child(task_id=child_id, actor_user_id=db_user_translator)
    row = query_one("SELECT * FROM tasks WHERE id=%s", (child_id,))
    assert row["status"] == tasks.CHILD_REVIEW
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


def test_submit_child_fails_when_not_ready(
    monkeypatch, db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.approve_raw(task_id=parent_id, actor_user_id=db_user_admin)
    child_id = query_one(
        "SELECT id FROM tasks WHERE parent_task_id=%s AND country_code='DE'",
        (parent_id,),
    )["id"]
    monkeypatch.setattr(tasks, "_find_child_task_target_lang_item",
                        lambda **kwargs: {"id": 1, "lang": "de", "product_id": db_product["product_id"]})
    monkeypatch.setattr("appcore.pushes.compute_readiness",
                        lambda i, p: {"has_video": True, "has_cover": False,
                                      "has_copywriting": False})
    monkeypatch.setattr("appcore.pushes.is_ready", lambda r: False)
    monkeypatch.setattr(
        tasks,
        "_detail_images_status",
        lambda product_id, lang: {"ok": True, "required": False, "reason": ""},
    )
    monkeypatch.setattr(
        tasks,
        "_product_link_availability_status",
        lambda product_id, lang, product: {"ok": True, "required": True, "reason": ""},
    )

    with pytest.raises(tasks.NotReadyError) as exc:
        tasks.submit_child(task_id=child_id, actor_user_id=db_user_translator)
    assert "has_cover" in str(exc.value.missing) or "has_cover" in str(exc.value)
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


def test_submit_child_fails_when_detail_images_not_ready(monkeypatch):
    from appcore import tasks

    monkeypatch.setattr(
        tasks,
        "query_one",
        lambda sql, args=(): {
            "id": 44,
            "parent_task_id": 10,
            "media_product_id": 9,
            "country_code": "DE",
            "status": tasks.CHILD_ASSIGNED,
            "assignee_id": 2,
        },
    )

    monkeypatch.setattr(
        tasks,
        "_find_child_task_target_lang_item",
        lambda **kwargs: {
            "id": 1,
            "object_key": "x",
            "cover_object_key": "c",
            "lang": "de",
            "product_id": 9,
        },
    )
    monkeypatch.setattr(
        "appcore.pushes.compute_readiness",
        lambda i, p: {
            "has_object": True,
            "has_cover": True,
            "has_copywriting": True,
            "has_push_texts": True,
            "is_listed": True,
            "lang_supported": True,
            "shopify_image_confirmed": True,
        },
    )
    monkeypatch.setattr("appcore.pushes.is_ready", lambda r: True)
    monkeypatch.setattr(
        tasks,
        "_detail_images_status",
        lambda product_id, lang: {
            "ok": False,
            "required": True,
            "reason": "英文详情图 2 张，目标语种详情图 0 张",
        },
    )
    monkeypatch.setattr(
        tasks,
        "_product_link_availability_status",
        lambda product_id, lang, product: {"ok": True, "required": True, "reason": ""},
    )
    monkeypatch.setattr(tasks, "_find_product", lambda product_id: {"id": product_id})
    monkeypatch.setattr(tasks, "_manual_confirmed_child_step_keys", lambda task_id: set())

    with pytest.raises(tasks.NotReadyError) as exc:
        tasks.submit_child(task_id=44, actor_user_id=2)

    assert "detail_images" in exc.value.missing


def test_submit_child_ignores_manual_confirmations_when_step_result_missing(monkeypatch):
    from appcore import tasks

    monkeypatch.setattr(
        tasks,
        "query_one",
        lambda sql, args=(): {
            "id": 44,
            "parent_task_id": 10,
            "media_product_id": 9,
            "country_code": "DE",
            "status": tasks.CHILD_ASSIGNED,
            "assignee_id": 2,
        },
    )
    monkeypatch.setattr(
        tasks,
        "_find_child_task_target_lang_item",
        lambda **kwargs: {
            "id": 1,
            "object_key": "x",
            "cover_object_key": "c",
            "lang": "de",
            "product_id": 9,
        },
    )
    monkeypatch.setattr(tasks, "_find_product", lambda product_id: {"id": product_id})
    monkeypatch.setattr(tasks, "_manual_confirmed_child_step_keys", lambda task_id: {"detail_images"})
    monkeypatch.setattr(
        "appcore.pushes.compute_readiness",
        lambda i, p: {
            "has_object": True,
            "has_cover": True,
            "has_copywriting": True,
            "has_push_texts": True,
            "is_listed": True,
            "lang_supported": True,
            "shopify_image_confirmed": True,
        },
    )
    monkeypatch.setattr(
        tasks,
        "_detail_images_status",
        lambda product_id, lang: {
            "ok": False,
            "required": True,
            "reason": "英文详情图 2 张，目标语种详情图 0 张",
        },
    )
    monkeypatch.setattr(
        tasks,
        "_product_link_availability_status",
        lambda product_id, lang, product: {"ok": True, "required": True, "reason": "", "links": []},
    )
    monkeypatch.setattr(
        tasks,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("submit should fail before opening a DB transaction")),
    )

    with pytest.raises(tasks.NotReadyError) as exc:
        tasks.submit_child(task_id=44, actor_user_id=2)

    assert "detail_images" in exc.value.missing


def test_child_acceptance_payload_marks_only_result_steps_as_manual_submittable(monkeypatch):
    from appcore import tasks

    monkeypatch.setattr(
        tasks,
        "_detail_images_status",
        lambda product_id, lang: {"ok": False, "required": True, "reason": "missing detail"},
    )
    monkeypatch.setattr(
        tasks,
        "_product_link_availability_status",
        lambda product_id, lang, product: {"ok": False, "required": True, "reason": "link down", "links": []},
    )

    payload = tasks._child_acceptance_payload(
        task_id=44,
        row={
            "id": 44,
            "media_product_id": 9,
            "country_code": "DE",
            "product_code": "demo-rjc",
        },
        item={
            "id": 1,
            "product_id": 9,
            "lang": "de",
            "object_key": "",
            "cover_object_key": "",
        },
        product={"id": 9, "product_code": "demo-rjc"},
        readiness={
            "has_object": False,
            "has_cover": False,
            "has_copywriting": False,
            "has_push_texts": False,
            "is_listed": False,
            "lang_supported": False,
            "shopify_image_confirmed": False,
        },
        include_evidence=False,
        manual_confirmed_keys=set(),
    )

    by_key = {check["key"]: check for check in payload["checks"]}
    assert by_key["localized_media_item"]["manual_output"]["kind"] == "video"
    assert by_key["translated_video"]["manual_output"]["kind"] == "video"
    assert by_key["translated_cover"]["manual_output"]["kind"] == "image"
    assert by_key["translated_copywriting"]["manual_output"]["kind"] == "text"
    assert by_key["push_texts"]["manual_output"]["kind"] == "text"
    assert by_key["detail_images"]["manual_output"]["kind"] == "images"

    for status_key in ("product_listed", "language_supported", "shopify_images", "product_links"):
        assert "manual_output" not in by_key[status_key]


def test_detail_images_status_keeps_all_target_image_evidence(monkeypatch):
    from appcore import medias, tasks

    source_rows = [
        {"id": 100 + idx, "object_key": f"1/medias/9/en-{idx}.jpg"}
        for idx in range(1, 11)
    ]
    target_rows = [
        {
            "id": 200 + idx,
            "object_key": f"1/medias/9/de-{idx}.jpg",
            "file_size": idx * 1000,
            "width": 800,
            "height": 1200,
        }
        for idx in range(1, 11)
    ]

    def fake_list_detail_images(product_id, lang):
        assert product_id == 9
        return source_rows if lang == "en" else target_rows

    monkeypatch.setattr(medias, "list_detail_images", fake_list_detail_images)
    monkeypatch.setattr(medias, "detail_image_is_gif", lambda row: False)

    status = tasks._detail_images_status(9, "DE")

    assert status["source_count"] == 10
    assert status["target_count"] == 10
    assert len(status["evidence"]) == 10
    assert [item["detail_image_id"] for item in status["evidence"]] == list(range(201, 211))


def test_submit_child_step_manual_output_reconciles_completion(monkeypatch):
    from appcore import medias, tasks

    events = []
    def fake_query_one(sql, args=()):
        if "FROM media_items" in sql and "WHERE id=%s" in sql:
            return {"id": 1093, "source_raw_id": 251}
        if "media_items" in sql:
            return None
        return {
            "id": 44,
            "assignee_id": 2,
            "status": tasks.CHILD_ASSIGNED,
            "media_product_id": 9,
            "country_code": "DE",
        }
    monkeypatch.setattr(tasks, "query_one", fake_query_one)
    monkeypatch.setattr(
        medias,
        "create_item",
        lambda *args, **kwargs: events.append(("media_item", args, kwargs)) or 301,
    )
    monkeypatch.setattr(
        medias,
        "find_current_item_by_source",
        lambda **kwargs: events.append(("find_current", kwargs)) and None,
    )
    monkeypatch.setattr(
        tasks,
        "_record_manual_output_event",
        lambda **kwargs: events.append(("event", kwargs)),
    )
    monkeypatch.setattr(
        tasks,
        "complete_child_if_ready",
        lambda **kwargs: events.append(("complete", kwargs)) or {"completed": True, "missing": []},
    )

    result = tasks.submit_child_step_manual_output(
        task_id=44,
        step_key="translated_video",
        actor_user_id=2,
        files=[{"filename": "manual.mp4", "object_key": "1/medias/manual.mp4", "file_size": 123}],
    )

    assert result["media_item_id"] == 301
    assert result["completion"] == {"completed": True, "missing": []}
    assert events[-1] == ("complete", {"task_id": 44, "actor_user_id": 2})


def test_submit_child_step_manual_output_archives_and_replaces_same_source_lang(monkeypatch):
    from appcore import medias, tasks

    events = []

    def fake_query_one(sql, args=()):
        if "FROM tasks" in sql:
            return {
                "id": 44,
                "assignee_id": 2,
                "status": tasks.CHILD_ASSIGNED,
                "media_product_id": 9,
                "media_item_id": 1093,
                "country_code": "DE",
            }
        if "FROM media_items" in sql and "WHERE id=%s" in sql:
            return {"id": 1093, "source_raw_id": 251}
        return None

    def fake_create_item(*args, **kwargs):
        raise AssertionError("same source/lang should replace the current media item")

    def fake_find_current_item_by_source(**kwargs):
        events.append(("find_current", kwargs))
        return {
            "id": 301,
            "product_id": 9,
            "lang": "de",
            "source_raw_id": 251,
        }

    def fake_archive_and_replace_item_version(item_id, **kwargs):
        events.append(("archive_replace", item_id, kwargs))
        return {"version_id": 77, "media_item_id": item_id, "version_no": 2}

    def fake_execute(sql, args=()):
        events.append(("execute", sql, args))
        return 1

    monkeypatch.setattr(tasks, "query_one", fake_query_one)
    monkeypatch.setattr(tasks, "execute", fake_execute)
    monkeypatch.setattr(medias, "find_current_item_by_source", fake_find_current_item_by_source)
    monkeypatch.setattr(medias, "archive_and_replace_item_version", fake_archive_and_replace_item_version)
    monkeypatch.setattr(medias, "create_item", fake_create_item)
    monkeypatch.setattr(
        tasks,
        "_record_manual_output_event",
        lambda **kwargs: events.append(("event", kwargs)),
    )
    monkeypatch.setattr(
        tasks,
        "complete_child_if_ready",
        lambda **kwargs: events.append(("complete", kwargs)) or {"completed": True, "missing": []},
    )

    result = tasks.submit_child_step_manual_output(
        task_id=44,
        step_key="translated_video",
        actor_user_id=2,
        files=[{"filename": "same-name.mp4", "object_key": "1/medias/same-name.mp4", "file_size": 123}],
    )

    assert result["media_item_id"] == 301
    assert result["archived_version_id"] == 77
    assert result["source_raw_id"] == 251
    assert ("find_current", {"product_id": 9, "lang": "de", "source_raw_id": 251}) in events
    assert (
        "archive_replace",
        301,
        {
            "actor_user_id": 2,
            "filename": "same-name.mp4",
            "object_key": "1/medias/same-name.mp4",
            "display_name": "same-name.mp4",
            "file_size": 123,
            "task_id": 44,
        },
    ) in events
    assert not [event for event in events if event[0] == "media_item"]
    assert not [
        event for event in events
        if event[0] == "execute" and "UPDATE media_items SET source_raw_id" in event[1]
    ]


def test_submit_child_step_manual_output_allows_done_status_and_creates_item_without_current_source_version(monkeypatch):
    from appcore import medias, tasks

    events = []
    
    # 模拟 query_one 寻找 existing_item 或者是任务详情
    # 查找已有 item (假设之前有一条 id=301 的旧 item 记录)
    def fake_query_one(sql, args=()):
        if "FROM tasks" in sql:
            return {
                "id": 44,
                "assignee_id": 2,
                "status": tasks.CHILD_DONE,
                "media_product_id": 9,
                "media_item_id": 1093,
                "country_code": "DE",
            }
        if "FROM media_items" in sql and "id=%s" in sql:
            return {"id": 1093, "source_raw_id": 251}
        return None
    monkeypatch.setattr(tasks, "query_one", fake_query_one)
    monkeypatch.setattr(
        medias,
        "find_current_item_by_source",
        lambda **kwargs: events.append(("find_current", kwargs)) and None,
    )
    monkeypatch.setattr(
        medias,
        "create_item",
        lambda *args, **kwargs: events.append(("media_item", args, kwargs)) or 302,
    )

    # 拦截 execute，记录是否被 UPDATE 了，并且检查 `deleted_at=NULL` 和 `id=301` 传参
    monkeypatch.setattr(
        tasks,
        "execute",
        lambda sql, args=(): events.append(("execute", sql, args)) or 1,
    )
    
    # 拦截后台构建缩略图和推送状态刷新的调用，避免跑多线程里的真正网络下载和 db 变动
    monkeypatch.setattr(
        "web.services.media_items.build_item_thumbnail",
        lambda *args, **kwargs: events.append(("build_thumbnail", args)),
    )
    monkeypatch.setattr(
        "appcore.pushes._refresh_push_status_cache_for_item_safely",
        lambda item_id: events.append(("refresh_cache", item_id)),
    )

    monkeypatch.setattr(
        tasks,
        "_record_manual_output_event",
        lambda **kwargs: events.append(("event", kwargs)),
    )
    monkeypatch.setattr(
        tasks,
        "complete_child_if_ready",
        lambda **kwargs: events.append(("complete", kwargs)) or {"completed": True, "missing": []},
    )

    # 调用重传逻辑，测试用例应当正常执行通过！
    result = tasks.submit_child_step_manual_output(
        task_id=44,
        step_key="translated_video",
        actor_user_id=2,
        files=[{"filename": "new-manual.mp4", "object_key": "1/medias/new-manual.mp4", "file_size": 456}],
    )

    assert result["media_item_id"] == 302
    assert result["object_key"] == "1/medias/new-manual.mp4"
    assert result["source_raw_id"] == 251
    media_events = [ev for ev in events if ev[0] == "media_item"]
    assert len(media_events) == 1
    assert media_events[0][2]["task_id"] == 44

    # 检查数据库是否执行了 UPDATE 覆盖更新和 un-soft-delete 动作
    filename_updates = [
        ev for ev in events
        if ev[0] == "execute" and "UPDATE media_items SET filename" in ev[1]
    ]
    assert filename_updates == []
    assert (
        "execute",
        "UPDATE media_items SET source_raw_id=%s WHERE id=%s",
        (251, 302),
    ) in events


def test_submit_child_step_manual_cover_does_not_use_other_task_video(monkeypatch):
    import pytest
    from appcore import tasks

    def fake_query_one(sql, args=()):
        if "FROM tasks" in sql:
            return {
                "id": 44,
                "assignee_id": 2,
                "status": tasks.CHILD_ASSIGNED,
                "media_product_id": 9,
                "media_item_id": 1093,
                "country_code": "DE",
            }
        if "FROM media_items" in sql:
            return None
        return None

    monkeypatch.setattr(tasks, "query_one", fake_query_one)
    monkeypatch.setattr(
        tasks,
        "_find_target_lang_item",
        lambda product_id, lang: {
            "id": 301,
            "product_id": product_id,
            "lang": lang,
            "task_id": 41,
            "object_key": "other-task.mp4",
        },
    )

    with pytest.raises(ValueError, match="target media item required before cover"):
        tasks.submit_child_step_manual_output(
            task_id=44,
            step_key="translated_cover",
            actor_user_id=2,
            files=[{"filename": "cover.png", "object_key": "1/medias/cover.png", "file_size": 123}],
        )


def test_submit_child_step_manual_cover_updates_current_task_video_cover(monkeypatch):
    from appcore import medias, tasks

    events = []

    def fake_query_one(sql, args=()):
        if "FROM tasks" in sql:
            return {
                "id": 44,
                "assignee_id": 2,
                "status": tasks.CHILD_ASSIGNED,
                "media_product_id": 9,
                "media_item_id": 1093,
                "country_code": "DE",
            }
        if "FROM media_items" in sql and "task_id=%s" in sql:
            return {
                "id": 302,
                "product_id": 9,
                "lang": "de",
                "task_id": 44,
                "object_key": "1/medias/current-task-video.mp4",
            }
        raise AssertionError(sql)

    monkeypatch.setattr(tasks, "query_one", fake_query_one)
    monkeypatch.setattr(
        medias,
        "update_item_cover",
        lambda item_id, object_key: events.append(("cover", item_id, object_key)) or 1,
    )
    monkeypatch.setattr(
        tasks,
        "_record_manual_output_event",
        lambda **kwargs: events.append(("event", kwargs)),
    )
    monkeypatch.setattr(
        tasks,
        "complete_child_if_ready",
        lambda **kwargs: events.append(("complete", kwargs)) or {"completed": False, "missing": []},
    )
    monkeypatch.setattr(
        "appcore.pushes._refresh_push_status_cache_for_item_safely",
        lambda item_id: events.append(("refresh_cache", item_id)),
    )

    result = tasks.submit_child_step_manual_output(
        task_id=44,
        step_key="translated_cover",
        actor_user_id=2,
        files=[{"filename": "cover.png", "object_key": "1/medias/current-task-cover.png"}],
    )

    assert result["media_item_id"] == 302
    assert result["object_key"] == "1/medias/current-task-cover.png"
    assert ("cover", 302, "1/medias/current-task-cover.png") in events


def test_submit_child_moves_ready_task_to_review(monkeypatch):
    from appcore import tasks

    class FakeCursor:
        def __init__(self):
            self.rowcount = 1
            self.executed = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, args=None):
            self.executed.append((sql, args))
            self.rowcount = 1

    class FakeConn:
        def __init__(self):
            self.cursor_obj = FakeCursor()

        def begin(self):
            pass

        def cursor(self):
            return self.cursor_obj

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    conn = FakeConn()
    monkeypatch.setattr(
        tasks,
        "query_one",
        lambda sql, args=(): {
            "id": 44,
            "parent_task_id": 10,
            "media_product_id": 9,
            "country_code": "DE",
            "status": tasks.CHILD_ASSIGNED,
            "assignee_id": 2,
        },
    )
    monkeypatch.setattr(
        tasks,
        "_find_child_task_target_lang_item",
        lambda **kwargs: {
            "id": 1,
            "object_key": "x",
            "cover_object_key": "c",
            "lang": "de",
            "product_id": 9,
        },
    )
    monkeypatch.setattr(tasks, "_find_product", lambda product_id: {"id": product_id})
    monkeypatch.setattr(tasks, "_manual_confirmed_child_step_keys", lambda task_id: set())
    monkeypatch.setattr(
        "appcore.pushes.compute_readiness",
        lambda i, p: {
            "has_object": True,
            "has_cover": True,
            "has_copywriting": True,
            "has_push_texts": True,
            "is_listed": True,
            "lang_supported": True,
            "shopify_image_confirmed": True,
        },
    )
    monkeypatch.setattr(
        tasks,
        "_detail_images_status",
        lambda product_id, lang: {"ok": True, "required": True, "reason": ""},
    )
    monkeypatch.setattr(
        tasks,
        "_product_link_availability_status",
        lambda product_id, lang, product: {"ok": True, "required": True, "reason": "", "links": []},
    )
    monkeypatch.setattr(tasks, "get_conn", lambda: conn)

    tasks.submit_child(task_id=44, actor_user_id=2)

    assert any(
        "UPDATE tasks SET status=%s" in sql
        and args == (tasks.CHILD_REVIEW, 44, tasks.CHILD_ASSIGNED)
        for sql, args in conn.cursor_obj.executed
    )
    assert not any(
        "completed_at=COALESCE(completed_at, NOW())" in sql
        for sql, _args in conn.cursor_obj.executed
    )
    assert any(
        "INSERT INTO task_events" in sql and args[1] == "submitted"
        for sql, args in conn.cursor_obj.executed
    )


def test_reject_child_from_push_reopens_done_child_and_records_issue_payload(monkeypatch):
    import json

    from appcore import tasks

    sequence = []

    class FakeCursor:
        def __init__(self):
            self.rowcount = 1
            self.rows = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, args=()):
            if "FROM tasks WHERE id=%s AND parent_task_id IS NOT NULL FOR UPDATE" in sql:
                self.rows = [{
                    "id": 44,
                    "parent_task_id": 10,
                    "status": tasks.CHILD_DONE,
                    "media_product_id": 9,
                }]
                sequence.append(("select_child", args))
                return
            if "UPDATE tasks SET status=%s, last_reason=%s, completed_at=NULL" in sql:
                self.rowcount = 1
                sequence.append(("update_child", args))
                return
            if "UPDATE tasks SET status=%s, completed_at=NULL" in sql and "parent_task_id IS NULL" in sql:
                self.rowcount = 1
                sequence.append(("reopen_parent", args))
                return
            if "INSERT INTO task_events" in sql:
                payload = json.loads(args[3]) if args[3] else {}
                sequence.append(("event", args[0], args[1], payload))
                self.rowcount = 1
                return
            if "SELECT media_product_id FROM tasks" in sql:
                self.rows = [{"media_product_id": 9}]
                sequence.append(("select_product_id", args))
                return
            if "SELECT name FROM media_products" in sql:
                self.rows = [{"name": "硬币收纳盒"}]
                sequence.append(("select_product_name", args))
                return
            raise AssertionError(sql)

        def fetchone(self):
            return self.rows[0] if self.rows else None

    class FakeConn:
        def __init__(self):
            self.cursor_obj = FakeCursor()

        def begin(self):
            sequence.append("begin")

        def cursor(self):
            return self.cursor_obj

        def commit(self):
            sequence.append("commit")

        def rollback(self):
            sequence.append("rollback")

        def close(self):
            sequence.append("close")

    notifications = []
    monkeypatch.setattr(tasks, "get_conn", lambda: FakeConn())
    monkeypatch.setattr(
        tasks.notifications_svc,
        "notify_child_rejected",
        lambda cur, **kwargs: notifications.append(kwargs),
    )

    result = tasks.reject_child_from_push(
        task_id=44,
        actor_user_id=1,
        issue_keys=["has_object", "has_push_texts"],
        reason="视频字幕错位，英文文案格式也不对",
    )

    update_child = next(item for item in sequence if item[0] == "update_child")
    assert update_child[1][0] == tasks.CHILD_ASSIGNED
    assert update_child[1][2] == 44
    assert "管理员已拒绝" in update_child[1][1]
    assert "视频字幕错位" in update_child[1][1]
    event = next(item for item in sequence if item[0] == "event" and item[2] == "push_rework_rejected")
    assert event[3]["issue_keys"] == ["has_object", "has_push_texts"]
    assert event[3]["task_check_keys"] == ["translated_video", "push_texts"]
    assert event[3]["issue_labels"] == ["视频", "英文文案格式"]
    assert ("reopen_parent", (tasks.PARENT_RAW_DONE, 10, tasks.PARENT_ALL_DONE)) in sequence
    assert notifications == [{"task_id": 44, "product_name": "硬币收纳盒"}]
    assert result["status"] == tasks.CHILD_ASSIGNED
    assert result["issue_keys"] == ["has_object", "has_push_texts"]


def test_record_push_material_approved_writes_task_event(monkeypatch):
    import json

    from appcore import tasks

    captured = {}

    def fake_execute(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return 1

    monkeypatch.setattr(tasks, "execute", fake_execute)

    result = tasks.record_push_material_approved(
        task_id=44,
        actor_user_id=1,
        item_id=7,
        product_code="demo-rjc",
        lang="de",
        upstream_status=201,
    )

    payload = json.loads(captured["args"][3])
    assert "INSERT INTO task_events" in captured["sql"]
    assert captured["args"][0] == 44
    assert captured["args"][1] == "push_material_approved"
    assert captured["args"][2] == 1
    assert payload == {
        "source": "push_management",
        "item_id": 7,
        "product_code": "demo-rjc",
        "lang": "de",
        "upstream_status": 201,
    }
    assert result["event_type"] == "push_material_approved"
    assert result["task_id"] == 44


def test_complete_raw_parent_if_ready_marks_parent_done_and_unblocks_children(monkeypatch):
    from appcore import tasks
    from appcore import task_raw_source_bridge as bridge

    sequence = []

    class FakeCursor:
        rowcount = 1
        rows = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, args=()):
            if "UPDATE tasks SET status=%s" in sql and "parent_task_id IS NULL" in sql:
                sequence.append(("parent_done", args[0], args[1]))
                self.rowcount = 1
                return
            if "SELECT id FROM tasks WHERE parent_task_id" in sql:
                sequence.append("select_children")
                self.rows = [{"id": 701}, {"id": 702}]
                self.rowcount = 2
                return
            if "UPDATE tasks SET status=%s" in sql and "WHERE id IN" in sql:
                sequence.append(("children_assigned", args[0], args[1:]))
                self.rowcount = 2
                return
            if "INSERT INTO task_events" in sql:
                sequence.append(("event", args[0], args[1]))
                self.rowcount = 1
                return
            if "SELECT media_product_id FROM tasks" in sql:
                self.rows = [{"media_product_id": 9}]
                return
            if "SELECT name FROM media_products" in sql:
                self.rows = [{"name": "demo product"}]
                return
            raise AssertionError(sql)

        def fetchall(self):
            return list(self.rows)

        def fetchone(self):
            return self.rows[0] if self.rows else None

    class FakeConn:
        def begin(self):
            sequence.append("begin")

        def cursor(self):
            return FakeCursor()

        def commit(self):
            sequence.append("commit")

        def rollback(self):
            sequence.append("rollback")

        def close(self):
            sequence.append("close")

    monkeypatch.setattr(tasks, "get_conn", lambda: FakeConn())
    monkeypatch.setattr(
        bridge,
        "ensure_raw_source_for_parent_task",
        lambda **kwargs: sequence.append("raw_source_ready")
        or {"raw_source_id": 301, "created": True, "updated": False},
    )
    monkeypatch.setattr(
        tasks,
        "notifications_svc",
        type(
            "FakeNotifications",
            (),
            {
                "notify_child_assigned": staticmethod(
                    lambda cur, *, task_id, product_name: sequence.append(("child_notified", task_id, product_name))
                )
            },
        ),
        raising=False,
    )

    result = tasks.complete_raw_parent_if_ready(task_id=501, actor_user_id=11)

    assert result == {"completed": True, "raw_source_id": 301}
    assert "raw_source_ready" in sequence
    assert ("parent_done", tasks.PARENT_RAW_DONE, 501) in sequence
    assert ("children_assigned", tasks.CHILD_ASSIGNED, (701, 702)) in sequence
    assert ("event", 501, "auto_completed") in sequence


def test_recover_pending_manual_raw_results_completes_stalled_manual_upload(monkeypatch):
    from appcore import tasks

    queried = []
    completed = []

    def fake_query_all(sql, args=()):
        queried.append((sql, args))
        return [{"task_id": 501, "actor_user_id": 11, "manual_event_id": 901}]

    monkeypatch.setattr(tasks, "query_all", fake_query_all)
    monkeypatch.setattr(
        tasks,
        "complete_raw_parent_if_ready",
        lambda **kwargs: completed.append(kwargs) or {"completed": True, "raw_source_id": 301},
    )

    result = tasks.recover_pending_manual_raw_results(limit=5)

    assert result == {
        "scanned": 1,
        "completed": 1,
        "failed": 0,
        "task_ids": [501],
        "errors": [],
    }
    assert queried[0][1] == (tasks.PARENT_RAW_REVIEW, 5)
    assert completed == [
        {
            "task_id": 501,
            "actor_user_id": 11,
            "approved_actor_user_id": 11,
        }
    ]


def test_submit_child_fails_when_target_lang_item_missing(
    monkeypatch, db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.approve_raw(task_id=parent_id, actor_user_id=db_user_admin)
    child_id = query_one(
        "SELECT id FROM tasks WHERE parent_task_id=%s AND country_code='DE'",
        (parent_id,),
    )["id"]
    monkeypatch.setattr(tasks, "_find_child_task_target_lang_item", lambda **kwargs: None)
    with pytest.raises(tasks.NotReadyError, match="lang_item_missing|missing"):
        tasks.submit_child(task_id=child_id, actor_user_id=db_user_translator)
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


def test_submit_child_auto_done_keeps_parent_raw_task_completed(
    monkeypatch, db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE", "FR"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.approve_raw(task_id=parent_id, actor_user_id=db_user_admin)
    monkeypatch.setattr(tasks, "_find_child_task_target_lang_item",
                        lambda **kwargs: {
                            "id": 1,
                            "object_key": "x",
                            "cover_object_key": "c",
                            "lang": "de",
                            "product_id": db_product["product_id"],
                        })
    monkeypatch.setattr("appcore.pushes.compute_readiness",
                        lambda i, p: {"has_object": True, "has_cover": True,
                                      "has_copywriting": True, "has_push_texts": True,
                                      "is_listed": True, "lang_supported": True,
                                      "shopify_image_confirmed": True})
    monkeypatch.setattr(
        tasks,
        "_detail_images_status",
        lambda product_id, lang: {"ok": True, "required": False, "reason": ""},
    )
    monkeypatch.setattr(
        tasks,
        "_product_link_availability_status",
        lambda product_id, lang, product: {"ok": True, "required": True, "reason": "", "links": []},
    )

    de_id, fr_id = (
        query_one("SELECT id FROM tasks WHERE parent_task_id=%s AND country_code='DE'", (parent_id,))["id"],
        query_one("SELECT id FROM tasks WHERE parent_task_id=%s AND country_code='FR'", (parent_id,))["id"],
    )
    tasks.submit_child(task_id=de_id, actor_user_id=db_user_translator)
    tasks.submit_child(task_id=fr_id, actor_user_id=db_user_translator)
    children = query_all("SELECT status FROM tasks WHERE parent_task_id=%s", (parent_id,))
    assert all(child["status"] == tasks.CHILD_REVIEW for child in children)
    tasks.approve_child(task_id=de_id, actor_user_id=db_user_admin)
    parent = query_one("SELECT status, completed_at FROM tasks WHERE id=%s", (parent_id,))
    assert parent["status"] == tasks.PARENT_RAW_DONE
    tasks.approve_child(task_id=fr_id, actor_user_id=db_user_admin)
    parent = query_one("SELECT status, completed_at FROM tasks WHERE id=%s", (parent_id,))
    assert parent["status"] == tasks.PARENT_ALL_DONE
    assert parent["completed_at"] is not None
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


def test_reject_child_returns_to_assigned(
    monkeypatch, db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.approve_raw(task_id=parent_id, actor_user_id=db_user_admin)
    de_id = query_one(
        "SELECT id FROM tasks WHERE parent_task_id=%s AND country_code='DE'",
        (parent_id,),
    )["id"]
    execute("UPDATE tasks SET status=%s WHERE id=%s", (tasks.CHILD_REVIEW, de_id))
    tasks.reject_child(task_id=de_id, actor_user_id=db_user_admin,
                       reason="DE 文案翻译有错请修改")
    row = query_one("SELECT * FROM tasks WHERE id=%s", (de_id,))
    assert row["status"] == tasks.CHILD_ASSIGNED
    assert row["assignee_id"] == db_user_translator
    assert "DE 文案翻译有错" in row["last_reason"]
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


def test_cancel_child_does_not_change_parent(
    db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE", "FR"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.approve_raw(task_id=parent_id, actor_user_id=db_user_admin)
    de_id = query_one(
        "SELECT id FROM tasks WHERE parent_task_id=%s AND country_code='DE'",
        (parent_id,),
    )["id"]
    tasks.cancel_child(task_id=de_id, actor_user_id=db_user_admin,
                       reason="DE 站点暂停上架，取消")
    de = query_one("SELECT * FROM tasks WHERE id=%s", (de_id,))
    assert de["status"] == tasks.CHILD_CANCELLED
    parent = query_one("SELECT * FROM tasks WHERE id=%s", (parent_id,))
    assert parent["status"] == tasks.PARENT_RAW_DONE
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


def test_owner_change_does_not_cascade_to_language_assignees(
    db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    from appcore.users import create_user, get_by_username
    execute("DELETE FROM users WHERE username=%s", ("_t_tc_tr2",))
    create_user("_t_tc_tr2", "x", role="user")
    new_translator = get_by_username("_t_tc_tr2")["id"]

    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["DE", "FR"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.approve_raw(task_id=parent_id, actor_user_id=db_user_admin)
    fr_id = query_one(
        "SELECT id FROM tasks WHERE parent_task_id=%s AND country_code='FR'",
        (parent_id,),
    )["id"]
    execute("UPDATE tasks SET status='done', completed_at=NOW(), assignee_id=%s WHERE id=%s",
            (db_user_translator, fr_id))

    affected = tasks.on_product_owner_changed(
        product_id=db_product["product_id"],
        new_user_id=new_translator,
        actor_user_id=db_user_admin,
    )

    de = query_one("SELECT assignee_id FROM tasks WHERE parent_task_id=%s AND country_code='DE'",
                   (parent_id,))
    fr = query_one("SELECT assignee_id FROM tasks WHERE id=%s", (fr_id,))
    assert affected == 0
    assert de["assignee_id"] == db_user_translator
    assert fr["assignee_id"] == db_user_translator

    events = query_all(
        "SELECT * FROM task_events "
        "WHERE event_type='assignee_changed' "
        "AND task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)",
        (parent_id, parent_id),
    )
    assert events == []
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))
    execute("DELETE FROM users WHERE username=%s", ("_t_tc_tr2",))


def test_update_product_owner_does_not_invoke_task_cascade(
    monkeypatch, db_user_admin, db_user_translator, db_product
):
    """Verify appcore.medias.update_product_owner keeps task assignees isolated."""
    from appcore import medias, tasks
    monkeypatch.setattr(
        tasks,
        "on_product_owner_changed",
        lambda **kw: (_ for _ in ()).throw(
            AssertionError("product owner changes must not cascade task assignees")
        ),
    )
    medias.update_product_owner(db_product["product_id"], db_user_translator)
