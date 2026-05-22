"""Omni-translate route 测试。

聚焦 PUT /api/omni-translate/<task_id>/source-language 和 resume 端点：
它们必须按任务 plugin_config 的真实步骤恢复，而不是假定固定 asr_clean。
"""
from __future__ import annotations

import base64
import json
from unittest.mock import patch

import pytest

from appcore.voice_ai_rank_cache import candidate_signature


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


def test_superadmin_list_filters_omni_translate_projects_by_user_id(authed_client_no_db):
    with patch("web.routes.omni_translate.db_query", side_effect=[[], []]) as m_q, \
         patch("appcore.settings.get_retention_hours", return_value=72), \
         patch("web.routes.omni_translate._is_superadmin_user", return_value=True), \
         patch("web.routes.omni_translate.recover_all_interrupted_tasks"):
        resp = authed_client_no_db.get("/omni-translate?user_id=237&lang=de")

    assert resp.status_code == 200
    sql = m_q.call_args_list[-1].args[0].lower()
    args = m_q.call_args_list[-1].args[1]
    assert "p.user_id = %s" in sql
    assert args == (237, "de")


def test_omni_translate_voice_library_accepts_pagination(authed_client_no_db, monkeypatch):
    captured: dict = {}

    monkeypatch.setattr(
        "web.routes.omni_translate._query_viewable_project",
        lambda task_id, columns: {
            "state_json": json.dumps({"target_lang": "en", "steps": {"voice_match": "waiting"}}),
            "user_id": 1,
        },
    )

    def fake_list_voices(**kwargs):
        captured.update(kwargs)
        return {"items": [{"voice_id": "omni-page-2", "name": "Page 2"}], "total": 91}

    monkeypatch.setattr("appcore.voice_library_browse.list_voices", fake_list_voices)
    monkeypatch.setattr("appcore.video_translate_defaults.resolve_default_voice", lambda *args, **kwargs: None)

    resp = authed_client_no_db.get("/api/omni-translate/task-voice-pages/voice-library?page=2&page_size=30")

    assert resp.status_code == 200
    assert resp.get_json()["items"][0]["voice_id"] == "omni-page-2"
    assert resp.get_json()["page"] == 2
    assert resp.get_json()["page_size"] == 30
    assert captured == {
        "language": "en",
        "gender": None,
        "q": None,
        "page": 2,
        "page_size": 30,
    }


def test_omni_translate_voice_library_persists_interrupted_ai_rank_state(authed_client_no_db, monkeypatch):
    candidates = [{"voice_id": "v1", "similarity": 0.91}]
    state = {
        "target_lang": "en",
        "steps": {"voice_match": "waiting"},
        "voice_match_candidates": candidates,
        "voice_ai_rank_status": "running",
        "voice_ai_rank_candidate_signature": candidate_signature(candidates),
        "voice_ai_rank_usage_log_id": 74035,
    }
    saved: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        "web.routes.omni_translate._query_viewable_project",
        lambda task_id, columns: {
            "state_json": json.dumps(state, ensure_ascii=False),
            "user_id": 1,
        },
    )
    monkeypatch.setattr(
        "appcore.voice_library_browse.list_voices",
        lambda **kwargs: {"items": [{"voice_id": "v1", "name": "A"}], "total": 1},
    )
    monkeypatch.setattr(
        "web.routes.omni_translate.save_project_state",
        lambda task_id, next_state, execute_func=None: saved.append((task_id, dict(next_state))),
    )

    resp = authed_client_no_db.get("/api/omni-translate/task-stale-ai-rank/voice-library")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["voice_ai_rank_status"] == "interrupted"
    assert payload["voice_ai_rank_usage_log_id"] is None
    assert payload["voice_ai_rank_recovery"]["start_step"] == "voice_match"
    assert saved[0][0] == "task-stale-ai-rank"
    assert saved[0][1]["voice_ai_rank_status"] == "interrupted"
    assert saved[0][1]["voice_ai_rank_usage_log_id"] is None


