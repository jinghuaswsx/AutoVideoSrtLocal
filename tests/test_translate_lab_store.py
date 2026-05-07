from appcore import translate_lab_store as store


def test_find_project_by_display_name_scopes_by_user_and_active_rows():
    calls = []

    def fake_query_one(sql, args):
        calls.append((sql, args))
        return None

    row = store.find_project_by_display_name(61, "Demo", query_one_func=fake_query_one)

    assert row is None
    assert calls == [
        (
            "SELECT id FROM projects WHERE user_id = %s AND display_name = %s "
            "AND deleted_at IS NULL",
            (61, "Demo"),
        )
    ]


def test_list_user_projects_scopes_by_user_type_and_active_rows():
    calls = []

    def fake_query(sql, args):
        calls.append((sql, args))
        return [{"id": "lab-1", "state_json": "{}"}]

    rows = store.list_user_projects(61, query_func=fake_query)

    assert rows == [{"id": "lab-1", "state_json": "{}"}]
    assert calls == [
        (
            "SELECT id, original_filename, display_name, thumbnail_path, status, "
            "created_at, expires_at, deleted_at, state_json "
            "FROM projects "
            "WHERE user_id = %s AND type = %s AND deleted_at IS NULL "
            "ORDER BY created_at DESC",
            (61, "translate_lab"),
        )
    ]


def test_get_user_project_preserves_route_detail_lookup_shape():
    calls = []

    def fake_query_one(sql, args):
        calls.append((sql, args))
        return {"id": "lab-1", "type": "translate_lab"}

    row = store.get_user_project("lab-1", 61, query_one_func=fake_query_one)

    assert row == {"id": "lab-1", "type": "translate_lab"}
    assert calls == [
        ("SELECT * FROM projects WHERE id = %s AND user_id = %s", ("lab-1", 61))
    ]


def test_update_helpers_scope_to_translate_lab_type():
    calls = []

    def fake_execute(sql, args):
        calls.append((sql, args))

    store.set_project_display_name("lab-1", "Demo", execute_func=fake_execute)
    store.set_project_thumbnail_path("lab-1", "thumb.jpg", execute_func=fake_execute)

    assert calls == [
        (
            "UPDATE projects SET display_name = %s WHERE id = %s AND type = %s",
            ("Demo", "lab-1", "translate_lab"),
        ),
        (
            "UPDATE projects SET thumbnail_path = %s WHERE id = %s AND type = %s",
            ("thumb.jpg", "lab-1", "translate_lab"),
        ),
    ]


def test_get_active_user_project_id_scopes_by_type_and_user():
    calls = []

    def fake_query_one(sql, args):
        calls.append((sql, args))
        return {"id": "lab-1"}

    row = store.get_active_user_project_id("lab-1", 61, query_one_func=fake_query_one)

    assert row == {"id": "lab-1"}
    assert calls == [
        (
            "SELECT id FROM projects WHERE id = %s AND user_id = %s "
            "AND type = %s AND deleted_at IS NULL",
            ("lab-1", 61, "translate_lab"),
        )
    ]


def test_soft_delete_project_scopes_by_user_and_type():
    calls = []

    def fake_execute(sql, args):
        calls.append((sql, args))

    store.soft_delete_project("lab-1", 61, execute_func=fake_execute)

    assert calls == [
        (
            "UPDATE projects SET deleted_at=NOW() "
            "WHERE id = %s AND user_id = %s AND type = %s",
            ("lab-1", 61, "translate_lab"),
        )
    ]
