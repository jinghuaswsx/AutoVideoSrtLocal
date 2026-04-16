"""视频翻译（测试）模块骨架路由的渲染冒烟测试。

列表页与详情页直接依赖 ``appcore.db.query``/``appcore.db.query_one``，
这里用 monkeypatch 替换掉 ``web.routes.translate_lab`` 中的同名引用，
避免触达真实数据库。
"""
from __future__ import annotations

import json

from web import store


def test_translate_lab_list_page_renders(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.translate_lab.db_query",
        lambda sql, args: [],
    )

    resp = authed_client_no_db.get("/translate-lab")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "视频翻译（测试）" in body


def test_translate_lab_detail_page_renders(authed_client_no_db, monkeypatch):
    task = store.create_translate_lab(
        "lab-1",
        "uploads/lab-1.mp4",
        "output/lab-1",
        original_filename="demo.mp4",
        user_id=1,
    )
    row = {
        "id": task["id"],
        "user_id": 1,
        "type": "translate_lab",
        "display_name": "demo",
        "original_filename": "demo.mp4",
        "status": "uploaded",
        "created_at": None,
        "expires_at": None,
        "deleted_at": None,
        "state_json": json.dumps(task, ensure_ascii=False),
    }
    monkeypatch.setattr(
        "web.routes.translate_lab.db_query_one",
        lambda sql, args: row,
    )

    resp = authed_client_no_db.get(f"/translate-lab/{task['id']}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "视频翻译（测试）" in body


def test_layout_contains_translate_lab_link(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.translate_lab.db_query",
        lambda sql, args: [],
    )

    resp = authed_client_no_db.get("/translate-lab")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "/translate-lab" in body


# ── Task 13：API 路由测试 ──────────────────────────────

def test_start_task_triggers_runner(authed_client_no_db, monkeypatch):
    """POST /api/translate-lab/<id>/start 写入 options 并调用 runner.start。"""
    started: dict = {}

    def fake_start(task_id, user_id=None, **kwargs):
        started["task_id"] = task_id
        started["user_id"] = user_id

    monkeypatch.setattr(
        "web.services.translate_lab_runner.start", fake_start,
    )
    monkeypatch.setattr(
        "web.routes.translate_lab._get_lab_task",
        lambda tid, uid: {
            "id": tid,
            "_user_id": uid,
            "type": "translate_lab",
            "status": "uploaded",
        },
    )
    monkeypatch.setattr(
        "appcore.task_state.update", lambda tid, **kw: None,
    )

    resp = authed_client_no_db.post(
        "/api/translate-lab/lab-1/start",
        json={
            "source_language": "zh",
            "target_language": "en",
            "voice_match_mode": "auto",
        },
    )
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    assert started["task_id"] == "lab-1"
    assert started["user_id"] == 1


def test_confirm_voice_sets_chosen(authed_client_no_db, monkeypatch):
    """POST /confirm-voice 把选中音色写入 chosen_voice。"""
    updates: dict = {}

    def fake_update(task_id, **fields):
        updates["task_id"] = task_id
        updates.update(fields)

    monkeypatch.setattr(
        "web.routes.translate_lab._get_lab_task",
        lambda tid, uid: {
            "id": tid, "_user_id": uid, "type": "translate_lab",
        },
    )
    monkeypatch.setattr(
        "appcore.task_state.get",
        lambda tid: {"pending_voice_choice": [{"voice_id": "abc",
                                                "name": "Rachel"}]},
    )
    monkeypatch.setattr("appcore.task_state.update", fake_update)

    resp = authed_client_no_db.post(
        "/api/translate-lab/lab-1/confirm-voice",
        json={"voice_id": "abc"},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["chosen"]["voice_id"] == "abc"
    assert updates["chosen_voice"]["voice_id"] == "abc"
    assert updates["status"] == "running"


def test_confirm_voice_requires_voice_id(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.translate_lab._get_lab_task",
        lambda tid, uid: {
            "id": tid, "_user_id": uid, "type": "translate_lab",
        },
    )
    resp = authed_client_no_db.post(
        "/api/translate-lab/lab-1/confirm-voice",
        json={},
    )
    assert resp.status_code == 400


def test_sync_voice_library(authed_client_no_db, monkeypatch):
    """POST /voice-library/sync 调用 sync_all_shared_voices 并回传条目数。"""

    def fake_sync(api_key):
        assert api_key == "k"
        return 42

    monkeypatch.setattr(
        "web.routes.translate_lab.sync_all_shared_voices", fake_sync,
    )
    monkeypatch.setattr(
        "web.routes.translate_lab.resolve_key",
        lambda uid, service, env: "k",
    )

    resp = authed_client_no_db.post(
        "/api/translate-lab/voice-library/sync",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["total"] == 42


def test_embed_voice_library(authed_client_no_db, monkeypatch):
    """POST /voice-library/embed 调用 embed_missing_voices。"""
    seen: dict = {}

    def fake_embed(cache_dir, limit=None):
        seen["cache_dir"] = cache_dir
        seen["limit"] = limit
        return 7

    monkeypatch.setattr(
        "web.routes.translate_lab.embed_missing_voices", fake_embed,
    )

    resp = authed_client_no_db.post(
        "/api/translate-lab/voice-library/embed",
        json={"limit": 10},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["count"] == 7
    assert seen["limit"] == 10
    assert "voice_embed_cache" in seen["cache_dir"]


# ── Task 14：UI 回归测试 ──────────────────────────────


def test_detail_page_contains_7_step_indicators(
    authed_client_no_db, monkeypatch,
):
    """详情页必须包含 7 步流水线的所有 step code。"""
    monkeypatch.setattr(
        "web.routes.translate_lab._get_lab_task",
        lambda tid, uid: {
            "id": tid, "user_id": uid,
            "type": "translate_lab", "status": "running",
            "display_name": "demo",
        },
    )
    monkeypatch.setattr(
        "web.routes.translate_lab.db_query_one",
        lambda sql, args: {
            "id": "lab-1", "user_id": 1,
            "type": "translate_lab",
            "display_name": "demo",
            "original_filename": "demo.mp4",
            "status": "running",
            "created_at": None, "expires_at": None, "deleted_at": None,
            "state_json": "{}",
        },
    )
    resp = authed_client_no_db.get("/translate-lab/lab-1")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for step in ["extract", "shot_decompose", "voice_match",
                 "translate", "tts_verify", "subtitle", "compose"]:
        assert step in body, f"missing step {step}"


def test_list_page_has_create_and_sync_buttons(
    authed_client_no_db, monkeypatch,
):
    """列表页必须含「新建任务」和「同步音色库」按钮及其 DOM hook。"""
    monkeypatch.setattr(
        "web.routes.translate_lab.db_query",
        lambda sql, args: [],
    )
    resp = authed_client_no_db.get("/translate-lab")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert 'id="openCreateBtn"' in body
    assert 'id="syncVoiceLibraryBtn"' in body
    # 表单字段存在
    assert 'name="voice_match_mode"' in body
    assert 'id="createSourceLang"' in body
    assert 'id="createTargetLang"' in body
    # JS bundle 已挂载
    assert "translate_lab.js" in body


def test_upload_and_create_returns_task_id(
    authed_client_no_db, monkeypatch, tmp_path,
):
    """POST /api/translate-lab 接收视频文件并调 create_translate_lab。"""
    seen: dict = {}

    def fake_create(task_id, video_path, task_dir, **kwargs):
        seen["task_id"] = task_id
        seen["video_path"] = video_path
        seen["task_dir"] = task_dir
        seen.update(kwargs)
        return {"id": task_id, "type": "translate_lab"}

    monkeypatch.setattr(
        "web.routes.translate_lab.store.create_translate_lab", fake_create,
    )
    monkeypatch.setattr(
        "web.routes.translate_lab.db_execute", lambda sql, args: None,
    )
    monkeypatch.setattr(
        "web.routes.translate_lab.db_query_one",
        lambda sql, args: None,
    )
    monkeypatch.setattr(
        "web.routes.translate_lab.task_state.update",
        lambda tid, **kw: None,
    )
    monkeypatch.setattr(
        "web.routes.translate_lab.OUTPUT_DIR", str(tmp_path / "output"),
    )
    monkeypatch.setattr(
        "web.routes.translate_lab.UPLOAD_DIR", str(tmp_path / "uploads"),
    )

    # 缩略图生成：强制失败避免加载 ffmpeg 依赖（代码里已 try/except）
    def _raise(*args, **kwargs):
        raise RuntimeError("no ffmpeg in test")
    monkeypatch.setattr(
        "pipeline.ffutil.extract_thumbnail", _raise, raising=False,
    )

    from io import BytesIO
    resp = authed_client_no_db.post(
        "/api/translate-lab",
        data={
            "video": (BytesIO(b"fake-mp4"), "sample.mp4"),
            "source_language": "zh",
            "target_language": "de",
            "voice_match_mode": "manual",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["task_id"] == seen["task_id"]
    assert data["source_language"] == "zh"
    assert data["target_language"] == "de"
    assert data["voice_match_mode"] == "manual"
    # create_translate_lab 收到正确 options
    assert seen["source_language"] == "zh"
    assert seen["target_language"] == "de"
    assert seen["voice_match_mode"] == "manual"


def test_upload_rejects_bad_extension(authed_client_no_db, monkeypatch):
    """非视频扩展名应当被 validate_video_extension 拦截。"""
    from io import BytesIO
    resp = authed_client_no_db.post(
        "/api/translate-lab",
        data={"video": (BytesIO(b"x"), "foo.txt")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_upload_rejects_bad_target_language(
    authed_client_no_db, monkeypatch, tmp_path,
):
    """未在白名单中的 target_language 返回 400。"""
    monkeypatch.setattr(
        "web.routes.translate_lab.OUTPUT_DIR", str(tmp_path / "output"),
    )
    monkeypatch.setattr(
        "web.routes.translate_lab.UPLOAD_DIR", str(tmp_path / "uploads"),
    )
    from io import BytesIO
    resp = authed_client_no_db.post(
        "/api/translate-lab",
        data={
            "video": (BytesIO(b"x"), "foo.mp4"),
            "target_language": "ru",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_download_subtitle_not_ready_returns_404(authed_client_no_db, monkeypatch):
    """subtitle_path 未生成时应返回 404。"""
    monkeypatch.setattr(
        "web.routes.translate_lab._get_lab_task",
        lambda tid, uid: {
            "id": tid, "_user_id": uid, "type": "translate_lab",
        },
    )
    resp = authed_client_no_db.get(
        "/api/translate-lab/lab-1/subtitle",
    )
    assert resp.status_code == 404


def test_download_subtitle_returns_file(authed_client_no_db, monkeypatch, tmp_path):
    """subtitle_path 指向真实文件时能正常返回内容。"""
    srt = tmp_path / "sample.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
    monkeypatch.setattr(
        "web.routes.translate_lab._get_lab_task",
        lambda tid, uid: {
            "id": tid, "_user_id": uid, "type": "translate_lab",
            "subtitle_path": str(srt),
        },
    )
    resp = authed_client_no_db.get("/api/translate-lab/lab-1/subtitle")
    assert resp.status_code == 200
    assert "hello" in resp.get_data(as_text=True)


def test_stream_shot_audio_not_ready_returns_404(authed_client_no_db, monkeypatch):
    """tts_results 为空时应返回 404。"""
    monkeypatch.setattr(
        "web.routes.translate_lab._get_lab_task",
        lambda tid, uid: {
            "id": tid, "_user_id": uid, "type": "translate_lab",
            "tts_results": [],
        },
    )
    resp = authed_client_no_db.get(
        "/api/translate-lab/lab-1/audio/1",
    )
    assert resp.status_code == 404


def test_stream_shot_audio_returns_file(authed_client_no_db, monkeypatch, tmp_path):
    """指定分镜的 audio_path 指向真实文件时能正常返回。"""
    audio = tmp_path / "shot_1.mp3"
    audio.write_bytes(b"ID3\x00\x00\x00fake-mp3")
    monkeypatch.setattr(
        "web.routes.translate_lab._get_lab_task",
        lambda tid, uid: {
            "id": tid, "_user_id": uid, "type": "translate_lab",
            "tts_results": [
                {"shot_index": 1, "audio_path": str(audio)},
            ],
        },
    )
    resp = authed_client_no_db.get("/api/translate-lab/lab-1/audio/1")
    assert resp.status_code == 200
    assert resp.mimetype == "audio/mpeg"


def test_stream_final_video_not_ready_returns_404(authed_client_no_db, monkeypatch):
    """final_video / compose_result 均缺失时 404。"""
    monkeypatch.setattr(
        "web.routes.translate_lab._get_lab_task",
        lambda tid, uid: {
            "id": tid, "_user_id": uid, "type": "translate_lab",
        },
    )
    resp = authed_client_no_db.get("/api/translate-lab/lab-1/final-video")
    assert resp.status_code == 404


def test_stream_final_video_returns_file(authed_client_no_db, monkeypatch, tmp_path):
    """compose_result.hard_video 指向真实文件时能正常返回。"""
    video = tmp_path / "out.mp4"
    video.write_bytes(b"\x00\x00\x00\x20ftypmp42")
    monkeypatch.setattr(
        "web.routes.translate_lab._get_lab_task",
        lambda tid, uid: {
            "id": tid, "_user_id": uid, "type": "translate_lab",
            "compose_result": {"hard_video": str(video)},
        },
    )
    resp = authed_client_no_db.get("/api/translate-lab/lab-1/final-video")
    assert resp.status_code == 200
    assert resp.mimetype == "video/mp4"


def test_delete_translate_lab_task(authed_client_no_db, monkeypatch):
    """DELETE /api/translate-lab/<id> 软删除并返回 ok。"""
    seen: dict = {}

    def fake_query_one(sql, args):
        return {"id": "lab-1"}

    def fake_execute(sql, args):
        seen["sql"] = sql
        seen["args"] = args

    monkeypatch.setattr(
        "web.routes.translate_lab.db_query_one", fake_query_one,
    )
    monkeypatch.setattr(
        "web.routes.translate_lab.db_execute", fake_execute,
    )
    monkeypatch.setattr(
        "web.routes.translate_lab.task_state.update",
        lambda tid, **kw: None,
    )

    resp = authed_client_no_db.delete("/api/translate-lab/lab-1")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    assert "deleted_at=NOW()" in seen["sql"]
