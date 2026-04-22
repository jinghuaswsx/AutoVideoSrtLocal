import base64
import io
import json
from unittest.mock import patch


def test_list_page_renders(authed_client_no_db):
    with patch("web.routes.multi_translate.db_query", return_value=[]), \
         patch("appcore.settings.get_retention_hours", return_value=72), \
         patch("appcore.task_recovery.recover_all_interrupted_tasks"):
        resp = authed_client_no_db.get("/multi-translate")
    assert resp.status_code == 200
    assert "多语种视频翻译".encode("utf-8") in resp.data


def test_list_filters_by_lang(authed_client_no_db):
    with patch("web.routes.multi_translate.db_query") as m_q, \
         patch("appcore.settings.get_retention_hours", return_value=72), \
         patch("appcore.task_recovery.recover_all_interrupted_tasks"):
        m_q.return_value = []
        authed_client_no_db.get("/multi-translate?lang=de")
    sql = m_q.call_args.args[0]
    assert "type = 'multi_translate'" in sql
    args = m_q.call_args.args[1]
    assert "de" in args


def test_detail_404_for_other_user(authed_client_no_db):
    with patch("web.routes.multi_translate.db_query_one", return_value=None), \
         patch("appcore.task_recovery.recover_project_if_needed"):
        resp = authed_client_no_db.get("/multi-translate/unknown")
    assert resp.status_code == 404


def test_rematch_excludes_default_voice_from_top10(authed_client_no_db):
    state = {
        "target_lang": "de",
        "voice_match_query_embedding": base64.b64encode(b"fake-embedding").decode("ascii"),
    }
    with patch(
        "web.routes.multi_translate.db_query_one",
        return_value={"state_json": json.dumps(state, ensure_ascii=False)},
    ), patch(
        "web.routes.multi_translate.db_execute",
    ), patch(
        "appcore.video_translate_defaults.resolve_default_voice",
        return_value="default-voice-id",
    ), patch(
        "pipeline.voice_embedding.deserialize_embedding",
        return_value="decoded-embedding",
    ), patch(
        "pipeline.voice_match.match_candidates",
        return_value=[{"voice_id": "voice-b", "similarity": 0.91}],
    ) as m_match:
        resp = authed_client_no_db.post(
            "/api/multi-translate/task-1/rematch",
            json={"gender": "female"},
        )

    assert resp.status_code == 200
    assert resp.get_json()["candidates"][0]["voice_id"] == "voice-b"
    assert m_match.call_args.kwargs["exclude_voice_ids"] == {"default-voice-id"}


def test_multi_translate_start_accepts_local_multipart_and_marks_local_primary(tmp_path, authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.multi_translate.OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr("web.routes.multi_translate.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("web.routes.multi_translate.db_query_one", lambda sql, args: None)
    monkeypatch.setattr("web.routes.multi_translate.db_execute", lambda sql, args: None)
    started = {}
    monkeypatch.setattr(
        "web.routes.multi_translate.multi_pipeline_runner.start",
        lambda task_id, user_id=None: started.update({"task_id": task_id, "user_id": user_id}),
    )

    response = authed_client_no_db.post(
        "/api/multi-translate/start",
        data={
            "target_lang": "de",
            "video": (io.BytesIO(b"multi-video"), "demo.mp4"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    payload = response.get_json()
    from web import store

    task = store.get(payload["task_id"])
    assert task["type"] == "multi_translate"
    assert task["target_lang"] == "de"
    assert task["delivery_mode"] == "local_primary"
    assert task["source_tos_key"] == ""
    assert task["source_object_info"]["content_type"] == "video/mp4"
    assert task["source_object_info"]["file_size"] == len(b"multi-video")
    assert task["source_object_info"]["storage_backend"] == "local"
    assert task["source_object_info"]["original_filename"] == "demo.mp4"
    assert task["source_object_info"]["uploaded_at"]
    assert started["task_id"] == payload["task_id"]


def test_multi_translate_list_page_uses_local_multipart_upload():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "multi_translate_list.html").read_text(encoding="utf-8")

    assert "new FormData(uploadForm)" in template
    assert "fetch('/api/multi-translate/start'" in template
    assert "/api/multi-translate/bootstrap" not in template
    assert "/api/multi-translate/complete" not in template
    assert "/api/multi-translate/compat-bootstrap" not in template
    assert "/api/multi-translate/compat-complete" not in template
    assert "xhr.open('PUT'" not in template


def test_multi_translate_complete_rejects_new_pure_tos_creation(authed_client_no_db):
    resp = authed_client_no_db.post(
        "/api/multi-translate/complete",
        json={
            "task_id": "multi-task-from-tos",
            "object_key": "uploads/1/multi-task-from-tos/demo.mp4",
            "original_filename": "demo.mp4",
            "target_lang": "de",
        },
    )

    assert resp.status_code == 410
    assert "本地" in resp.get_json()["error"]
