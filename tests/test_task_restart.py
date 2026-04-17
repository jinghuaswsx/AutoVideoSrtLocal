"""重跑服务 web.services.task_restart 的行为锁定。"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


from appcore import task_state
from web import store
from web.services import task_restart


@pytest.fixture
def done_task(tmp_path, monkeypatch):
    """构造一个已 done 的任务，task_dir 含中间文件 + thumbnail + 源视频。"""
    task_id = "task-restart-1"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    # 中间产物、结果、缩略图
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
        variants={"normal": {"label": "普通版", "result": {"hard_video": "x"}, "preview_files": {}}},
        translation_history=[{"result": {"sentences": []}}],
        subtitle_font="Impact",
        subtitle_size=14,
        subtitle_position_y=0.68,
    )

    deleted_keys: list[str] = []
    monkeypatch.setattr(
        task_restart.tos_clients,
        "delete_object",
        lambda key: deleted_keys.append(key),
    )
    monkeypatch.setattr(task_restart, "ensure_local_source_video", lambda tid: None)

    yield {"task_id": task_id, "task_dir": task_dir, "video_path": video_path, "deleted_keys": deleted_keys}


def test_restart_clears_tos_artifacts(done_task, monkeypatch):
    class _Runner:
        def __init__(self):
            self.started = None

        def start(self, task_id, user_id=None):
            self.started = (task_id, user_id)

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

    assert sorted(done_task["deleted_keys"]) == sorted([
        "artifacts/1/task-restart-1/normal/c.zip",
        "artifacts/1/task-restart-1/normal/h.mp4",
        "artifacts/1/task-restart-1/normal/s.srt",
    ])


def test_restart_keeps_source_video_and_thumbnail(done_task):
    class _Runner:
        def start(self, task_id, user_id=None): pass

    task_restart.restart_task(
        done_task["task_id"],
        voice_id=None, voice_gender="male",
        subtitle_font="Impact", subtitle_size=14,
        subtitle_position_y=0.68, subtitle_position="bottom",
        interactive_review=False, user_id=1, runner=_Runner(),
    )

    assert done_task["video_path"].exists(), "source video under uploads/ must survive restart"
    assert (done_task["task_dir"] / "thumbnail.jpg").exists(), "thumbnail should be kept for list page"


def test_restart_purges_intermediate_and_result_files(done_task):
    class _Runner:
        def start(self, task_id, user_id=None): pass

    task_restart.restart_task(
        done_task["task_id"],
        voice_id=None, voice_gender="male",
        subtitle_font="Impact", subtitle_size=14,
        subtitle_position_y=0.68, subtitle_position="bottom",
        interactive_review=False, user_id=1, runner=_Runner(),
    )

    leftover = sorted(os.listdir(done_task["task_dir"]))
    assert "asr_result.json" not in leftover
    assert f"{done_task['task_id']}_hard.normal.mp4" not in leftover
    assert "subtitle.normal.srt" not in leftover
    assert "tts_segments" not in leftover
    assert "thumbnail.jpg" in leftover


def test_restart_resets_state_and_persists_new_config(done_task):
    class _Runner:
        def start(self, task_id, user_id=None): pass

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
    # reset 字段
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
    # 新配置被写入
    assert task["voice_id"] == "v-new"
    assert task["voice_gender"] == "female"
    assert task["subtitle_font"] == "Oswald Bold"
    assert task["subtitle_size"] == 18
    assert task["subtitle_position_y"] == 0.42
    assert task["subtitle_position"] == "top"
    assert task["interactive_review"] is True
    # 身份信息保留
    assert task["source_tos_key"] == "uploads/1/task-restart-1/src.mp4"
    assert task["display_name"] == "my-clip"
    assert task["type"] == "de_translate"
    assert task["delivery_mode"] == "pure_tos"


def test_restart_triggers_pipeline_start(done_task):
    started = []

    class _Runner:
        def start(self, task_id, user_id=None):
            started.append((task_id, user_id))

    task_restart.restart_task(
        done_task["task_id"],
        voice_id=None, voice_gender="male",
        subtitle_font="Impact", subtitle_size=14,
        subtitle_position_y=0.68, subtitle_position="bottom",
        interactive_review=False, user_id=1, runner=_Runner(),
    )
    assert started == [(done_task["task_id"], 1)]


def test_restart_swallows_tos_delete_failures(done_task, monkeypatch):
    def _fail_delete(key):
        raise RuntimeError("tos down")

    monkeypatch.setattr(task_restart.tos_clients, "delete_object", _fail_delete)

    class _Runner:
        def __init__(self): self.started = False
        def start(self, task_id, user_id=None): self.started = True

    runner = _Runner()
    # 不应该 raise —— pipeline 仍要起，TOS 故障不能把用户的重跑拦住
    task_restart.restart_task(
        done_task["task_id"],
        voice_id=None, voice_gender="male",
        subtitle_font="Impact", subtitle_size=14,
        subtitle_position_y=0.68, subtitle_position="bottom",
        interactive_review=False, user_id=1, runner=runner,
    )
    assert runner.started is True
