from unittest.mock import patch


def test_sync_start_returns_sync_id(authed_client_no_db):
    with patch("web.routes.admin.vlst.start_sync",
               return_value="sync_fake"), \
         patch("web.routes.admin.medias.list_enabled_language_codes",
               return_value=["de"]):
        resp = authed_client_no_db.post("/admin/voice-library/sync/de")
    assert resp.status_code == 202
    assert resp.get_json()["sync_id"] == "sync_fake"


def test_sync_start_409_when_busy(authed_client_no_db):
    def busy(**kw):
        raise RuntimeError("another sync is running")
    with patch("web.routes.admin.vlst.start_sync", side_effect=busy), \
         patch("web.routes.admin.medias.list_enabled_language_codes",
               return_value=["de"]):
        resp = authed_client_no_db.post("/admin/voice-library/sync/de")
    assert resp.status_code == 409


def test_sync_start_400_for_disabled_language(authed_client_no_db):
    with patch("web.routes.admin.medias.list_enabled_language_codes",
               return_value=["de"]):
        resp = authed_client_no_db.post("/admin/voice-library/sync/fr")
    assert resp.status_code == 400


def test_sync_start_500_on_other_runtime_error(authed_client_no_db):
    with patch("web.routes.admin.vlst.start_sync",
               side_effect=RuntimeError("ELEVENLABS_API_KEY 未配置")), \
         patch("web.routes.admin.medias.list_enabled_language_codes",
               return_value=["de"]):
        resp = authed_client_no_db.post("/admin/voice-library/sync/de")
    assert resp.status_code == 500


def test_sync_status_returns_current_and_summary(authed_client_no_db):
    with patch("web.routes.admin.vlst.get_current",
               return_value={"sync_id": "x", "language": "de", "status": "running"}), \
         patch("web.routes.admin.vlst.summarize",
               return_value=[{
                   "language": "de",
                   "total_rows": 1,
                   "embedded_rows": 1,
                   "total_available": 927,
                   "target_total": 927,
               }]):
        resp = authed_client_no_db.get("/admin/voice-library/sync-status")
    data = resp.get_json()
    assert data["current"]["language"] == "de"
    assert data["summary"][0]["language"] == "de"
    assert data["summary"][0]["target_total"] == 927


def test_sync_non_admin_forbidden(authed_user_client_no_db):
    resp = authed_user_client_no_db.post("/admin/voice-library/sync/de")
    # admin_required either returns 403 or redirects to login
    assert resp.status_code in (302, 403)


def test_sync_status_non_admin_forbidden(authed_user_client_no_db):
    resp = authed_user_client_no_db.get("/admin/voice-library/sync-status")
    assert resp.status_code in (302, 403)
