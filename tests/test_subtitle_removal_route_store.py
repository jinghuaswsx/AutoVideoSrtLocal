from appcore import subtitle_removal_route_store as store


def test_list_submitter_rows_reads_distinct_active_submitters():
    calls = []

    def fake_query(sql, args):
        calls.append((sql, args))
        return [{"user_id": 1}]

    rows = store.list_submitter_rows("u.username", query_func=fake_query)

    assert rows == [{"user_id": 1}]
    assert calls == [
        (
            "SELECT DISTINCT p.user_id, u.username, u.username AS submitter_name "
            "FROM projects p LEFT JOIN users u ON u.id = p.user_id "
            "WHERE p.type = 'subtitle_removal' AND p.deleted_at IS NULL "
            "ORDER BY submitter_name ASC, p.user_id ASC",
            (),
        )
    ]


def test_get_project_created_at_reads_single_project_timestamp():
    calls = []

    def fake_query_one(sql, args):
        calls.append((sql, args))
        return {"created_at": "2026-05-07T12:00:00"}

    row = store.get_project_created_at("sr-1", query_one_func=fake_query_one)

    assert row == {"created_at": "2026-05-07T12:00:00"}
    assert calls == [("SELECT created_at FROM projects WHERE id = %s", ("sr-1",))]


def test_list_inflight_projects_reads_active_subtitle_removal_rows():
    calls = []

    def fake_query(sql, args):
        calls.append((sql, args))
        return [{"id": "sr-1"}]

    rows = store.list_inflight_projects(query_func=fake_query)

    assert rows == [{"id": "sr-1"}]
    assert calls == [
        (
            "SELECT id, user_id, status, state_json "
            "FROM projects "
            "WHERE type = 'subtitle_removal' "
            "AND deleted_at IS NULL "
            "AND status IN ('queued', 'running', 'submitted') "
            "ORDER BY created_at ASC",
            (),
        )
    ]


def test_get_detail_project_reads_active_subtitle_removal_project():
    calls = []

    def fake_query_one(sql, args):
        calls.append((sql, args))
        return {"id": "sr-1"}

    row = store.get_detail_project("sr-1", query_one_func=fake_query_one)

    assert row == {"id": "sr-1"}
    assert calls == [
        (
            "SELECT * FROM projects WHERE id = %s "
            "AND type = 'subtitle_removal' AND deleted_at IS NULL",
            ("sr-1",),
        )
    ]


def test_list_tasks_applies_submitter_and_search_filters():
    calls = []

    def fake_query(sql, args):
        calls.append((sql, args))
        return [{"id": "sr-1"}]

    rows = store.list_tasks(
        "u.username",
        user_id_filter=2,
        query_text="last",
        query_func=fake_query,
    )

    assert rows == [{"id": "sr-1"}]
    assert calls == [
        (
            "SELECT p.id, p.user_id, p.status, p.display_name, p.original_filename, "
            "p.state_json, p.created_at, u.username, u.username AS submitter_name "
            "FROM projects p LEFT JOIN users u ON u.id = p.user_id "
            "WHERE p.type = 'subtitle_removal' AND p.deleted_at IS NULL "
            "AND p.user_id = %s "
            "AND (LOWER(COALESCE(p.display_name, '')) LIKE %s OR "
            "LOWER(COALESCE(p.original_filename, '')) LIKE %s OR "
            "LOWER(COALESCE(p.state_json, '')) LIKE %s) "
            "ORDER BY p.created_at DESC",
            (2, "%last%", "%last%", "%last%"),
        )
    ]


def test_display_name_and_delete_updates_are_scoped():
    calls = []

    def fake_execute(sql, args):
        calls.append((sql, args))

    store.set_project_display_name("sr-1", "Demo", execute_func=fake_execute)
    store.soft_delete_project("sr-1", 7, execute_func=fake_execute)

    assert calls == [
        ("UPDATE projects SET display_name=%s WHERE id=%s", ("Demo", "sr-1")),
        (
            "UPDATE projects SET deleted_at = NOW() WHERE id = %s AND user_id = %s",
            ("sr-1", 7),
        ),
    ]
