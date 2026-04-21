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


def test_ai_pricing_post_creates_row_and_invalidates_cache(authed_client_no_db, monkeypatch):
    _, rows, state = _install_pricing_store(monkeypatch)

    resp = authed_client_no_db.post(
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


def test_ai_pricing_put_updates_flat_price(authed_client_no_db, monkeypatch):
    _, rows, state = _install_pricing_store(monkeypatch)

    resp = authed_client_no_db.put(
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


def test_ai_pricing_delete_removes_row(authed_client_no_db, monkeypatch):
    _, rows, state = _install_pricing_store(monkeypatch)

    resp = authed_client_no_db.delete("/admin/settings/ai-pricing/1")

    assert resp.status_code == 200
    assert rows == []
    assert state["invalidations"] == 1


def test_ai_pricing_post_missing_price_fields_returns_400(authed_client_no_db, monkeypatch):
    _install_pricing_store(monkeypatch)

    resp = authed_client_no_db.post(
        "/admin/settings/ai-pricing",
        json={
            "provider": "gemini_vertex",
            "model": "gemini-3.1-pro-preview",
            "units_type": "tokens",
            "note": "缺价格",
        },
    )

    assert resp.status_code == 400


def test_ai_pricing_list_returns_rows(authed_client_no_db, monkeypatch):
    _, rows, _ = _install_pricing_store(monkeypatch)

    resp = authed_client_no_db.get("/admin/settings/ai-pricing/list")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == rows
