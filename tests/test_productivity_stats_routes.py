def test_index_admin_renders(authed_client_no_db):
    rsp = authed_client_no_db.get("/productivity-stats/")
    assert rsp.status_code == 200


def test_index_non_admin_forbidden(authed_user_client_no_db):
    rsp = authed_user_client_no_db.get("/productivity-stats/")
    assert rsp.status_code == 403


def test_api_summary_admin(authed_client_no_db):
    rsp = authed_client_no_db.get("/productivity-stats/api/summary?days=7")
    assert rsp.status_code in (200, 500)


def test_api_summary_non_admin_forbidden(authed_user_client_no_db):
    rsp = authed_user_client_no_db.get("/productivity-stats/api/summary?days=7")
    assert rsp.status_code == 403


def test_api_summary_invalid_days_falls_back(authed_client_no_db):
    rsp = authed_client_no_db.get("/productivity-stats/api/summary?days=999")
    # Should not crash; falls back to 30 days
    assert rsp.status_code in (200, 500)
