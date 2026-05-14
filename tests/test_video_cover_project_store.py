from appcore import video_cover_project_store


def test_list_projects_admin_uses_global_scope_and_creator_join():
    calls = []

    def fake_query(sql, args):
        calls.append((sql, args))
        return []

    rows = video_cover_project_store.list_projects(
        user_id=7,
        is_admin=True,
        query_func=fake_query,
    )

    assert rows == []
    sql, args = calls[0]
    assert "LEFT JOIN users u ON u.id = p.user_id" in sql
    assert "u.username AS creator_name" in sql
    assert "p.user_id = %s" not in sql
    assert args == ("video_cover",)


def test_list_projects_non_admin_scopes_to_user():
    calls = []

    def fake_query(sql, args):
        calls.append((sql, args))
        return []

    video_cover_project_store.list_projects(
        user_id=7,
        is_admin=False,
        query_func=fake_query,
    )

    sql, args = calls[0]
    assert "p.user_id = %s" in sql
    assert args == ("video_cover", 7)


def test_get_project_admin_does_not_scope_to_user():
    calls = []

    def fake_query_one(sql, args):
        calls.append((sql, args))
        return None

    video_cover_project_store.get_project(
        "task-1",
        user_id=7,
        is_admin=True,
        query_one_func=fake_query_one,
    )

    sql, args = calls[0]
    assert "LEFT JOIN users u ON u.id = p.user_id" in sql
    assert "p.user_id = %s" not in sql
    assert args == ("task-1", "video_cover")


def test_get_project_non_admin_scopes_to_user():
    calls = []

    def fake_query_one(sql, args):
        calls.append((sql, args))
        return None

    video_cover_project_store.get_project(
        "task-1",
        user_id=7,
        is_admin=False,
        query_one_func=fake_query_one,
    )

    sql, args = calls[0]
    assert "p.user_id = %s" in sql
    assert args == ("task-1", "video_cover", 7)
