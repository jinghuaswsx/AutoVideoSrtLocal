import json

from appcore import copywriting_route_store as store


class FakeCursor:
    def __init__(self, row=None, rows=None):
        self.calls = []
        self._row = row
        self._rows = rows or []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, args):
        self.calls.append((sql, args))

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_obj = cursor
        self.closed = False
        self.committed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


def test_list_user_projects_scopes_by_user_type_and_active_rows():
    cursor = FakeCursor(rows=[{"id": "cw-1"}])
    conn = FakeConnection(cursor)

    rows = store.list_user_projects(53, connection_factory=lambda: conn)

    assert rows == [{"id": "cw-1"}]
    assert conn.closed is True
    assert cursor.calls == [
        (
            "SELECT id, display_name, original_filename, thumbnail_path, "
            "status, created_at, expires_at "
            "FROM projects "
            "WHERE user_id = %s AND type = %s AND deleted_at IS NULL "
            "ORDER BY created_at DESC",
            (53, "copywriting"),
        )
    ]


def test_insert_project_serializes_state_and_retention_policy():
    cursor = FakeCursor()
    task = {"id": "cw-1", "video_path": "uploads/a.mp4"}

    store.insert_project(
        cursor,
        task_id="cw-1",
        user_id=53,
        original_filename="a.mp4",
        display_name="a",
        thumbnail_path="thumb.jpg",
        task_dir="out/cw-1",
        state=task,
        retention_hours=72,
    )

    assert cursor.calls == [
        (
            "INSERT INTO projects "
            "(id, user_id, type, original_filename, display_name, "
            "thumbnail_path, status, task_dir, state_json, "
            "created_at, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'uploaded', %s, %s, "
            "NOW(), DATE_ADD(NOW(), INTERVAL %s HOUR))",
            (
                "cw-1",
                53,
                "copywriting",
                "a.mp4",
                "a",
                "thumb.jpg",
                "out/cw-1",
                json.dumps(task, ensure_ascii=False),
                72,
            ),
        )
    ]


def test_get_project_thumbnail_path_scopes_by_type():
    cursor = FakeCursor(row={"thumbnail_path": "thumb.jpg"})
    conn = FakeConnection(cursor)

    thumbnail_path = store.get_project_thumbnail_path(
        "cw-1",
        connection_factory=lambda: conn,
    )

    assert thumbnail_path == "thumb.jpg"
    assert conn.closed is True
    assert cursor.calls == [
        (
            "SELECT thumbnail_path FROM projects WHERE id = %s AND type = %s",
            ("cw-1", "copywriting"),
        )
    ]


def test_get_inputs_returns_row_or_empty_dict():
    cursor = FakeCursor(row={"project_id": "cw-1", "language": "en"})
    conn = FakeConnection(cursor)

    row = store.get_inputs("cw-1", connection_factory=lambda: conn)

    assert row == {"project_id": "cw-1", "language": "en"}
    assert conn.closed is True
    assert cursor.calls == [
        (
            "SELECT * FROM copywriting_inputs WHERE project_id = %s",
            ("cw-1",),
        )
    ]


def test_insert_inputs_writes_expected_columns():
    cursor = FakeCursor()

    store.insert_inputs(
        cursor,
        task_id="cw-1",
        product_title="Title",
        price="12.00",
        selling_points="Fast",
        target_audience="Buyer",
        extra_info="Info",
        language="en",
    )

    assert cursor.calls == [
        (
            "INSERT INTO copywriting_inputs "
            "(project_id, product_title, price, selling_points, "
            "target_audience, extra_info, language) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            ("cw-1", "Title", "12.00", "Fast", "Buyer", "Info", "en"),
        )
    ]


