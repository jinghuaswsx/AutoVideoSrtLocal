from __future__ import annotations


def test_roas_page_returns_html_for_owner(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {
            "id": pid,
            "user_id": 1,
            "name": "测试产品",
            "product_code": "baseball-cap-organizer-rjc",
            "purchase_price": "7.4",
            "standalone_price": "20.95",
        },
    )
    monkeypatch.setattr(r.medias, "get_product_covers", lambda pid: {})
    monkeypatch.setattr(r, "_can_access_product", lambda product: product is not None)
    monkeypatch.setattr("appcore.product_roas.get_configured_rmb_per_usd", lambda: 6.83)

    resp = authed_client_no_db.get("/medias/6/roas")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "独立站保本 ROAS" in body
    assert 'data-roas-field="purchase_price"' in body
    assert 'data-roas-field="standalone_price"' in body
    assert "baseball-cap-organizer-rjc" in body


def test_roas_page_404_when_product_missing(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(r.medias, "get_product", lambda pid: None)
    monkeypatch.setattr(r, "_can_access_product", lambda product: False)

    resp = authed_client_no_db.get("/medias/9999/roas")

    assert resp.status_code == 404


def test_roas_page_404_when_user_cannot_access(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 999, "name": "x"},
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: False)

    resp = authed_client_no_db.get("/medias/6/roas")

    assert resp.status_code == 404


def test_roas_page_redirects_to_login_when_anonymous(monkeypatch):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "appcore.medias.list_enabled_language_codes",
        lambda: ["en"],
    )

    from web.app import create_app

    client = create_app().test_client()
    resp = client.get("/medias/6/roas", follow_redirects=False)

    assert resp.status_code in (301, 302)
    assert "/login" in resp.headers.get("Location", "")


def test_roas_page_includes_status_bar_and_back_link(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "user_id": 1, "name": "x", "product_code": "x-rjc"},
    )
    monkeypatch.setattr(r.medias, "get_product_covers", lambda pid: {})
    monkeypatch.setattr(r, "_can_access_product", lambda product: product is not None)
    monkeypatch.setattr("appcore.product_roas.get_configured_rmb_per_usd", lambda: 6.83)

    body = authed_client_no_db.get("/medias/6/roas").get_data(as_text=True)

    assert 'class="oc-roas-status-bar"' in body
    assert 'data-state="idle"' in body
    assert 'data-roas-status' in body
    assert 'href="/medias"' in body
    assert "返回素材管理" in body


def test_roas_page_loads_controller_script_and_bootstraps(authed_client_no_db, monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {
            "id": pid,
            "user_id": 1,
            "name": "x",
            "product_code": "x-rjc",
            "purchase_price": "7.4",
        },
    )
    monkeypatch.setattr(r.medias, "get_product_covers", lambda pid: {})
    monkeypatch.setattr(r, "_can_access_product", lambda product: product is not None)
    monkeypatch.setattr("appcore.product_roas.get_configured_rmb_per_usd", lambda: 6.83)

    body = authed_client_no_db.get("/medias/6/roas").get_data(as_text=True)

    assert "roas_form.js" in body
    assert "new RoasFormController" in body
    assert '"id": 6' in body or '"id":6' in body
    assert "fillFromProduct" in body