def test_omni_rematch_uses_speed_aware_voice_match(authed_client_no_db):
    normalized_utterances = [{"text": "hello world", "start_time": 0, "end_time": 1}]
    state = {
        "target_lang": "de",
        "voice_match_query_embedding": base64.b64encode(b"fake-embedding").decode("ascii"),
        "utterances": [{"text": "hola mundo", "start_time": 0, "end_time": 1}],
        "utterances_en": normalized_utterances,
    }
    with patch(
        "web.routes.omni_translate.db_query_one",
        return_value={"state_json": json.dumps(state, ensure_ascii=False), "user_id": 1},
    ), patch(
        "web.routes.omni_translate.db_execute",
    ), patch(
        "appcore.video_translate_defaults.resolve_default_voice",
        return_value="default-voice-id",
    ), patch(
        "pipeline.voice_embedding.deserialize_embedding",
        return_value="decoded-embedding",
    ), patch(
        "pipeline.voice_match.match_candidates",
        side_effect=AssertionError("legacy matcher should not be used"),
    ), patch(
        "pipeline.voice_match_speed.match_candidates_speed_aware",
        return_value=[{"voice_id": "voice-b", "similarity": 0.91}],
    ) as m_match, patch(
        "appcore.voice_library_browse.fetch_voices_by_ids",
        return_value=[{"voice_id": "voice-b", "name": "B", "gender": "female"}],
    ) as m_fetch:
        resp = authed_client_no_db.post(
            "/api/omni-translate/task-1/rematch",
            json={"gender": "female"},
        )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["candidates"][0]["voice_id"] == "voice-b"
    assert payload["extra_items"][0]["voice_id"] == "voice-b"
    assert m_match.call_args.kwargs["exclude_voice_ids"] == {"default-voice-id"}
    assert m_match.call_args.kwargs["candidate_pool_size"] == 20
    assert m_match.call_args.kwargs["top_k"] == 20
    assert m_match.call_args.kwargs["gender"] == "female"
    assert m_match.call_args.kwargs["source_utterances"] == normalized_utterances
    assert m_fetch.call_args.kwargs["voice_ids"] == ["voice-b"]


def test_omni_translate_voice_ai_ranking_rerun_uses_saved_candidates(
    authed_client_no_db,
    monkeypatch,
    tmp_path,
):
    source = tmp_path / "voice_ai_ranking" / "source_sample.mp3"
    source.parent.mkdir()
    source.write_bytes(b"source-audio")
    state = {
        "task_dir": str(tmp_path),
        "target_lang": "de",
        "voice_match_candidates": [
            {"voice_id": "v1", "name": "A"},
            {"voice_id": "v2", "name": "B"},
        ],
        "voice_ai_rank_debug": {
            "request": {
                "visual": {
                    "media": [{
                        "role": "source_sample",
                        "relative_path": "voice_ai_ranking/source_sample.mp3",
                    }]
                }
            }
        },
    }
    saved = {}
    seen = {}

    monkeypatch.setattr(
        "web.routes.omni_translate._query_viewable_project",
        lambda task_id, columns: {"state_json": json.dumps(state), "user_id": 1},
    )
    monkeypatch.setattr(
        "web.routes.omni_translate.save_project_state",
        lambda task_id, payload, execute_func=None: saved.update(payload),
    )
    monkeypatch.setattr(
        "appcore.task_state.update",
        lambda task_id, **kwargs: seen.setdefault("state_update", kwargs),
    )

    def fake_rank_voice_candidates(**kwargs):
        seen.update(kwargs)
        return {
            "status": "done",
            "rankings": [{"voice_id": "v2", "llm_rank": 1, "reason_summary": "better"}],
            "candidates": [
                {"voice_id": "v1", "name": "A"},
                {"voice_id": "v2", "name": "B", "llm_rank": 1, "llm_reason_summary": "better"},
            ],
            "model": "google/gemini-3.5-flash",
            "provider": "openrouter",
            "candidate_limit": 3,
            "debug": {"status": "done", "result": {"visual": {"rankings": []}}},
        }

    monkeypatch.setattr(
        "appcore.voice_ai_ranking.rank_voice_candidates",
        fake_rank_voice_candidates,
    )

    resp = authed_client_no_db.post(
        "/api/omni-translate/task-ai/voice-ai-ranking",
        json={"candidate_limit": 3},
    )

    payload = resp.get_json()
    assert resp.status_code == 200
    assert seen["candidate_limit"] == 3
    assert seen["source_audio_path"] == source
    assert len(seen["candidates"]) == 2
    assert saved["voice_ai_rankings"][0]["voice_id"] == "v2"
    assert saved["voice_match_candidates"][1]["llm_rank"] == 1
    assert saved["voice_ai_rank_provider"] == "openrouter"
    assert payload["voice_ai_rank_status"] == "done"
    assert payload["candidate_limit"] == 3


