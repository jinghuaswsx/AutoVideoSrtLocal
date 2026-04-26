"""Omni-translate route 测试。

聚焦本次新增的 PUT /api/omni-translate/<task_id>/source-language 端点
（改写源语言并触发 resume from asr_clean）。
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


def test_update_source_language_explicit_es_triggers_resume(authed_client_no_db):
    """body.source_language='es' → 改写 task + resume from asr_clean。"""
    fake_task = {"_user_id": 1, "source_language": "zh"}
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
    assert update_kwargs["asr_normalize_artifact"] is None
    assert update_kwargs["detected_source_language"] is None
    assert update_kwargs["status"] == "running"

    mock_runner.resume.assert_called_once_with("t-1", "asr_clean", user_id=1)


def test_update_source_language_empty_means_auto_detect(authed_client_no_db):
    """body.source_language='' → source_language='zh' (默认 ASR 引擎) + user_specified=False。"""
    fake_task = {"_user_id": 1, "source_language": "es"}
    with patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.put(
            "/api/omni-translate/t-1/source-language",
            json={"source_language": ""},
        )
    assert resp.status_code == 200
    update_kwargs = mock_store.update.call_args.kwargs
    assert update_kwargs["source_language"] == "zh"
    assert update_kwargs["user_specified_source_language"] is False
    mock_runner.resume.assert_called_once_with("t-1", "asr_clean", user_id=1)


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


def test_update_source_language_404_for_other_user(authed_client_no_db):
    """task 属于别人 → 404。"""
    fake_task = {"_user_id": 999}
    with patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.put(
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


def test_update_source_language_pendings_all_steps_from_asr_normalize(authed_client_no_db):
    """改语言后，asr_normalize 及之后所有步骤都 reset 为 pending。"""
    fake_task = {"_user_id": 1, "source_language": "es"}
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
    # asr_normalize 之后的步骤都该 pending（按 RESUMABLE_STEPS 顺序）
    assert "asr_normalize" in pending_steps
    assert "voice_match" in pending_steps
    assert "alignment" in pending_steps
    assert "translate" in pending_steps
    assert "tts" in pending_steps
    assert "subtitle" in pending_steps
    assert "compose" in pending_steps
    # ASR 之前的步骤不应该 pending
    assert "extract" not in pending_steps
    assert "asr" not in pending_steps


# ---------------------------------------------------------------------------
# 扩展 source_language 允许列表（11 个 code）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang", ["fr", "it", "ja", "de", "nl", "sv", "fi"])
def test_update_source_language_accepts_extended_codes(authed_client_no_db, lang):
    """新增 fr/it/ja/de/nl/sv/fi 7 个 code 都应被接受（200 + user_specified=True）。"""
    fake_task = {"_user_id": 1, "source_language": "zh"}
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
