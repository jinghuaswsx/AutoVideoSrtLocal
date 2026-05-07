import json

from appcore import video_review_route_store as store


def test_list_user_projects_scopes_by_user_type_and_active_rows():
    calls = []

    def fake_query(sql, args):
        calls.append((sql, args))
        return [{"id": "vr-1"}]

    rows = store.list_user_projects(17, query_func=fake_query)

    assert rows == [{"id": "vr-1"}]
    assert calls == [
        (
            "SELECT id, display_name, original_filename, thumbnail_path, status, created_at "
            "FROM projects "
            "WHERE user_id = %s AND type = %s AND deleted_at IS NULL "
            "ORDER BY created_at DESC",
            (17, "video_review"),
        )
    ]


def test_get_user_project_scopes_by_user_type_and_active_rows():
    calls = []

    def fake_query_one(sql, args):
        calls.append((sql, args))
        return {"id": "vr-1"}

    row = store.get_user_project("vr-1", 17, query_one_func=fake_query_one)

    assert row == {"id": "vr-1"}
    assert calls == [
        (
            "SELECT * FROM projects "
            "WHERE id = %s AND user_id = %s AND type = %s AND deleted_at IS NULL",
            ("vr-1", 17, "video_review"),
        )
    ]


def test_get_user_project_state_scopes_by_user_and_type():
    calls = []

    def fake_query_one(sql, args):
        calls.append((sql, args))
        return {"state_json": "{}"}

    row = store.get_user_project_state("vr-1", 17, query_one_func=fake_query_one)

    assert row == {"state_json": "{}"}
    assert calls == [
        (
            "SELECT state_json FROM projects WHERE id = %s AND user_id = %s AND type = %s",
            ("vr-1", 17, "video_review"),
        )
    ]


def test_insert_project_serializes_state_and_retention_policy():
    calls = []

    def fake_execute(sql, args):
        calls.append((sql, args))
        return 1

    state = {"video_path": "uploads/a.mp4", "steps": {"review": "pending"}}

    result = store.insert_project(
        task_id="vr-1",
        user_id=17,
        original_filename="a.mp4",
        display_name="a",
        task_dir="out/vr-1",
        state=state,
        retention_hours=24,
        execute_func=fake_execute,
    )

    assert result == 1
    assert calls[0][0] == (
        "INSERT INTO projects "
        "(id, user_id, type, original_filename, display_name, "
        "status, task_dir, state_json, created_at, expires_at) "
        "VALUES (%s, %s, %s, %s, %s, 'uploaded', %s, %s, "
        "NOW(), DATE_ADD(NOW(), INTERVAL %s HOUR))"
    )
    assert calls[0][1] == (
        "vr-1",
        17,
        "video_review",
        "a.mp4",
        "a",
        "out/vr-1",
        json.dumps(state, ensure_ascii=False),
        24,
    )


def test_status_and_delete_updates_are_scoped():
    calls = []

    def fake_execute(sql, args):
        calls.append((sql, args))

    store.set_project_status("vr-1", "running", execute_func=fake_execute)
    store.soft_delete_project("vr-1", 17, execute_func=fake_execute)

    assert calls == [
        ("UPDATE projects SET status = %s WHERE id = %s", ("running", "vr-1")),
        (
            "UPDATE projects SET deleted_at = NOW() "
            "WHERE id = %s AND user_id = %s AND type = %s",
            ("vr-1", 17, "video_review"),
        ),
    ]