def test_omni_translate_force_speed_fallback_marks_ai_rank_stale(authed_client_no_db):
    candidates = [
        {"voice_id": "v1", "similarity": 0.9, "speed_match_score": 0.8},
        {"voice_id": "v2", "similarity": 0.8, "speed_match_score": 0.95},
    ]
    running_signature = candidate_signature(candidates)
    state = {
        "target_lang": "es",
        "voice_match_candidates": candidates,
        "voice_ai_rank_status": "running",
        "voice_ai_rank_candidate_signature": running_signature,
        "voice_ai_rankings": [{"voice_id": "v1", "llm_rank": 1}],
        "voice_ai_rank_debug": {"status": "running"},
        "voice_ai_rank_usage_log_id": 123,
    }
    saved = {}
    state_updates = {}

    with patch(
        "web.routes.omni_translate._query_viewable_project",
        return_value={"state_json": json.dumps(state), "user_id": 1},
    ), patch(
        "web.routes.omni_translate.save_project_state",
        side_effect=lambda task_id, payload, execute_func=None: saved.update(payload),
    ), patch(
        "web.routes.omni_translate.task_state.update",
        side_effect=lambda task_id, **kwargs: state_updates.update(kwargs),
    ):
        resp = authed_client_no_db.post(
            "/api/omni-translate/task-ai/voice-ai-ranking/force-speed-fallback",
            json={"gender": None},
        )

    payload = resp.get_json()
    assert resp.status_code == 200
    assert payload["voice_ai_rank_status"] == "speed_fallback"
    assert payload["voice_ai_rankings"] == []
    assert payload["candidates"] == candidates
    assert saved["voice_ai_rank_candidate_signature"].startswith("speed_fallback:")
    assert saved["voice_ai_rank_candidate_signature"] != running_signature
    assert state_updates["voice_ai_rank_status"] == "speed_fallback"


def test_omni_translate_rematch_derives_gender_rankings_from_all_cache(authed_client_no_db):
    all_candidates = [
        {"voice_id": "voice-a", "similarity": 0.95, "gender": "male", "llm_rank": 1, "llm_reason_summary": "stable"},
        {"voice_id": "voice-b", "similarity": 0.91, "gender": "female", "llm_rank": 2, "llm_reason_summary": "bright"},
    ]
    female_candidates = [{"voice_id": "voice-b", "similarity": 0.91, "gender": "female"}]
    state = {
        "target_lang": "de",
        "voice_match_query_embedding": base64.b64encode(b"fake-embedding").decode("ascii"),
        "utterances": [{"text": "hola mundo", "start_time": 0, "end_time": 1}],
        "voice_match_candidates": all_candidates,
        "voice_ai_rank_cache": {
            "all": {
                "condition": "all",
                "candidate_signature": candidate_signature(all_candidates),
                "candidates": all_candidates,
                "rankings": [
                    {"voice_id": "voice-a", "llm_rank": 1, "reason_summary": "stable"},
                    {"voice_id": "voice-b", "llm_rank": 2, "reason_summary": "bright"},
                ],
                "status": "done",
                "model": "google/gemini-3.5-flash",
                "provider": "openrouter",
                "debug": {"status": "done"},
                "candidate_limit": 10,
                "usage_log_id": 98765,
                "source": "llm",
            },
        },
    }
    saved = {}
    with patch(
        "web.routes.omni_translate.db_query_one",
        return_value={"state_json": json.dumps(state, ensure_ascii=False), "user_id": 1},
    ), patch(
        "web.routes.omni_translate.save_project_state",
        side_effect=lambda task_id, payload, execute_func=None: saved.update(payload),
    ), patch(
        "web.routes.omni_translate.task_state.update",
    ), patch(
        "appcore.video_translate_defaults.resolve_default_voice",
        return_value="default-voice-id",
    ), patch(
        "pipeline.voice_embedding.deserialize_embedding",
        return_value="decoded-embedding",
    ), patch(
        "pipeline.voice_match_speed.match_candidates_speed_aware",
        return_value=female_candidates,
    ), patch(
        "appcore.voice_library_browse.fetch_voices_by_ids",
        return_value=[{"voice_id": "voice-b", "name": "B", "gender": "female"}],
    ):
        resp = authed_client_no_db.post(
            "/api/omni-translate/task-1/rematch",
            json={"gender": "female"},
        )

    payload = resp.get_json()
    assert resp.status_code == 200
    assert payload["voice_ai_rank_status"] == "derived_from_all"
    assert payload["voice_ai_rank_cache_key"] == "female"
    assert payload["candidates"][0]["voice_id"] == "voice-b"
    assert payload["candidates"][0]["llm_rank"] == 1
    assert payload["voice_ai_rankings"] == [
        {"voice_id": "voice-b", "llm_rank": 1, "reason_summary": "bright"}
    ]
    assert saved["voice_ai_rank_usage_log_id"] == 98765


def test_superadmin_omni_translate_page_renders_user_filter(authed_client_no_db):
    creators = [
        {"id": 237, "display_name": "顾倩"},
        {"id": 238, "display_name": "translator238"},
    ]
    with patch("web.routes.omni_translate.db_query", side_effect=[creators, []]), \
         patch("appcore.settings.get_retention_hours", return_value=72), \
         patch("web.routes.omni_translate._is_superadmin_user", return_value=True), \
         patch("web.routes.omni_translate.recover_all_interrupted_tasks"):
        resp = authed_client_no_db.get("/omni-translate?user_id=237")

    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert 'id="creatorFilter"' in html
    assert '<option value="237" selected>顾倩</option>' in html
    assert 'value="238"' in html
    assert ">translator238</option>" in html


