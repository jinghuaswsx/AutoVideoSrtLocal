import csv
import io
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def superadmin_client_no_db(monkeypatch):
    """Flask client authenticated as the real superadmin shape, without DB IO."""
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "appcore.medias.list_enabled_language_codes",
        lambda: ["de", "fr", "es", "it", "pt", "ja", "nl", "sv", "fi", "en"],
    )
    from web.app import create_app

    fake_user = {
        "id": 1,
        "username": "admin",
        "role": "superadmin",
        "is_active": 1,
    }

    monkeypatch.setattr("web.auth.get_by_id", lambda user_id: fake_user if int(user_id) == 1 else None)

    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True

    return client


def test_ai_billing_template_uses_single_date_range_picker_and_quick_ranges():
    template = (ROOT / "web" / "templates" / "admin_ai_billing.html").read_text(encoding="utf-8")

    assert 'data-billing-date-range-trigger' in template
    assert 'data-billing-date-range-panel' in template
    assert 'data-range-shortcut="today"' in template
    assert 'data-range-shortcut="yesterday"' in template
    assert 'data-range-shortcut="last7"' in template
    assert 'data-range-shortcut="last30"' in template
    assert "initBillingDateRangePicker" in template


def test_ai_billing_template_shows_input_and_output_token_columns():
    template = (ROOT / "web" / "templates" / "admin_ai_billing.html").read_text(encoding="utf-8")

    assert "<th>输入 Token</th>" in template
    assert "<th>输出 Token</th>" in template
    assert "{{ row.input_tokens if row.input_tokens is not none else '' }}" in template
    assert "{{ row.output_tokens if row.output_tokens is not none else '' }}" in template
    assert 'colspan="{% if admin_mode %}15{% else %}14{% endif %}"' in template


def test_ai_billing_template_has_detail_filters_summary_and_payload_sizes():
    template = (ROOT / "web" / "templates" / "admin_ai_billing.html").read_text(encoding="utf-8")

    assert 'data-billing-multi' in template
    assert 'name="detail_status"' in template
    assert 'name="detail_module"' in template
    assert 'name="detail_provider"' in template
    assert 'name="detail_use_case"' in template
    assert 'name="detail_user_id"' in template
    assert 'type="text" name="detail_user_id"' not in template
    assert "row.user_display_name or row.username" in template
    assert "detail_summary.detail_total_calls" in template
    assert "detail_summary.detail_total_cost_cny" in template
    assert "detail_summary.detail_payload_mb" in template
    assert "<th>请求包大小</th>" in template
    assert "<th>返回包大小</th>" in template
    assert "row.request_payload_mb" in template
    assert "row.response_payload_mb" in template
    assert 'colspan="{% if admin_mode %}15{% else %}14{% endif %}"' in template


def _install_template_stub(monkeypatch):
    from flask import jsonify
    from web.routes import admin_ai_billing as route_mod
    monkeypatch.setattr(
        route_mod.medias,
        "_media_product_owner_name_expr",
        lambda: "COALESCE(NULLIF(TRIM(u.xingming), ''), u.username)",
    )

    def fake_render(template_name, **context):
        assert template_name == "admin_ai_billing.html"
        return jsonify(
            {
                "rows": context["rows"],
                "summary": context["summary"],
                "groups": context["groups"],
                "filters": context["filters"],
                "detail_filters": context["detail_filters"],
                "detail_filter_options": context["detail_filter_options"],
                "detail_summary": context["detail_summary"],
                "group_by": context["group_by"],
                "admin_mode": context["admin_mode"],
            }
        )

    monkeypatch.setattr(route_mod, "render_template", fake_render)
    return route_mod


def test_admin_ai_usage_forbidden_for_non_admin(authed_user_client_no_db):
    resp = authed_user_client_no_db.get("/admin/ai-usage")
    assert resp.status_code == 403


