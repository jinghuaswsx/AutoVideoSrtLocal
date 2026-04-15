import pytest
from appcore import medias
from appcore.db import query_one


@pytest.fixture
def user_id():
    row = query_one("SELECT id FROM users ORDER BY id ASC LIMIT 1")
    assert row, "No users in DB; create one before running these tests."
    return row["id"]


def test_list_languages_returns_enabled_sorted():
    langs = medias.list_languages()
    codes = [l["code"] for l in langs]
    assert codes[0] == "en"
    assert set(codes) >= {"en", "de", "fr", "es", "it", "ja", "ko"}
    assert all(l["enabled"] == 1 for l in langs)


def test_is_valid_language():
    assert medias.is_valid_language("de") is True
    assert medias.is_valid_language("xx") is False


def test_list_languages_for_admin_includes_disabled_and_usage(monkeypatch):
    monkeypatch.setattr(
        medias,
        "query",
        lambda sql, args=(): [
            {"code": "en", "name_zh": "英语", "sort_order": 1, "enabled": 1},
            {"code": "pt", "name_zh": "葡萄牙语", "sort_order": 7, "enabled": 0},
        ] if "FROM media_languages" in sql else [],
    )
    monkeypatch.setattr(
        medias,
        "get_language_usage",
        lambda code: {
            "items_count": 0 if code == "en" else 2,
            "copy_count": 0,
            "cover_count": 0,
            "in_use": code == "pt",
        },
    )

    langs = medias.list_languages_for_admin()

    assert [item["code"] for item in langs] == ["en", "pt"]
    assert langs[0]["enabled"] == 1
    assert langs[1]["enabled"] == 0
    assert langs[1]["items_count"] == 2
    assert langs[1]["in_use"] is True


def test_normalize_language_code_lowercases_and_validates():
    assert medias.normalize_language_code(" PT-BR ") == "pt-br"
    with pytest.raises(ValueError, match="格式不合法"):
        medias.normalize_language_code("中文")


def test_create_language_rejects_duplicate_code(monkeypatch):
    monkeypatch.setattr(
        medias,
        "query_one",
        lambda sql, args=(): {"code": "pt"} if args == ("pt",) else None,
    )

    with pytest.raises(ValueError, match="已存在"):
        medias.create_language("PT", "葡萄牙语", 7, True)


def test_validate_language_update_rejects_disabling_en():
    with pytest.raises(ValueError, match="不能停用"):
        medias.validate_language_update("en", enabled=False)


def test_delete_language_rejects_default_and_in_use(monkeypatch):
    with pytest.raises(ValueError, match="不能删除"):
        medias.delete_language("en")

    monkeypatch.setattr(
        medias,
        "get_language_usage",
        lambda code: {"items_count": 1, "copy_count": 0, "cover_count": 0, "in_use": True},
    )
    with pytest.raises(ValueError, match="只能停用"):
        medias.delete_language("de")


def test_create_item_with_lang(user_id):
    pid = medias.create_product(user_id, "多语素材测试")
    try:
        iid_en = medias.create_item(pid, user_id, "en.mp4", "k/en", lang="en")
        iid_de = medias.create_item(pid, user_id, "de.mp4", "k/de", lang="de")
        items = medias.list_items(pid)
        by_id = {i["id"]: i for i in items}
        assert by_id[iid_en]["lang"] == "en"
        assert by_id[iid_de]["lang"] == "de"
        en_only = medias.list_items(pid, lang="en")
        assert [i["id"] for i in en_only] == [iid_en]
    finally:
        medias.soft_delete_product(pid)


def test_replace_copywritings_per_lang(user_id):
    pid = medias.create_product(user_id, "多语文案测试")
    try:
        medias.replace_copywritings(pid, [{"title": "T_en", "body": "B"}], lang="en")
        medias.replace_copywritings(pid, [
            {"title": "T_de_1", "body": "B1"},
            {"title": "T_de_2", "body": "B2"},
        ], lang="de")
        en = medias.list_copywritings(pid, lang="en")
        de = medias.list_copywritings(pid, lang="de")
        assert [c["title"] for c in en] == ["T_en"]
        assert [c["title"] for c in de] == ["T_de_1", "T_de_2"]
        # 替换 de 不应影响 en
        medias.replace_copywritings(pid, [], lang="de")
        assert medias.list_copywritings(pid, lang="en")[0]["title"] == "T_en"
        assert medias.list_copywritings(pid, lang="de") == []
    finally:
        medias.soft_delete_product(pid)


def test_product_covers_per_lang(user_id):
    pid = medias.create_product(user_id, "多语主图测试")
    try:
        medias.set_product_cover(pid, "en", "covers/en.jpg")
        medias.set_product_cover(pid, "de", "covers/de.jpg")
        covers = medias.get_product_covers(pid)
        assert covers["en"] == "covers/en.jpg"
        assert covers["de"] == "covers/de.jpg"
        # 解析：其他语种回退英文
        assert medias.resolve_cover(pid, "fr") == "covers/en.jpg"
        assert medias.resolve_cover(pid, "de") == "covers/de.jpg"
        medias.delete_product_cover(pid, "de")
        assert medias.resolve_cover(pid, "de") == "covers/en.jpg"
    finally:
        medias.soft_delete_product(pid)


def test_lang_coverage_map(user_id):
    pid = medias.create_product(user_id, "覆盖度测试")
    try:
        medias.create_item(pid, user_id, "a.mp4", "k/a", lang="en")
        medias.create_item(pid, user_id, "b.mp4", "k/b", lang="en")
        medias.create_item(pid, user_id, "c.mp4", "k/c", lang="de")
        medias.replace_copywritings(pid, [{"title": "x"}], lang="en")
        medias.set_product_cover(pid, "en", "covers/en.jpg")
        cov = medias.lang_coverage_by_product([pid])[pid]
        assert cov["en"]["items"] == 2
        assert cov["en"]["copy"] == 1
        assert cov["en"]["cover"] is True
        assert cov["de"]["items"] == 1
        assert cov["de"]["cover"] is False
        assert cov["fr"]["items"] == 0
    finally:
        medias.soft_delete_product(pid)


def test_get_product_covers_batch(user_id):
    pid1 = medias.create_product(user_id, "batch-covers-1")
    pid2 = medias.create_product(user_id, "batch-covers-2")
    try:
        medias.set_product_cover(pid1, "en", "k/p1_en.jpg")
        medias.set_product_cover(pid1, "de", "k/p1_de.jpg")
        medias.set_product_cover(pid2, "en", "k/p2_en.jpg")
        m = medias.get_product_covers_batch([pid1, pid2])
        assert m[pid1] == {"en": "k/p1_en.jpg", "de": "k/p1_de.jpg"}
        assert m[pid2] == {"en": "k/p2_en.jpg"}
        # 空参数
        assert medias.get_product_covers_batch([]) == {}
    finally:
        medias.soft_delete_product(pid1)
        medias.soft_delete_product(pid2)