def test_non_superadmin_omni_translate_ignores_user_filter(authed_client_no_db):
    with patch("web.routes.omni_translate.db_query", return_value=[]) as m_q, \
         patch("appcore.settings.get_retention_hours", return_value=72), \
         patch("web.routes.omni_translate.recover_all_interrupted_tasks"):
        resp = authed_client_no_db.get("/omni-translate?user_id=237")

    assert resp.status_code == 200
    sql = m_q.call_args.args[0].lower()
    args = m_q.call_args.args[1]
    assert "p.user_id = %s" in sql
    assert args == (1,)
    assert b'id="creatorFilter"' not in resp.data


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


def test_omni_list_includes_visible_to_all_for_permitted_users(authed_user_client_no_db):
    with patch("web.routes.omni_translate.db_query", return_value=[]) as mock_query, \
         patch("web.routes.omni_translate.medias.list_enabled_language_codes", return_value=["de", "en"]), \
         patch("appcore.settings.get_retention_hours", return_value=72), \
         patch("web.routes.omni_translate.recover_all_interrupted_tasks"):
        resp = authed_user_client_no_db.get("/omni-translate")

    assert resp.status_code == 200
    sql, args = mock_query.call_args.args
    assert (
        "p.user_id = %s OR JSON_UNQUOTE(JSON_EXTRACT(p.state_json, '$.visible_to_all')) = 'true'"
        in sql
    )
    assert args == (2,)


def test_omni_detail_query_includes_visible_to_all_for_permitted_users(authed_user_client_no_db):
    with patch("web.routes.omni_translate.db_query_one", return_value=None) as mock_query_one, \
         patch("web.routes.omni_translate.recover_project_if_needed"):
        resp = authed_user_client_no_db.get("/omni-translate/shared-task")

    assert resp.status_code == 404
    sql, args = mock_query_one.call_args.args
    assert (
        "user_id = %s OR JSON_UNQUOTE(JSON_EXTRACT(state_json, '$.visible_to_all')) = 'true'"
        in sql
    )
    assert args == ("shared-task", 2, "omni_translate")


def test_loudness_profile_route_saves_standard_without_starting_runner(
    authed_client_no_db,
):
    fake_task = {
        "_user_id": 1,
        "loudness_profile": "bg_boost",
        "separation": {"tts_loudness": {"profile": "bg_boost"}},
    }
    with patch("web.routes.omni_translate.recover_task_if_needed"), \
         patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.post(
            "/api/omni-translate/t-1/loudness-profile",
            json={"profile": "standard"},
        )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["profile"] == "standard"
    assert body["manual_boost_pct"] is None
    assert body["applied_profile"] == "bg_boost"
    assert body["needs_resume"] is True
    mock_store.update.assert_called_once_with(
        "t-1",
        loudness_profile="standard",
        loudness_manual_boost_pct=None,
    )
    mock_runner.resume.assert_not_called()
    mock_runner.start.assert_not_called()


def test_loudness_profile_route_saves_manual_boost_pct(authed_client_no_db):
    fake_task = {
        "_user_id": 1,
        "separation": {"tts_loudness": {"profile": "standard"}},
    }
    with patch("web.routes.omni_translate.recover_task_if_needed"), \
         patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.post(
            "/api/omni-translate/t-1/loudness-profile",
            json={"profile": "manual_boost", "manual_boost_pct": 200},
        )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["profile"] == "manual_boost"
    assert body["manual_boost_pct"] == 200
    assert body["applied_profile"] == "standard"
    assert body["applied_manual_boost_pct"] is None
    assert body["needs_resume"] is True
    mock_store.update.assert_called_once_with(
        "t-1",
        loudness_profile="manual_boost",
        loudness_manual_boost_pct=200,
    )
    mock_runner.resume.assert_not_called()
    mock_runner.start.assert_not_called()


def test_loudness_profile_route_saves_voice_only(authed_client_no_db):
    fake_task = {
        "_user_id": 1,
        "separation": {"tts_loudness": {"profile": "standard"}},
    }
    with patch("web.routes.omni_translate.recover_task_if_needed"), \
         patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.post(
            "/api/omni-translate/t-1/loudness-profile",
            json={"profile": "voice_only"},
        )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["profile"] == "voice_only"
    assert body["manual_boost_pct"] is None
    assert body["applied_profile"] == "standard"
    assert body["needs_resume"] is True
    mock_store.update.assert_called_once_with(
        "t-1",
        loudness_profile="voice_only",
        loudness_manual_boost_pct=None,
    )
    mock_runner.resume.assert_not_called()
    mock_runner.start.assert_not_called()