def test_my_ai_usage_only_returns_current_user_rows(authed_user_client_no_db, monkeypatch):
    route_mod = _install_template_stub(monkeypatch)
    captured = []

    def fake_query(sql, args=()):
        captured.append((sql, args))
        if "COUNT(*) AS total_calls" in sql:
            return [{
                "total_cost_cny": 1.23,
                "total_calls": 1,
                "billed_calls": 1,
                "unbilled_calls": 0,
            }]
        if "GROUP BY" in sql and "group_value" in sql:
            return [{"group_value": "video_translate", "calls": 1, "request_units": 20, "cost_cny": 1.23}]
        if "ORDER BY ul.called_at DESC" in sql:
            if args and args[0] == 2:
                return [{
                    "id": 101,
                    "user_id": 2,
                    "username": "test-user",
                    "project_id": "task-u2",
                    "service": "openrouter",
                    "use_case_code": "video_translate.localize",
                    "module": "video_translate",
                    "provider": "openrouter",
                    "model_name": "gpt",
                    "success": 1,
                    "input_tokens": 10,
                    "output_tokens": 8,
                    "audio_duration_seconds": None,
                    "request_units": 18,
                    "units_type": "tokens",
                    "cost_cny": 1.23,
                    "cost_source": "response",
                    "extra_data": None,
                }]
            return [
                {"id": 100, "user_id": 1, "username": "admin"},
                {"id": 101, "user_id": 2, "username": "test-user"},
            ]
        return []

    monkeypatch.setattr(route_mod, "query", fake_query)

    resp = authed_user_client_no_db.get("/my-ai-usage")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["admin_mode"] is False
    assert payload["rows"] == [{
        "id": 101,
        "user_id": 2,
        "username": "test-user",
        "project_id": "task-u2",
        "service": "openrouter",
        "use_case_code": "video_translate.localize",
        "module": "video_translate",
        "provider": "openrouter",
        "model_name": "gpt",
        "success": 1,
        "input_tokens": 10,
        "output_tokens": 8,
        "audio_duration_seconds": None,
        "request_units": 18,
        "units_type": "tokens",
        "cost_cny": 1.23,
        "cost_source": "response",
        "extra_data": None,
        "request_payload_mb": None,
        "response_payload_mb": None,
    }]
    assert any(args and args[0] == 2 for _, args in captured)


def test_admin_ai_usage_user_id_filter_is_parameterized(authed_client_no_db, monkeypatch):
    route_mod = _install_template_stub(monkeypatch)
    captured = []

    def fake_query(sql, args=()):
        captured.append((sql, args))
        if "COUNT(*) AS total_calls" in sql:
            total_calls = 0 if -1 in args else 2
            return [{
                "total_cost_cny": 0,
                "total_calls": total_calls,
                "billed_calls": 0,
                "unbilled_calls": total_calls,
            }]
        if "GROUP BY" in sql and "group_value" in sql:
            return []
        if "ORDER BY ul.called_at DESC" in sql:
            if -1 in args:
                return []
            return [
                {"id": 1, "user_id": 1, "username": "admin"},
                {"id": 2, "user_id": 2, "username": "other"},
            ]
        return []

    monkeypatch.setattr(route_mod, "query", fake_query)

    resp = authed_client_no_db.get("/admin/ai-usage?user_id=' OR 1=1 --")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["rows"] == []
    assert any(-1 in args for _, args in captured)
    assert all("' OR 1=1 --" not in sql for sql, _ in captured)


