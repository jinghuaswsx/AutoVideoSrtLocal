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


def test_set_project_thumbnail_path_scopes_by_type():
    calls = []

    def fake_execute(sql, args):
        calls.append((sql, args))

    store.set_project_thumbnail_path(
        "multi-1",
        "multi_translate",
        "output/multi-1/thumbnail.jpg",
        execute_func=fake_execute,
    )

    assert calls == [
        (
            "UPDATE projects SET thumbnail_path = %s WHERE id = %s AND type = %s",
            ("output/multi-1/thumbnail.jpg", "multi-1", "multi_translate"),
        )
    ]


def test_get_viewable_project_omits_user_scope_for_admin():
    calls = []

    def fake_query_one(sql, args):
        calls.append((sql, args))
        return {"id": "multi-1"}

    row = store.get_viewable_project(
        "multi-1",
        "multi_translate",
        user_id=7,
        is_admin=True,
        columns="id, user_id",
        include_deleted=False,
        query_one_func=fake_query_one,
    )

    assert row == {"id": "multi-1"}
    assert calls == [
        (
            "SELECT id, user_id FROM projects WHERE id = %s "
            "AND type = %s AND deleted_at IS NULL",
            ("multi-1", "multi_translate"),
        )
    ]


def test_get_viewable_project_scopes_normal_user():
    calls = []

    def fake_query_one(sql, args):
        calls.append((sql, args))
        return {"state_json": "{}"}

    row = store.get_viewable_project(
        "omni-1",
        "omni_translate",
        user_id=7,
        is_admin=False,
        columns="state_json",
        query_one_func=fake_query_one,
    )

    assert row == {"state_json": "{}"}
    assert calls == [
        (
            "SELECT state_json FROM projects WHERE id = %s "
            "AND user_id = %s AND type = %s",
            ("omni-1", 7, "omni_translate"),
        )
    ]


def test_get_viewable_project_can_include_visible_to_all_for_normal_user():
    calls = []

    def fake_query_one(sql, args):
        calls.append((sql, args))
        return {"state_json": "{}"}

    row = store.get_viewable_project(
        "omni-1",
        "omni_translate",
        user_id=7,
        is_admin=False,
        columns="state_json",
        include_visible_to_all=True,
        query_one_func=fake_query_one,
    )

    assert row == {"state_json": "{}"}
    assert calls == [
        (
            "SELECT state_json FROM projects WHERE id = %s "
            "AND (user_id = %s OR JSON_UNQUOTE(JSON_EXTRACT(state_json, '$.visible_to_all')) = 'true') "
            "AND type = %s",
            ("omni-1", 7, "omni_translate"),
        )
    ]


def test_list_projects_with_creator_scopes_user_and_lang_filter():
    calls = []

    def fake_query(sql, args):
        calls.append((sql, args))
        return [{"id": "multi-1"}]

    rows = store.list_projects_with_creator(
        user_id=7,
        project_type="multi_translate",
        is_admin=False,
        owner_name_expr="u.username",
        target_lang="de",
        query_func=fake_query,
    )

    assert rows == [{"id": "multi-1"}]
    assert calls == [
        (
            "SELECT p.id, p.original_filename, p.display_name, p.thumbnail_path, p.status, "
            "       p.state_json, p.created_at, p.expires_at, p.deleted_at, "
            "       u.username AS creator_name "
            "FROM projects p "
            "LEFT JOIN users u ON u.id = p.user_id "
            "WHERE p.user_id = %s AND p.type = 'multi_translate' AND p.deleted_at IS NULL "
            "  AND JSON_EXTRACT(p.state_json, '$.target_lang') = %s "
            "ORDER BY p.created_at DESC",
            (7, "de"),
        )
    ]


def test_list_projects_with_creator_omits_user_scope_for_admin():
    calls = []

    def fake_query(sql, args):
        calls.append((sql, args))
        return []

    rows = store.list_projects_with_creator(
        user_id=7,
        project_type="omni_translate",
        is_admin=True,
        owner_name_expr="COALESCE(u.display_name, u.username)",
        query_func=fake_query,
    )

    assert rows == []
    assert calls == [
        (
            "SELECT p.id, p.original_filename, p.display_name, p.thumbnail_path, p.status, "
            "       p.state_json, p.created_at, p.expires_at, p.deleted_at, "
            "       COALESCE(u.display_name, u.username) AS creator_name "
            "FROM projects p "
            "LEFT JOIN users u ON u.id = p.user_id "
            "WHERE p.type = 'omni_translate' AND p.deleted_at IS NULL "
            "ORDER BY p.created_at DESC",
            (),
        )
    ]


def test_list_projects_with_creator_accepts_english_redub_project_type():
    calls = []

    def fake_query(sql, args):
        calls.append((sql, args))
        return [{"id": "english-1"}]

    rows = store.list_projects_with_creator(
        user_id=7,
        project_type="english_redub",
        is_admin=True,
        owner_name_expr="u.username",
        query_func=fake_query,
    )

    assert rows == [{"id": "english-1"}]
    assert calls == [
        (
            "SELECT p.id, p.original_filename, p.display_name, p.thumbnail_path, p.status, "
            "       p.state_json, p.created_at, p.expires_at, p.deleted_at, "
            "       u.username AS creator_name "
            "FROM projects p "
            "LEFT JOIN users u ON u.id = p.user_id "
            "WHERE p.type = 'english_redub' AND p.deleted_at IS NULL "
            "ORDER BY p.created_at DESC",
            (),
        )
    ]


