import io
import json


def test_link_check_page_renders_form(authed_user_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.link_check.query", lambda sql, args=(): [])

    response = authed_user_client_no_db.get("/link-check")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="linkCheckProjectForm"' in html
    assert 'name="reference_images"' in html


def test_link_check_page_renders_project_list_contract_without_legacy_shell(authed_user_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.link_check.query", lambda sql, args=(): [])

    response = authed_user_client_no_db.get("/link-check")
    html = response.get_data(as_text=True)

    assert 'id="linkCheckProjectList"' in html
    assert 'id="linkCheckError"' in html
    assert 'id="linkCheckStatus"' in html
    assert 'id="linkCheckSummary"' not in html
    assert 'id="linkCheckResults"' not in html
    assert 'id="linkCheckDetailDialog"' not in html


def test_create_link_check_task_accepts_optional_reference_images(authed_user_client_no_db, monkeypatch):
    from web import store

    created = {}

    def fake_create(task_id, task_dir, **kwargs):
        created.update({"task_id": task_id, "task_dir": task_dir, **kwargs})
        return {"id": task_id, "type": "link_check", "_user_id": 2}

    monkeypatch.setattr(store, "create_link_check", fake_create)
    monkeypatch.setattr("web.routes.link_check.medias.list_languages", lambda: [])
    monkeypatch.setattr(
        "web.routes.link_check.medias.get_language",
        lambda code: {"code": "de", "name_zh": "德语", "enabled": 1},
    )
    monkeypatch.setattr("web.routes.link_check.link_check_runner.start", lambda tid: True)

    response = authed_user_client_no_db.post(
        "/api/link-check/tasks",
        data={
            "link_url": "https://shop.example.com/de/products/demo",
            "target_language": "de",
            "reference_images": [(io.BytesIO(b"fake-image"), "ref-1.jpg")],
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 202
    assert created["target_language"] == "de"
    assert len(created["reference_images"]) == 1


def test_get_task_serializes_preview_urls(authed_user_client_no_db, monkeypatch):
    from web import store

    monkeypatch.setattr(
        "web.routes.link_check.query_one",
        lambda sql, args: {
            "id": args[0],
            "type": "link_check",
            "display_name": "Demo Link Check",
            "status": "done",
            "state_json": json.dumps({}, ensure_ascii=False),
        },
    )
    monkeypatch.setattr(
        store,
        "get",
        lambda task_id: {
            "id": task_id,
            "type": "link_check",
            "_user_id": 2,
            "status": "done",
            "link_url": "https://shop.example.com/de/products/demo",
            "target_language": "de",
            "target_language_name": "德语",
            "progress": {
                "total": 1,
                "downloaded": 1,
                "analyzed": 1,
                "compared": 1,
                "binary_checked": 1,
                "same_image_llm_done": 1,
                "failed": 0,
            },
            "summary": {"overall_decision": "done"},
            "reference_images": [{"id": "ref-1", "filename": "ref.jpg", "local_path": "C:/tmp/ref.jpg"}],
            "items": [
                {
                    "id": "site-1",
                    "kind": "carousel",
                    "source_url": "https://img/site.jpg",
                    "_local_path": "C:/tmp/site.jpg",
                    "analysis": {"decision": "pass", "decision_source": "binary_quick_check"},
                    "reference_match": {"status": "matched", "score": 0.9, "reference_id": "ref-1"},
                    "binary_quick_check": {
                        "status": "pass",
                        "binary_similarity": 0.93,
                        "foreground_overlap": 0.89,
                        "threshold": 0.90,
                        "reason": "ok",
                    },
                    "same_image_llm": {
                        "status": "done",
                        "answer": "是",
                        "channel": "cloud",
                        "channel_label": "Google Cloud (Vertex AI)",
                        "model": "gemini-3.1-flash-lite-preview",
                        "reason": "",
                    },
                    "status": "done",
                    "error": "",
                }
            ],
        },
    )

    response = authed_user_client_no_db.get("/api/link-check/tasks/lc-1")
    payload = response.get_json()

    assert payload["items"][0]["site_preview_url"].endswith("/api/link-check/tasks/lc-1/images/site/site-1")
    assert payload["reference_images"][0]["preview_url"].endswith("/api/link-check/tasks/lc-1/images/reference/ref-1")
    assert payload["items"][0]["binary_quick_check"]["binary_similarity"] == 0.93
    assert payload["items"][0]["same_image_llm"]["answer"] == "是"


def test_get_task_serializes_locale_and_download_evidence(authed_user_client_no_db, monkeypatch):
    from web import store

    locale_evidence = {
        "target_language": "de",
        "requested_url": "https://shop.example.com/de/products/demo",
        "lock_source": "warmup_attempt_2",
        "locked": True,
        "failure_reason": "",
        "attempts": [
            {
                "phase": "initial",
                "requested_url": "https://shop.example.com/products/demo",
                "resolved_url": "https://shop.example.com/products/demo",
                "page_language": "en",
                "locked": False,
            },
            {
                "phase": "warmup",
                "requested_url": "https://shop.example.com/de/products/demo",
                "resolved_url": "https://shop.example.com/de/products/demo",
                "page_language": "de",
                "locked": True,
            },
        ],
    }
    download_evidence = {
        "requested_url": "https://cdn.example.com/site-1.jpg",
        "final_url": "https://cdn.example.com/site-1.jpg",
        "http_status": 200,
        "content_type": "image/jpeg",
        "content_length": 12345,
        "preserved_asset": False,
    }

    monkeypatch.setattr(
        "web.routes.link_check.query_one",
        lambda sql, args: {
            "id": args[0],
            "type": "link_check",
            "display_name": "Demo Link Check",
            "status": "done",
            "state_json": json.dumps(
                {
                    "id": args[0],
                    "type": "link_check",
                    "status": "done",
                    "link_url": "https://shop.example.com/de/products/demo",
                    "target_language": "de",
                    "target_language_name": "德语",
                    "locale_evidence": locale_evidence,
                    "items": [
                        {
                            "id": "site-1",
                            "kind": "carousel",
                            "source_url": "https://img/site.jpg",
                            "download_evidence": download_evidence,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        },
    )
    monkeypatch.setattr(store, "get", lambda task_id: None)

    response = authed_user_client_no_db.get("/api/link-check/tasks/lc-1")
    payload = response.get_json()

    assert payload["locale_evidence"] == locale_evidence
    assert payload["items"][0]["download_evidence"] == download_evidence


def test_get_task_backfills_locale_evidence_defaults_from_empty_or_partial_state(
    authed_user_client_no_db, monkeypatch
):
    from web import store

    current_locale_evidence = {}

    monkeypatch.setattr(
        "web.routes.link_check.query_one",
        lambda sql, args: {
            "id": args[0],
            "type": "link_check",
            "display_name": "Demo Link Check",
            "status": "done",
            "state_json": json.dumps(
                {
                    "id": args[0],
                    "type": "link_check",
                    "status": "done",
                    "link_url": "https://shop.example.com/de/products/demo",
                    "target_language": "de",
                    "target_language_name": "德语",
                    "locale_evidence": current_locale_evidence,
                    "items": [],
                },
                ensure_ascii=False,
            ),
        },
    )
    monkeypatch.setattr(store, "get", lambda task_id: None)

    current_locale_evidence = {}
    response = authed_user_client_no_db.get("/api/link-check/tasks/lc-empty")
    payload = response.get_json()

    assert payload["locale_evidence"] == {
        "target_language": "de",
        "requested_url": "https://shop.example.com/de/products/demo",
        "lock_source": "",
        "locked": False,
        "failure_reason": "",
        "attempts": [],
    }

    current_locale_evidence = {"locked": True}
    response = authed_user_client_no_db.get("/api/link-check/tasks/lc-partial")
    payload = response.get_json()

    assert payload["locale_evidence"] == {
        "target_language": "de",
        "requested_url": "https://shop.example.com/de/products/demo",
        "lock_source": "",
        "locked": True,
        "failure_reason": "",
        "attempts": [],
    }


def test_get_task_normalizes_invalid_locale_evidence_values(authed_user_client_no_db, monkeypatch):
    from web import store

    current_locale_evidence = {}

    monkeypatch.setattr(
        "web.routes.link_check.query_one",
        lambda sql, args: {
            "id": args[0],
            "type": "link_check",
            "display_name": "Demo Link Check",
            "status": "done",
            "state_json": json.dumps(
                {
                    "id": args[0],
                    "type": "link_check",
                    "status": "done",
                    "link_url": "https://shop.example.com/de/products/demo",
                    "target_language": "de",
                    "target_language_name": "德语",
                    "locale_evidence": current_locale_evidence,
                    "items": [],
                },
                ensure_ascii=False,
            ),
        },
    )
    monkeypatch.setattr(store, "get", lambda task_id: None)

    current_locale_evidence = {
        "target_language": None,
        "requested_url": None,
        "lock_source": None,
        "locked": 1,
        "failure_reason": None,
        "attempts": None,
    }
    response = authed_user_client_no_db.get("/api/link-check/tasks/lc-invalid-none")
    payload = response.get_json()

    assert payload["locale_evidence"] == {
        "target_language": "de",
        "requested_url": "https://shop.example.com/de/products/demo",
        "lock_source": "",
        "locked": True,
        "failure_reason": "",
        "attempts": [],
    }

    current_locale_evidence = {
        "requested_url": "https://custom.example/manual",
        "locked": 0,
        "attempts": "oops",
    }
    response = authed_user_client_no_db.get("/api/link-check/tasks/lc-invalid-type")
    payload = response.get_json()

    assert payload["locale_evidence"] == {
        "target_language": "de",
        "requested_url": "https://custom.example/manual",
        "lock_source": "",
        "locked": False,
        "failure_reason": "",
        "attempts": [],
    }

    current_locale_evidence = None
    response = authed_user_client_no_db.get("/api/link-check/tasks/lc-invalid-null-evidence")
    payload = response.get_json()

    assert payload["locale_evidence"] == {
        "target_language": "de",
        "requested_url": "https://shop.example.com/de/products/demo",
        "lock_source": "",
        "locked": False,
        "failure_reason": "",
        "attempts": [],
    }

    current_locale_evidence = "oops"
    response = authed_user_client_no_db.get("/api/link-check/tasks/lc-invalid-string-evidence")
    payload = response.get_json()

    assert payload["locale_evidence"] == {
        "target_language": "de",
        "requested_url": "https://shop.example.com/de/products/demo",
        "lock_source": "",
        "locked": False,
        "failure_reason": "",
        "attempts": [],
    }

    current_locale_evidence = []
    response = authed_user_client_no_db.get("/api/link-check/tasks/lc-invalid-empty-list-evidence")
    payload = response.get_json()

    assert payload["locale_evidence"] == {
        "target_language": "de",
        "requested_url": "https://shop.example.com/de/products/demo",
        "lock_source": "",
        "locked": False,
        "failure_reason": "",
        "attempts": [],
    }

    current_locale_evidence = [1, 2]
    response = authed_user_client_no_db.get("/api/link-check/tasks/lc-invalid-list-evidence")
    payload = response.get_json()

    assert payload["locale_evidence"] == {
        "target_language": "de",
        "requested_url": "https://shop.example.com/de/products/demo",
        "lock_source": "",
        "locked": False,
        "failure_reason": "",
        "attempts": [],
    }
