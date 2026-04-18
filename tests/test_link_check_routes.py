import io


def test_link_check_page_renders_form(authed_user_client_no_db):
    response = authed_user_client_no_db.get("/link-check")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="linkCheckForm"' in html
    assert 'name="reference_images"' in html


def test_link_check_page_contains_progress_and_results_shell(authed_user_client_no_db):
    response = authed_user_client_no_db.get("/link-check")
    html = response.get_data(as_text=True)

    assert 'id="linkCheckSummary"' in html
    assert 'id="linkCheckResults"' in html


def test_create_link_check_task_accepts_optional_reference_images(authed_user_client_no_db, monkeypatch):
    from web import store

    created = {}

    def fake_create(task_id, task_dir, **kwargs):
        created.update({"task_id": task_id, "task_dir": task_dir, **kwargs})
        return {"id": task_id, "type": "link_check", "_user_id": 2}

    monkeypatch.setattr(store, "create_link_check", fake_create)
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
            "progress": {"total": 1, "downloaded": 1, "analyzed": 1, "compared": 1, "failed": 0},
            "summary": {"overall_decision": "done"},
            "reference_images": [{"id": "ref-1", "filename": "ref.jpg", "local_path": "C:/tmp/ref.jpg"}],
            "items": [{
                "id": "site-1",
                "kind": "carousel",
                "source_url": "https://img/site.jpg",
                "_local_path": "C:/tmp/site.jpg",
                "analysis": {"decision": "pass"},
                "reference_match": {"status": "matched", "score": 0.9, "reference_id": "ref-1"},
                "status": "done",
                "error": "",
            }],
        },
    )

    response = authed_user_client_no_db.get("/api/link-check/tasks/lc-1")
    payload = response.get_json()

    assert payload["items"][0]["site_preview_url"].endswith("/api/link-check/tasks/lc-1/images/site/site-1")
    assert payload["reference_images"][0]["preview_url"].endswith("/api/link-check/tasks/lc-1/images/reference/ref-1")
