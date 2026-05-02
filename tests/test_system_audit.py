def test_record_inserts_audit_row(monkeypatch):
    from appcore import system_audit

    captured = {}

    def fake_execute(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return 123

    monkeypatch.setattr(system_audit, "execute", fake_execute)

    log_id = system_audit.record(
        actor_user_id=7,
        actor_username="alice",
        action="media_video_access",
        module="medias",
        target_type="media_item",
        target_id=42,
        target_label="demo.mp4",
        status="success",
        request_method="GET",
        request_path="/medias/object",
        ip_address="1.2.3.4",
        user_agent="pytest",
        detail={"object_key": "7/medias/1/demo.mp4"},
    )

    assert log_id == 123
    assert "INSERT INTO system_audit_logs" in captured["sql"]
    assert captured["args"][0] == 7
    assert captured["args"][2] == "media_video_access"
    assert captured["args"][3] == "medias"
    assert captured["args"][5] == "42"
    assert '"object_key": "7/medias/1/demo.mp4"' in captured["args"][-1]


def test_record_swallows_db_errors(monkeypatch):
    from appcore import system_audit

    def boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(system_audit, "execute", boom)

    assert system_audit.record(
        actor_user_id=1,
        actor_username="admin",
        action="login_success",
        module="auth",
    ) is None


def test_list_logs_builds_parameterized_filters(monkeypatch):
    from appcore import system_audit

    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [{"id": 1, "action": "login_success"}]

    monkeypatch.setattr(system_audit, "query", fake_query)

    rows = system_audit.list_logs(
        date_from="2026-05-01",
        date_to="2026-05-02",
        actor_user_id=2,
        module="medias",
        action="media_video_access",
        keyword="demo",
        limit=50,
        offset=0,
    )

    assert rows == [{"id": 1, "action": "login_success"}]
    assert "actor_user_id = %s" in captured["sql"]
    assert "module = %s" in captured["sql"]
    assert "action = %s" in captured["sql"]
    assert "LIKE %s" in captured["sql"]
    assert captured["args"][:5] == (
        "2026-05-01",
        "2026-05-02",
        2,
        "medias",
        "media_video_access",
    )
    assert captured["args"][-2:] == (50, 0)


def test_count_logs_uses_same_filters(monkeypatch):
    from appcore import system_audit

    captured = {}

    def fake_query_one(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return {"cnt": 9}

    monkeypatch.setattr(system_audit, "query_one", fake_query_one)

    count = system_audit.count_logs(
        date_from="2026-05-01",
        date_to="2026-05-02",
        actor_user_id=2,
        module="auth",
        action="login_success",
        keyword="alice",
    )

    assert count == 9
    assert "COUNT(*) AS cnt" in captured["sql"]
    assert "actor_user_id = %s" in captured["sql"]
    assert captured["args"][:5] == (
        "2026-05-01",
        "2026-05-02",
        2,
        "auth",
        "login_success",
    )


def test_list_daily_media_downloads_limits_actions(monkeypatch):
    from appcore import system_audit

    captured = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return []

    monkeypatch.setattr(system_audit, "query", fake_query)

    system_audit.list_daily_media_downloads(
        date_from="2026-05-02",
        date_to="2026-05-02",
    )

    assert "media_video_access" in captured["sql"]
    assert "raw_source_video_access" in captured["sql"]
    assert "detail_images_zip_download" in captured["sql"]
    assert captured["args"][:2] == ("2026-05-02", "2026-05-02")