def test_admin_ai_usage_csv_export_has_header_and_all_rows(authed_client_no_db, monkeypatch):
    from web.routes import admin_ai_billing as route_mod
    monkeypatch.setattr(
        route_mod.medias,
        "_media_product_owner_name_expr",
        lambda: "COALESCE(NULLIF(TRIM(u.xingming), ''), u.username)",
    )

    def fake_query(sql, args=()):
        if "COUNT(*) AS total_calls" in sql:
            return [{
                "total_cost_cny": 2.46,
                "total_calls": 2,
                "billed_calls": 2,
                "unbilled_calls": 0,
            }]
        if "GROUP BY" in sql and "group_value" in sql:
            return [{"group_value": "video_translate", "calls": 2, "request_units": 36, "cost_cny": 2.46}]
        if "ORDER BY ul.called_at DESC" in sql:
            return [
                {
                    "id": 11,
                    "called_at": "2026-04-21 10:00:00",
                    "user_id": 1,
                    "username": "admin",
                    "project_id": "task-1",
                    "service": "openrouter",
                    "use_case_code": "video_translate.localize",
                    "module": "video_translate",
                    "provider": "openrouter",
                    "model_name": "gpt",
                    "success": 1,
                    "input_tokens": 10,
                    "output_tokens": 8,
                    "audio_duration_seconds": None,
                    "request_units": 18,
                    "units_type": "tokens",
                    "cost_cny": 1.23,
                    "cost_source": "response",
                    "extra_data": {"trace_id": "a"},
                },
                {
                    "id": 12,
                    "called_at": "2026-04-21 11:00:00",
                    "user_id": 2,
                    "username": "other",
                    "project_id": "task-2",
                    "service": "doubao_asr",
                    "use_case_code": "video_translate.asr",
                    "module": "video_translate",
                    "provider": "doubao_asr",
                    "model_name": "big-model",
                    "success": 1,
                    "input_tokens": None,
                    "output_tokens": None,
                    "audio_duration_seconds": 12.4,
                    "request_units": 13,
                    "units_type": "seconds",
                    "cost_cny": 1.23,
                    "cost_source": "pricebook",
                    "extra_data": None,
                },
            ]
        return []

    monkeypatch.setattr(route_mod, "query", fake_query)

    resp = authed_client_no_db.get("/admin/ai-usage/export.csv")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == route_mod.CSV_COLUMNS
    assert len(rows) - 1 == 2


def test_admin_ai_usage_includes_material_evaluation_rows(authed_client_no_db, monkeypatch):
    route_mod = _install_template_stub(monkeypatch)

    def fake_query(sql, args=()):
        if "COUNT(*) AS total_calls" in sql:
            return [{
                "total_cost_cny": 0.88,
                "total_calls": 1,
                "billed_calls": 1,
                "unbilled_calls": 0,
            }]
        if "GROUP BY" in sql and "group_value" in sql:
            return [{
                "group_value": "material",
                "calls": 1,
                "request_units": 7242,
                "cost_cny": 0.88,
            }]
        if "ORDER BY ul.called_at DESC" in sql:
            return [{
                "id": 501,
                "called_at": "2026-04-23 22:34:58",
                "user_id": 1,
                "username": "admin",
                "project_id": "media-product-335",
                "service": "openrouter",
                "use_case_code": "material_evaluation.evaluate",
                "module": "material",
                "provider": "openrouter",
                "model_name": "google/gemini-3.1-pro-preview",
                "success": 1,
                "input_tokens": 3702,
                "output_tokens": 3792,
                "audio_duration_seconds": None,
                "request_units": 7494,
                "units_type": "tokens",
                "cost_cny": 0.88,
                "cost_source": "response",
                "extra_data": {"use_case": "material_evaluation.evaluate"},
            }]
        return []

    monkeypatch.setattr(route_mod, "query", fake_query)

    resp = authed_client_no_db.get("/admin/ai-usage?module=material&use_case=material_evaluation.evaluate")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["rows"][0]["use_case_code"] == "material_evaluation.evaluate"
    assert payload["rows"][0]["module"] == "material"
    assert payload["rows"][0]["provider"] == "openrouter"
    assert payload["rows"][0]["model_name"] == "google/gemini-3.1-pro-preview"


