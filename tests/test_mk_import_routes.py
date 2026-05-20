from __future__ import annotations


def test_mk_import_check_returns_empty_for_blank_query(authed_client_no_db):
    resp = authed_client_no_db.get("/mk-import/check")

    assert resp.status_code == 200
    assert resp.get_json() == {"imported": [], "missing": []}


def test_mk_import_check_rejects_too_many_filenames(authed_client_no_db):
    filenames = ",".join(f"{idx}.mp4" for idx in range(101))

    resp = authed_client_no_db.get(
        "/mk-import/check",
        query_string={"filenames": filenames},
    )

    assert resp.status_code == 400
    assert resp.get_json() == {"error": "too_many_filenames", "max": 100}


def test_mk_import_check_splits_imported_and_missing(authed_client_no_db, monkeypatch):
    import appcore.db as db
    from web.routes import mk_import as route

    captured = {}

    def fake_query_all(sql, args=None):
        raise AssertionError("route should delegate filename lookup to mk_import service")

    def fake_list_imported_filenames(filenames):
        captured["filenames"] = filenames
        return {"a.mp4"}

    monkeypatch.setattr(db, "query_all", fake_query_all)
    monkeypatch.setattr(route.mk_import_svc, "list_imported_filenames", fake_list_imported_filenames)

    resp = authed_client_no_db.get(
        "/mk-import/check",
        query_string={"filenames": "a.mp4,b.mp4,a.mp4"},
    )

    assert resp.status_code == 200
    assert resp.get_json() == {"imported": ["a.mp4"], "missing": ["b.mp4"]}
    assert captured["filenames"] == ["a.mp4", "b.mp4", "a.mp4"]


def test_mk_import_check_accepts_post_json_for_long_unicode_filenames(authed_client_no_db, monkeypatch):
    from web.routes import mk_import as route

    captured = {}
    filenames = [
        "2026.04.09-物理综合实验DIY-混剪-苏齐齐.mp4",
        "2026.04.01-煮蛋器-原素材-指派-陈兆阳.mp4",
    ]

    def fake_list_imported_filenames(values):
        captured["filenames"] = values
        return {filenames[0]}

    monkeypatch.setattr(route.mk_import_svc, "list_imported_filenames", fake_list_imported_filenames)

    resp = authed_client_no_db.post(
        "/mk-import/check",
        json={"filenames": filenames},
    )

    assert resp.status_code == 200
    assert resp.get_json() == {"imported": [filenames[0]], "missing": [filenames[1]]}
    assert captured["filenames"] == filenames


def test_mk_import_check_post_rejects_too_many_filenames(authed_client_no_db):
    resp = authed_client_no_db.post(
        "/mk-import/check",
        json={"filenames": [f"{idx}.mp4" for idx in range(101)]},
    )

    assert resp.status_code == 400
    assert resp.get_json() == {"error": "too_many_filenames", "max": 100}


def test_mk_import_video_rejects_non_admin(authed_user_client_no_db):
    resp = authed_user_client_no_db.post("/mk-import/video", json={})

    assert resp.status_code == 403
    assert resp.get_json() == {"error": "admin_required"}


def test_mk_import_video_rejects_bad_payload(authed_client_no_db):
    resp = authed_client_no_db.post(
        "/mk-import/video",
        json={"mk_video_metadata": {"filename": "x.mp4"}, "translator_id": "7"},
    )

    assert resp.status_code == 400
    assert resp.get_json() == {"error": "bad_payload"}


