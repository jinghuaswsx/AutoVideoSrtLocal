def test_fine_ai_evaluation_routes_delegate_to_service(authed_client_no_db, monkeypatch):
    from web.routes import medias as routes

    calls = []
    monkeypatch.setattr(routes.medias, "get_product", lambda pid: {"id": pid, "user_id": 1})
    monkeypatch.setattr(routes, "_can_access_product", lambda product: True)

    class FakeService:
        def create_run(self, product_id, **kwargs):
            calls.append(("create", product_id, kwargs))
            return {
                "evaluation_run_id": "eval_test",
                "product_id": str(product_id),
                "status": "queued",
                "countries": ["DE", "FR", "IT", "ES", "JP"],
                "created_at": "2026-05-22T00:00:00Z",
            }

        def start_run_async(self, evaluation_run_id):
            calls.append(("start", evaluation_run_id))
            return True

        def get_status(self, product_id, evaluation_run_id):
            calls.append(("status", product_id, evaluation_run_id))
            return {
                "evaluation_run_id": evaluation_run_id,
                "product_id": str(product_id),
                "status": "running",
                "progress": {"current_step": "country_evaluation_DE"},
            }

        def get_result(self, product_id, evaluation_run_id):
            calls.append(("result", product_id, evaluation_run_id))
            return {"evaluation_run_id": evaluation_run_id, "product_id": str(product_id), "status": "completed"}

        def get_latest_result(self, product_id):
            calls.append(("latest", product_id))
            return {"evaluation_run_id": "eval_test", "product_id": str(product_id), "status": "completed"}

        def rerun_country(self, product_id, evaluation_run_id, country_code, **kwargs):
            calls.append(("rerun", product_id, evaluation_run_id, country_code, kwargs))
            return {
                "evaluation_run_id": evaluation_run_id,
                "product_id": str(product_id),
                "country_code": country_code,
                "status": "running",
            }

    monkeypatch.setattr("web.routes.medias.fine_ai_evaluation.get_service", lambda: FakeService())

    post = authed_client_no_db.post(
        "/medias/api/products/123/ai-evaluation",
        json={"force_refresh": False, "countries": ["DE", "FR", "IT", "ES", "JP"]},
    )
    status = authed_client_no_db.get("/medias/api/products/123/ai-evaluation/eval_test/status")
    result = authed_client_no_db.get("/medias/api/products/123/ai-evaluation/eval_test")
    latest = authed_client_no_db.get("/medias/api/products/123/ai-evaluation/latest")
    rerun = authed_client_no_db.post(
        "/medias/api/products/123/ai-evaluation/eval_test/countries/DE/rerun",
        json={"force_refresh": True},
    )

    assert post.status_code == 202
    assert status.status_code == 200
    assert result.status_code == 200
    assert latest.status_code == 200
    assert rerun.status_code == 202
    assert post.get_json()["success"] is True
    assert status.get_json()["data"]["progress"]["current_step"] == "country_evaluation_DE"
    assert calls[0][0] == "create"
    assert ("start", "eval_test") in calls


def test_fine_ai_evaluation_product_detail_page_renders_independent_shell(authed_client_no_db, monkeypatch):
    from web.routes import medias as routes

    monkeypatch.setattr(routes.medias, "get_product", lambda pid: {"id": pid, "user_id": 1})
    monkeypatch.setattr(routes, "_can_access_product", lambda product: True)

    resp = authed_client_no_db.get("/medias/products/123/ai-evaluation/eval_test")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "AI精细评估独立页" in body
    assert "fine_ai_evaluation_detail.js" in body
    assert '"mode": "product"' in body
    assert '"product_id": "123"' in body
    assert '"/medias/api/products/123/ai-evaluation/eval_test/status"' in body
    assert '"/medias/api/products/123/ai-evaluation/eval_test"' in body
    assert '"/medias/api/products/123/ai-evaluation/eval_test/countries/{country}/rerun"' in body


def test_fine_ai_evaluation_product_not_found(authed_client_no_db, monkeypatch):
    from web.routes import medias as routes

    monkeypatch.setattr(routes.medias, "get_product", lambda pid: None)

    resp = authed_client_no_db.post("/medias/api/products/404/ai-evaluation", json={})

    assert resp.status_code == 404
    assert resp.get_json()["error"]["code"] == "PRODUCT_NOT_FOUND"