def test_loudness_profile_route_saves_clean_background(authed_client_no_db):
    fake_task = {
        "_user_id": 1,
        "separation": {"tts_loudness": {"profile": "standard"}},
    }
    with patch("web.routes.omni_translate.recover_task_if_needed"), \
         patch("web.routes.omni_translate.store") as mock_store, \
         patch("web.routes.omni_translate.omni_pipeline_runner") as mock_runner:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.post(
            "/api/omni-translate/t-1/loudness-profile",
            json={"profile": "clean_background"},
        )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["profile"] == "clean_background"
    assert body["manual_boost_pct"] is None
    assert body["applied_profile"] == "standard"
    assert body["needs_resume"] is True
    mock_store.update.assert_called_once_with(
        "t-1",
        loudness_profile="clean_background",
        loudness_manual_boost_pct=None,
    )
    mock_runner.resume.assert_not_called()
    mock_runner.start.assert_not_called()


def test_omni_get_task_prefers_fresh_project_state_for_loudness_profile(
    authed_client_no_db, monkeypatch, tmp_path,
):
    from appcore import task_state

    task_id = "stale-loudness-get"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    monkeypatch.setattr(task_state, "_db_upsert", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *args, **kwargs: None)
    task_state.create(task_id, "video.mp4", str(task_dir), user_id=1)
    task_state.update(
        task_id,
        type="omni_translate",
        plugin_config=CFG_ASR_CLEAN,
        loudness_profile="standard",
        loudness_manual_boost_pct=None,
    )

    fresh_state = dict(task_state.get(task_id) or {})
    fresh_state["loudness_profile"] = "bg_boost"
    fresh_state["loudness_manual_boost_pct"] = None

    def fake_project_row(row_task_id, columns="*", *, include_deleted=True):
        assert row_task_id == task_id
        return {
            "id": task_id,
            "user_id": 1,
            "task_dir": str(task_dir),
            "state_json": json.dumps(fresh_state, ensure_ascii=False),
        }

    monkeypatch.setattr("web.routes.omni_translate._query_viewable_project", fake_project_row)
    monkeypatch.setattr("web.routes.omni_translate.recover_task_if_needed", lambda *_args, **_kwargs: None)

    resp = authed_client_no_db.get(f"/api/omni-translate/{task_id}")

    assert resp.status_code == 200
    assert resp.get_json()["loudness_profile"] == "bg_boost"


