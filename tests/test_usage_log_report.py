from __future__ import annotations


def test_get_usage_report_scopes_non_admin_to_current_user(monkeypatch):
    from appcore import usage_log

    calls = []

    def fake_query(sql, args=()):
        calls.append((sql, args))
        if "SELECT DISTINCT service" in sql:
            return [{"service": "gemini"}, {"service": "openai"}]
        if "COUNT(*) AS total_calls" in sql:
            return [
                {
                    "total_calls": 2,
                    "total_input_tokens": 10,
                    "total_output_tokens": 20,
                    "total_audio_seconds": 0,
                }
            ]
        return [
            {
                "username": "alice",
                "service": "gemini",
                "model_name": "flash",
                "day": "2026-05-07",
                "calls": 2,
            }
        ]

    monkeypatch.setattr(usage_log, "query", fake_query, raising=False)

    report = usage_log.get_usage_report(
        admin=False,
        user_id=7,
        service="gemini",
        date_from="2026-05-01",
        date_to="2026-05-07",
    )

    assert report["rows"][0]["username"] == "alice"
    assert report["summary"]["total_calls"] == 2
    assert report["service_list"] == ["gemini", "openai"]
    rows_sql, rows_args = calls[0]
    summary_sql, summary_args = calls[1]
    assert "AND ul.user_id = %s" in rows_sql
    assert "AND ul.service = %s" in rows_sql
    assert "DATE(ul.called_at) >= %s" in rows_sql
    assert "DATE(ul.called_at) <= %s" in rows_sql
    assert rows_args == (7, "gemini", "2026-05-01", "2026-05-07")
    assert summary_args == rows_args
    assert "AND ul.user_id = %s" in summary_sql


def test_get_usage_report_does_not_scope_admin_to_current_user(monkeypatch):
    from appcore import usage_log

    calls = []

    def fake_query(sql, args=()):
        calls.append((sql, args))
        if "COUNT(*) AS total_calls" in sql:
            return []
        return []

    monkeypatch.setattr(usage_log, "query", fake_query, raising=False)

    report = usage_log.get_usage_report(
        admin=True,
        user_id=7,
        service="",
        date_from="2026-05-07",
        date_to="2026-05-07",
    )

    rows_sql, rows_args = calls[0]
    assert "AND ul.user_id = %s" not in rows_sql
    assert rows_args == ("2026-05-07", "2026-05-07")
    assert report["summary"] == {
        "total_calls": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_audio_seconds": 0,
    }


def test_get_usage_payload_reads_payload_by_log_id(monkeypatch):
    from appcore import usage_log

    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [{"request_data": {"prompt": "hello"}, "response_data": {"text": "world"}}]

    monkeypatch.setattr(usage_log, "query", fake_query, raising=False)

    assert usage_log.get_usage_payload(42) == {
        "request_data": {"prompt": "hello"},
        "response_data": {"text": "world"},
    }
    assert "FROM usage_log_payloads" in captured["sql"]
    assert "WHERE log_id = %s" in captured["sql"]
    assert "JOIN usage_logs" not in captured["sql"]
    assert captured["args"] == (42,)


def test_get_user_usage_payload_scopes_by_user_id(monkeypatch):
    from appcore import usage_log

    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return []

    monkeypatch.setattr(usage_log, "query", fake_query, raising=False)

    assert usage_log.get_user_usage_payload(42, user_id=7) is None
    assert "JOIN usage_logs ul ON ul.id = p.log_id" in captured["sql"]
    assert "p.log_id = %s AND ul.user_id = %s" in captured["sql"]
    assert captured["args"] == (42, 7)
