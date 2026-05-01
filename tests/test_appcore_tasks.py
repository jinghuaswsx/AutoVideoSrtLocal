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


def test_find_target_lang_item_normalizes_country_code(monkeypatch):
    calls = []

    def fake_query_one(sql, args):
        calls.append(args)
        return {"id": 123}

    monkeypatch.setattr(tasks, "query_one", fake_query_one)

    assert tasks._find_target_lang_item(7, " DE ") == {"id": 123}
    assert calls[0] == (7, "de")


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
    assert "approved" in types
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
    tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
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


def test_cancel_parent_cascades_non_done_children(
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
    # 走一遍，让 DE 子任务 done
    tasks.claim_parent(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.mark_uploaded(task_id=parent_id, actor_user_id=db_user_admin)
    tasks.approve_raw(task_id=parent_id, actor_user_id=db_user_admin)
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
    assert all(c["status"] == tasks.CHILD_CANCELLED for c in others)
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


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
    monkeypatch.setattr(tasks, "_find_target_lang_item",
                        lambda product_id, lang: {"id": 1, "object_key": "x", "cover_object_key": "c", "lang": lang, "product_id": product_id})
    monkeypatch.setattr("appcore.pushes.compute_readiness",
                        lambda i, p: {"has_video": True, "has_cover": True,
                                      "has_copywriting": True, "has_push_texts": True,
                                      "is_listed": True})
    monkeypatch.setattr("appcore.pushes.is_ready", lambda r: True)

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
    monkeypatch.setattr(tasks, "_find_target_lang_item",
                        lambda product_id, lang: {"id": 1, "lang": lang, "product_id": product_id})
    monkeypatch.setattr("appcore.pushes.compute_readiness",
                        lambda i, p: {"has_video": True, "has_cover": False,
                                      "has_copywriting": False})
    monkeypatch.setattr("appcore.pushes.is_ready", lambda r: False)

    with pytest.raises(tasks.NotReadyError) as exc:
        tasks.submit_child(task_id=child_id, actor_user_id=db_user_translator)
    assert "has_cover" in str(exc.value.missing) or "has_cover" in str(exc.value)
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


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
    monkeypatch.setattr(tasks, "_find_target_lang_item", lambda *a, **k: None)
    with pytest.raises(tasks.NotReadyError, match="lang_item_missing|missing"):
        tasks.submit_child(task_id=child_id, actor_user_id=db_user_translator)
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))


def test_approve_child_auto_all_done_when_last_child(
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
    monkeypatch.setattr(tasks, "_find_target_lang_item",
                        lambda product_id, lang: {"id": 1})
    monkeypatch.setattr("appcore.pushes.compute_readiness",
                        lambda i, p: {"ok": True})
    monkeypatch.setattr("appcore.pushes.is_ready", lambda r: True)

    de_id, fr_id = (
        query_one("SELECT id FROM tasks WHERE parent_task_id=%s AND country_code='DE'", (parent_id,))["id"],
        query_one("SELECT id FROM tasks WHERE parent_task_id=%s AND country_code='FR'", (parent_id,))["id"],
    )
    tasks.submit_child(task_id=de_id, actor_user_id=db_user_translator)
    tasks.approve_child(task_id=de_id, actor_user_id=db_user_admin)
    parent = query_one("SELECT status FROM tasks WHERE id=%s", (parent_id,))
    assert parent["status"] == tasks.PARENT_RAW_DONE   # 还没全部完成

    tasks.submit_child(task_id=fr_id, actor_user_id=db_user_translator)
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
    monkeypatch.setattr(tasks, "_find_target_lang_item", lambda *a, **k: {"id": 1})
    monkeypatch.setattr("appcore.pushes.compute_readiness", lambda *a, **k: {"ok": True})
    monkeypatch.setattr("appcore.pushes.is_ready", lambda r: True)
    de_id = query_one(
        "SELECT id FROM tasks WHERE parent_task_id=%s AND country_code='DE'",
        (parent_id,),
    )["id"]
    tasks.submit_child(task_id=de_id, actor_user_id=db_user_translator)
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


def test_owner_change_cascades_to_non_terminal_children(
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

    tasks.on_product_owner_changed(
        product_id=db_product["product_id"],
        new_user_id=new_translator,
        actor_user_id=db_user_admin,
    )

    de = query_one("SELECT assignee_id FROM tasks WHERE parent_task_id=%s AND country_code='DE'",
                   (parent_id,))
    fr = query_one("SELECT assignee_id FROM tasks WHERE id=%s", (fr_id,))
    assert de["assignee_id"] == new_translator     # 未完成跟换
    assert fr["assignee_id"] == db_user_translator # 已 done 不变

    events = query_all(
        "SELECT * FROM task_events WHERE event_type='assignee_changed'"
    )
    assert len(events) >= 1
    execute("DELETE FROM task_events WHERE task_id IN (SELECT id FROM tasks WHERE parent_task_id=%s OR id=%s)", (parent_id, parent_id))
    execute("DELETE FROM tasks WHERE parent_task_id=%s OR id=%s", (parent_id, parent_id))
    execute("DELETE FROM users WHERE username=%s", ("_t_tc_tr2",))


def test_update_product_owner_invokes_task_cascade(
    monkeypatch, db_user_admin, db_user_translator, db_product
):
    """Verify appcore.medias.update_product_owner triggers tasks.on_product_owner_changed."""
    from appcore import medias, tasks
    called = []
    monkeypatch.setattr(tasks, "on_product_owner_changed",
                        lambda **kw: called.append(kw))
    medias.update_product_owner(db_product["product_id"], db_user_translator)
    assert len(called) == 1
    assert called[0]["product_id"] == db_product["product_id"]
    assert called[0]["new_user_id"] == db_user_translator
