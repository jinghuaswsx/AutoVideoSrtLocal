"""Smoke + isolation verification for the omni_translate_0615 module (Task 9).

Covers:
1. Profile registration
2. Runner import / class attributes
3. Blueprint URL registration (+ V1 / V2 isolation sanity)
4. Authenticated → 200 for the list page
5. Unauthenticated → 302 redirect to login
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# 1. Profile
# ---------------------------------------------------------------------------

def test_omni_0615_profile_registered():
    from appcore.translate_profiles import get_profile
    p = get_profile("omni_0615")
    assert p.code == "omni_0615"
    assert p.name == "全能视频翻译0615"


# ---------------------------------------------------------------------------
# 2. Runner
# ---------------------------------------------------------------------------

def test_omni_0615_runner_imports():
    from appcore.runtime_omni_0615 import OmniTranslate0615Runner
    assert OmniTranslate0615Runner.project_type == "omni_translate_0615"
    assert OmniTranslate0615Runner.profile_code == "omni_0615"


# ---------------------------------------------------------------------------
# 3. Blueprint URL map
# ---------------------------------------------------------------------------

def test_omni_0615_blueprint_registers(monkeypatch):
    # Mirror authed_client_no_db monkeypatches so create_app() doesn't need DB.
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.db.query", lambda *args, **kwargs: [])
    monkeypatch.setattr("appcore.db.query_one", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.scheduled_tasks.query", lambda *args, **kwargs: [])

    from web.app import create_app
    app = create_app()
    rules = {r.rule for r in app.url_map.iter_rules()}

    # 0615 routes present
    assert any(rule.startswith("/omni-translate-0615") for rule in rules)

    # Isolation sanity: V1 and V2 still registered and untouched
    assert "/omni-translate" in rules
    assert any(rule.startswith("/omni-translate-v2") for rule in rules)


# ---------------------------------------------------------------------------
# 4. Authenticated → 200
# ---------------------------------------------------------------------------

def test_omni_0615_route_authed_200(authed_client_no_db):
    # The list view calls db_query / db_query_one via the module-level aliases
    # (translation_route_store.query, etc.) which hold direct references that
    # bypass the appcore.db monkeypatch.  Patch them at the route module level.
    with patch("web.routes.omni_translate_0615.db_query", return_value=[]), \
         patch("web.routes.omni_translate_0615.db_query_one", return_value=None), \
         patch("web.routes.omni_translate_0615.recover_all_interrupted_tasks", return_value=None), \
         patch("web.routes.omni_translate_0615._is_superadmin_user", return_value=False), \
         patch("appcore.settings.get_retention_hours", return_value=72):
        resp = authed_client_no_db.get("/omni-translate-0615")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 5. Unauthenticated → redirect to login (302)
# ---------------------------------------------------------------------------

def test_omni_0615_route_unauthed_redirects(monkeypatch):
    """GET /omni-translate-0615 without a session must redirect to login (302)."""
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.db.query", lambda *args, **kwargs: [])
    monkeypatch.setattr("appcore.db.query_one", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.scheduled_tasks.query", lambda *args, **kwargs: [])

    from web.app import create_app
    app = create_app()
    client = app.test_client()  # no session → anonymous user

    resp = client.get("/omni-translate-0615")
    # flask-login redirects unauthenticated users to the login view
    assert resp.status_code == 302
    assert "login" in resp.headers.get("Location", "").lower()
