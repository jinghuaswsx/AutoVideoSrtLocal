import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _base_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOVIDEOSRT_DISABLE_DOTENV", "1")
    monkeypatch.setenv("VOLC_API_KEY", "test-volc-key")
    monkeypatch.setenv("VOLC_RESOURCE_ID", "volc.seedasr.auc")
    monkeypatch.setenv("TOS_ACCESS_KEY", "test-tos-ak")
    monkeypatch.setenv("TOS_SECRET_KEY", "test-tos-sk")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-elevenlabs-key")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("VOICES_FILE", str(ROOT / "voices" / "voices.json"))


@pytest.fixture
def logged_in_client():
    """Returns a Flask test client authenticated as a test user (uses live DB)."""
    from web.app import create_app
    from appcore.users import create_user, get_by_username
    from appcore.db import execute

    username = "_test_web_user_"
    password = "testpass"

    execute("DELETE FROM users WHERE username = %s", (username,))
    create_user(username, password, role="admin")

    app = create_app()
    client = app.test_client()
    client.post("/login", data={"username": username, "password": password}, follow_redirects=True)

    yield client

    execute("DELETE FROM users WHERE username = %s", (username,))


@pytest.fixture
def authed_client_no_db(monkeypatch):
    """Flask client authenticated via session with a patched user loader."""
    from web.app import create_app

    fake_user = {
        "id": 1,
        "username": "test-admin",
        "role": "admin",
        "is_active": 1,
    }

    monkeypatch.setattr("web.auth.get_by_id", lambda user_id: fake_user if int(user_id) == 1 else None)

    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True

    return client


@pytest.fixture
def authed_user_client_no_db(monkeypatch):
    """Flask client for a normal user with app startup recovery disabled."""
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    from web.app import create_app

    fake_user = {
        "id": 2,
        "username": "test-user",
        "role": "user",
        "is_active": 1,
    }

    monkeypatch.setattr("web.auth.get_by_id", lambda user_id: fake_user if int(user_id) == 2 else None)

    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "2"
        session["_fresh"] = True

    return client
