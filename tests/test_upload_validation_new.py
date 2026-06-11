import io
import json
import os
import pytest

def _make_file(filename: str, content: bytes = b"fake-data"):
    return (io.BytesIO(content), filename)

@pytest.fixture
def client(monkeypatch, tmp_path):
    fake_user = {
        "id": 1,
        "username": "admin",
        "role": "admin",
        "permissions": '{"data_analytics": true, "order_profit": true, "product_profit": true}',
        "is_active": 1,
    }
    # Mock Flask app initialization and DB setups
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("web.auth.get_by_id", lambda uid: fake_user if int(uid) == 1 else None)
    
    # Mock order_analytics dependencies
    monkeypatch.setattr("web.routes.order_analytics.meta_ad_accounts.AVAILABLE_STORE_CODES", {"teststore"})
    monkeypatch.setattr("web.routes.order_analytics.shopify_unsettled_payouts.create_project_from_file", lambda *a, **kw: {})
    monkeypatch.setattr("web.routes.order_analytics.oa.parse_shopify_file", lambda *a, **kw: [{"dummy": 1}])
    monkeypatch.setattr("web.routes.order_analytics.oa.parse_meta_ad_file", lambda *a, **kw: [{"dummy": 1}])
    monkeypatch.setattr("web.routes.order_analytics.oa.import_orders", lambda *a, **kw: {"imported": 1, "skipped": 0})
    monkeypatch.setattr("web.routes.order_analytics.oa.match_orders_to_products", lambda *a, **kw: 0)
    monkeypatch.setattr("web.routes.order_analytics.oa.get_import_stats", lambda *a, **kw: {"total_rows": 1, "product_count": 0, "country_count": 0, "matched_rows": 0})
    monkeypatch.setattr("web.routes.order_analytics.oa.import_meta_ad_rows", lambda *a, **kw: {})
    monkeypatch.setattr("web.routes.order_analytics.oa.get_meta_ad_stats", lambda *a, **kw: {"country_count": 0, "matched_rows": 0, "min_date": None, "max_date": None})
    monkeypatch.setattr("web.routes.order_analytics._audit_order_analytics_action", lambda *a, **kw: None)

    # Mock order_profit dependencies
    monkeypatch.setattr("web.routes.order_profit.import_payments_csv", lambda *a, **kw: {})

    # Mock product_profit_report dependencies
    monkeypatch.setattr("web.routes.product_profit_report.import_payments_csv", lambda *a, **kw: {})

    # Mock product_research dependencies
    monkeypatch.setattr("config.UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr("web.upload_util.save_uploaded_file_to_path", lambda file, dest: dest)

    # Mock link_check dependencies
    monkeypatch.setattr("web.routes.link_check.OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr("web.routes.link_check._enabled_language_map", lambda: {"en": {"enabled": True}})
    monkeypatch.setattr("web.routes.link_check.detect_target_language_from_url", lambda *a, **kw: "en")
    monkeypatch.setattr("web.routes.link_check.store.create_link_check", lambda *a, **kw: None)
    monkeypatch.setattr("web.routes.link_check.link_check_runner.start", lambda *a, **kw: None)

    # Mock tasks dependencies
    monkeypatch.setattr("web.routes.tasks.tasks_svc.CHILD_MANUAL_OUTPUT_STEP_KINDS", {"translation"})
    monkeypatch.setattr("web.routes.tasks.tasks_svc.submit_child_step_manual_output", lambda **kw: {"step_key": "translation", "kind": "manual"})
    monkeypatch.setattr("web.routes.tasks.local_media_storage.write_stream", lambda *a, **kw: tmp_path / "manual_upload")
    monkeypatch.setattr("web.routes.tasks._audit_task_action", lambda *a, **kw: None)

    from web.app import create_app
    app = create_app()
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["_user_id"] = "1"
        sess["_fresh"] = True
    return c

class TestNewUploadValidationEndpoints:

    # 1. order_analytics.py /order-analytics/unsettled-payouts/projects
    def test_unsettled_payout_project_create_validation(self, client):
        # valid extension
        resp = client.post(
            "/order-analytics/unsettled-payouts/projects",
            data={"store_code": "teststore", "file": _make_file("test.xlsx")},
            content_type="multipart/form-data"
        )
        assert resp.status_code == 200

        # invalid extension
        resp = client.post(
            "/order-analytics/unsettled-payouts/projects",
            data={"store_code": "teststore", "file": _make_file("test.exe")},
            content_type="multipart/form-data"
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid_file_type"

    # 2. order_analytics.py /order-analytics/upload
    def test_order_analytics_upload_validation(self, client):
        # valid extension
        resp = client.post(
            "/order-analytics/upload",
            data={"file": _make_file("test.csv")},
            content_type="multipart/form-data"
        )
        assert resp.status_code == 200

        # invalid extension
        resp = client.post(
            "/order-analytics/upload",
            data={"file": _make_file("test.jpg")},
            content_type="multipart/form-data"
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid_file_type"

    # 3. order_analytics.py /order-analytics/ad-upload
    def test_order_analytics_ad_upload_validation(self, client):
        # valid extension
        resp = client.post(
            "/order-analytics/ad-upload",
            data={"file": _make_file("test.xls")},
            content_type="multipart/form-data"
        )
        assert resp.status_code == 200

        # invalid extension
        resp = client.post(
            "/order-analytics/ad-upload",
            data={"file": _make_file("test.php")},
            content_type="multipart/form-data"
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid_file_type"

    # 4. order_profit.py /order-profit/api/payments_csv/import
    def test_order_profit_import_payments_csv_validation(self, client):
        # valid extension
        resp = client.post(
            "/order-profit/api/payments_csv/import",
            data={"file": _make_file("test.csv")},
            content_type="multipart/form-data"
        )
        assert resp.status_code == 200

        # invalid extension
        resp = client.post(
            "/order-profit/api/payments_csv/import",
            data={"file": _make_file("test.png")},
            content_type="multipart/form-data"
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid_file_type"

    # 5. product_profit_report.py /order-analytics/product-profit/payments_csv/import
    def test_product_profit_report_import_payments_csv_validation(self, client):
        # valid extension
        resp = client.post(
            "/order-analytics/product-profit/payments_csv/import",
            data={"store_code": "newjoyloo", "file": _make_file("test.xlsx")},
            content_type="multipart/form-data"
        )
        assert resp.status_code == 200

        # invalid extension
        resp = client.post(
            "/order-analytics/product-profit/payments_csv/import",
            data={"store_code": "newjoyloo", "file": _make_file("test.html")},
            content_type="multipart/form-data"
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid_file_type"

    # 6. product_research.py /api/product-research/assets/upload
    def test_product_research_upload_asset_validation(self, client):
        # valid spreadsheet
        resp = client.post(
            "/api/product-research/assets/upload",
            data={"asset_type": "spreadsheet", "file": _make_file("test.csv")},
            content_type="multipart/form-data"
        )
        assert resp.status_code == 201

        # valid json
        resp = client.post(
            "/api/product-research/assets/upload",
            data={"asset_type": "json", "file": _make_file("test.json")},
            content_type="multipart/form-data"
        )
        assert resp.status_code == 201

        # valid image
        resp = client.post(
            "/api/product-research/assets/upload",
            data={"asset_type": "image", "file": _make_file("test.jpg")},
            content_type="multipart/form-data"
        )
        assert resp.status_code == 201

        # valid video
        resp = client.post(
            "/api/product-research/assets/upload",
            data={"asset_type": "video", "file": _make_file("test.mp4")},
            content_type="multipart/form-data"
        )
        assert resp.status_code == 201

        # invalid
        resp = client.post(
            "/api/product-research/assets/upload",
            data={"asset_type": "image", "file": _make_file("test.exe")},
            content_type="multipart/form-data"
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid_file_type"

    # 7. link_check.py /api/link-check/tasks
    def test_link_check_reference_images_validation(self, client):
        # valid
        resp = client.post(
            "/api/link-check/tasks",
            data={"link_url": "https://example.com", "reference_images": [_make_file("test.jpg")]},
            content_type="multipart/form-data"
        )
        assert resp.status_code == 202

        # invalid
        resp = client.post(
            "/api/link-check/tasks",
            data={"link_url": "https://example.com", "reference_images": [_make_file("test.exe")]},
            content_type="multipart/form-data"
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid_file_type"

    # 8. tasks.py /tasks/api/child/<int:tid>/steps/<step_key>/manual-output
    def test_tasks_manual_output_validation(self, client):
        # valid video
        resp = client.post(
            "/tasks/api/child/123/steps/translation/manual-output",
            data={"file": _make_file("test.mp4")},
            content_type="multipart/form-data"
        )
        assert resp.status_code == 200

        # valid image
        resp = client.post(
            "/tasks/api/child/123/steps/translation/manual-output",
            data={"file": _make_file("test.png")},
            content_type="multipart/form-data"
        )
        assert resp.status_code == 200

        # invalid
        resp = client.post(
            "/tasks/api/child/123/steps/translation/manual-output",
            data={"file": _make_file("test.txt")},
            content_type="multipart/form-data"
        )
        assert resp.status_code == 400
        assert resp.get_json()["error"] == "invalid_file_type"
