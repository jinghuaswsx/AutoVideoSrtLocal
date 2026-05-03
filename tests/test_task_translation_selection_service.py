from __future__ import annotations

from web.services.task_translation_selection import select_task_translation


def test_select_task_translation_updates_variant_and_selected_index():
    variant_updates = []
    task_updates = []
    selected = {"full_text": "chosen"}

    outcome = select_task_translation(
        "task-1",
        {
            "translation_history": [
                {"result": {"full_text": "old"}},
                {"result": selected},
            ]
        },
        {"index": 1},
        update_variant=lambda *args, **kwargs: variant_updates.append((args, kwargs)),
        update_task=lambda *args, **kwargs: task_updates.append((args, kwargs)),
    )

    assert outcome.status_code == 200
    assert outcome.payload == {"status": "ok", "selected_index": 1}
    assert variant_updates == [(("task-1", "normal"), {"localized_translation": selected})]
    assert task_updates == [(("task-1",), {"selected_translation_index": 1})]


def test_select_task_translation_rejects_missing_or_invalid_index_without_writes():
    variant_updates = []
    task_updates = []

    missing = select_task_translation(
        "task-1",
        {"translation_history": [{"result": {"full_text": "old"}}]},
        {},
        update_variant=lambda *args, **kwargs: variant_updates.append((args, kwargs)),
        update_task=lambda *args, **kwargs: task_updates.append((args, kwargs)),
    )
    invalid = select_task_translation(
        "task-1",
        {"translation_history": [{"result": {"full_text": "old"}}]},
        {"index": "0"},
        update_variant=lambda *args, **kwargs: variant_updates.append((args, kwargs)),
        update_task=lambda *args, **kwargs: task_updates.append((args, kwargs)),
    )
    out_of_range = select_task_translation(
        "task-1",
        {"translation_history": [{"result": {"full_text": "old"}}]},
        {"index": 3},
        update_variant=lambda *args, **kwargs: variant_updates.append((args, kwargs)),
        update_task=lambda *args, **kwargs: task_updates.append((args, kwargs)),
    )

    assert missing.status_code == 400
    assert missing.payload == {"error": "index is required"}
    assert invalid.status_code == 400
    assert "error" in invalid.payload
    assert out_of_range.status_code == 400
    assert "error" in out_of_range.payload
    assert variant_updates == []
    assert task_updates == []
