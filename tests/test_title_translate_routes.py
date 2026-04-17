def test_page_renders(authed_client_no_db):
    resp = authed_client_no_db.get("/title-translate")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "多语言标题翻译" in html
    assert 'href="/title-translate"' in html
    assert 'class="active"' in html


def test_languages_api_returns_enabled_targets(authed_client_no_db, monkeypatch):
    from web.routes import title_translate as r

    expected = [
        {"code": "de", "name_zh": "德语", "sort_order": 2},
        {"code": "fr", "name_zh": "法语", "sort_order": 3},
    ]
    monkeypatch.setattr(r.title_translate_settings, "list_title_translate_languages", lambda: expected)

    resp = authed_client_no_db.get("/api/title-translate/languages")
    assert resp.status_code == 200
    assert resp.get_json() == {"languages": expected}
