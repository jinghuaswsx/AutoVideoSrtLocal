from appcore import image_translate_store as store


def test_list_user_projects_scopes_by_user_type_and_active_rows():
    calls = []

    def fake_query(sql, args):
        calls.append((sql, args))
        return [{"id": "img-1"}]

    rows = store.list_user_projects(41, query_func=fake_query)

    assert rows == [{"id": "img-1"}]
    assert calls == [
        (
            "SELECT id, created_at, status, state_json "
            "FROM projects "
            "WHERE user_id = %s AND type = %s AND deleted_at IS NULL "
            "ORDER BY created_at DESC "
            "LIMIT 100",
            (41, "image_translate"),
        )
    ]


def test_soft_delete_project_scopes_by_user_and_type():
    calls = []

    def fake_execute(sql, args):
        calls.append((sql, args))

    store.soft_delete_project("img-1", 41, execute_func=fake_execute)

    assert calls == [
        (
            "UPDATE projects SET deleted_at = NOW() "
            "WHERE id = %s AND user_id = %s AND type = %s",
            ("img-1", 41, "image_translate"),
        )
    ]
