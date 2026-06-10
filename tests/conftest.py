import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("AUTOVIDEOSRT_DISABLE_BACKGROUND_THREADS", "1")

_EXTERNAL_TEST_DIRS = {"audio", "e2e", "manual"}
_EXTERNAL_TEST_FILES = {
    "tests/test_multi_translate_e2e_smoke.py",
    "tests/test_translate_lab_e2e.py",
}
_LIVE_DB_TEST_FILES = {
    "tests/test_appcore_db.py",
    "tests/test_appcore_medias.py",
    "tests/test_appcore_medias_multi_lang.py",
    "tests/test_appcore_medias_raw_sources.py",
    "tests/test_appcore_mk_import.py",
    "tests/test_appcore_productivity_stats.py",
    "tests/test_appcore_pushes.py",
    "tests/test_appcore_raw_video_pool.py",
    "tests/test_appcore_task_state_db.py",
    "tests/test_appcore_tasks.py",
    "tests/test_appcore_users.py",
    "tests/test_bulk_translate_associations.py",
    "tests/test_bulk_translate_migration.py",
    "tests/test_manual_ad_spend.py",
    "tests/test_order_profit_aggregation.py",
    "tests/test_pushes_routes.py",
    "tests/test_quality_assessment_service.py",
    "tests/test_video_translate_profile_dao.py",
}


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def pytest_ignore_collect(collection_path, config):
    path = Path(str(collection_path))
    try:
        rel = path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return False

    parts = rel.split("/")
    if len(parts) >= 2 and parts[0] == "tests":
        if parts[1] in _EXTERNAL_TEST_DIRS and not _truthy_env("AUTOVIDEOSRT_RUN_EXTERNAL_TESTS"):
            return True
        if rel in _EXTERNAL_TEST_FILES and not _truthy_env("AUTOVIDEOSRT_RUN_EXTERNAL_TESTS"):
            return True
        if rel in _LIVE_DB_TEST_FILES and not _truthy_env("AUTOVIDEOSRT_RUN_LIVE_DB_TESTS"):
            return True
    return False


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
    if not _truthy_env("AUTOVIDEOSRT_RUN_LIVE_DB_TESTS"):
        pytest.skip("requires AUTOVIDEOSRT_RUN_LIVE_DB_TESTS=1 and a configured MySQL database")

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
    monkeypatch.setattr("appcore.medias.count_item_versions", lambda item_ids: {})
    monkeypatch.setattr("appcore.medias.list_product_skus", lambda product_id: [])
    monkeypatch.setattr("appcore.medias.list_product_skus_batch", lambda product_ids: {})
    monkeypatch.setattr("appcore.medias.list_shopify_product_ids", lambda product_id: [])
    monkeypatch.setattr("appcore.medias.list_shopify_product_ids_batch", lambda product_ids: {})
    monkeypatch.setattr("appcore.medias.list_yuncang_unit_prices", lambda skus: {})
    monkeypatch.setattr("appcore.sku_actual_roas.get_latest_sku_actual_roas", lambda skus: {})
    monkeypatch.setattr("appcore.media_video_materials.list_mk_bindings_for_items", lambda item_ids: {})
    monkeypatch.setattr("appcore.media_product_ad_status_cache.get_product_ad_summary_cache", lambda pids: {})
    monkeypatch.setattr("appcore.media_product_ad_status_cache.get_product_lang_ad_summary_cache", lambda pids: {})
    monkeypatch.setattr("appcore.media_product_order_stats.get_product_order_stats", lambda pids: {})
    monkeypatch.setattr("appcore.media_product_stability.get_product_stability_cache", lambda pids: {})
    monkeypatch.setattr("appcore.product_roas.get_configured_rmb_per_usd", lambda: 7.2)
    monkeypatch.setattr("appcore.meta_hot_posts.store.list_category_options", lambda: [])
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
    if not _truthy_env("AUTOVIDEOSRT_RUN_LIVE_DB_TESTS"):
        pytest.skip("requires AUTOVIDEOSRT_RUN_LIVE_DB_TESTS=1 and a configured MySQL database")

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
    monkeypatch.setattr("appcore.medias.count_item_versions", lambda item_ids: {})
    monkeypatch.setattr("appcore.medias.list_product_skus", lambda product_id: [])
    monkeypatch.setattr("appcore.medias.list_product_skus_batch", lambda product_ids: {})
    monkeypatch.setattr("appcore.medias.list_shopify_product_ids", lambda product_id: [])
    monkeypatch.setattr("appcore.medias.list_shopify_product_ids_batch", lambda product_ids: {})
    monkeypatch.setattr("appcore.medias.list_yuncang_unit_prices", lambda skus: {})
    monkeypatch.setattr("appcore.sku_actual_roas.get_latest_sku_actual_roas", lambda skus: {})
    monkeypatch.setattr("appcore.media_video_materials.list_mk_bindings_for_items", lambda item_ids: {})
    monkeypatch.setattr("appcore.media_product_ad_status_cache.get_product_ad_summary_cache", lambda pids: {})
    monkeypatch.setattr("appcore.media_product_ad_status_cache.get_product_lang_ad_summary_cache", lambda pids: {})
    monkeypatch.setattr("appcore.media_product_order_stats.get_product_order_stats", lambda pids: {})
    monkeypatch.setattr("appcore.media_product_stability.get_product_stability_cache", lambda pids: {})
    monkeypatch.setattr("appcore.product_roas.get_configured_rmb_per_usd", lambda: 7.2)
    monkeypatch.setattr("appcore.meta_hot_posts.store.list_category_options", lambda: [])
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
