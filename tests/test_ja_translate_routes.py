from __future__ import annotations

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


def test_multi_translate_start_routes_ja_to_ja_module(tmp_path, authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.multi_translate.OUTPUT_DIR", str(tmp_path / "multi-output"))
    monkeypatch.setattr("web.routes.multi_translate.UPLOAD_DIR", str(tmp_path / "multi-uploads"))
    monkeypatch.setattr("web.routes.ja_translate.OUTPUT_DIR", str(tmp_path / "ja-output"))
    monkeypatch.setattr("web.routes.ja_translate.UPLOAD_DIR", str(tmp_path / "ja-uploads"))
    monkeypatch.setattr("web.routes.ja_translate.db_query_one", lambda sql, args: None)
    monkeypatch.setattr("web.routes.ja_translate.db_execute", lambda sql, args: None)
    started = {}
    monkeypatch.setattr(
        "web.routes.ja_translate.ja_pipeline_runner.start",
        lambda task_id, user_id=None: started.update({"task_id": task_id, "user_id": user_id}),
    )

    response = authed_client_no_db.post(
        "/api/multi-translate/start",
        data={
            "target_lang": "ja",
            "video": (io.BytesIO(b"ja-video"), "demo.mp4"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    payload = response.get_json()
    from web import store

    task = store.get(payload["task_id"])
    assert task["type"] == "ja_translate"
    assert task["target_lang"] == "ja"
    assert payload["redirect_url"] == f"/ja-translate/{payload['task_id']}"
    assert started["task_id"] == payload["task_id"]


def test_layout_contains_ja_translate_menu_entry():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "layout.html").read_text(encoding="utf-8")

    assert 'href="/ja-translate"' in template
    assert "视频翻译（日语）" in template


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
