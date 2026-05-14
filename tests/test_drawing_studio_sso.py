import hashlib
import hmac
from urllib.parse import parse_qs, urlencode, urlparse


def _expected_sig(secret: str, params: dict[str, str]) -> str:
    canonical = urlencode([(key, params[key]) for key in sorted(params)])
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def test_build_drawing_studio_sso_url_signs_current_user_payload(monkeypatch):
    from appcore.drawing_studio_sso import build_drawing_studio_sso_url

    monkeypatch.setenv("DRAWING_STUDIO_SSO_SECRET", "unit-test-secret")

    url = build_drawing_studio_sso_url(
        user_id=7,
        username="alice",
        role="admin",
        now=1_700_000_000,
        nonce="nonce-1",
    )

    parsed = urlparse(url)
    assert parsed.scheme == "http"
    assert parsed.netloc == "127.0.0.1:81"
    assert parsed.path == "/api/auth/autovideosrt-sso"

    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    assert query["avs_user_id"] == "7"
    assert query["avs_username"] == "alice"
    assert query["avs_role"] == "admin"
    assert query["exp"] == "1700000120"
    assert query["nonce"] == "nonce-1"

    signed_params = {key: value for key, value in query.items() if key != "sig"}
    assert query["sig"] == _expected_sig("unit-test-secret", signed_params)


def test_drawing_studio_sso_requires_login(authed_client_no_db):
    client = authed_client_no_db.application.test_client()

    response = client.get("/drawing-studio/sso")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_drawing_studio_sso_requires_configured_secret(authed_client_no_db, monkeypatch):
    monkeypatch.delenv("DRAWING_STUDIO_SSO_SECRET", raising=False)

    response = authed_client_no_db.get("/drawing-studio/sso")

    assert response.status_code == 503
    assert "DRAWING_STUDIO_SSO_SECRET" in response.get_data(as_text=True)


def test_drawing_studio_sso_redirects_authenticated_user_to_canvas_realm(
    authed_client_no_db,
    monkeypatch,
):
    monkeypatch.setenv("DRAWING_STUDIO_SSO_SECRET", "unit-test-secret")

    response = authed_client_no_db.get("/drawing-studio/sso")

    assert response.status_code == 302
    location = response.headers["Location"]
    parsed = urlparse(location)
    assert parsed.scheme == "http"
    assert parsed.netloc == "127.0.0.1:81"
    assert parsed.path == "/api/auth/autovideosrt-sso"

    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    assert query["avs_user_id"] == "1"
    assert query["avs_username"] == "admin"
    assert query["avs_role"] == "admin"
    signed_params = {key: value for key, value in query.items() if key != "sig"}
    assert query["sig"] == _expected_sig("unit-test-secret", signed_params)


def test_drawing_studio_sso_requires_menu_permission(monkeypatch):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setenv("DRAWING_STUDIO_SSO_SECRET", "unit-test-secret")

    fake_user = {
        "id": 3,
        "username": "blocked-user",
        "role": "user",
        "is_active": 1,
        "permissions": {"drawing_studio": False},
    }
    monkeypatch.setattr("web.auth.get_by_id", lambda user_id: fake_user if int(user_id) == 3 else None)

    from web.app import create_app

    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "3"
        session["_fresh"] = True

    response = client.get("/drawing-studio/sso")

    assert response.status_code == 403
