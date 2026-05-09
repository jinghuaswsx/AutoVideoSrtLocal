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


def test_list_enabled_product_domains_defaults_to_newjoyloo_only(monkeypatch):
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

    assert [row["domain"] for row in rows] == ["newjoyloo.com"]

    options = product_link_domains.list_product_domain_options(10)
    by_domain = {row["domain"]: row for row in options}
    assert by_domain["newjoyloo.com"]["product_enabled"] is True
    assert by_domain["newjoyloo.com"]["effective_enabled"] is True
    assert by_domain["omurio.com"]["product_enabled"] is False
    assert by_domain["omurio.com"]["effective_enabled"] is False


def test_resolve_product_page_url_rows_respects_empty_enabled_domain_list(monkeypatch):
    from appcore import product_link_domains

    monkeypatch.setattr(product_link_domains, "list_enabled_product_domains", lambda product_id: [])

    rows = product_link_domains.resolve_product_page_url_rows(
        {"id": 10, "product_code": "demo-rjc"},
        "de",
    )

    assert rows == []


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


def test_get_default_domain_returns_db_value_when_set(monkeypatch):
    from appcore import product_link_domains

    captured: dict = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        return [{"domain": "omurio.com"}]

    monkeypatch.setattr(product_link_domains, "_query", fake_query)

    assert product_link_domains.get_default_domain() == "omurio.com"
    assert "is_default=1" in captured["sql"].replace(" ", "")


def test_get_default_domain_falls_back_to_hardcoded_when_db_empty(monkeypatch):
    from appcore import product_link_domains

    monkeypatch.setattr(product_link_domains, "_query", lambda *a, **kw: [])

    assert product_link_domains.get_default_domain() == product_link_domains.DEFAULT_LINK_DOMAINS[0]


def test_set_default_domain_clears_other_rows_and_forces_enabled(monkeypatch):
    from appcore import product_link_domains

    calls: list[tuple[str, tuple]] = []

    def fake_execute(sql, args=()):
        calls.append((" ".join(sql.split()), args))
        return None

    monkeypatch.setattr(product_link_domains, "_execute", fake_execute)

    product_link_domains.set_default_domain(7)

    assert calls == [
        ("UPDATE media_link_domains SET is_default=0 WHERE id<>%s", (7,)),
        ("UPDATE media_link_domains SET is_default=1, enabled=1 WHERE id=%s", (7,)),
    ]


def test_set_default_domain_with_zero_clears_all_defaults(monkeypatch):
    from appcore import product_link_domains

    calls: list[tuple[str, tuple]] = []

    def fake_execute(sql, args=()):
        calls.append((" ".join(sql.split()), args))
        return None

    monkeypatch.setattr(product_link_domains, "_execute", fake_execute)

    product_link_domains.set_default_domain(0)

    assert calls == [("UPDATE media_link_domains SET is_default=0", ())]


def test_resolve_product_page_url_rows_puts_default_domain_first(monkeypatch):
    from appcore import product_link_domains

    monkeypatch.setattr(
        product_link_domains,
        "list_enabled_product_domains",
        lambda product_id: [
            {"id": 1, "domain": "newjoyloo.com", "is_default": False},
            {"id": 2, "domain": "omurio.com", "is_default": True},
        ],
    )
    product = {"id": 10, "product_code": "demo-rjc"}

    rows = product_link_domains.resolve_product_page_url_rows(product, "de")

    assert [row["domain"] for row in rows] == ["omurio.com", "newjoyloo.com"]
    assert product_link_domains.first_product_page_url(product, "de") == (
        "https://omurio.com/de/products/demo-rjc"
    )


def test_list_domains_exposes_is_default_flag(monkeypatch):
    from appcore import product_link_domains

    monkeypatch.setattr(
        product_link_domains,
        "_query",
        lambda *a, **kw: [
            {"id": 1, "domain": "newjoyloo.com", "enabled": 1, "is_default": 0, "sort_order": 10},
            {"id": 2, "domain": "omurio.com", "enabled": 1, "is_default": 1, "sort_order": 20},
        ],
    )

    rows = product_link_domains.list_domains()
    by_domain = {row["domain"]: row for row in rows}

    assert by_domain["omurio.com"]["is_default"] is True
    assert by_domain["newjoyloo.com"]["is_default"] is False
