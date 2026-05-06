"""Tests for /api/omni-translate/start receiving plugin_config (Phase 3)."""
from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _baseline_cfg():
    return {
        "asr_post": "asr_clean",
        "shot_decompose": False,
        "translate_algo": "standard",
        "source_anchored": True,
        "tts_strategy": "five_round_rewrite",
        "subtitle": "asr_realign",
        "voice_separation": True,
        "loudness_match": True,
    }


def _post_create(client, *, plugin_config=None, preset_id=None):
    """POST multipart form 创建 omni 任务。返回 Flask Response。"""
    form = {
        "source_language": "es",
        "target_lang": "de",
        "display_name": "test task",
    }
    if plugin_config is not None:
        form["plugin_config"] = json.dumps(plugin_config)
    if preset_id is not None:
        form["preset_id"] = str(preset_id)
    return client.post(
        "/api/omni-translate/start",
        data={
            **form,
            "video": (io.BytesIO(b"fake-video-bytes"), "test.mp4"),
        },
        content_type="multipart/form-data",
    )


@pytest.fixture
def patched_routes(monkeypatch):
    """Patch 掉 upload / runner / DAO，留 plugin_config 解析逻辑可测。"""
    # upload util 跳过文件实际写入
    monkeypatch.setattr(
        "web.routes.omni_translate.save_uploaded_video"
        if False else "web.upload_util.save_uploaded_video",
        lambda f, *a, **kw: ("/tmp/fake.mp4", 16, "video/mp4"),
    )
    monkeypatch.setattr(
        "web.upload_util.validate_video_extension",
        lambda fn: True,
    )
    monkeypatch.setattr(
        "web.upload_util.build_source_object_info",
        lambda **kw: {"original_filename": kw.get("original_filename", "x")},
    )
    # enabled target lang 包含 de
    monkeypatch.setattr(
        "web.routes.omni_translate._list_enabled_target_langs",
        lambda: ("de", "en", "fr"),
    )
    # 缩略图生成跳过
    monkeypatch.setattr(
        "web.routes.omni_translate._ensure_uploaded_video_thumbnail",
        lambda *a, **kw: "",
    )
    # display_name 冲突解决跳过
    monkeypatch.setattr(
        "web.routes.omni_translate._resolve_name_conflict",
        lambda user_id, name: name,
    )
    # store / runner / preset DAO 全 stub
    captured = {"create_args": None, "update_kwargs": None, "runner_args": None}

    class _StoreStub:
        def create(self, task_id, video_path, task_dir, **kw):
            captured["create_args"] = (task_id, video_path, task_dir, kw)

        def update(self, task_id, **kw):
            captured["update_kwargs"] = kw

        def set_preview_file(self, task_id, key, path):
            pass

    monkeypatch.setattr("web.routes.omni_translate.store", _StoreStub())

    class _RunnerStub:
        def start(self, task_id, *, user_id):
            captured["runner_args"] = (task_id, user_id)

    monkeypatch.setattr("web.routes.omni_translate.omni_pipeline_runner", _RunnerStub())

    return captured


# ---------------------------------------------------------------------------
# inline plugin_config
# ---------------------------------------------------------------------------


def test_create_with_inline_plugin_config_writes_field(
    authed_client_no_db, patched_routes,
):
    cfg = _baseline_cfg()
    cfg["asr_post"] = "asr_normalize"
    cfg["source_anchored"] = False
    resp = _post_create(authed_client_no_db, plugin_config=cfg)
    assert resp.status_code == 201
    saved = patched_routes["update_kwargs"]
    assert saved["plugin_config"] == cfg


def test_create_inline_plugin_config_invalid_returns_400(
    authed_client_no_db, patched_routes,
):
    bad = _baseline_cfg()
    bad["asr_post"] = "magic"
    resp = _post_create(authed_client_no_db, plugin_config=bad)
    assert resp.status_code == 400
    assert "plugin_config" in resp.get_json()["error"]