def test_create_project_with_inputs_commits_single_transaction():
    cursor = FakeCursor()
    conn = FakeConnection(cursor)
    task = {"id": "cw-1", "video_path": "uploads/a.mp4"}

    store.create_project_with_inputs(
        task_id="cw-1",
        user_id=53,
        original_filename="a.mp4",
        display_name="a",
        thumbnail_path="thumb.jpg",
        task_dir="out/cw-1",
        state=task,
        retention_hours=72,
        product_title="Title",
        price="12.00",
        selling_points="Fast",
        target_audience="Buyer",
        extra_info="Info",
        language="en",
        connection_factory=lambda: conn,
    )

    assert conn.closed is True
    assert conn.committed is True
    assert cursor.calls == [
        (
            "INSERT INTO projects "
            "(id, user_id, type, original_filename, display_name, "
            "thumbnail_path, status, task_dir, state_json, "
            "created_at, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'uploaded', %s, %s, "
            "NOW(), DATE_ADD(NOW(), INTERVAL %s HOUR))",
            (
                "cw-1",
                53,
                "copywriting",
                "a.mp4",
                "a",
                "thumb.jpg",
                "out/cw-1",
                json.dumps(task, ensure_ascii=False),
                72,
            ),
        ),
        (
            "INSERT INTO copywriting_inputs "
            "(project_id, product_title, price, selling_points, "
            "target_audience, extra_info, language) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            ("cw-1", "Title", "12.00", "Fast", "Buyer", "Info", "en"),
        ),
    ]


def test_update_inputs_uses_allowlisted_fields_only():
    cursor = FakeCursor()
    conn = FakeConnection(cursor)

    store.update_inputs(
        "cw-1",
        {
            "product_title": "Title",
            "price": "12.00",
            "project_id": "evil",
            "product_image_url": "evil.jpg",
        },
        connection_factory=lambda: conn,
    )

    assert conn.closed is True
    assert conn.committed is True
    assert cursor.calls == [
        (
            "UPDATE copywriting_inputs SET product_title = %s, price = %s "
            "WHERE project_id = %s",
            ["Title", "12.00", "cw-1"],
        )
    ]


def test_update_product_image_url_commits_path_change():
    cursor = FakeCursor()
    conn = FakeConnection(cursor)

    store.update_product_image_url(
        "cw-1",
        "out/cw-1/product_image.jpg",
        connection_factory=lambda: conn,
    )

    assert conn.closed is True
    assert conn.committed is True
    assert cursor.calls == [
        (
            "UPDATE copywriting_inputs SET product_image_url = %s "
            "WHERE project_id = %s",
            ("out/cw-1/product_image.jpg", "cw-1"),
        )
    ]


def test_get_prompt_text_scopes_by_user_and_type_and_picks_language_text():
    cursor = FakeCursor(row={"prompt_text": "English", "prompt_text_zh": "Chinese"})
    conn = FakeConnection(cursor)

    text = store.get_prompt_text(
        5,
        user_id=7,
        language="zh",
        connection_factory=lambda: conn,
    )

    assert text == "Chinese"
    assert conn.closed is True
    assert cursor.calls == [
        (
            "SELECT prompt_text, prompt_text_zh FROM user_prompts "
            "WHERE id = %s AND user_id = %s AND type = 'copywriting'",
            (5, 7),
        )
    ]


def test_get_input_language_and_product_image_path_use_defaults_and_rows():
    lang_cursor = FakeCursor(row={"language": ""})
    lang_conn = FakeConnection(lang_cursor)
    image_cursor = FakeCursor(row={"product_image_url": "out/cw-1/product.jpg"})
    image_conn = FakeConnection(image_cursor)

    language = store.get_input_language(
        "cw-1",
        default="en",
        connection_factory=lambda: lang_conn,
    )
    product_image_path = store.get_product_image_path(
        "cw-1",
        connection_factory=lambda: image_conn,
    )

    assert language == "en"
    assert product_image_path == "out/cw-1/product.jpg"
    assert lang_conn.closed is True
    assert image_conn.closed is True
    assert lang_cursor.calls == [
        (
            "SELECT language FROM copywriting_inputs WHERE project_id = %s",
            ("cw-1",),
        )
    ]
    assert image_cursor.calls == [
        (
            "SELECT product_image_url FROM copywriting_inputs WHERE project_id = %s",
            ("cw-1",),
        )
    ]
