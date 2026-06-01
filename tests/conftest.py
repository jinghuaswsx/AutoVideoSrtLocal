import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("AUTOVIDEOSRT_DISABLE_BACKGROUND_THREADS", "1")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "allow_shopify_browser_automation: opt into fully mocked Shopify browser helper tests",
    )


@pytest.fixture(autouse=True)
def _base_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOVIDEOSRT_DISABLE_DOTENV", "1")
    monkeypatch.setenv("TOS_ACCESS_KEY", "test-tos-ak")
    monkeypatch.setenv("TOS_SECRET_KEY", "test-tos-sk")
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setenv("WTF_CSRF_ENABLED", "0")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("VOICES_FILE", str(ROOT / "voices" / "voices.json"))


@pytest.fixture(autouse=True)
def _disable_shopify_browser_automation(monkeypatch, request):
    if request.node.get_closest_marker("allow_shopify_browser_automation"):
        return

    from tools.shopify_image_localizer.browser import session
    from tools.shopify_image_localizer.rpa import ez_cdp, run_product_cdp

    def _noop_preload_chrome_tab_to_url(**_kwargs):
        return None

    def _noop_fetch_storefront_image_display_sizes(**_kwargs):
        return {}

    def _noop_ensure_cdp_chrome(*_args, **_kwargs):
        return False

    def _noop_open_managed_tab(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        run_product_cdp,
        "_preload_chrome_tab_to_url",
        _noop_preload_chrome_tab_to_url,
    )
    monkeypatch.setattr(
        run_product_cdp,
        "fetch_storefront_image_display_sizes",
        _noop_fetch_storefront_image_display_sizes,
    )
    monkeypatch.setattr(ez_cdp, "ensure_cdp_chrome", _noop_ensure_cdp_chrome)
    monkeypatch.setattr(ez_cdp, "open_managed_tab", _noop_open_managed_tab)
    monkeypatch.setattr(session, "start_chrome", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(session, "open_urls_in_chrome", lambda *_args, **_kwargs: None)


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
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.db.query", lambda *args, **kwargs: [])
    monkeypatch.setattr("appcore.db.query_one", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.scheduled_tasks.query", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "appcore.medias.list_enabled_language_codes",
        lambda: ["de", "fr", "es", "it", "pt", "ja", "nl", "sv", "fi", "en"],
    )
    from web.app import create_app

    fake_user = {
        "id": 1,
        "username": "admin",
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
def db_clean():
    """DB-touching helper for tests that need a clean translation_quality_assessments table.

    Imports happen lazily inside the fixture to avoid module-level side effects
    on test collection.
    """
    from appcore import db as _db

    class _DBHelper:
        def query(self, sql, args=None): return _db.query(sql, args)
        def query_one(self, sql, args=None): return _db.query_one(sql, args)
        def execute(self, sql, args=None): return _db.execute(sql, args)

    helper = _DBHelper()
    helper.execute("DELETE FROM translation_quality_assessments WHERE task_id LIKE 'task-%'")
    yield helper
    helper.execute("DELETE FROM translation_quality_assessments WHERE task_id LIKE 'task-%'")


@pytest.fixture
def authed_user_client_no_db(monkeypatch):
    """Flask client for a normal user with app startup recovery disabled."""
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.db.query", lambda *args, **kwargs: [])
    monkeypatch.setattr("appcore.db.query_one", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.scheduled_tasks.query", lambda *args, **kwargs: [])
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
