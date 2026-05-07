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

    def cursor(self):
        return self.cursor_obj

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
