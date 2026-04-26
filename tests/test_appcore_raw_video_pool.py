import pytest
from appcore.db import execute, query_one


@pytest.fixture
def db_user_admin():
    from appcore.users import create_user, get_by_username
    username = "_t_rvp_admin"
    execute("DELETE FROM users WHERE username=%s", (username,))
    create_user(username, "x", role="admin")
    uid = get_by_username(username)["id"]
    yield uid
    execute("DELETE FROM users WHERE username=%s", (username,))


@pytest.fixture
def db_user_processor():
    from appcore.users import create_user, get_by_username
    username = "_t_rvp_proc"
    execute("DELETE FROM users WHERE username=%s", (username,))
    create_user(username, "x", role="user")
    uid = get_by_username(username)["id"]
    execute(
        "UPDATE users SET permissions=JSON_SET(COALESCE(permissions, '{}'), "
        "'$.can_process_raw_video', true) WHERE id=%s",
        (uid,),
    )
    yield uid
    execute("DELETE FROM users WHERE username=%s", (username,))


def _insert_pending_parent_task(creator_uid, product_name, item_filename):
    """Helper: create media_product + media_item + pending parent task. Return (task_id, product_id, item_id)."""
    # Pre-clean potential leftovers
    execute("DELETE FROM media_products WHERE name=%s", (product_name,))
    pid = execute(
        "INSERT INTO media_products (user_id, name) VALUES (%s, %s)",
        (creator_uid, product_name),
    )
    iid = execute(
        "INSERT INTO media_items (product_id, user_id, filename, object_key, lang) "
        "VALUES (%s, %s, %s, %s, %s)",
        (pid, creator_uid, item_filename, f"k/{item_filename}", "en"),
    )
    tid = execute(
        "INSERT INTO tasks (parent_task_id, media_product_id, media_item_id, status, created_by) "
        "VALUES (NULL, %s, %s, %s, %s)",
        (pid, iid, "pending", creator_uid),
    )
    return tid, pid, iid


def test_list_visible_tasks_admin_sees_all(db_user_admin, db_user_processor):
    from appcore import raw_video_pool
    tid_a, pid_a, _ = _insert_pending_parent_task(db_user_admin, "_t_rvp_p1", "_t_rvp_v1.mp4")
    tid_b, pid_b, _ = _insert_pending_parent_task(db_user_admin, "_t_rvp_p2", "_t_rvp_v2.mp4")
    execute("UPDATE tasks SET assignee_id=%s, status='raw_in_progress', claimed_at=NOW() WHERE id=%s",
            (db_user_processor, tid_b))

    result = raw_video_pool.list_visible_tasks(viewer_user_id=db_user_admin, viewer_role="admin")
    assert any(t["task_id"] == tid_a for t in result["pending"])
    assert any(t["task_id"] == tid_b for t in result["in_progress"])

    execute("DELETE FROM tasks WHERE id IN (%s,%s)", (tid_a, tid_b))
    execute("DELETE FROM media_items WHERE product_id IN (%s,%s)", (pid_a, pid_b))
    execute("DELETE FROM media_products WHERE id IN (%s,%s)", (pid_a, pid_b))


def test_list_visible_tasks_processor_sees_pool_and_own(db_user_admin, db_user_processor):
    from appcore import raw_video_pool
    tid_a, pid_a, _ = _insert_pending_parent_task(db_user_admin, "_t_rvp_p3", "_t_rvp_v3.mp4")
    tid_b, pid_b, _ = _insert_pending_parent_task(db_user_admin, "_t_rvp_p4", "_t_rvp_v4.mp4")
    execute("UPDATE tasks SET assignee_id=%s, status='raw_in_progress' WHERE id=%s",
            (db_user_processor, tid_b))

    result = raw_video_pool.list_visible_tasks(viewer_user_id=db_user_processor, viewer_role="user")
    pending_ids = [t["task_id"] for t in result["pending"]]
    inprog_ids = [t["task_id"] for t in result["in_progress"]]
    assert tid_a in pending_ids
    assert tid_b in inprog_ids
    # Other user's in-progress task should NOT appear in processor's view
    other_uid = db_user_admin
    tid_c, pid_c, _ = _insert_pending_parent_task(db_user_admin, "_t_rvp_p_other", "_t_rvp_v_other.mp4")
    execute("UPDATE tasks SET assignee_id=%s, status='raw_in_progress' WHERE id=%s",
            (other_uid, tid_c))
    result2 = raw_video_pool.list_visible_tasks(viewer_user_id=db_user_processor, viewer_role="user")
    assert tid_c not in [t["task_id"] for t in result2["in_progress"]]

    execute("DELETE FROM tasks WHERE id IN (%s,%s,%s)", (tid_a, tid_b, tid_c))
    execute("DELETE FROM media_items WHERE product_id IN (%s,%s,%s)", (pid_a, pid_b, pid_c))
    execute("DELETE FROM media_products WHERE id IN (%s,%s,%s)", (pid_a, pid_b, pid_c))