def test_list_projects_with_creator_admin_can_filter_by_creator_and_lang():
    calls = []

    def fake_query(sql, args):
        calls.append((sql, args))
        return [{"id": "multi-1"}]

    rows = store.list_projects_with_creator(
        user_id=7,
        project_type="multi_translate",
        is_admin=True,
        owner_name_expr="u.username",
        target_lang="de",
        filter_user_id=237,
        query_func=fake_query,
    )

    assert rows == [{"id": "multi-1"}]
    assert calls == [
        (
            "SELECT p.id, p.original_filename, p.display_name, p.thumbnail_path, p.status, "
            "       p.state_json, p.created_at, p.expires_at, p.deleted_at, "
            "       u.username AS creator_name "
            "FROM projects p "
            "LEFT JOIN users u ON u.id = p.user_id "
            "WHERE p.type = 'multi_translate' AND p.deleted_at IS NULL AND p.user_id = %s "
            "  AND JSON_EXTRACT(p.state_json, '$.target_lang') = %s "
            "ORDER BY p.created_at DESC",
            (237, "de"),
        )
    ]


def test_list_projects_with_creator_normal_user_ignores_creator_filter():
    calls = []

    def fake_query(sql, args):
        calls.append((sql, args))
        return []

    rows = store.list_projects_with_creator(
        user_id=7,
        project_type="multi_translate",
        is_admin=False,
        owner_name_expr="u.username",
        filter_user_id=237,
        query_func=fake_query,
    )

    assert rows == []

    assert calls == [
        (
            "SELECT p.id, p.original_filename, p.display_name, p.thumbnail_path, p.status, "
            "       p.state_json, p.created_at, p.expires_at, p.deleted_at, "
            "       u.username AS creator_name "
            "FROM projects p "
            "LEFT JOIN users u ON u.id = p.user_id "
            "WHERE p.user_id = %s AND p.type = 'multi_translate' AND p.deleted_at IS NULL "
            "ORDER BY p.created_at DESC",
            (7,),
        )
    ]


def test_list_projects_with_creator_can_include_visible_to_all_for_normal_user():
    calls = []

    def fake_query(sql, args):
        calls.append((sql, args))
        return [{"id": "omni-1"}]

    rows = store.list_projects_with_creator(
        user_id=7,
        project_type="omni_translate",
        is_admin=False,
        owner_name_expr="u.username",
        include_visible_to_all=True,
        query_func=fake_query,
    )

    assert rows == [{"id": "omni-1"}]
    assert calls == [
        (
            "SELECT p.id, p.original_filename, p.display_name, p.thumbnail_path, p.status, "
            "       p.state_json, p.created_at, p.expires_at, p.deleted_at, "
            "       u.username AS creator_name "
            "FROM projects p "
            "LEFT JOIN users u ON u.id = p.user_id "
            "WHERE (p.user_id = %s OR JSON_UNQUOTE(JSON_EXTRACT(p.state_json, '$.visible_to_all')) = 'true') "
            "AND p.type = 'omni_translate' AND p.deleted_at IS NULL "
            "ORDER BY p.created_at DESC",
            (7,),
        )
    ]


def test_list_project_creators_uses_owner_display_name_expr():
    calls = []

    def fake_query(sql, args):
        calls.append((sql, args))
        return [{"id": 237, "display_name": "顾倩"}]

    rows = store.list_project_creators(
        project_type="omni_translate",
        owner_name_expr="u.username",
        query_func=fake_query,
    )

    assert rows == [{"id": 237, "display_name": "顾倩"}]
    assert calls == [
        (
            "SELECT DISTINCT p.user_id AS id, "
            "COALESCE(u.username, CONCAT('用户 #', p.user_id)) AS display_name "
            "FROM projects p "
            "LEFT JOIN users u ON u.id = p.user_id "
            "WHERE p.type = 'omni_translate' AND p.deleted_at IS NULL "
            "AND p.user_id IS NOT NULL "
            "ORDER BY display_name ASC, p.user_id ASC",
            (),
        )
    ]


def test_list_projects_with_state_scopes_user_type_and_active_rows():
    calls = []

    def fake_query(sql, args):
        calls.append((sql, args))
        return [{"id": "ja-1", "state_json": "{}"}]

    rows = store.list_projects_with_state(
        user_id=7,
        project_type="ja_translate",
        is_admin=False,
        query_func=fake_query,
    )

    assert rows == [{"id": "ja-1", "state_json": "{}"}]
    assert calls == [
        (
            "SELECT id, original_filename, display_name, thumbnail_path, status, "
            "       state_json, created_at, expires_at, deleted_at "
            "FROM projects "
            "WHERE user_id = %s AND type = %s AND deleted_at IS NULL "
            "ORDER BY created_at DESC",
            (7, "ja_translate"),
        )
    ]


def test_list_projects_with_state_omits_user_scope_for_admin():
    calls = []

    def fake_query(sql, args):
        calls.append((sql, args))
        return []

    rows = store.list_projects_with_state(
        user_id=7,
        project_type="ja_translate",
        is_admin=True,
        query_func=fake_query,
    )

    assert rows == []
    assert calls == [
        (
            "SELECT id, original_filename, display_name, thumbnail_path, status, "
            "       state_json, created_at, expires_at, deleted_at "
            "FROM projects "
            "WHERE type = %s AND deleted_at IS NULL "
            "ORDER BY created_at DESC",
            ("ja_translate",),
        )
    ]
