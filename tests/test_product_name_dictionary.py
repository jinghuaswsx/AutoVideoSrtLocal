import pytest
from appcore import product_name_dictionary
from appcore.mingkong_materials import enrich_and_fetch_english_titles
from appcore.medias import create_product, update_product
from appcore.mingkong_material_preselections import upsert_preselection

def test_get_names(monkeypatch):
    query_calls = []

    def mock_query(sql, args=()):
        query_calls.append((sql, args))
        return [
            {"product_code": "code1", "product_cn_name": "中文1", "product_en_name": "english1"},
            {"product_code": "code2", "product_cn_name": "中文2", "product_en_name": None},
        ]

    # Test get_names with empty input
    assert product_name_dictionary.get_names([]) == {}
    assert product_name_dictionary.get_names([" ", None]) == {}
    assert len(query_calls) == 0

    # Test get_names with valid input
    monkeypatch.setattr("appcore.product_name_dictionary.query", mock_query)
    res = product_name_dictionary.get_names(["Code1", "code2 "])
    
    assert len(query_calls) == 1
    # Check that it generated the right placeholders and normalized to lower
    assert "%s" in query_calls[0][0]
    assert query_calls[0][1] == ("code1", "code2")
    
    assert res == {
        "code1": {"cn_name": "中文1", "en_name": "english1"},
        "code2": {"cn_name": "中文2", "en_name": ""},
    }

def test_sync_names(monkeypatch):
    execute_calls = []

    def mock_execute(sql, args=()):
        execute_calls.append((sql, args))
        return 1

    monkeypatch.setattr("appcore.product_name_dictionary.execute", mock_execute)

    # Empty inputs shouldn't write to DB
    product_name_dictionary.sync_names("", "cn", "en")
    product_name_dictionary.sync_names("code", "", " ")
    assert len(execute_calls) == 0

    # Valid inputs should write to DB with lowercase code and stripped strings
    product_name_dictionary.sync_names("  Code1 ", "中文名", "English Name")
    assert len(execute_calls) == 1
    assert "INSERT INTO product_name_dictionary" in execute_calls[0][0]
    assert execute_calls[0][1] == ("code1", "中文名", "English Name")

def test_enrich_and_fetch_english_titles_fallback_and_sync(monkeypatch):
    execute_calls = []
    
    def mock_query(sql, args=()):
        if "INSERT INTO product_name_dictionary" in sql:
            execute_calls.append((sql, args))
            return 1
        if "media_products" in sql:
            return [
                {"product_code": "new-code", "name": "新中文", "shopify_title": "New English"}
            ]
        if "FROM dianxiaomi_product_assets" in sql:
            return []
        if "FROM product_name_dictionary" in sql:
            # Return dictionary record for fallback
            return [
                {"product_code": "dict-code", "product_cn_name": "字典中文", "product_en_name": "Dict English"}
            ]
        return []

    # Mock DB functions used inside enrichment
    monkeypatch.setattr("appcore.mingkong_materials.query", mock_query)
    monkeypatch.setattr("appcore.mingkong_materials.execute", mock_query)

    items = [
        {
            "product_code": "dict-code",
            "product_url": "https://example.com/p1",
        },
        {
            "product_code": "new-code",
            "product_url": "https://example.com/p2",
            "product_cn_name": "新中文",
            "product_english_title": "New English",
        }
    ]

    enrich_and_fetch_english_titles(items, query_fn=mock_query)

    # Verify fallback worked for the first item
    assert items[0]["product_cn_name"] == "字典中文"
    assert items[0]["product_english_title"] == "Dict English"

    # Verify synchronization happened for both items (including the fallback one to ensure dictionary stays updated)
    # First item: syncs "dict-code", "字典中文", "Dict English"
    # Second item: syncs "new-code", "新中文", "New English"
    synced_codes = [call[1][0] for call in execute_calls]
    assert "dict-code" in synced_codes
    assert "new-code" in synced_codes

def test_medias_product_creation_and_update_sync(monkeypatch):
    synced = []
    def mock_sync_names(code, cn, en):
        synced.append((code, cn, en))

    monkeypatch.setattr("appcore.product_name_dictionary.sync_names", mock_sync_names)
    monkeypatch.setattr("appcore.medias.execute", lambda *a, **kw: 999)
    monkeypatch.setattr("appcore.medias.get_product", lambda pid: {
        "id": pid,
        "product_code": "med-code",
        "name": "中文测试",
        "shopify_title": "Shopify Title"
    })

    # Test create_product with Chinese name
    create_product(1, "产品中文", product_code="code-c")
    assert ("code-c", "产品中文", None) in synced

    # Test create_product with English name
    create_product(1, "Product English", product_code="code-e")
    assert ("code-e", None, "Product English") in synced

    # Test update_product
    synced.clear()
    update_product(999, name="中文测试", product_code="med-code")
    assert ("med-code", "中文测试", "Shopify Title") in synced

def test_preselections_sync(monkeypatch):
    synced = []
    def mock_sync_names(code, cn, en):
        synced.append((code, cn, en))

    monkeypatch.setattr("appcore.product_name_dictionary.sync_names", mock_sync_names)
    monkeypatch.setattr("appcore.mingkong_material_preselections.execute", lambda *a, **kw: 1)
    monkeypatch.setattr("appcore.mingkong_material_preselections.get_preselection", lambda k: {})

    payload = {
        "material_key": "mat-1",
        "product_code": "presel-code",
        "product_name": "预选中文",
        "product_english_name": "Presel English",
        "raw_video_url": "https://example.com/v",
        "countries": ["US"],
    }
    upsert_preselection(payload, user_id=1)
    assert ("presel-code", "预选中文", "Presel English") in synced
