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

    captured = {}

    def fake_query_all(sql, args=None):
        captured["args"] = args
        return [{"filename": "a.mp4"}]

    monkeypatch.setattr(db, "query_all", fake_query_all)

    resp = authed_client_no_db.get(
        "/mk-import/check",
        query_string={"filenames": "a.mp4,b.mp4,a.mp4"},
    )

    assert resp.status_code == 200
    assert resp.get_json() == {"imported": ["a.mp4"], "missing": ["b.mp4"]}
    assert captured["args"] == ("a.mp4", "b.mp4", "a.mp4")


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

    def fake_import_mk_video(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "media_item_id": 12}

    monkeypatch.setattr(route.mk_import_svc, "import_mk_video", fake_import_mk_video)

    resp = authed_client_no_db.post(
        "/mk-import/video",
        json={"mk_video_metadata": {"filename": "x.mp4"}, "translator_id": 7},
    )

    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "media_item_id": 12}
    assert captured["mk_video_metadata"] == {"filename": "x.mp4"}
    assert captured["translator_id"] == 7
    assert captured["actor_user_id"] == 1


def test_mk_import_video_maps_service_errors(authed_client_no_db, monkeypatch):
    from web.routes import mk_import as route

    cases = [
        (route.mk_import_svc.DuplicateError("dupe"), 422, "duplicate_filename"),
        (route.mk_import_svc.DownloadError("404"), 502, "download_failed"),
        (route.mk_import_svc.StorageError("tos"), 500, "storage_failed"),
        (route.mk_import_svc.DBError("sql"), 500, "db_failed"),
    ]

    for exc, expected_status, expected_error in cases:
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
