import csv
import io


def _install_template_stub(monkeypatch):
    from flask import jsonify
    from web.routes import admin_ai_billing as route_mod

    def fake_render(template_name, **context):
        assert template_name == "admin_ai_billing.html"
        return jsonify(
            {
                "rows": context["rows"],
                "summary": context["summary"],
                "groups": context["groups"],
                "filters": context["filters"],
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
