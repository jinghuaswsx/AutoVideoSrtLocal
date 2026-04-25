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


def test_high_level_status_rollup():
    assert tasks.high_level_status("pending") == "in_progress"
    assert tasks.high_level_status("raw_in_progress") == "in_progress"
    assert tasks.high_level_status("review") == "in_progress"
    assert tasks.high_level_status("done") == "completed"
    assert tasks.high_level_status("all_done") == "completed"
    assert tasks.high_level_status("cancelled") == "terminated"


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
def db_product(db_user_admin):
    """Make a media product owned by db_user_admin."""
    execute(
        "INSERT INTO media_products (user_id, name) VALUES (%s, %s)",
        (db_user_admin, "_t_tc_product"),
    )
    pid = query_one("SELECT LAST_INSERT_ID() AS id")["id"]
    # 加一条 en item
    execute(
        "INSERT INTO media_items (product_id, user_id, filename, object_key, lang) "
        "VALUES (%s, %s, %s, %s, %s)",
        (pid, db_user_admin, "x.mp4", "k/x.mp4", "en"),
    )
    iid = query_one("SELECT LAST_INSERT_ID() AS id")["id"]
    yield {"product_id": pid, "item_id": iid}
    execute("DELETE FROM media_items WHERE product_id=%s", (pid,))
    execute("DELETE FROM media_products WHERE id=%s", (pid,))


def test_create_parent_task_inserts_parent_and_children(
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
    parent = query_one("SELECT * FROM tasks WHERE id=%s", (parent_id,))
    assert parent["parent_task_id"] is None
    assert parent["status"] == tasks.PARENT_PENDING
    assert parent["assignee_id"] is None
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


def test_create_parent_task_rejects_empty_countries(
    db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    with pytest.raises(ValueError, match="countries"):
        tasks.create_parent_task(
            media_product_id=db_product["product_id"],
            media_item_id=db_product["item_id"],
            countries=[],
            translator_id=db_user_translator,
            created_by=db_user_admin,
        )


def test_create_parent_task_uppercases_countries(
    db_user_admin, db_user_translator, db_product
):
    from appcore import tasks
    parent_id = tasks.create_parent_task(
        media_product_id=db_product["product_id"],
        media_item_id=db_product["item_id"],
        countries=["de", "fr"],
        translator_id=db_user_translator,
        created_by=db_user_admin,
    )
    children = query_all(
        "SELECT country_code FROM tasks WHERE parent_task_id=%s",
        (parent_id,),
    )
    assert {c["country_code"] for c in children} == {"DE", "FR"}
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


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
