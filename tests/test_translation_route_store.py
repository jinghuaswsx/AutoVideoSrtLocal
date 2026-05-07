from appcore import translation_route_store as store


def test_find_project_by_display_name_scopes_by_user_and_active_rows():
    calls = []

    def fake_query_one(sql, args):
        calls.append((sql, args))
        return None

    row = store.find_project_by_display_name(7, "Demo", query_one_func=fake_query_one)

    assert row is None
    assert calls == [
        (
            "SELECT id FROM projects WHERE user_id = %s AND display_name = %s "
            "AND deleted_at IS NULL",
            (7, "Demo"),
        )
    ]


def test_list_user_projects_scopes_by_user_type_and_active_rows():
    calls = []

    def fake_query(sql, args):
        calls.append((sql, args))
        return [{"id": "de-1"}]

    rows = store.list_user_projects(7, "de_translate", query_func=fake_query)

    assert rows == [{"id": "de-1"}]
    assert calls == [
        (
            "SELECT id, original_filename, display_name, thumbnail_path, status, "
            "created_at, expires_at, deleted_at "
            "FROM projects WHERE user_id = %s AND type = %s AND deleted_at IS NULL "
            "ORDER BY created_at DESC",
            (7, "de_translate"),
        )
    ]


def test_get_user_project_scopes_by_user_and_type():
    calls = []

    def fake_query_one(sql, args):
        calls.append((sql, args))
        return {"id": "fr-1"}

    row = store.get_user_project("fr-1", 7, "fr_translate", query_one_func=fake_query_one)

    assert row == {"id": "fr-1"}
    assert calls == [
        (
            "SELECT * FROM projects WHERE id = %s AND user_id = %s "
            "AND type = %s",
            ("fr-1", 7, "fr_translate"),
        )
    ]


def test_get_active_project_storage_scopes_by_user_type_and_active_rows():
    calls = []

    def fake_query_one(sql, args):
        calls.append((sql, args))
        return {"id": "de-1", "task_dir": "output/de-1", "state_json": "{}"}

    row = store.get_active_project_storage(
        "de-1",
        7,
        "de_translate",
        query_one_func=fake_query_one,
    )

    assert row == {"id": "de-1", "task_dir": "output/de-1", "state_json": "{}"}
    assert calls == [
        (
            "SELECT id, task_dir, state_json FROM projects "
            "WHERE id = %s AND user_id = %s AND type = %s AND deleted_at IS NULL",
            ("de-1", 7, "de_translate"),
        )
    ]


def test_get_active_project_id_scopes_by_user_type_and_active_rows():
    calls = []

    def fake_query_one(sql, args):
        calls.append((sql, args))
        return {"id": "fr-1"}

    row = store.get_active_project_id("fr-1", 7, "fr_translate", query_one_func=fake_query_one)

    assert row == {"id": "fr-1"}
    assert calls == [
        (
            "SELECT id FROM projects WHERE id = %s AND user_id = %s "
            "AND type = %s AND deleted_at IS NULL",
            ("fr-1", 7, "fr_translate"),
        )
    ]


def test_soft_delete_project_scopes_by_user_and_type():
    calls = []

    def fake_execute(sql, args):
        calls.append((sql, args))

    store.soft_delete_project("de-1", 7, "de_translate", execute_func=fake_execute)

    assert calls == [
        (
            "UPDATE projects SET deleted_at=NOW() "
            "WHERE id = %s AND user_id = %s AND type = %s",
            ("de-1", 7, "de_translate"),
        )
    ]
