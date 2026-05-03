from __future__ import annotations

from datetime import datetime

from web.services.task_upload import initialize_uploaded_av_task


def test_initialize_uploaded_av_task_persists_display_name_and_av_state():
    created = []
    updates = []
    executions = []
    conflicts = []

    def resolve_conflict(user_id, desired_name, *, query_one=None):
        conflicts.append((user_id, desired_name, query_one))
        return f"{desired_name} (2)"

    result = initialize_uploaded_av_task(
        "task-1",
        video_path="uploads/task-1.mp4",
        task_dir="output/task-1",
        original_filename="demo.mp4",
        form_payload={"display_name": "  Demo  "},
        av_inputs={
            "target_language": "de",
            "target_language_name": "German",
            "target_market": "OTHER",
            "sync_granularity": "sentence",
        },
        source_updates={
            "source_language": "en",
            "user_specified_source_language": True,
        },
        file_size=123,
        content_type="video/mp4",
        user_id=7,
        clock=lambda: datetime(2026, 5, 3, 12, 1, 2),
        create_task=lambda *args, **kwargs: created.append((args, kwargs)),
        update_task=lambda *args, **kwargs: updates.append((args, kwargs)),
        execute=lambda *args, **kwargs: executions.append((args, kwargs)),
        query_one=lambda *args, **kwargs: {"id": "existing"},
        resolve_name_conflict=resolve_conflict,
    )

    assert created == [
        (
            ("task-1", "uploads/task-1.mp4", "output/task-1"),
            {"original_filename": "demo.mp4", "user_id": 7},
        )
    ]
    assert conflicts[0][0:2] == (7, "Demo")
    assert callable(conflicts[0][2])
    assert executions == [
        (("UPDATE projects SET display_name=%s WHERE id=%s", ("Demo (2)", "task-1")), {})
    ]
    assert len(updates) == 1
    assert updates[0][0] == ("task-1",)
    fields = updates[0][1]
    assert fields["display_name"] == "Demo (2)"
    assert fields["type"] == "translation"
    assert fields["source_language"] == "en"
    assert fields["user_specified_source_language"] is True
    assert fields["pipeline_version"] == "av"
    assert fields["target_lang"] == "de"
    assert fields["av_translate_inputs"]["target_language"] == "de"
    assert fields["steps"]["extract"] == "pending"
    assert fields["step_messages"]["extract"] == ""
    assert fields["source_tos_key"] == ""
    assert fields["delivery_mode"] == "local_primary"
    assert fields["source_object_info"] == {
        "file_size": 123,
        "content_type": "video/mp4",
        "original_filename": "demo.mp4",
        "storage_backend": "local",
        "uploaded_at": "2026-05-03T12:01:02",
    }
    assert result.payload == {
        "task_id": "task-1",
        "redirect_url": "/sentence_translate/task-1",
    }


def test_initialize_uploaded_av_task_uses_default_name_without_user_db_write():
    executions = []
    conflicts = []
    updates = []

    initialize_uploaded_av_task(
        "task-anon",
        video_path="uploads/task-anon.mp4",
        task_dir="output/task-anon",
        original_filename="clip.final.mp4",
        form_payload={},
        av_inputs={"target_language": "fr"},
        source_updates={
            "source_language": "zh",
            "user_specified_source_language": True,
        },
        file_size=5,
        content_type="video/mp4",
        user_id=None,
        clock=lambda: datetime(2026, 5, 3, 12, 1, 2),
        create_task=lambda *args, **kwargs: None,
        update_task=lambda *args, **kwargs: updates.append((args, kwargs)),
        execute=lambda *args, **kwargs: executions.append((args, kwargs)),
        query_one=lambda *args, **kwargs: None,
        resolve_name_conflict=lambda *args, **kwargs: conflicts.append((args, kwargs)),
    )

    assert executions == []
    assert conflicts == []
    assert updates[0][1]["display_name"] == "clip.final"