def test_resume_uses_fresh_loudness_profile_before_starting_runner(
    authed_client_no_db, monkeypatch, tmp_path,
):
    from appcore import task_state

    task_id = "stale-loudness-resume"
    task_dir = tmp_path / task_id
    task_dir.mkdir()
    monkeypatch.setattr(task_state, "_db_upsert", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *args, **kwargs: None)
    task_state.create(task_id, "video.mp4", str(task_dir), user_id=1)
    task_state.update(
        task_id,
        type="omni_translate",
        plugin_config=CFG_ASR_CLEAN,
        loudness_profile="standard",
        loudness_manual_boost_pct=None,
    )

    fresh_state = dict(task_state.get(task_id) or {})
    fresh_state["loudness_profile"] = "manual_boost"
    fresh_state["loudness_manual_boost_pct"] = 70

    def fake_project_row(row_task_id, columns="*", *, include_deleted=True):
        assert row_task_id == task_id
        return {
            "id": task_id,
            "user_id": 1,
            "task_dir": str(task_dir),
            "state_json": json.dumps(fresh_state, ensure_ascii=False),
        }

    captured = {}

    def fake_resume(row_task_id, start_step, user_id=None):
        task = task_state.get(row_task_id) or {}
        captured["profile"] = task.get("loudness_profile")
        captured["manual_pct"] = task.get("loudness_manual_boost_pct")
        captured["start_step"] = start_step
        captured["user_id"] = user_id

    monkeypatch.setattr("web.routes.omni_translate._query_viewable_project", fake_project_row)
    monkeypatch.setattr("web.routes.omni_translate.recover_task_if_needed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("web.routes.omni_translate.omni_pipeline_runner.resume", fake_resume)

    resp = authed_client_no_db.post(
        f"/api/omni-translate/{task_id}/resume",
        json={"start_step": "loudness_match"},
    )

    assert resp.status_code == 200
    assert captured == {
        "profile": "manual_boost",
        "manual_pct": 70,
        "start_step": "loudness_match",
        "user_id": 1,
    }


def test_loudness_profile_route_allows_visible_project_user_with_permission(authed_user_client_no_db):
    fake_task = {
        "_user_id": 1,
        "visible_to_all": True,
        "separation": {"tts_loudness": {"profile": "bg_boost"}},
    }
    with patch("web.routes.omni_translate.db_query_one", return_value=None), \
         patch("web.routes.omni_translate.recover_task_if_needed") as mock_recover, \
         patch("web.routes.omni_translate.store") as mock_store:
        mock_store.get.return_value = fake_task
        resp = authed_user_client_no_db.post(
            "/api/omni-translate/t-1/loudness-profile",
            json={"profile": "standard"},
        )

    assert resp.status_code == 200
    assert resp.get_json()["profile"] == "standard"
    mock_recover.assert_called_once_with("t-1")
    mock_store.update.assert_called_once_with(
        "t-1",
        loudness_profile="standard",
        loudness_manual_boost_pct=None,
    )


@pytest.mark.parametrize("pct", [0, 5, 55, 101, 210, "abc", None])
def test_loudness_profile_route_rejects_invalid_manual_pct(
    authed_client_no_db, pct,
):
    fake_task = {"_user_id": 1}
    with patch("web.routes.omni_translate.recover_task_if_needed"), \
         patch("web.routes.omni_translate.store") as mock_store:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.post(
            "/api/omni-translate/t-1/loudness-profile",
            json={"profile": "manual_boost", "manual_boost_pct": pct},
        )

    assert resp.status_code == 400
    assert "manual_boost_pct" in resp.get_json()["error"]
    mock_store.update.assert_not_called()


def test_loudness_profile_route_rejects_unknown_profile(authed_client_no_db):
    fake_task = {"_user_id": 1}
    with patch("web.routes.omni_translate.recover_task_if_needed"), \
         patch("web.routes.omni_translate.store") as mock_store:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.post(
            "/api/omni-translate/t-1/loudness-profile",
            json={"profile": "louder"},
        )

    assert resp.status_code == 400
    assert "loudness profile" in resp.get_json()["error"]
    mock_store.update.assert_not_called()


def test_loudness_profile_route_rejects_malformed_json(authed_client_no_db):
    fake_task = {"_user_id": 1}
    with patch("web.routes.omni_translate.recover_task_if_needed"), \
         patch("web.routes.omni_translate.store") as mock_store:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.post(
            "/api/omni-translate/t-1/loudness-profile",
            data="{",
            content_type="application/json",
        )

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid JSON body"
    mock_store.update.assert_not_called()


def test_loudness_profile_route_rejects_json_array(authed_client_no_db):
    fake_task = {"_user_id": 1}
    with patch("web.routes.omni_translate.recover_task_if_needed"), \
         patch("web.routes.omni_translate.store") as mock_store:
        mock_store.get.return_value = fake_task
        resp = authed_client_no_db.post(
            "/api/omni-translate/t-1/loudness-profile",
            json=["standard"],
        )

    assert resp.status_code == 400
    assert resp.get_json()["error"] == "JSON body must be an object"
    mock_store.update.assert_not_called()


def test_duplicate_project_copies_source_config_and_starts_runner(
    authed_client_no_db,
    tmp_path,
    monkeypatch,
):
    from web.routes import omni_translate as route

    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"source-video-bytes")
    original_task = {
        "id": "source-task",
        "_user_id": 7,
        "type": "omni_translate",
        "status": "done",
        "video_path": str(source_video),
        "task_dir": str(tmp_path / "source-task"),
        "original_filename": "source.mp4",
        "display_name": "原项目",
        "source_language": "es",
        "target_lang": "de",
        "plugin_config": CFG_ASR_CLEAN,
        "voice_gender": "female",
        "voice_id": "voice-1",
        "subtitle_position": "top",
        "subtitle_font": "Arial",
        "subtitle_size": 18,
        "subtitle_position_y": 0.42,
        "interactive_review": True,
        "artifacts": {"export": {"hard_video": "old.mp4"}},
        "result": {"hard_video": "old.mp4"},
    }
    captured = {}

    class StoreStub:
        def get(self, task_id):
            return original_task if task_id == "source-task" else None

        def create(self, task_id, video_path, task_dir, **kwargs):
            captured["create"] = (task_id, video_path, task_dir, kwargs)

        def update(self, task_id, **kwargs):
            captured["update"] = (task_id, kwargs)

        def set_preview_file(self, task_id, name, path):
            captured["preview"] = (task_id, name, path)

    class RunnerStub:
        def start(self, task_id, *, user_id):
            captured["runner"] = (task_id, user_id)

    monkeypatch.setattr(route, "store", StoreStub())
    monkeypatch.setattr(route, "omni_pipeline_runner", RunnerStub())
    monkeypatch.setattr(route, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(route, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(route.uuid, "uuid4", lambda: "dup-task")
    monkeypatch.setattr(route, "_resolve_name_conflict", lambda user_id, name: name)
    monkeypatch.setattr(route, "_ensure_uploaded_video_thumbnail", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        route,
        "_query_viewable_project",
        lambda task_id, columns="*", include_deleted=True: {
            "id": task_id,
            "user_id": 7,
            "state_json": json.dumps(original_task, ensure_ascii=False),
            "original_filename": "source.mp4",
            "display_name": "原项目",
        },
    )

    resp = authed_client_no_db.post("/api/omni-translate/source-task/duplicate")

    assert resp.status_code == 201
    body = resp.get_json()
    assert body["task_id"] == "dup-task"
    assert body["redirect_url"] == "/omni-translate/dup-task"

    created_task_id, new_video_path, new_task_dir, create_kwargs = captured["create"]
    assert created_task_id == "dup-task"
    assert create_kwargs["user_id"] == 1
    assert create_kwargs["original_filename"] == "source.mp4"
    assert new_task_dir.endswith("dup-task")
    assert (tmp_path / "uploads" / "dup-task.mp4").read_bytes() == b"source-video-bytes"
    assert new_video_path == str(tmp_path / "uploads" / "dup-task.mp4")

    updated_task_id, update_kwargs = captured["update"]
    assert updated_task_id == "dup-task"
    assert update_kwargs["display_name"] == "原项目 副本"
    assert update_kwargs["type"] == "omni_translate"
    assert update_kwargs["source_language"] == "es"
    assert update_kwargs["target_lang"] == "de"
    assert update_kwargs["plugin_config"] == CFG_ASR_CLEAN
    assert update_kwargs["voice_gender"] == "female"
    assert update_kwargs["voice_id"] == "voice-1"
    assert update_kwargs["subtitle_position"] == "top"
    assert update_kwargs["subtitle_font"] == "Arial"
    assert update_kwargs["subtitle_size"] == 18
    assert update_kwargs["subtitle_position_y"] == 0.42
    assert update_kwargs["interactive_review"] is True
    assert update_kwargs["source_object_info"]["file_size"] == len(b"source-video-bytes")
    assert "artifacts" not in update_kwargs
    assert "result" not in update_kwargs
    assert captured["preview"] == ("dup-task", "source_video", new_video_path)
    assert captured["runner"] == ("dup-task", 1)


def test_duplicate_project_returns_409_when_source_video_missing(
    authed_client_no_db,
    tmp_path,
    monkeypatch,
):
    from web.routes import omni_translate as route

    original_task = {
        "id": "missing-source",
        "type": "omni_translate",
        "video_path": str(tmp_path / "missing.mp4"),
        "task_dir": str(tmp_path / "missing-source"),
        "original_filename": "missing.mp4",
        "display_name": "缺源项目",
        "source_language": "es",
        "target_lang": "de",
        "plugin_config": CFG_ASR_CLEAN,
    }

    class StoreStub:
        def get(self, task_id):
            return original_task

        def create(self, *args, **kwargs):
            raise AssertionError("duplicate must not create without source video")

    class RunnerStub:
        def start(self, *args, **kwargs):
            raise AssertionError("duplicate must not start without source video")

    monkeypatch.setattr(route, "store", StoreStub())
    monkeypatch.setattr(route, "omni_pipeline_runner", RunnerStub())
    monkeypatch.setattr(
        route,
        "_query_viewable_project",
        lambda task_id, columns="*", include_deleted=True: {
            "id": task_id,
            "user_id": 1,
            "state_json": json.dumps(original_task, ensure_ascii=False),
            "original_filename": "missing.mp4",
            "display_name": "缺源项目",
        },
    )

    resp = authed_client_no_db.post("/api/omni-translate/missing-source/duplicate")

    assert resp.status_code == 409
    assert "源视频" in resp.get_json()["error"]


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


def test_resume_from_translate_clears_omni_current_and_downstream_state(
    tmp_path, authed_client_no_db, monkeypatch,
):
    from web import store
    from web.routes import omni_translate as omni_module

    resume_calls = []
    monkeypatch.setattr(
        omni_module.omni_pipeline_runner,
        "resume",
        lambda task_id, start_step, user_id=None: resume_calls.append((task_id, start_step, user_id)),
    )

    task_id = "omni-resume-clear-translate"
    store.create(task_id, "/tmp/source.mp4", str(tmp_path))
    script_segments = [{"index": 0, "text": "source"}]
    store.update(
        task_id,
        _user_id=1,
        type="omni_translate",
        plugin_config=CFG_DYNAMIC_ALL,
        source_language="es",
        target_lang="de",
        current_review_step="translate",
        script_segments=script_segments,
        segments=[{"index": 0, "translated": "stale translated"}],
        source_full_text_zh="old source text",
        translations=[{"translated_text": "old shot translation"}],
        localized_translation={"full_text": "old translation"},
        tts_script={"full_text": "old tts"},
        tts_audio_path="/tmp/old-tts.mp3",
        timeline_manifest={"tracks": []},
        corrected_subtitle={"chunks": [{"text": "old subtitle"}]},
        english_asr_result={"utterances": [{"text": "old subtitle asr"}]},
        srt_path="/tmp/old.srt",
        result={"hard_video": "/tmp/old-hard.mp4"},
        exports={"normal": {"archive_url": "/old.zip"}},
        final_compose_summary={"compose_completed": True},
        tts_duration_rounds=[{"round": 1}],
        tts_duration_status="done",
        tts_final_round=1,
        tts_final_reason="old",
        tts_final_distance=0,
        artifacts={
            "alignment": {"title": "keep alignment"},
            "translate": {"title": "old translate"},
            "tts": {"title": "old tts"},
            "subtitle": {"title": "old subtitle"},
            "compose": {"title": "old compose"},
            "export": {"title": "old export"},
        },
        preview_files={
            "source_video": "/tmp/source.mp4",
            "audio_extract": "/tmp/audio.wav",
            "separation_vocals": "/tmp/vocals.wav",
            "tts_full_audio": "/tmp/old-tts.mp3",
            "srt": "/tmp/old.srt",
            "hard_video": "/tmp/old-hard.mp4",
            "soft_video": "/tmp/old-soft.mp4",
        },
        llm_debug_refs={
            "alignment": [{"id": "keep"}],
            "translate": [{"id": "drop-translate"}],
            "tts": [{"id": "drop-tts"}],
        },
        step_model_tags={
            "alignment": "keep-model",
            "translate": "drop-model",
            "tts": "drop-tts-model",
        },
        variants={
            "normal": {
                "label": "普通版",
                "localized_translation": {"full_text": "old translation"},
                "tts_script": {"full_text": "old tts"},
                "segments": [{"text": "old tts segment"}],
                "tts_audio_path": "/tmp/old-tts.mp3",
                "timeline_manifest": {"tracks": []},
                "english_asr_result": {"utterances": [{"text": "old subtitle asr"}]},
                "corrected_subtitle": {"chunks": [{"text": "old subtitle"}]},
                "srt_path": "/tmp/old.srt",
                "result": {"hard_video": "/tmp/old-hard.mp4"},
                "exports": {"archive_url": "/old.zip"},
                "artifacts": {
                    "translate": {"title": "old translate"},
                    "tts": {"title": "old tts"},
                    "subtitle": {"title": "old subtitle"},
                    "compose": {"title": "old compose"},
                    "export": {"title": "old export"},
                },
                "preview_files": {
                    "tts_full_audio": "/tmp/old-tts.mp3",
                    "srt": "/tmp/old.srt",
                    "hard_video": "/tmp/old-hard.mp4",
                },
            }
        },
    )

    resp = authed_client_no_db.post(
        f"/api/omni-translate/{task_id}/resume",
        json={"start_step": "translate"},
    )

    assert resp.status_code == 200, resp.get_json()
    task = store.get(task_id)
    assert task["steps"]["translate"] == "pending"
    assert task["steps"]["tts"] == "pending"
    assert task["current_review_step"] == ""
    assert task["status"] == "running"
    assert task["segments"] == script_segments
    assert task["localized_translation"] == {}
    assert task["tts_script"] == {}
    assert task["tts_audio_path"] == ""
    assert task["corrected_subtitle"] == {}
    assert task["english_asr_result"] == {}
    assert task["srt_path"] == ""
    assert task["result"] == {}
    assert task["exports"] == {}
    assert task["tts_duration_rounds"] == []
    assert task["tts_duration_status"] is None
    assert task["artifacts"] == {"alignment": {"title": "keep alignment"}}
    assert task["preview_files"] == {
        "source_video": "/tmp/source.mp4",
        "audio_extract": "/tmp/audio.wav",
        "separation_vocals": "/tmp/vocals.wav",
    }
    assert task["llm_debug_refs"] == {"alignment": [{"id": "keep"}]}
    assert task["step_model_tags"] == {"alignment": "keep-model"}
    normal = task["variants"]["normal"]
    assert normal["localized_translation"] == {}
    assert normal["tts_script"] == {}
    assert normal["segments"] == []
    assert normal["tts_audio_path"] == ""
    assert normal["corrected_subtitle"] == {}
    assert normal["result"] == {}
    assert normal["exports"] == {}
    assert normal["artifacts"] == {}
    assert normal["preview_files"] == {}
    assert resume_calls == [(task_id, "translate", 1)]


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
