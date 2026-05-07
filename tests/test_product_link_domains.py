from __future__ import annotations


def test_normalize_link_domain_accepts_https_url():
    from appcore import product_link_domains

    assert product_link_domains.normalize_domain(" https://Omurio.com/ ") == "omurio.com"
    assert product_link_domains.normalize_domain("newjoyloo.com") == "newjoyloo.com"


def test_build_product_page_url_uses_domain_and_language_path():
    from appcore import product_link_domains

    assert product_link_domains.build_product_page_url(
        "newjoyloo.com", "en", "demo-rjc"
    ) == "https://newjoyloo.com/products/demo-rjc"
    assert product_link_domains.build_product_page_url(
        "omurio.com", "de", "demo-rjc"
    ) == "https://omurio.com/de/products/demo-rjc"


def test_domain_language_keys_are_stable_and_parseable():
    from appcore import product_link_domains

    key = product_link_domains.domain_lang_key(" https://Omurio.com/ ", " DE ")

    assert key == "omurio.com:de"
    assert product_link_domains.parse_domain_lang_key(key) == {
        "domain": "omurio.com",
        "lang": "de",
        "legacy": False,
    }
    assert product_link_domains.parse_domain_lang_key("de") == {
        "domain": "",
        "lang": "de",
        "legacy": True,
    }


def test_resolve_product_page_url_rows_expands_enabled_domains_and_overrides(monkeypatch):
    from appcore import product_link_domains

    monkeypatch.setattr(
        product_link_domains,
        "list_enabled_product_domains",
        lambda product_id: [
            {"id": 1, "domain": "newjoyloo.com"},
            {"id": 2, "domain": "omurio.com"},
        ],
    )
    product = {
        "id": 10,
        "product_code": "demo-rjc",
        "localized_links_json": {
            "de": {
                "newjoyloo.com": "https://newjoyloo.com/de/products/demo-special-rjc"
            }
        },
    }

    rows = product_link_domains.resolve_product_page_url_rows(product, "de")

    assert rows == [
        {
            "domain": "newjoyloo.com",
            "lang": "de",
            "status_key": "newjoyloo.com:de",
            "url": "https://newjoyloo.com/de/products/demo-special-rjc",
        },
        {
            "domain": "omurio.com",
            "lang": "de",
            "status_key": "omurio.com:de",
            "url": "https://omurio.com/de/products/demo-rjc",
        },
    ]


def test_list_enabled_product_domains_defaults_to_active_global_domains(monkeypatch):
    from appcore import product_link_domains

    def fake_query(sql, args=()):
        if "FROM media_link_domains" in sql:
            return [
                {"id": 1, "domain": "newjoyloo.com", "enabled": 1, "sort_order": 10},
                {"id": 2, "domain": "omurio.com", "enabled": 1, "sort_order": 20},
                {"id": 3, "domain": "off.example", "enabled": 0, "sort_order": 30},
            ]
        if "FROM media_product_link_domains" in sql:
            return []
        raise AssertionError(sql)

    monkeypatch.setattr(product_link_domains, "_query", fake_query)

    rows = product_link_domains.list_enabled_product_domains(10)

    assert [row["domain"] for row in rows] == ["newjoyloo.com", "omurio.com"]


def test_list_enabled_product_domains_uses_product_overrides(monkeypatch):
    from appcore import product_link_domains

    def fake_query(sql, args=()):
        if "FROM media_link_domains" in sql:
            return [
                {"id": 1, "domain": "newjoyloo.com", "enabled": 1, "sort_order": 10},
                {"id": 2, "domain": "omurio.com", "enabled": 1, "sort_order": 20},
            ]
        if "FROM media_product_link_domains" in sql:
            return [
                {"domain_id": 1, "enabled": 0},
                {"domain_id": 2, "enabled": 1},
            ]
        raise AssertionError(sql)

    monkeypatch.setattr(product_link_domains, "_query", fake_query)

    rows = product_link_domains.list_enabled_product_domains(10)

    assert [row["domain"] for row in rows] == ["omurio.com"]