def test_mk_import_video_returns_service_result(authed_client_no_db, monkeypatch):
    from web.routes import mk_import as route

    captured = {}
    warnings = [{"type": "product_link_unavailable", "detail": "HTTP 404"}]

    def fake_import_mk_video(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "media_item_id": 12, "warnings": warnings}

    monkeypatch.setattr(route, "ensure_translation_work_user", lambda user_id: {"id": user_id})
    monkeypatch.setattr(route.mk_import_svc, "import_mk_video", fake_import_mk_video)

    resp = authed_client_no_db.post(
        "/mk-import/video",
        json={"mk_video_metadata": {"filename": "x.mp4"}, "translator_id": 7},
    )

    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "media_item_id": 12, "warnings": warnings}
    assert captured["mk_video_metadata"] == {"filename": "x.mp4"}
    assert captured["translator_id"] == 7
    assert captured["actor_user_id"] == 1


def test_mk_import_video_accepts_product_owner_id(authed_client_no_db, monkeypatch):
    from web.routes import mk_import as route

    captured = {}

    def fake_import_mk_video(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "media_item_id": 12}

    monkeypatch.setattr(route, "ensure_translation_work_user", lambda user_id: {"id": user_id})
    monkeypatch.setattr(route.mk_import_svc, "import_mk_video", fake_import_mk_video)

    resp = authed_client_no_db.post(
        "/mk-import/video",
        json={"mk_video_metadata": {"filename": "x.mp4"}, "product_owner_id": 8},
    )

    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "media_item_id": 12}
    assert captured["translator_id"] == 8
    assert captured["actor_user_id"] == 1


def test_mk_import_video_product_owner_id_takes_priority_over_legacy_translator_id(
    authed_client_no_db,
    monkeypatch,
):
    from web.routes import mk_import as route

    captured = {}
    ensure_calls = []

    monkeypatch.setattr(
        route,
        "ensure_translation_work_user",
        lambda user_id: ensure_calls.append(user_id) or {"id": user_id},
    )
    monkeypatch.setattr(
        route.mk_import_svc,
        "import_mk_video",
        lambda **kwargs: captured.update(kwargs) or {"ok": True},
    )

    resp = authed_client_no_db.post(
        "/mk-import/video",
        json={
            "mk_video_metadata": {"filename": "x.mp4"},
            "product_owner_id": 8,
            "translator_id": 7,
        },
    )

    assert resp.status_code == 200
    assert ensure_calls == [8]
    assert captured["translator_id"] == 8


def test_mk_import_video_rejects_non_translation_work_user(authed_client_no_db, monkeypatch):
    from web.routes import mk_import as route

    calls = []
    monkeypatch.setattr(
        route,
        "ensure_translation_work_user",
        lambda user_id: (_ for _ in ()).throw(ValueError("该用户不在翻译工作范围")),
        raising=False,
    )
    monkeypatch.setattr(route.mk_import_svc, "import_mk_video", lambda **kwargs: calls.append(kwargs))

    resp = authed_client_no_db.post(
        "/mk-import/video",
        json={"mk_video_metadata": {"filename": "x.mp4"}, "translator_id": 7},
    )

    assert resp.status_code == 400
    assert "翻译工作范围" in resp.get_json()["detail"]
    assert calls == []


def test_mk_import_video_maps_service_errors(authed_client_no_db, monkeypatch):
    from web.routes import mk_import as route

    cases = [
        (route.mk_import_svc.DuplicateError("dupe"), 422, "duplicate_filename"),
        (route.mk_import_svc.DownloadError("404"), 502, "download_failed"),
        (route.mk_import_svc.StorageError("tos"), 500, "storage_failed"),
        (route.mk_import_svc.DBError("sql"), 500, "db_failed"),
    ]

    for exc, expected_status, expected_error in cases:
        monkeypatch.setattr(route, "ensure_translation_work_user", lambda user_id: {"id": user_id})
        monkeypatch.setattr(
            route.mk_import_svc,
            "import_mk_video",
            lambda **kwargs: (_ for _ in ()).throw(exc),
        )

        resp = authed_client_no_db.post(
            "/mk-import/video",
            json={"mk_video_metadata": {"filename": "x.mp4"}, "translator_id": 7},
        )

        assert resp.status_code == expected_status
        assert resp.get_json()["error"] == expected_error
