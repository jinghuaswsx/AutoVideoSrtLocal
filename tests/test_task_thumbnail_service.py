from __future__ import annotations


def test_resolve_task_thumbnail_row_uses_admin_deleted_filter():
    from web.services.task_thumbnail import resolve_task_thumbnail_row

    calls = []

    def query_one(sql, args):
        calls.append((sql, args))
        return {"thumbnail_path": "/tmp/thumb.jpg", "task_dir": "/tmp"}

    row = resolve_task_thumbnail_row(
        "task-1",
        user_id=7,
        is_admin=True,
        query_one=query_one,
        path_exists=lambda path: True,
    )

    assert row == {"thumbnail_path": "/tmp/thumb.jpg", "task_dir": "/tmp"}
    assert calls == [
        (
            "SELECT thumbnail_path, task_dir FROM projects WHERE id = %s AND deleted_at IS NULL",
            ("task-1",),
        )
    ]


def test_resolve_task_thumbnail_row_uses_user_and_deleted_filter_for_normal_user():
    from web.services.task_thumbnail import resolve_task_thumbnail_row

    calls = []

    def query_one(sql, args):
        calls.append((sql, args))
        return {"thumbnail_path": "/tmp/thumb.jpg", "task_dir": "/tmp"}

    row = resolve_task_thumbnail_row(
        "task-1",
        user_id=7,
        is_admin=False,
        query_one=query_one,
        path_exists=lambda path: True,
    )

    assert row == {"thumbnail_path": "/tmp/thumb.jpg", "task_dir": "/tmp"}
    assert calls == [
        (
            "SELECT thumbnail_path, task_dir FROM projects WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
            ("task-1", 7),
        )
    ]


def test_resolve_task_thumbnail_row_returns_none_for_missing_file():
    from web.services.task_thumbnail import resolve_task_thumbnail_row

    row = resolve_task_thumbnail_row(
        "task-1",
        user_id=7,
        is_admin=False,
        query_one=lambda sql, args: {"thumbnail_path": "/tmp/missing.jpg", "task_dir": "/tmp"},
        path_exists=lambda path: False,
    )

    assert row is None
