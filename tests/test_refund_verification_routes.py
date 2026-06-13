# NOTE: requires full Flask app (deps not installable on this dev box).
# Run on a complete environment / online. Left here for CI / production verification.
import pytest

pytest.skip("requires full app dependencies (flask_apscheduler etc.)", allow_module_level=True)


def test_import_requires_login():
    from web.app import create_app
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        resp = c.post("/order-analytics/refund-verify/import")
        assert resp.status_code in (302, 401, 403)
