from __future__ import annotations

import base64
import io
import json


def _denylisted_english_redub_user(username: str) -> dict:
    return {
        "id": 1,
        "username": username,
        "role": "user",
        "is_active": 1,
        "permissions": '{"english_redub": true}',
    }


def test_english_redub_list_page_is_registered(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.english_redub.recover_all_interrupted_tasks",
        lambda: None,
    )
    monkeypatch.setattr(
        "web.routes.english_redub.translation_route_store.list_projects_with_creator",
        lambda **kwargs: [],
    )

    resp = authed_client_no_db.get("/english-redub")

    assert resp.status_code == 200
    assert "英语视频重新配音" in resp.get_data(as_text=True)


def test_english_redub_rejects_denylisted_user_page_and_api(
    authed_client_no_db,
    monkeypatch,
):
    monkeypatch.setattr(
        "web.auth.get_by_id",
        lambda user_id: _denylisted_english_redub_user("zhangwei"),
    )
    with authed_client_no_db.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True

    page_resp = authed_client_no_db.get("/english-redub")
    api_resp = authed_client_no_db.post("/api/english-redub/start")

    assert page_resp.status_code == 302
    assert page_resp.headers["Location"].endswith("/")
    assert api_resp.status_code == 403


def test_english_redub_allows_other_default_user(
    authed_user_client_no_db,
    monkeypatch,
):
    monkeypatch.setattr(
        "web.routes.english_redub.recover_all_interrupted_tasks",
        lambda: None,
    )
    monkeypatch.setattr(
        "web.routes.english_redub.translation_route_store.list_projects_with_creator",
        lambda **kwargs: [],
    )

    resp = authed_user_client_no_db.get("/english-redub")

    assert resp.status_code == 200
    assert "英语视频重新配音" in resp.get_data(as_text=True)