def test_admin_ai_usage_detail_filters_do_not_change_top_summary(authed_client_no_db, monkeypatch):
    route_mod = _install_template_stub(monkeypatch)
    captured = []

    def fake_query(sql, args=()):
        captured.append((sql, args))
        if "COUNT(*) AS total_calls" in sql and "usage_log_payloads" not in sql:
            assert "ul.module = %s" not in sql
            assert "ul.provider = %s" not in sql
            assert "ul.success = %s" not in sql
            assert "ul.use_case_code = %s" not in sql
            return [{
                "total_cost_cny": 9.99,
                "total_calls": 7,
                "billed_calls": 6,
                "unbilled_calls": 1,
            }]
        if "GROUP BY" in sql and "group_value" in sql:
            return []
        if "detail_total_calls" in sql:
            assert "LEFT JOIN usage_log_payloads p ON p.log_id = ul.id" in sql
            assert "$.network_estimate.estimated_base64_payload_bytes" in sql
            assert "$.estimated_base64_payload_bytes" in sql
            assert args[-5:] == (2, "image", "image.translate", "gemini_vertex", 1)
            return [{
                "detail_total_calls": 1,
                "detail_total_cost_cny": 0.53,
                "detail_payload_bytes": 1572864,
                "payload_recorded_calls": 1,
            }]
        if "ORDER BY ul.called_at DESC" in sql:
            assert "LEFT JOIN usage_log_payloads p ON p.log_id = ul.id" in sql
            assert "$.network_estimate.estimated_base64_payload_bytes" in sql
            assert args[-7:-2] == (2, "image", "image.translate", "gemini_vertex", 1)
            return [{
                "id": 801,
                "called_at": "2026-04-28 12:00:00",
                "user_id": 2,
                "username": "designer",
                "project_id": "asset-1",
                "service": "gemini",
                "use_case_code": "image.translate",
                "module": "image",
                "provider": "gemini_vertex",
                "model_name": "gemini-3.1-flash",
                "success": 1,
                "input_tokens": 10,
                "output_tokens": 12,
                "audio_duration_seconds": None,
                "request_units": 22,
                "units_type": "tokens",
                "cost_cny": 0.53,
                "cost_source": "response",
                "extra_data": None,
                "request_payload_bytes": 524288,
                "response_payload_bytes": 1048576,
            }]
        return []

    monkeypatch.setattr(route_mod, "query", fake_query)

    resp = authed_client_no_db.get(
        "/admin/ai-usage?detail_user_id=2&detail_module=image"
        "&detail_use_case=image.translate&detail_provider=gemini_vertex"
        "&detail_status=success"
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["summary"]["total_calls"] == 7
    assert payload["detail_filters"] == {
        "user_ids": [2],
        "modules": ["image"],
        "use_cases": ["image.translate"],
        "providers": ["gemini_vertex"],
        "statuses": [True],
    }
    assert payload["detail_summary"]["detail_total_calls"] == 1
    assert payload["detail_summary"]["detail_payload_mb"] == "1.50 MB"
    assert payload["rows"][0]["request_payload_mb"] == "0.50 MB"
    assert payload["rows"][0]["response_payload_mb"] == "1.00 MB"
    assert any("detail_total_calls" in sql for sql, _ in captured)


def _install_pricing_store(monkeypatch):
    from web.routes import settings as route_mod

    rows = [
        {
            "id": 1,
            "provider": "elevenlabs",
            "model": "*",
            "units_type": "chars",
            "unit_input_cny": None,
            "unit_output_cny": None,
            "unit_flat_cny": 0.000165,
            "note": "待复核：≈0.165 RMB/千字符",
            "updated_at": "2026-04-21 10:00:00",
        }
    ]
    state = {"invalidations": 0}

    def fake_query(sql, args=()):
        if "FROM ai_model_prices" in sql:
            if "WHERE id = %s" in sql:
                row_id = int(args[0])
                return [row.copy() for row in rows if row["id"] == row_id]
            return [row.copy() for row in rows]
        return []

    def fake_execute(sql, args=()):
        if sql.strip().startswith("INSERT INTO ai_model_prices"):
            next_id = max((row["id"] for row in rows), default=0) + 1
            rows.append(
                {
                    "id": next_id,
                    "provider": args[0],
                    "model": args[1],
                    "units_type": args[2],
                    "unit_input_cny": args[3],
                    "unit_output_cny": args[4],
                    "unit_flat_cny": args[5],
                    "note": args[6],
                    "updated_at": "2026-04-21 11:00:00",
                }
            )
            return next_id
        if sql.strip().startswith("UPDATE ai_model_prices"):
            row_id = int(args[-1])
            for row in rows:
                if row["id"] == row_id:
                    row.update(
                        {
                            "units_type": args[0],
                            "unit_input_cny": args[1],
                            "unit_output_cny": args[2],
                            "unit_flat_cny": args[3],
                            "note": args[4],
                            "updated_at": "2026-04-21 12:00:00",
                        }
                    )
                    return 1
            return 0
        if sql.strip().startswith("DELETE FROM ai_model_prices"):
            row_id = int(args[0])
            before = len(rows)
            rows[:] = [row for row in rows if row["id"] != row_id]
            return 1 if len(rows) != before else 0
        return 0

    monkeypatch.setattr(route_mod, "query", fake_query)
    monkeypatch.setattr(route_mod, "execute", fake_execute)
    monkeypatch.setattr(route_mod.pricing, "invalidate_cache", lambda: state.__setitem__("invalidations", state["invalidations"] + 1))
    return route_mod, rows, state


def test_ai_pricing_write_routes_forbidden_for_non_admin(authed_user_client_no_db):
    payload = {
        "provider": "elevenlabs",
        "model": "eleven_multilingual_v2",
        "units_type": "chars",
        "unit_flat_cny": 0.0002,
        "note": "test",
    }

    assert authed_user_client_no_db.post("/admin/settings/ai-pricing", json=payload).status_code == 403
    assert authed_user_client_no_db.put("/admin/settings/ai-pricing/1", json=payload).status_code == 403
    assert authed_user_client_no_db.delete("/admin/settings/ai-pricing/1").status_code == 403


def test_ai_pricing_response_service_shapes_payloads():
    from web.services.settings_ai_pricing import (
        build_ai_pricing_error_response,
        build_ai_pricing_list_response,
        build_ai_pricing_not_found_response,
        build_ai_pricing_success_response,
    )

    row = {
        "id": 1,
        "provider": "elevenlabs",
        "model": "*",
        "units_type": "chars",
        "unit_input_cny": None,
        "unit_output_cny": None,
        "unit_flat_cny": 0.000165,
        "note": "ok",
        "updated_at": "2026-04-21 10:00:00",
    }

    assert build_ai_pricing_list_response([row]).payload == {"items": [row]}
    created = build_ai_pricing_success_response(row, status_code=201)
    assert created.payload == {"ok": True, "item": row}
    assert created.status_code == 201
    assert build_ai_pricing_success_response(None).payload == {"ok": True, "item": None}
    assert build_ai_pricing_success_response().payload == {"ok": True}
    assert build_ai_pricing_error_response(ValueError("bad")).payload == {"error": "bad"}
    assert build_ai_pricing_not_found_response().payload == {"error": "not found"}


def test_ai_pricing_post_creates_row_and_invalidates_cache(superadmin_client_no_db, monkeypatch):
    _, rows, state = _install_pricing_store(monkeypatch)

    resp = superadmin_client_no_db.post(
        "/admin/settings/ai-pricing",
        json={
            "provider": "elevenlabs",
            "model": "eleven_multilingual_v2",
            "units_type": "chars",
            "unit_flat_cny": 0.0002,
            "note": "待复核",
        },
    )

    assert resp.status_code == 201
    assert any(row["model"] == "eleven_multilingual_v2" for row in rows)
    assert state["invalidations"] == 1


def test_ai_pricing_put_updates_flat_price(superadmin_client_no_db, monkeypatch):
    _, rows, state = _install_pricing_store(monkeypatch)

    resp = superadmin_client_no_db.put(
        "/admin/settings/ai-pricing/1",
        json={
            "provider": "elevenlabs",
            "model": "*",
            "units_type": "chars",
            "unit_flat_cny": 0.0003,
            "note": "已复核",
        },
    )

    assert resp.status_code == 200
    assert rows[0]["unit_flat_cny"] == 0.0003
    assert rows[0]["note"] == "已复核"
    assert state["invalidations"] == 1


def test_ai_pricing_delete_removes_row(superadmin_client_no_db, monkeypatch):
    _, rows, state = _install_pricing_store(monkeypatch)

    resp = superadmin_client_no_db.delete("/admin/settings/ai-pricing/1")

    assert resp.status_code == 200
    assert rows == []
    assert state["invalidations"] == 1


def test_ai_pricing_post_missing_price_fields_returns_400(superadmin_client_no_db, monkeypatch):
    _install_pricing_store(monkeypatch)

    resp = superadmin_client_no_db.post(
        "/admin/settings/ai-pricing",
        json={
            "provider": "gemini_vertex",
            "model": "gemini-3.1-pro-preview",
            "units_type": "tokens",
            "note": "缺价格",
        },
    )

    assert resp.status_code == 400


def test_ai_pricing_list_returns_rows(superadmin_client_no_db, monkeypatch):
    _, rows, _ = _install_pricing_store(monkeypatch)

    resp = superadmin_client_no_db.get("/admin/settings/ai-pricing/list")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == rows
