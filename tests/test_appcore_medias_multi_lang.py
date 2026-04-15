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
