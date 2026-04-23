from __future__ import annotations

import os

import pytest

from web import store
from web.services import task_restart


@pytest.fixture
def done_task(tmp_path, monkeypatch):
    task_id = "task-restart-1"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    (task_dir / "asr_result.json").write_text("{}", encoding="utf-8")
    (task_dir / f"{task_id}_hard.normal.mp4").write_bytes(b"fake hard")
    (task_dir / "subtitle.normal.srt").write_text("", encoding="utf-8")
    (task_dir / "thumbnail.jpg").write_bytes(b"thumb")
    (task_dir / "tts_segments").mkdir()
    (task_dir / "tts_segments" / "seg0.mp3").write_bytes(b"seg")

    video_path = tmp_path / "uploads" / f"{task_id}.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"source")

    store.create(task_id, str(video_path), str(task_dir), user_id=1)
    store.update(
        task_id,
        status="done",
        type="de_translate",
        delivery_mode="pure_tos",
        display_name="my-clip",
        source_tos_key="uploads/1/task-restart-1/src.mp4",
        source_object_info={"file_size": 1},
        tos_uploads={
            "normal:hard_video": {"tos_key": "artifacts/1/task-restart-1/normal/h.mp4"},
            "normal:srt": {"tos_key": "artifacts/1/task-restart-1/normal/s.srt"},
            "normal:capcut_archive": {"tos_key": "artifacts/1/task-restart-1/normal/c.zip"},
        },
        preview_files={"hard_video": str(task_dir / f"{task_id}_hard.normal.mp4")},
        result={"hard_video": str(task_dir / f"{task_id}_hard.normal.mp4")},
        variants={"normal": {"label": "normal", "result": {"hard_video": "x"}, "preview_files": {}}},
        translation_history=[{"result": {"sentences": []}}],
        subtitle_font="Impact",
        subtitle_size=14,
        subtitle_position_y=0.68,
    )

    monkeypatch.setattr(task_restart, "ensure_local_source_video", lambda tid: None)

    yield {"task_id": task_id, "task_dir": task_dir, "video_path": video_path}


class _Runner:
    def __init__(self):
        self.started = []

    def start(self, task_id, user_id=None):
        self.started.append((task_id, user_id))


def test_restart_clears_legacy_artifact_metadata(done_task):
    runner = _Runner()
    task_restart.restart_task(
        done_task["task_id"],
        voice_id="v-new",
        voice_gender="female",
        subtitle_font="Oswald Bold",
        subtitle_size=18,
        subtitle_position_y=0.42,
        subtitle_position="bottom",
        interactive_review=False,
        user_id=1,
        runner=runner,
    )

    task = store.get(done_task["task_id"])
    assert task["tos_uploads"] == {}
    assert task["source_tos_key"] == ""
    assert task["delivery_mode"] == "local_primary"


def test_restart_keeps_source_video_and_thumbnail(done_task):
    task_restart.restart_task(
        done_task["task_id"],
        voice_id=None,
        voice_gender="male",
        subtitle_font="Impact",
        subtitle_size=14,
        subtitle_position_y=0.68,
        subtitle_position="bottom",
        interactive_review=False,
        user_id=1,
        runner=_Runner(),
    )

    assert done_task["video_path"].exists()
    assert (done_task["task_dir"] / "thumbnail.jpg").exists()


def test_restart_purges_intermediate_and_result_files(done_task):
    task_restart.restart_task(
        done_task["task_id"],
        voice_id=None,
        voice_gender="male",
        subtitle_font="Impact",
        subtitle_size=14,
        subtitle_position_y=0.68,
        subtitle_position="bottom",
        interactive_review=False,
        user_id=1,
        runner=_Runner(),
    )

    leftover = sorted(os.listdir(done_task["task_dir"]))
    assert "asr_result.json" not in leftover
    assert f"{done_task['task_id']}_hard.normal.mp4" not in leftover
    assert "subtitle.normal.srt" not in leftover
    assert "tts_segments" not in leftover
    assert "thumbnail.jpg" in leftover


def test_restart_resets_state_and_persists_new_config(done_task):
    task_restart.restart_task(
        done_task["task_id"],
        voice_id="v-new",
        voice_gender="female",
        subtitle_font="Oswald Bold",
        subtitle_size=18,
        subtitle_position_y=0.42,
        subtitle_position="top",
        interactive_review=True,
        user_id=1,
        runner=_Runner(),
    )

    task = store.get(done_task["task_id"])
    assert task["status"] == "uploaded"
    assert task["tos_uploads"] == {}
    assert task["result"] == {}
    assert task["exports"] == {}
    assert task["preview_files"] == {}
    assert task["translation_history"] == []
    assert task["variants"] != {} and "normal" in task["variants"]
    assert task["variants"]["normal"].get("result") == {}
    assert all(status == "pending" for status in task["steps"].values())
    assert all(msg == "" for msg in task["step_messages"].values())
    assert task["voice_id"] == "v-new"
    assert task["voice_gender"] == "female"
    assert task["subtitle_font"] == "Oswald Bold"
    assert task["subtitle_size"] == 18
    assert task["subtitle_position_y"] == 0.42
    assert task["subtitle_position"] == "top"
    assert task["interactive_review"] is True
    assert task["source_tos_key"] == ""
    assert task["display_name"] == "my-clip"
    assert task["type"] == "de_translate"
    assert task["delivery_mode"] == "local_primary"


def test_restart_triggers_pipeline_start(done_task):
    runner = _Runner()
    task_restart.restart_task(
        done_task["task_id"],
        voice_id=None,
        voice_gender="male",
        subtitle_font="Impact",
        subtitle_size=14,
        subtitle_position_y=0.68,
        subtitle_position="bottom",
        interactive_review=False,
        user_id=1,
        runner=runner,
    )

    assert runner.started == [(done_task["task_id"], 1)]


def test_restart_stops_when_source_video_cannot_be_restored(done_task, monkeypatch):
    runner = _Runner()
    monkeypatch.setattr(
        task_restart,
        "ensure_local_source_video",
        lambda task_id: (_ for _ in ()).throw(RuntimeError("source missing")),
    )

    with pytest.raises(RuntimeError, match="source missing"):
        task_restart.restart_task(
            done_task["task_id"],
            voice_id=None,
            voice_gender="male",
            subtitle_font="Impact",
            subtitle_size=14,
            subtitle_position_y=0.68,
            subtitle_position="bottom",
            interactive_review=False,
            user_id=1,
            runner=runner,
        )

    assert runner.started == []
