"""Omni-translate route 测试。

聚焦 PUT /api/omni-translate/<task_id>/source-language 和 resume 端点：
它们必须按任务 plugin_config 的真实步骤恢复，而不是假定固定 asr_clean。
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest


CFG_ASR_CLEAN = {
    "asr_post": "asr_clean",
    "shot_decompose": False,
    "translate_algo": "standard",
    "source_anchored": True,
    "tts_strategy": "five_round_rewrite",
    "subtitle": "asr_realign",
    "voice_separation": True,
    "loudness_match": True,
    "av_sync_audit": "off",
}

CFG_ASR_NORMALIZE = {
    **CFG_ASR_CLEAN,
    "asr_post": "asr_normalize",
    "source_anchored": False,
}

CFG_DYNAMIC_ALL = {
    **CFG_ASR_NORMALIZE,
    "shot_decompose": True,
    "translate_algo": "shot_char_limit",
    "av_sync_audit": "report_only",
}


def test_build_plugin_config_annotation_names_omni_current():
    from web.services.omni_preset_annotation import build_plugin_config_annotation

    annotation = build_plugin_config_annotation(
        "t-1",
        {"plugin_config": CFG_ASR_CLEAN},
    )

    assert annotation["name"] == "omni-current"
    assert annotation["source"] == "snapshot"
    assert "ASR 原样清洗" in annotation["summary"]
    assert "Source anchored" in annotation["summary"]


def test_build_plugin_config_annotation_marks_custom_config():
    from web.services.omni_preset_annotation import build_plugin_config_annotation

    cfg = {
        **CFG_ASR_CLEAN,
        "voice_separation": False,
        "loudness_match": False,
    }
    annotation = build_plugin_config_annotation(
        "t-1",
        {"plugin_config": cfg},
    )

    assert annotation["name"] == "自定义配置"
    assert annotation["source"] == "snapshot"
    assert "人声分离关闭" in annotation["summary"]
    assert "响度匹配关闭" in annotation["summary"]


def test_omni_translate_llm_debug_route_serves_registered_prompt_payload(
    authed_client_no_db, tmp_path, monkeypatch,
):
    from appcore import task_state

    monkeypatch.setattr(task_state, "_db_upsert", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *args, **kwargs: None)
    task_id = "omni-llm-debug"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    prompt_file = task_dir / "localized_translate_messages.json"
    prompt_file.write_text(json.dumps({
        "phase": "initial_translate",
        "source_language": "es",
        "target_language": "de",
        "messages": [
            {"role": "system", "content": "Translate from ASR."},
            {"role": "user", "content": "Hola mundo"},
        ],
        "request_payload": {
            "type": "chat",
            "use_case_code": "video_translate.localize",
            "provider": "openrouter",
            "model": "claude-sonnet",
        },
    }, ensure_ascii=False), encoding="utf-8")
    task_state.create(task_id, "video.mp4", str(task_dir), user_id=1)
    task_state.update(
        task_id,
        type="omni_translate",
        llm_debug_refs={
            "translate": [{
                "id": "translate-initial",
                "label": "初始翻译",
                "path": "localized_translate_messages.json",
                "source_language": "es",
                "target_language": "de",
            }],
        },
    )

    resp = authed_client_no_db.get(f"/api/omni-translate/{task_id}/llm-debug/translate")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["step"] == "translate"
    assert body["summary"]["call_count"] == 1
    assert body["items"][0]["messages"][1]["content"] == "Hola mundo"
    assert body["items"][0]["request_payload"]["provider"] == "openrouter"


def test_update_source_language_explicit_es_triggers_resume(authed_client_no_db):
    """body.source_language='es' → 改写 task + resume from asr_clean。"""
    fake_task = {
        "_user_id": 1,
        "source_language": "zh",
        "utterances_raw": [{"text": "old raw"}],
        "plugin_config": CFG_ASR_CLEAN,
        "artifacts": {"asr_clean": {"title": "old clean"}, "translate": {"title": "old translate"}},
    }
    with patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.put(
            "/api/omni-translate/t-1/source-language",
            json={"source_language": "es"},
        )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "started"
    assert body["source_language"] == "es"
    assert body["user_specified_source_language"] is True

    update_kwargs = mock_store.update.call_args.kwargs
    assert update_kwargs["source_language"] == "es"
    assert update_kwargs["user_specified_source_language"] is True
    assert update_kwargs["utterances_en"] is None
    assert update_kwargs["utterances_raw"] is None
    assert update_kwargs["asr_normalize_artifact"] is None
    assert update_kwargs["detected_source_language"] is None
    assert "asr_clean" not in update_kwargs["artifacts"]
    assert "translate" not in update_kwargs["artifacts"]
    assert update_kwargs["status"] == "running"

    mock_runner.resume.assert_called_once_with("t-1", "asr_clean", user_id=1)


def test_update_source_language_uses_actual_asr_normalize_step(authed_client_no_db):
    fake_task = {
        "_user_id": 1,
        "source_language": "es",
        "plugin_config": CFG_ASR_NORMALIZE,
        "artifacts": {
            "asr_normalize": {"title": "old normalize"},
            "loudness_match": {"title": "old loudness"},
        },
    }
    with patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.put(
            "/api/omni-translate/t-1/source-language",
            json={"source_language": "pt"},
        )

    assert resp.status_code == 200
    update_kwargs = mock_store.update.call_args.kwargs
    assert "asr_normalize" not in update_kwargs["artifacts"]
    assert "loudness_match" not in update_kwargs["artifacts"]
    pending_steps = [
        call.args[1] for call in mock_store.set_step.call_args_list
        if call.args[2] == "pending"
    ]
    assert pending_steps[:3] == ["asr_normalize", "voice_match", "alignment"]
    assert "asr_clean" not in pending_steps
    mock_runner.resume.assert_called_once_with("t-1", "asr_normalize", user_id=1)


def test_update_source_language_rejects_empty_auto_detect(authed_client_no_db):
    """body.source_language='' → 400；源语言必须由人工明确选择。"""
    fake_task = {"_user_id": 1, "source_language": "es"}
    with patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.put(
            "/api/omni-translate/t-1/source-language",
            json={"source_language": ""},
        )
    assert resp.status_code == 400
    assert "source_language" in resp.get_json()["error"]
    mock_store.update.assert_not_called()
    mock_runner.resume.assert_not_called()


def test_update_source_language_pt_is_accepted(authed_client_no_db):
    """body.source_language='pt' (新增葡语) → 200。"""
    fake_task = {"_user_id": 1, "source_language": "es"}
    with patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner"):
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.put(
            "/api/omni-translate/t-1/source-language",
            json={"source_language": "pt"},
        )
    assert resp.status_code == 200
    update_kwargs = mock_store.update.call_args.kwargs
    assert update_kwargs["source_language"] == "pt"
    assert update_kwargs["user_specified_source_language"] is True


def test_update_source_language_rejects_unsupported_lang(authed_client_no_db):
    """body.source_language='ru' → 400 不在 5 选项。"""
    fake_task = {"_user_id": 1}
    with patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.put(
            "/api/omni-translate/t-1/source-language",
            json={"source_language": "ru"},
        )
    assert resp.status_code == 400
    mock_store.update.assert_not_called()
    mock_runner.resume.assert_not_called()


def test_update_source_language_404_for_non_admin_other_user(authed_user_client_no_db):
    """普通用户访问别人的 task → 404。"""
    fake_task = {"_user_id": 999}
    with patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner:
        mock_store.get.return_value = fake_task
        resp = authed_user_client_no_db.put(
            "/api/omni-translate/t-1/source-language",
            json={"source_language": "es"},
        )
    assert resp.status_code == 404
    mock_store.update.assert_not_called()
    mock_runner.resume.assert_not_called()


def test_update_source_language_404_when_task_missing(authed_client_no_db):
    """task 不存在 → 404。"""
    with patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner:
        mock_store.get.return_value = None
        resp = authed_client_no_db.put(
            "/api/omni-translate/t-missing/source-language",
            json={"source_language": "es"},
        )
    assert resp.status_code == 404
    mock_store.update.assert_not_called()
    mock_runner.resume.assert_not_called()


def test_update_source_language_pendings_all_steps_from_asr_clean(authed_client_no_db):
    """改语言后，asr_clean 及之后所有步骤都 reset 为 pending。"""
    fake_task = {"_user_id": 1, "source_language": "es", "plugin_config": CFG_ASR_CLEAN}
    with patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner"):
        mock_store.get.return_value = fake_task
        authed_client_no_db.put(
            "/api/omni-translate/t-1/source-language",
            json={"source_language": "pt"},
        )
    pending_steps = [
        call.args[1] for call in mock_store.set_step.call_args_list
        if call.args[2] == "pending"
    ]
    # asr_clean 之后的步骤都该 pending（按 RESUMABLE_STEPS 顺序）
    assert "asr_clean" in pending_steps
    assert "asr_normalize" not in pending_steps
    assert "voice_match" in pending_steps
    assert "alignment" in pending_steps
    assert "translate" in pending_steps
    assert "tts" in pending_steps
    assert "subtitle" in pending_steps
    assert "compose" in pending_steps
    assert "export" in pending_steps
    # ASR 之前的步骤不应该 pending
    assert "extract" not in pending_steps
    assert "asr" not in pending_steps


def test_resume_rejects_start_step_not_in_actual_pipeline(authed_client_no_db):
    fake_task = {"_user_id": 1, "source_language": "es", "plugin_config": CFG_ASR_CLEAN}
    with patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.post(
            "/api/omni-translate/t-1/resume",
            json={"start_step": "asr_normalize"},
        )

    assert resp.status_code == 400
    assert "asr_normalize" in resp.get_json()["error"]
    mock_runner.resume.assert_not_called()


def test_resume_accepts_actual_asr_normalize_without_alias(authed_client_no_db):
    fake_task = {
        "_user_id": 1,
        "source_language": "es",
        "plugin_config": CFG_ASR_NORMALIZE,
        "artifacts": {
            "asr_normalize": {"title": "old normalize"},
            "translate": {"title": "old translate"},
        },
    }
    with patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.post(
            "/api/omni-translate/t-1/resume",
            json={"start_step": "asr_normalize"},
        )

    assert resp.status_code == 200
    assert resp.get_json()["start_step"] == "asr_normalize"
    update_kwargs = mock_store.update.call_args.kwargs
    assert update_kwargs["utterances_en"] is None
    assert update_kwargs["asr_normalize_artifact"] is None
    assert "asr_normalize" not in update_kwargs["artifacts"]
    assert "translate" not in update_kwargs["artifacts"]
    pending_steps = [
        call.args[1] for call in mock_store.set_step.call_args_list
        if call.args[2] == "pending"
    ]
    assert pending_steps[:3] == ["asr_normalize", "voice_match", "alignment"]
    assert "asr_clean" not in pending_steps
    mock_runner.resume.assert_called_once_with("t-1", "asr_normalize", user_id=1)


@pytest.mark.parametrize("start_step", ["separate", "shot_decompose", "av_sync_audit", "loudness_match"])
def test_resume_accepts_dynamic_steps_from_plugin_config(authed_client_no_db, start_step):
    fake_task = {
        "_user_id": 1,
        "source_language": "es",
        "plugin_config": CFG_DYNAMIC_ALL,
        "artifacts": {start_step: {"title": "old"}},
    }
    with patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.post(
            "/api/omni-translate/t-1/resume",
            json={"start_step": start_step},
        )

    assert resp.status_code == 200
    assert resp.get_json()["start_step"] == start_step
    update_kwargs = mock_store.update.call_args.kwargs
    assert update_kwargs["error"] == ""
    pending_steps = [
        call.args[1] for call in mock_store.set_step.call_args_list
        if call.args[2] == "pending"
    ]
    assert pending_steps[0] == start_step
    mock_runner.resume.assert_called_once_with("t-1", start_step, user_id=1)


# ---------------------------------------------------------------------------
# 扩展 source_language 允许列表（11 个 code）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["fr", "it", "ja", "de", "nl", "sv", "fi"])
def test_update_source_language_accepts_extended_codes(authed_client_no_db, lang):
    """新增 fr/it/ja/de/nl/sv/fi 7 个 code 都应被接受（200 + user_specified=True）。"""
    fake_task = {"_user_id": 1, "source_language": "zh", "plugin_config": CFG_ASR_CLEAN}
    with patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.put(
            "/api/omni-translate/t-1/source-language",
            json={"source_language": lang},
        )
    assert resp.status_code == 200, resp.get_json()
    update_kwargs = mock_store.update.call_args.kwargs
    assert update_kwargs["source_language"] == lang
    assert update_kwargs["user_specified_source_language"] is True
    mock_runner.resume.assert_called_once_with("t-1", "asr_clean", user_id=1)


def test_update_source_language_rejects_unsupported_extended(authed_client_no_db):
    """不在 11 选项里的 code（如 ru）依然被拒。"""
    fake_task = {"_user_id": 1}
    with patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.put(
            "/api/omni-translate/t-1/source-language",
            json={"source_language": "ru"},
        )
    assert resp.status_code == 400
    assert "source_language" in resp.get_json()["error"]
    mock_store.update.assert_not_called()
    mock_runner.resume.assert_not_called()


# ---------------------------------------------------------------------------
# Admin 代他人操作 omni 流程（superadmin / admin 都视作 is_admin）
#
# 需求：所有 omni mutating 路由（resume / restart / start / segments /
# alignment / source-language / export）admin 都能代任意 owner 触发。
# 关键：runner / restart_task 收到的 user_id 必须是 task owner 的 id（不是
# admin 自己的 id），否则 ai_billing / LLM 用量都会污染到 admin 账户。
# ---------------------------------------------------------------------------


def test_admin_can_resume_other_users_task_uses_owner_user_id(authed_client_no_db):
    """admin (id=1) 调 resume 操作 _user_id=99 的 task → 200，runner 收到 user_id=99。"""
    fake_task = {"_user_id": 99, "source_language": "es"}
    with patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.post(
            "/api/omni-translate/t-1/resume",
            json={"start_step": "translate"},
        )

    assert resp.status_code == 200, resp.get_json()
    mock_runner.resume.assert_called_once_with("t-1", "translate", user_id=99)


def test_admin_can_update_segments_other_users_task_uses_owner_user_id(authed_client_no_db):
    """admin 调 PUT segments 操作 _user_id=99 的 task → 200，runner 收到 user_id=99。"""
    fake_task = {"_user_id": 99, "variants": {"normal": {}}}
    with patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.put(
            "/api/omni-translate/t-1/segments",
            json={"segments": [{"index": 0, "translated": "Hola"}]},
        )

    assert resp.status_code == 200
    mock_runner.resume.assert_called_once_with("t-1", "tts", user_id=99)


def test_admin_can_export_other_users_task_uses_owner_user_id(authed_client_no_db):
    """admin 调 export 操作 _user_id=99 的 task → 200，runner 收到 user_id=99。"""
    fake_task = {"_user_id": 99}
    with patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.post("/api/omni-translate/t-1/export")

    assert resp.status_code == 200
    mock_runner.resume.assert_called_once_with("t-1", "compose", user_id=99)


def test_admin_can_update_source_language_other_users_task_uses_owner_user_id(authed_client_no_db):
    """admin 调 PUT source-language 操作 _user_id=99 的 task → 200，runner 收到 user_id=99。"""
    fake_task = {"_user_id": 99, "source_language": "zh"}
    with patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.put(
            "/api/omni-translate/t-1/source-language",
            json={"source_language": "es"},
        )

    assert resp.status_code == 200
    mock_runner.resume.assert_called_once_with("t-1", "asr_clean", user_id=99)


def test_admin_can_update_alignment_other_users_task_uses_owner_user_id(authed_client_no_db):
    """admin 调 PUT alignment 操作 _user_id=99 的 task → 200，runner 收到 user_id=99。"""
    fake_task = {
        "_user_id": 99,
        "utterances": [{"start_time": 0.0, "end_time": 1.0, "text": "hi"}],
        "scene_cuts": [],
        "interactive_review": False,
    }
    with patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner, \
         patch("web.routes.omni_translate.build_script_segments", return_value=[]), \
         patch("web.preview_artifacts.build_alignment_artifact", return_value={}):
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.put(
            "/api/omni-translate/t-1/alignment",
            json={"break_after": []},
        )

    assert resp.status_code == 200
    mock_runner.resume.assert_called_once_with("t-1", "translate", user_id=99)


def test_admin_can_restart_other_users_task_uses_owner_user_id(authed_client_no_db):
    """admin 调 restart 操作 _user_id=99 的 task → 200，restart_task 收到 user_id=99。"""
    fake_task = {"_user_id": 99}
    with patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.services.task_restart.restart_task") as mock_restart:
        mock_store.get.return_value = fake_task
        mock_restart.return_value = {"status": "restarted"}
        resp = authed_client_no_db.post(
            "/api/omni-translate/t-1/restart",
            json={"voice_id": "auto"},
        )

    assert resp.status_code == 200
    assert mock_restart.call_args.kwargs["user_id"] == 99


def test_admin_can_start_other_users_task_uses_owner_user_id(authed_client_no_db):
    """admin 调 start 操作 _user_id=99 的 task → 200，runner 收到 user_id=99。"""
    fake_task = {"_user_id": 99}
    with patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.post("/api/omni-translate/t-1/start")

    assert resp.status_code == 200
    mock_runner.start.assert_called_once_with("t-1", user_id=99)


def test_non_admin_cannot_resume_other_users_task(authed_user_client_no_db):
    """普通用户调 resume 操作别人 task → 404，runner 不被调用。"""
    fake_task = {"_user_id": 999, "source_language": "es"}
    with patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner:
        mock_store.get.return_value = fake_task
        resp = authed_user_client_no_db.post(
            "/api/omni-translate/t-1/resume",
            json={"start_step": "translate"},
        )

    assert resp.status_code == 404
    mock_runner.resume.assert_not_called()
