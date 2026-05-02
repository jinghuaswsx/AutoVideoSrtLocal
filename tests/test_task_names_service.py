from __future__ import annotations

from web.services.task_names import default_display_name, resolve_task_display_name_conflict


def test_default_display_name_uses_filename_stem_truncated_to_ten_chars():
    assert default_display_name("abcdefghijklmnop.mp4") == "abcdefghij"


def test_default_display_name_uses_fallback_for_empty_filename():
    assert default_display_name("") == "未命名"


def test_resolve_task_display_name_conflict_returns_name_when_available():
    calls = []

    def query_one(sql, args):
        calls.append((sql, args))
        return None

    resolved = resolve_task_display_name_conflict(7, "Example", query_one=query_one)

    assert resolved == "Example"
    assert calls == [
        (
            "SELECT id FROM projects WHERE user_id=%s AND display_name=%s AND deleted_at IS NULL",
            (7, "Example"),
        )
    ]


def test_resolve_task_display_name_conflict_appends_counter_until_available():
    taken = {"Example", "Example (2)"}
    calls = []

    def query_one(sql, args):
        calls.append(args)
        return {"id": "taken"} if args[1] in taken else None

    resolved = resolve_task_display_name_conflict(7, "Example", query_one=query_one)

    assert resolved == "Example (3)"
    assert calls == [
        (7, "Example"),
        (7, "Example (2)"),
        (7, "Example (3)"),
    ]


def test_resolve_task_display_name_conflict_excludes_current_task():
    calls = []

    def query_one(sql, args):
        calls.append((sql, args))
        return None

    resolved = resolve_task_display_name_conflict(
        7,
        "Example",
        query_one=query_one,
        exclude_task_id="task-1",
    )

    assert resolved == "Example"
    assert calls == [
        (
            "SELECT id FROM projects WHERE user_id=%s AND display_name=%s AND id!=%s AND deleted_at IS NULL",
            (7, "Example", "task-1"),
        )
    ]
