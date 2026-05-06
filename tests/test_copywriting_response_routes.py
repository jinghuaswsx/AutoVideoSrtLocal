from __future__ import annotations


def test_update_inputs_missing_task_returns_json_404(authed_client_no_db):
    resp = authed_client_no_db.put(
        "/api/copywriting/missing-task/inputs",
        json={"product_title": "demo"},
    )

    assert resp.status_code == 404
    assert "error" in resp.get_json()


def test_generate_duplicate_active_task_returns_conflict_payload(authed_client_no_db, monkeypatch):
    from appcore import task_state

    task_state._tasks["cw-duplicate-service"] = {
        "id": "cw-duplicate-service",
        "_user_id": 1,
        "type": "copywriting",
    }
    monkeypatch.setattr(
        "web.routes.copywriting.try_register_active_task",
        lambda *args, **kwargs: False,
        raising=False,
    )

    try:
        resp = authed_client_no_db.post("/api/copywriting/cw-duplicate-service/generate", json={})
    finally:
        task_state._tasks.pop("cw-duplicate-service", None)

    assert resp.status_code == 200
    assert resp.get_json() == {"status": "already_running"}


def test_rewrite_segment_missing_index_returns_json_400(authed_client_no_db):
    resp = authed_client_no_db.post(
        "/api/copywriting/any-task/rewrite-segment",
        json={"instruction": "shorter"},
    )

    assert resp.status_code == 400
    assert "error" in resp.get_json()
