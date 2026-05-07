import json

from appcore import video_creation_route_store as store


def test_list_user_projects_scopes_by_user_type_and_active_rows():
    calls = []

    def fake_query(sql, args):
        calls.append((sql, args))
        return [{"id": "vc-1"}]

    rows = store.list_user_projects(31, query_func=fake_query)

    assert rows == [{"id": "vc-1"}]
    assert calls == [
        (
            "SELECT id, display_name, original_filename, thumbnail_path, status, created_at "
            "FROM projects "
            "WHERE user_id = %s AND type = %s AND deleted_at IS NULL "
            "ORDER BY created_at DESC",
            (31, "video_creation"),
        )
    ]


def test_get_user_project_scopes_by_user_type_and_active_rows():
    calls = []

    def fake_query_one(sql, args):
        calls.append((sql, args))
        return {"id": "vc-1"}

    row = store.get_user_project("vc-1", 31, query_one_func=fake_query_one)

    assert row == {"id": "vc-1"}
    assert calls == [
        (
            "SELECT * FROM projects "
            "WHERE id = %s AND user_id = %s AND type = %s AND deleted_at IS NULL",
            ("vc-1", 31, "video_creation"),
        )
    ]


def test_insert_project_serializes_state_and_retention_policy():
    calls = []

    def fake_execute(sql, args):
        calls.append((sql, args))
        return 1

    state = {"task_dir": "out/vc-1", "steps": {"generate": "pending"}}

    result = store.insert_project(
        task_id="vc-1",
        user_id=31,
        original_filename="source.mp4",
        display_name="source",
        thumbnail_path="thumb.jpg",
        task_dir="out/vc-1",
        state=state,
        retention_hours=48,
        execute_func=fake_execute,
    )

    assert result == 1
    assert calls == [
        (
            "INSERT INTO projects "
            "(id, user_id, type, original_filename, display_name, thumbnail_path, "
            "status, task_dir, state_json, created_at, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'uploaded', %s, %s, "
            "NOW(), DATE_ADD(NOW(), INTERVAL %s HOUR))",
            (
                "vc-1",
                31,
                "video_creation",
                "source.mp4",
                "source",
                "thumb.jpg",
                "out/vc-1",
                json.dumps(state, ensure_ascii=False),
                48,
            ),
        )
    ]


def test_state_queries_can_require_active_rows():
    calls = []

    def fake_query_one(sql, args):
        calls.append((sql, args))
        return {"state_json": "{}"}

    store.get_user_project_state("vc-1", 31, query_one_func=fake_query_one)
    store.get_user_project_state("vc-2", 32, active_only=True, query_one_func=fake_query_one)

    assert calls == [
        (
            "SELECT state_json FROM projects WHERE id = %s AND user_id = %s AND type = %s",
            ("vc-1", 31, "video_creation"),
        ),
        (
            "SELECT state_json FROM projects "
            "WHERE id = %s AND user_id = %s AND type = %s AND deleted_at IS NULL",
            ("vc-2", 32, "video_creation"),
        ),
    ]


def test_delete_project_storage_query_and_soft_delete_are_scoped():
    query_calls = []
    execute_calls = []

    def fake_query_one(sql, args):
        query_calls.append((sql, args))
        return {"task_dir": "out/vc-1", "state_json": "{}"}

    def fake_execute(sql, args):
        execute_calls.append((sql, args))

    row = store.get_user_project_storage("vc-1", 31, query_one_func=fake_query_one)
    store.soft_delete_project("vc-1", 31, execute_func=fake_execute)

    assert row == {"task_dir": "out/vc-1", "state_json": "{}"}
    assert query_calls == [
        (
            "SELECT task_dir, state_json FROM projects "
            "WHERE id = %s AND user_id = %s AND type = %s AND deleted_at IS NULL",
            ("vc-1", 31, "video_creation"),
        )
    ]
    assert execute_calls == [
        (
            "UPDATE projects SET deleted_at = NOW() "
            "WHERE id = %s AND user_id = %s AND type = %s",
            ("vc-1", 31, "video_creation"),
        )
    ]


def test_set_project_status_uses_parameterized_status():
    calls = []

    def fake_execute(sql, args):
        calls.append((sql, args))

    store.set_project_status("vc-1", "running", execute_func=fake_execute)

    assert calls == [
        ("UPDATE projects SET status = %s WHERE id = %s", ("running", "vc-1"))
    ]
