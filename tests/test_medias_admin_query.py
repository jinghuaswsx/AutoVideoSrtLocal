from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _stub_medias_page_settings(monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(r.product_roas, "get_configured_rmb_per_usd", lambda: Decimal("6.83"))
    monkeypatch.setattr(r.shopify_image_localizer_release, "get_release_info", lambda: {})


def test_medias_page_accepts_short_admin_query_marker(authed_client_no_db, monkeypatch):
    _stub_medias_page_settings(monkeypatch)

    resp = authed_client_no_db.get("/medias/?a=1")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'window.MEDIAS_ADMIN_QUERY = "a=1";' in html


def test_medias_page_normalizes_legacy_admin_query_marker(authed_client_no_db, monkeypatch):
    _stub_medias_page_settings(monkeypatch)

    resp = authed_client_no_db.get("/medias/?admin=1")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'window.MEDIAS_ADMIN_QUERY = "a=1";' in html


def test_layout_persists_short_admin_query_on_same_origin_links():
    template = (ROOT / "web" / "templates" / "layout.html").read_text(encoding="utf-8")

    assert "persistMediasAdminQuery" in template
    assert "searchParams.set('a', '1')" in template
    assert "searchParams.delete('admin')" in template
    assert "document.addEventListener('click'" in template
    assert "window.open = function" in template
