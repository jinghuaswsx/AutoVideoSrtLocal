import time
from pathlib import Path

import numpy as np
import pytest

from appcore import voice_match_tasks as vmt


class _Boom(Exception):
    pass


@pytest.fixture(autouse=True)
def reset_tasks(monkeypatch):
    # 阻止后台线程执行真实的 _run_task_sync，避免去碰真正的 TOS / ffmpeg。
    # 需要测 runner 的用例自行显式调用 _run_task_sync。
    monkeypatch.setattr(vmt._EXECUTOR, "submit", lambda *a, **kw: None)
    vmt._TASKS.clear()
    yield
    vmt._TASKS.clear()


def test_create_task_returns_pending():
    tid = vmt.create_task(user_id=1, source_video_path="uploads/voice_match/demo.mp4",
                          language="de", gender="male")
    t = vmt.get_task(tid, user_id=1)
    assert t["status"] == "pending"
    assert t["progress"] == 0


def test_get_task_other_user_returns_none():
    tid = vmt.create_task(user_id=1, source_video_path="uploads/voice_match/demo.mp4",
                          language="de", gender="male")
    assert vmt.get_task(tid, user_id=2) is None


def test_run_task_success(monkeypatch, tmp_path):
    video_path = tmp_path / "demo.mp4"
    video_path.write_bytes(b"video")
    clip_path = tmp_path / "clip.wav"
    clip_path.write_bytes(b"wav")
    monkeypatch.setattr(vmt, "_extract_sample_clip",
                        lambda p, out_dir: str(clip_path))
    monkeypatch.setattr(vmt, "_embed_audio_file", lambda p: np.ones(256, dtype=np.float32))
    monkeypatch.setattr(vmt, "_match_candidates", lambda vec, **_: [
        {"voice_id": "x", "similarity": 0.9, "name": "X", "gender": "male",
         "language": "de", "preview_url": "https://x"}
    ])

    tid = vmt.create_task(user_id=1, source_video_path=str(video_path),
                          language="de", gender="male")
    vmt._run_task_sync(tid)
    t = vmt.get_task(tid, user_id=1)
    assert t["status"] == "done"
    assert t["result"]["candidates"][0]["voice_id"] == "x"
    assert t["result"]["sample_audio_path"] == str(clip_path)


def test_run_task_failure_marks_failed(monkeypatch, tmp_path):
    source_video = tmp_path / "demo.mp4"
    source_video.write_bytes(b"video")
    def boom(*a, **kw): raise _Boom("ffmpeg missing")
    monkeypatch.setattr(vmt, "_extract_sample_clip", boom)
    tid = vmt.create_task(user_id=1, source_video_path=str(source_video),
                          language="de", gender="male")
    vmt._run_task_sync(tid)
    t = vmt.get_task(tid, user_id=1)
    assert t["status"] == "failed"
    assert "ffmpeg missing" in t["error"]


def test_ttl_cleanup_removes_old_tasks(monkeypatch):
    tid = vmt.create_task(user_id=1, source_video_path="uploads/voice_match/demo.mp4",
                          language="de", gender="male")
    vmt._TASKS[tid]["_expires_at"] = time.time() - 1
    vmt._cleanup_expired()
    assert vmt.get_task(tid, user_id=1) is None