def test_english_redub_start_persists_fixed_english_and_script_mode(
    authed_client_no_db,
    monkeypatch,
    tmp_path,
):
    updates: dict = {}
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")

    monkeypatch.setattr(
        "web.routes.english_redub.save_uploaded_video",
        lambda *args, **kwargs: (str(video_path), 1, "video/mp4"),
    )
    monkeypatch.setattr(
        "web.routes.english_redub._ensure_uploaded_video_thumbnail",
        lambda *args, **kwargs: "",
    )
    monkeypatch.setattr(
        "web.routes.english_redub.english_redub_pipeline_runner.start",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr("web.routes.english_redub.store.create", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "web.routes.english_redub.store.update",
        lambda task_id, **kwargs: updates.update(kwargs),
    )
    monkeypatch.setattr(
        "web.routes.english_redub.store.set_preview_file",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "web.routes.english_redub._resolve_name_conflict",
        lambda user_id, desired_name: desired_name,
    )

    resp = authed_client_no_db.post(
        "/api/english-redub/start",
        data={
            "video": (io.BytesIO(b"video"), "demo.mp4"),
            "script_mode": "rewrite",
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 201
    assert updates["type"] == "english_redub"
    assert updates["source_language"] == "en"
    assert updates["target_lang"] == "en"
    assert updates["script_mode"] == "rewrite"


def test_english_redub_start_defaults_script_mode_to_original(
    authed_client_no_db,
    monkeypatch,
    tmp_path,
):
    updates: dict = {}
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")

    monkeypatch.setattr(
        "web.routes.english_redub.save_uploaded_video",
        lambda *args, **kwargs: (str(video_path), 1, "video/mp4"),
    )
    monkeypatch.setattr(
        "web.routes.english_redub._ensure_uploaded_video_thumbnail",
        lambda *args, **kwargs: "",
    )
    monkeypatch.setattr(
        "web.routes.english_redub.english_redub_pipeline_runner.start",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr("web.routes.english_redub.store.create", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "web.routes.english_redub.store.update",
        lambda task_id, **kwargs: updates.update(kwargs),
    )
    monkeypatch.setattr(
        "web.routes.english_redub.store.set_preview_file",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "web.routes.english_redub._resolve_name_conflict",
        lambda user_id, desired_name: desired_name,
    )

    resp = authed_client_no_db.post(
        "/api/english-redub/start",
        data={"video": (io.BytesIO(b"video"), "demo.mp4")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 201
    assert updates["script_mode"] == "original"


def test_english_redub_start_rejects_invalid_script_mode(
    authed_client_no_db,
):
    resp = authed_client_no_db.post(
        "/api/english-redub/start",
        data={
            "video": (io.BytesIO(b"video"), "demo.mp4"),
            "script_mode": "invalid",
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 400
    assert "script_mode" in resp.get_json()["error"]


def test_english_redub_voice_library_accepts_pagination(
    authed_client_no_db,
    monkeypatch,
):
    captured: dict = {}
    monkeypatch.setattr(
        "web.routes.english_redub._query_viewable_project",
        lambda task_id, columns: {
            "state_json": '{"target_lang":"en","steps":{"voice_match":"waiting"}}',
            "user_id": 1,
        },
    )

    def fake_list_voices(**kwargs):
        captured.update(kwargs)
        return {"items": [{"voice_id": "voice-page-3", "name": "Page 3"}], "total": 450}

    monkeypatch.setattr("appcore.voice_library_browse.list_voices", fake_list_voices)
    monkeypatch.setattr("appcore.video_translate_defaults.resolve_default_voice", lambda *args, **kwargs: None)

    resp = authed_client_no_db.get("/api/english-redub/task-voice-pages/voice-library?page=3&page_size=150")

    assert resp.status_code == 200
    assert resp.get_json()["page"] == 3
    assert resp.get_json()["page_size"] == 150
    assert captured == {
        "language": "en",
        "gender": None,
        "q": None,
        "page": 3,
        "page_size": 150,
    }
    assert resp.get_json()["items"][0]["voice_id"] == "voice-page-3"


def test_english_redub_voice_ai_ranking_rerun_uses_saved_candidates(
    authed_client_no_db,
    monkeypatch,
    tmp_path,
):
    source = tmp_path / "voice_ai_ranking" / "source_sample.mp3"
    source.parent.mkdir()
    source.write_bytes(b"source-audio")
    state = {
        "task_dir": str(tmp_path),
        "target_lang": "en",
        "utterances": [{"text": "hello"}],
        "voice_match_candidates": [
            {"voice_id": "v1", "name": "A"},
            {"voice_id": "v2", "name": "B"},
            {"voice_id": "v3", "name": "C"},
            {"voice_id": "v4", "name": "D"},
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
        "web.routes.english_redub._query_viewable_project",
        lambda task_id, columns: {
            "state_json": json.dumps(state),
            "user_id": 1,
        },
    )
    monkeypatch.setattr(
        "web.routes.english_redub.save_project_state",
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
            "rankings": [{"voice_id": "v2", "llm_rank": 1, "reason_summary": "更贴近原声"}],
            "candidates": [
                {"voice_id": "v1", "name": "A"},
                {"voice_id": "v2", "name": "B", "llm_rank": 1, "llm_reason_summary": "更贴近原声"},
            ],
            "model": "google/gemini-3.5-flash",
            "provider": "openrouter",
            "candidate_limit": 3,
            "debug": {"status": "done", "result": {"visual": {"rankings": []}}},
            "usage_log_id": 34567,
        }

    monkeypatch.setattr(
        "appcore.voice_ai_ranking.rank_voice_candidates",
        fake_rank_voice_candidates,
    )

    resp = authed_client_no_db.post(
        "/api/english-redub/task-ai/voice-ai-ranking",
        json={"candidate_limit": 3},
    )

    payload = resp.get_json()
    assert resp.status_code == 200
    assert seen["candidate_limit"] == 3
    assert seen["source_audio_path"] == source
    assert len(seen["candidates"]) == 4
    assert saved["voice_ai_rankings"][0]["voice_id"] == "v2"
    assert saved["voice_match_candidates"][1]["llm_rank"] == 1
    assert saved["voice_ai_rank_provider"] == "openrouter"
    assert saved["voice_ai_rank_candidate_limit"] == 3
    assert saved["voice_ai_rank_usage_log_id"] == 34567
    assert seen["state_update"]["voice_ai_rank_usage_log_id"] == 34567
    assert payload["voice_ai_rank_status"] == "done"
    assert payload["voice_ai_rankings"][0]["llm_rank"] == 1
    assert payload["voice_ai_rank_usage_log_id"] == 34567
    assert payload["candidate_limit"] == 3


def test_english_redub_analysis_route_starts_runner(
    authed_client_no_db,
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        "web.routes.english_redub.translation_route_store.get_active_project_id",
        lambda task_id, user_id, project_type, query_one_func=None: {"id": task_id},
    )
    monkeypatch.setattr(
        "web.routes.english_redub.store.get",
        lambda task_id: {"steps": {"analysis": "idle"}, "_user_id": 1},
    )
    monkeypatch.setattr(
        "web.routes.english_redub.english_redub_pipeline_runner.run_analysis",
        lambda task_id, user_id=None: calls.append((task_id, user_id)) or True,
        raising=False,
    )

    resp = authed_client_no_db.post("/api/english-redub/task-analysis/analysis/run")

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "started"
    assert calls == [("task-analysis", 1)]


def test_english_redub_analysis_route_returns_409_when_runner_is_active(
    authed_client_no_db,
    monkeypatch,
):
    monkeypatch.setattr(
        "web.routes.english_redub.translation_route_store.get_active_project_id",
        lambda task_id, user_id, project_type, query_one_func=None: {"id": task_id},
    )
    monkeypatch.setattr(
        "web.routes.english_redub.store.get",
        lambda task_id: {"steps": {"analysis": "idle"}, "_user_id": 1},
    )
    monkeypatch.setattr(
        "web.routes.english_redub.english_redub_pipeline_runner.run_analysis",
        lambda task_id, user_id=None: False,
        raising=False,
    )

    resp = authed_client_no_db.post("/api/english-redub/task-analysis/analysis/run")

    assert resp.status_code == 409
    assert "正在运行" in resp.get_json()["error"]


def test_english_redub_voice_ai_ranking_uses_cached_gender_result(
    authed_client_no_db,
    monkeypatch,
):
    from appcore.voice_ai_rank_cache import cache_rank_result

    candidates = [{"voice_id": "female-a", "similarity": 0.91}]
    state = {
        "target_lang": "en",
        "voice_match_candidates": candidates,
        "voice_ai_rank_active_key": "female",
    }
    cache_rank_result(
        state,
        key="female",
        candidates=[{
            "voice_id": "female-a",
            "similarity": 0.91,
            "llm_rank": 1,
            "llm_reason_summary": "更自然",
        }],
        rankings=[{"voice_id": "female-a", "llm_rank": 1, "reason_summary": "更自然"}],
        status="done",
        model="google/gemini-3.5-flash",
        provider="openrouter",
        debug={"status": "done", "result": {"visual": {"rankings": []}}},
        candidate_limit=3,
    )
    saved = {}

    monkeypatch.setattr(
        "web.routes.english_redub._query_viewable_project",
        lambda task_id, columns: {
            "state_json": json.dumps(state),
            "user_id": 1,
        },
    )
    monkeypatch.setattr(
        "web.routes.english_redub.save_project_state",
        lambda task_id, payload, execute_func=None: saved.update(payload),
    )
    monkeypatch.setattr(
        "appcore.voice_ai_ranking.rank_voice_candidates",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("cache should skip LLM call")),
    )

    resp = authed_client_no_db.post(
        "/api/english-redub/task-ai/voice-ai-ranking",
        json={"gender": "female"},
    )

    payload = resp.get_json()
    assert resp.status_code == 200
    assert payload["voice_ai_rank_cached"] is True
    assert payload["voice_ai_rank_cache_key"] == "female"
    assert payload["candidates"][0]["llm_rank"] == 1
    assert saved["voice_ai_rank_active_key"] == "female"
    assert saved["voice_match_candidates"][0]["llm_rank"] == 1


def test_english_redub_rematch_applies_cached_gender_ai_rank(
    authed_client_no_db,
    monkeypatch,
):
    from appcore.voice_ai_rank_cache import cache_rank_result

    candidates = [{"voice_id": "male-a", "similarity": 0.88}]
    state = {
        "target_lang": "en",
        "voice_match_query_embedding": base64.b64encode(b"fake-embedding").decode("ascii"),
        "voice_match_candidates": [{"voice_id": "all-a", "similarity": 0.9}],
        "voice_ai_rank_status": "done",
        "voice_ai_rank_debug": {"status": "done"},
        "voice_ai_rankings": [{"voice_id": "all-a", "llm_rank": 1}],
    }
    cache_rank_result(
        state,
        key="male",
        candidates=[{
            "voice_id": "male-a",
            "similarity": 0.88,
            "llm_rank": 2,
            "llm_reason_summary": "男声缓存",
        }],
        rankings=[{"voice_id": "male-a", "llm_rank": 2, "reason_summary": "男声缓存"}],
        status="done",
        model="google/gemini-3.5-flash",
        provider="openrouter",
        debug={"status": "done", "result": {"visual": {"rankings": []}}},
        candidate_limit=3,
    )
    saved = {}

    monkeypatch.setattr(
        "web.routes.english_redub._query_viewable_project",
        lambda task_id, columns: {
            "state_json": json.dumps(state),
            "user_id": 1,
        },
    )
    monkeypatch.setattr(
        "web.routes.english_redub.save_project_state",
        lambda task_id, payload, execute_func=None: saved.update(payload),
    )
    monkeypatch.setattr("appcore.english_redub_settings.get_voice_match_strategy", lambda: "legacy")
    monkeypatch.setattr("appcore.video_translate_defaults.resolve_default_voice", lambda *args, **kwargs: None)
    monkeypatch.setattr("pipeline.voice_embedding.deserialize_embedding", lambda raw: "decoded")
    monkeypatch.setattr("pipeline.voice_match.match_candidates", lambda *args, **kwargs: candidates)
    monkeypatch.setattr(
        "appcore.voice_library_browse.fetch_voices_by_ids",
        lambda **kwargs: [{"voice_id": "male-a", "name": "Male A", "gender": "male"}],
    )

    resp = authed_client_no_db.post(
        "/api/english-redub/task-ai/rematch",
        json={"gender": "male"},
    )

    payload = resp.get_json()
    assert resp.status_code == 200
    assert payload["voice_ai_rank_cached"] is True
    assert payload["voice_ai_rank_cache_key"] == "male"
    assert payload["candidates"][0]["llm_rank"] == 2
    assert payload["voice_ai_rank_status"] == "done"
    assert saved["voice_ai_rank_active_key"] == "male"
    assert saved["voice_match_candidates"][0]["llm_rank"] == 2


def test_english_redub_rematch_derives_gender_rank_from_all_ai_rank_cache(
    authed_client_no_db,
    monkeypatch,
):
    from appcore.voice_ai_rank_cache import cache_rank_result

    female_candidates = [
        {"voice_id": "female-a", "similarity": 0.91},
        {"voice_id": "female-b", "similarity": 0.89},
        {"voice_id": "female-c", "similarity": 0.81},
    ]
    state = {
        "target_lang": "en",
        "voice_match_query_embedding": base64.b64encode(b"fake-embedding").decode("ascii"),
    }
    cache_rank_result(
        state,
        key="all",
        candidates=[
            {"voice_id": "male-a", "similarity": 0.95, "llm_rank": 1, "llm_reason_summary": "男声第一"},
            {"voice_id": "female-a", "similarity": 0.91, "llm_rank": 2, "llm_reason_summary": "女声第一"},
            {"voice_id": "male-b", "similarity": 0.9, "llm_rank": 3, "llm_reason_summary": "男声第二"},
            {"voice_id": "female-b", "similarity": 0.89, "llm_rank": 4, "llm_reason_summary": "女声第二"},
        ],
        rankings=[
            {"voice_id": "male-a", "llm_rank": 1, "reason_summary": "男声第一"},
            {"voice_id": "female-a", "llm_rank": 2, "reason_summary": "女声第一"},
            {"voice_id": "male-b", "llm_rank": 3, "reason_summary": "男声第二"},
            {"voice_id": "female-b", "llm_rank": 4, "reason_summary": "女声第二"},
        ],
        status="done",
        model="google/gemini-3.5-flash",
        provider="openrouter",
        debug={"status": "done", "result": {"visual": {"rankings": []}}},
        candidate_limit=10,
    )
    saved = {}

    monkeypatch.setattr(
        "web.routes.english_redub._query_viewable_project",
        lambda task_id, columns: {
            "state_json": json.dumps(state),
            "user_id": 1,
        },
    )
    monkeypatch.setattr(
        "web.routes.english_redub.save_project_state",
        lambda task_id, payload, execute_func=None: saved.update(payload),
    )
    monkeypatch.setattr("appcore.english_redub_settings.get_voice_match_strategy", lambda: "legacy")
    monkeypatch.setattr("appcore.video_translate_defaults.resolve_default_voice", lambda *args, **kwargs: None)
    monkeypatch.setattr("pipeline.voice_embedding.deserialize_embedding", lambda raw: "decoded")
    monkeypatch.setattr("pipeline.voice_match.match_candidates", lambda *args, **kwargs: female_candidates)
    monkeypatch.setattr(
        "appcore.voice_library_browse.fetch_voices_by_ids",
        lambda **kwargs: [
            {"voice_id": "female-a", "name": "Female A", "gender": "female"},
            {"voice_id": "female-b", "name": "Female B", "gender": "female"},
            {"voice_id": "female-c", "name": "Female C", "gender": "female"},
        ],
    )

    resp = authed_client_no_db.post(
        "/api/english-redub/task-ai/rematch",
        json={"gender": "female"},
    )

    payload = resp.get_json()
    assert resp.status_code == 200
    assert payload["voice_ai_rank_cache_key"] == "female"
    assert payload["voice_ai_rank_status"] == "derived_from_all"
    assert [(row["voice_id"], row.get("llm_rank")) for row in payload["candidates"]] == [
        ("female-a", 1),
        ("female-b", 2),
        ("female-c", None),
    ]
    assert saved["voice_ai_rank_active_key"] == "female"
    assert saved["voice_ai_rankings"] == [
        {"voice_id": "female-a", "llm_rank": 1, "reason_summary": "女声第一"},
        {"voice_id": "female-b", "llm_rank": 2, "reason_summary": "女声第二"},
    ]
