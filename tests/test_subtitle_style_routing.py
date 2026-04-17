"""字幕样式（字体/字号/位置）在 /start 路由上的持久化回归测试。

问题背景：修 bug 前 DE/FR 路由的 /start handler 只读 `subtitle_position`，
完全忽略前端提交的 `subtitle_font` / `subtitle_size` / `subtitle_position_y`，
导致德语/法语最终视频的字幕样式永远用默认值。本测试锁定三种语言都要把这三个字段
原样写入 task state，供 compose 阶段读取。
"""
from pathlib import Path

from web import store


def _install_common_stubs(monkeypatch, runner_path: str):
    """拦截真实流水线启动 + 数据库写入，保持测试只验证路由行为。"""
    started: list = []
    monkeypatch.setattr(f"{runner_path}.start", lambda task_id, user_id=None: started.append((task_id, user_id)))
    return started


def test_en_start_persists_subtitle_style(authed_client_no_db, tmp_path, monkeypatch):
    task_id = "task-en-subtitle-style"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    video_path = tmp_path / "uploads" / f"{task_id}.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video")

    store.create(task_id, str(video_path), str(task_dir), user_id=1)

    _install_common_stubs(monkeypatch, "web.services.pipeline_runner")
    monkeypatch.setattr("web.routes.task._extract_thumbnail", lambda v, d: None)
    monkeypatch.setattr("web.routes.task.db_execute", lambda sql, args: None)

    response = authed_client_no_db.post(
        f"/api/tasks/{task_id}/start",
        json={
            "voice_id": "auto",
            "subtitle_font": "Poppins Bold",
            "subtitle_size": 18,
            "subtitle_position_y": 0.42,
        },
    )

    assert response.status_code == 200
    task = store.get(task_id)
    assert task["subtitle_font"] == "Poppins Bold"
    assert task["subtitle_size"] == 18
    assert task["subtitle_position_y"] == 0.42


def test_de_start_persists_subtitle_style(authed_client_no_db, tmp_path, monkeypatch):
    task_id = "task-de-subtitle-style"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    video_path = tmp_path / "uploads" / f"{task_id}.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video")

    store.create(task_id, str(video_path), str(task_dir), user_id=1)

    _install_common_stubs(monkeypatch, "web.services.de_pipeline_runner")
    monkeypatch.setattr("web.routes.de_translate.recover_task_if_needed", lambda tid: None)

    response = authed_client_no_db.post(
        f"/api/de-translate/{task_id}/start",
        json={
            "voice_id": "auto",
            "subtitle_font": "Bebas Neue",
            "subtitle_size": 11,
            "subtitle_position_y": 0.55,
        },
    )

    assert response.status_code == 200
    task = store.get(task_id)
    assert task["subtitle_font"] == "Bebas Neue"
    assert task["subtitle_size"] == 11
    assert task["subtitle_position_y"] == 0.55


def test_fr_start_persists_subtitle_style(authed_client_no_db, tmp_path, monkeypatch):
    task_id = "task-fr-subtitle-style"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    video_path = tmp_path / "uploads" / f"{task_id}.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"video")

    store.create(task_id, str(video_path), str(task_dir), user_id=1)

    _install_common_stubs(monkeypatch, "web.services.fr_pipeline_runner")
    monkeypatch.setattr("web.routes.fr_translate.recover_task_if_needed", lambda tid: None)

    response = authed_client_no_db.post(
        f"/api/fr-translate/{task_id}/start",
        json={
            "voice_id": "auto",
            "subtitle_font": "Oswald Bold",
            "subtitle_size": 16,
            "subtitle_position_y": 0.72,
        },
    )

    assert response.status_code == 200
    task = store.get(task_id)
    assert task["subtitle_font"] == "Oswald Bold"
    assert task["subtitle_size"] == 16
    assert task["subtitle_position_y"] == 0.72
