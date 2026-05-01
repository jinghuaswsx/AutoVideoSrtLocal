from __future__ import annotations

import base64
import json
import io
from pathlib import Path
from unittest.mock import patch


def test_ja_translate_list_page_renders(authed_client_no_db):
    with patch("web.routes.ja_translate.db_query", return_value=[]), \
         patch("appcore.settings.get_retention_hours", return_value=72), \
         patch("web.routes.ja_translate.recover_all_interrupted_tasks"):
        resp = authed_client_no_db.get("/ja-translate")

    assert resp.status_code == 200
    assert "视频翻译（日语）".encode("utf-8") in resp.data


def test_ja_translate_start_creates_ja_task_and_starts_ja_runner(tmp_path, authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.ja_translate.OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr("web.routes.ja_translate.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("web.routes.ja_translate.db_query_one", lambda sql, args: None)
    monkeypatch.setattr("web.routes.ja_translate.db_execute", lambda sql, args: None)
    started = {}
    monkeypatch.setattr(
        "web.routes.ja_translate.ja_pipeline_runner.start",
        lambda task_id, user_id=None: started.update({"task_id": task_id, "user_id": user_id}),
    )

    response = authed_client_no_db.post(
        "/api/ja-translate/start",
        data={"video": (io.BytesIO(b"ja-video"), "demo.mp4")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    payload = response.get_json()
    from web import store

    task = store.get(payload["task_id"])
    assert task["type"] == "ja_translate"
    assert task["target_lang"] == "ja"
    assert task["source_language"] == "en"
    assert task["delivery_mode"] == "local_primary"
    assert payload["redirect_url"] == f"/ja-translate/{payload['task_id']}"
    assert started["task_id"] == payload["task_id"]


def test_multi_translate_start_keeps_ja_on_multi_module(tmp_path, authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.multi_translate.OUTPUT_DIR", str(tmp_path / "multi-output"))
    monkeypatch.setattr("web.routes.multi_translate.UPLOAD_DIR", str(tmp_path / "multi-uploads"))
    monkeypatch.setattr("web.routes.multi_translate.db_query_one", lambda sql, args: None)
    started = {}
    monkeypatch.setattr(
        "web.routes.multi_translate.multi_pipeline_runner.start",
        lambda task_id, user_id=None: started.update({"task_id": task_id, "user_id": user_id}),
    )

    response = authed_client_no_db.post(
        "/api/multi-translate/start",
        data={
            "target_lang": "ja",
            "source_language": "en",
            "video": (io.BytesIO(b"ja-video"), "demo.mp4"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    payload = response.get_json()
    from web import store

    task = store.get(payload["task_id"])
    assert task["type"] == "multi_translate"
    assert task["target_lang"] == "ja"
    assert started["task_id"] == payload["task_id"]


def test_layout_hides_ja_translate_menu_entry():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "layout.html").read_text(encoding="utf-8")

    assert 'href="/ja-translate"' not in template


def test_ja_translate_detail_falls_back_to_in_memory_task_when_db_type_is_stale(
    authed_client_no_db,
    monkeypatch,
):
    monkeypatch.setattr("web.routes.ja_translate.recover_project_if_needed", lambda task_id, project_type: None)
    monkeypatch.setattr("web.routes.ja_translate.db_query_one", lambda *args, **kwargs: None)

    from web import store

    task = store.create(
        "ja-detail-fallback",
        "/tmp/demo.mp4",
        "/tmp/ja-detail-fallback",
        original_filename="demo.mp4",
        user_id=1,
    )
    store.update(
        task["id"],
        type="ja_translate",
        display_name="Demo JA",
        target_lang="ja",
        source_language="en",
    )

    response = authed_client_no_db.get(f"/ja-translate/{task['id']}")

    assert response.status_code == 200
    assert "Demo JA".encode("utf-8") in response.data


def test_ja_voice_library_route_returns_shared_payload(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.ja_translate.db_query_one",
        lambda *args, **kwargs: {
            "state_json": json.dumps(
                {"target_lang": "ja", "steps": {"extract": "done", "asr": "done", "voice_match": "waiting"}},
                ensure_ascii=False,
            ),
            "user_id": 1,
        },
    )
    monkeypatch.setattr("appcore.voice_library_browse.list_voices", lambda **kwargs: {"items": [{"voice_id": "ja-1"}], "total": 1})
    monkeypatch.setattr("appcore.video_translate_defaults.resolve_default_voice", lambda *args, **kwargs: None)

    resp = authed_client_no_db.get("/api/ja-translate/task-ja/voice-library")

    assert resp.status_code == 200
    assert resp.get_json()["items"][0]["voice_id"] == "ja-1"
    assert resp.get_json()["voice_match_ready"] is True


def test_ja_confirm_voice_route_persists_selection_and_resumes_alignment(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.ja_translate.db_query_one",
        lambda *args, **kwargs: {"state_json": json.dumps({"target_lang": "ja"}, ensure_ascii=False)},
    )
    monkeypatch.setattr("web.routes.ja_translate.db_execute", lambda *args, **kwargs: None)
    resumed = {}
    monkeypatch.setattr("appcore.task_state.update", lambda task_id, **kwargs: resumed.update({"task_id": task_id, "kwargs": kwargs}))
    monkeypatch.setattr("appcore.task_state.set_step", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.task_state.set_current_review_step", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "web.routes.ja_translate.ja_pipeline_runner.resume",
        lambda task_id, start_step, user_id=None: resumed.update({"resume_step": start_step, "user_id": user_id}),
    )
    monkeypatch.setattr("appcore.video_translate_defaults.resolve_default_voice", lambda *args, **kwargs: "ja-default")

    resp = authed_client_no_db.post("/api/ja-translate/task-ja/confirm-voice", json={})

    assert resp.status_code == 200
    assert resumed["kwargs"]["selected_voice_id"] == "ja-default"
    assert resumed["resume_step"] == "alignment"


def test_ja_round_file_route_maps_shared_kind_names(tmp_path, authed_client_no_db, monkeypatch):
    from web.routes import ja_translate as r

    task_dir = tmp_path / "task-ja"
    task_dir.mkdir()
    target = task_dir / "ja_localized_rewrite_messages.round_2.json"
    target.write_text('{"ok": true}', encoding="utf-8")
    monkeypatch.setattr(r, "_get_viewable_task", lambda task_id: {"task_dir": str(task_dir)})

    resp = authed_client_no_db.get("/api/ja-translate/task-ja/round-file/2/localized_rewrite_messages")

    assert resp.status_code == 200
    assert resp.mimetype == "application/json"


def test_ja_rematch_route_reuses_saved_embedding(authed_client_no_db):
    state = {
        "target_lang": "ja",
        "voice_match_query_embedding": base64.b64encode(b"fake-embedding").decode("ascii"),
    }
    with patch(
        "web.routes.ja_translate.db_query_one",
        return_value={"state_json": json.dumps(state, ensure_ascii=False)},
    ), patch(
        "web.routes.ja_translate.db_execute",
    ), patch(
        "appcore.video_translate_defaults.resolve_default_voice",
        return_value="ja-default",
    ), patch(
        "pipeline.voice_embedding.deserialize_embedding",
        return_value="decoded-embedding",
    ), patch(
        "pipeline.voice_match.match_candidates",
        return_value=[{"voice_id": "ja-voice-b", "similarity": 0.91}],
    ) as m_match, patch(
        "appcore.voice_library_browse.fetch_voices_by_ids",
        return_value=[{"voice_id": "ja-voice-b", "name": "JaB", "gender": "female"}],
    ) as m_fetch:
        resp = authed_client_no_db.post(
            "/api/ja-translate/task-ja/rematch",
            json={"gender": "female"},
        )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["candidates"][0]["voice_id"] == "ja-voice-b"
    assert payload["extra_items"][0]["voice_id"] == "ja-voice-b"
    assert m_match.call_args.kwargs["exclude_voice_ids"] == {"ja-default"}
    assert m_match.call_args.kwargs["top_k"] == 10
    assert m_fetch.call_args.kwargs["voice_ids"] == ["ja-voice-b"]
