from __future__ import annotations


def test_medias_index_route_delegates_page_context_builder(
    authed_client_no_db,
    monkeypatch,
):
    captured = {}

    def fake_context_builder(args, extra):
        captured["query"] = args.get("q")
        captured["extra"] = extra
        return {
            "shopify_image_localizer_release": {},
            "material_roas_rmb_per_usd": 7.0,
            "medias_initial_query": "from-builder",
        }

    monkeypatch.setattr(
        "web.routes.medias.pages.build_medias_page_context",
        fake_context_builder,
    )

    response = authed_client_no_db.get("/medias/?q=abc-rjc")

    assert response.status_code == 200
    assert captured == {"query": "abc-rjc", "extra": {}}


def test_active_users_route_delegates_response_builder(
    authed_client_no_db,
    monkeypatch,
):
    monkeypatch.setattr(
        "web.routes.medias.pages.build_active_users_response",
        lambda: {"users": [{"id": 1, "username": "admin"}]},
    )

    response = authed_client_no_db.get("/medias/api/users/active")

    assert response.status_code == 200
    assert response.get_json() == {"users": [{"id": 1, "username": "admin"}]}


def test_active_users_route_keeps_admin_gate_before_service(
    authed_user_client_no_db,
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        "web.routes.medias.pages.build_active_users_response",
        lambda: calls.append("called") or {"users": []},
    )

    response = authed_user_client_no_db.get("/medias/api/users/active")

    assert response.status_code == 403
    assert calls == []


def test_languages_route_delegates_response_builder(
    authed_user_client_no_db,
    monkeypatch,
):
    monkeypatch.setattr(
        "web.routes.medias.pages.build_languages_response",
        lambda: {"items": [{"code": "en"}]},
    )

    response = authed_user_client_no_db.get("/medias/api/languages")

    assert response.status_code == 200
    assert response.get_json() == {"items": [{"code": "en"}]}
