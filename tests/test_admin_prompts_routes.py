from unittest.mock import patch


def test_list_prompts(authed_client_no_db):
    with patch("web.routes.admin_prompts.dao.list_all") as m_list:
        m_list.return_value = [
            {"id": 1, "slot": "base_translation", "lang": "de",
             "model_provider": "openrouter", "model_name": "gpt-4o-mini",
             "content": "X", "enabled": 1, "updated_at": None, "updated_by": 1},
        ]
        resp = authed_client_no_db.get("/admin/api/prompts")
    assert resp.status_code == 200
    assert resp.get_json()["items"][0]["slot"] == "base_translation"


def test_upsert_prompt(authed_client_no_db):
    with patch("web.routes.admin_prompts.dao.upsert") as m_up:
        resp = authed_client_no_db.put(
            "/admin/api/prompts",
            json={
                "slot": "base_translation", "lang": "de",
                "provider": "openrouter", "model": "gpt-4o-mini",
                "content": "new content",
            },
        )
    assert resp.status_code == 200
    m_up.assert_called_once()


def test_upsert_prompt_rejects_missing_required_fields(authed_client_no_db):
    resp = authed_client_no_db.put(
        "/admin/api/prompts",
        json={
            "slot": "base_translation",
            "provider": "openrouter",
            "model": "gpt-4o-mini",
        },
    )

    assert resp.status_code == 400
    assert resp.get_json() == {"error": "slot/provider/model/content required"}


def test_restore_default(authed_client_no_db):
    with patch("web.routes.admin_prompts.dao.delete") as m_del:
        resp = authed_client_no_db.delete(
            "/admin/api/prompts?slot=base_translation&lang=de"
        )
    assert resp.status_code == 200
    m_del.assert_called_once_with("base_translation", "de")


def test_restore_default_requires_slot(authed_client_no_db):
    resp = authed_client_no_db.delete("/admin/api/prompts")

    assert resp.status_code == 400
    assert resp.get_json() == {"error": "slot required"}


def test_resolve_prompt_config(authed_client_no_db):
    with patch("web.routes.admin_prompts.dao.resolve_prompt_config") as m_resolve:
        m_resolve.return_value = {
            "slot": "base_translation",
            "lang": "de",
            "content": "PROMPT",
        }
        resp = authed_client_no_db.get(
            "/admin/api/prompts/resolve?slot=base_translation&lang=de"
        )

    assert resp.status_code == 200
    assert resp.get_json()["content"] == "PROMPT"
    m_resolve.assert_called_once_with("base_translation", "de")


def test_resolve_prompt_config_requires_slot(authed_client_no_db):
    resp = authed_client_no_db.get("/admin/api/prompts/resolve")

    assert resp.status_code == 400
    assert resp.get_json() == {"error": "slot required"}


def test_resolve_prompt_config_returns_json_error(authed_client_no_db):
    with patch("web.routes.admin_prompts.dao.resolve_prompt_config") as m_resolve:
        m_resolve.side_effect = ValueError("bad slot")
        resp = authed_client_no_db.get(
            "/admin/api/prompts/resolve?slot=bad&lang=de"
        )

    assert resp.status_code == 400
    assert resp.get_json() == {"error": "bad slot"}


def test_non_admin_rejected(authed_user_client_no_db):
    resp = authed_user_client_no_db.get("/admin/api/prompts")
    assert resp.status_code in (302, 403)