def test_create_inline_plugin_config_malformed_json_returns_400(
    authed_client_no_db, patched_routes,
):
    # plugin_config 字段是非 JSON 字符串
    resp = authed_client_no_db.post(
        "/api/omni-translate/start",
        data={
            "source_language": "es",
            "target_lang": "de",
            "plugin_config": "{not json",
            "video": (io.BytesIO(b"x"), "test.mp4"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert "JSON" in resp.get_json()["error"]


def test_create_inline_plugin_config_silent_fixes_av_sentence(
    authed_client_no_db, patched_routes,
):
    cfg = _baseline_cfg()
    cfg["translate_algo"] = "av_sentence"
    cfg["source_anchored"] = True  # 应该被 silent fix 成 False
    resp = _post_create(authed_client_no_db, plugin_config=cfg)
    assert resp.status_code == 201
    saved_cfg = patched_routes["update_kwargs"]["plugin_config"]
    assert saved_cfg["translate_algo"] == "av_sentence"
    assert saved_cfg["source_anchored"] is False


# ---------------------------------------------------------------------------
# preset_id
# ---------------------------------------------------------------------------


def test_create_with_preset_id_loads_preset_config(
    authed_client_no_db, patched_routes, monkeypatch,
):
    preset_cfg = _baseline_cfg()
    preset_cfg["translate_algo"] = "shot_char_limit"
    preset_cfg["shot_decompose"] = True
    monkeypatch.setattr(
        "appcore.omni_preset_dao.get",
        lambda pid: {
            "id": pid, "scope": "system", "user_id": None,
            "name": "lab-current", "plugin_config": preset_cfg,
        },
    )
    resp = _post_create(authed_client_no_db, preset_id=4)
    assert resp.status_code == 201
    saved = patched_routes["update_kwargs"]["plugin_config"]
    assert saved["translate_algo"] == "shot_char_limit"
    assert saved["shot_decompose"] is True


def test_create_with_preset_id_unknown_returns_400(
    authed_client_no_db, patched_routes, monkeypatch,
):
    monkeypatch.setattr("appcore.omni_preset_dao.get", lambda pid: None)
    resp = _post_create(authed_client_no_db, preset_id=999)
    assert resp.status_code == 400


def test_create_with_preset_id_non_numeric_returns_400(
    authed_client_no_db, patched_routes,
):
    resp = authed_client_no_db.post(
        "/api/omni-translate/start",
        data={
            "source_language": "es",
            "target_lang": "de",
            "preset_id": "not-a-number",
            "video": (io.BytesIO(b"x"), "test.mp4"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_create_with_others_user_level_preset_returns_403(
    authed_client_no_db, patched_routes, monkeypatch,
):
    """admin (id=1) 用 user 99 的用户级 preset → 拒绝。"""
    monkeypatch.setattr(
        "appcore.omni_preset_dao.get",
        lambda pid: {
            "id": pid, "scope": "user", "user_id": 99,
            "name": "alice-private", "plugin_config": _baseline_cfg(),
        },
    )
    resp = _post_create(authed_client_no_db, preset_id=42)
    assert resp.status_code == 403


def test_create_with_own_user_level_preset_succeeds(
    authed_client_no_db, patched_routes, monkeypatch,
):
    """admin (id=1) 用自己 user_id=1 的用户级 preset → 通过。"""
    monkeypatch.setattr(
        "appcore.omni_preset_dao.get",
        lambda pid: {
            "id": pid, "scope": "user", "user_id": 1,
            "name": "my-preset", "plugin_config": _baseline_cfg(),
        },
    )
    resp = _post_create(authed_client_no_db, preset_id=5)
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# fallback to global default
# ---------------------------------------------------------------------------


def test_create_without_either_uses_global_default_preset(
    authed_client_no_db, patched_routes, monkeypatch,
):
    default_cfg = _baseline_cfg()
    default_cfg["translate_algo"] = "av_sentence"
    default_cfg["source_anchored"] = False  # av_sentence 兼容
    default_cfg["tts_strategy"] = "sentence_reconcile"
    default_cfg["subtitle"] = "sentence_units"
    monkeypatch.setattr(
        "appcore.omni_preset_dao.get_default",
        lambda: {
            "id": 1, "scope": "system", "user_id": None,
            "name": "av-sync-current", "plugin_config": default_cfg,
        },
    )
    resp = _post_create(authed_client_no_db)
    assert resp.status_code == 201
    saved = patched_routes["update_kwargs"]["plugin_config"]
    assert saved["translate_algo"] == "av_sentence"


def test_create_without_either_when_no_default_omits_plugin_config(
    authed_client_no_db, patched_routes, monkeypatch,
):
    """全站默认也没有时不写 plugin_config 字段，runtime 走硬编码 DEFAULT 兜底。"""
    monkeypatch.setattr("appcore.omni_preset_dao.get_default", lambda: None)
    resp = _post_create(authed_client_no_db)
    assert resp.status_code == 201
    saved = patched_routes["update_kwargs"]
    assert "plugin_config" not in saved


# ---------------------------------------------------------------------------
# inline plugin_config beats preset_id when both present
# ---------------------------------------------------------------------------


def test_create_inline_plugin_config_takes_priority_over_preset_id(
    authed_client_no_db, patched_routes, monkeypatch,
):
    inline = _baseline_cfg()
    inline["asr_post"] = "asr_normalize"
    monkeypatch.setattr(
        "appcore.omni_preset_dao.get",
        lambda pid: {
            "id": pid, "scope": "system", "user_id": None,
            "name": "x", "plugin_config": _baseline_cfg(),  # asr_post=asr_clean
        },
    )
    # 同时传 plugin_config（asr_normalize）+ preset_id（asr_clean）
    resp = authed_client_no_db.post(
        "/api/omni-translate/start",
        data={
            "source_language": "es",
            "target_lang": "de",
            "plugin_config": json.dumps(inline),
            "preset_id": "1",
            "video": (io.BytesIO(b"x"), "test.mp4"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201
    saved = patched_routes["update_kwargs"]["plugin_config"]
    # inline 优先
    assert saved["asr_post"] == "asr_normalize"
