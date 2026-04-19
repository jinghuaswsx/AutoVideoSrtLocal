import pytest
from appcore import medias
from appcore.db import query_one, execute as db_execute


def _hard_delete_by_code(code: str) -> None:
    db_execute("DELETE FROM media_products WHERE product_code=%s", (code,))


@pytest.fixture
def user_id():
    # 取一个真实存在的 user id
    row = query_one("SELECT id FROM users ORDER BY id ASC LIMIT 1")
    assert row, "No users in DB; create one before running these tests."
    return row["id"]


def test_create_and_list_product(user_id):
    pid = medias.create_product(user_id, "测试产品 A", color_people="张三")
    try:
        assert pid > 0
        p = medias.get_product(pid)
        assert p["name"] == "测试产品 A"
        rows, total = medias.list_products(user_id, keyword="测试")
        assert any(r["id"] == pid for r in rows)
        assert total >= 1
    finally:
        medias.soft_delete_product(pid)
    assert medias.get_product(pid) is None


def test_replace_copywritings(user_id):
    pid = medias.create_product(user_id, "文案测试")
    try:
        medias.replace_copywritings(pid, [
            {"title": "T1", "body": "B1"},
            {"title": "T2", "body": "B2"},
        ])
        cs = medias.list_copywritings(pid)
        assert [c["title"] for c in cs] == ["T1", "T2"]
        medias.replace_copywritings(pid, [{"title": "TOnly", "body": "BOnly"}])
        cs = medias.list_copywritings(pid)
        assert len(cs) == 1 and cs[0]["title"] == "TOnly"
    finally:
        medias.soft_delete_product(pid)


def test_soft_delete_product_cascades_items(user_id):
    pid = medias.create_product(user_id, "级联测试")
    medias.create_item(pid, user_id, "a.mp4", "key/a")
    medias.create_item(pid, user_id, "b.mp4", "key/b")
    assert len(medias.list_items(pid)) == 2
    medias.soft_delete_product(pid)
    assert medias.list_items(pid) == []


def test_count_items_by_product(user_id):
    pid = medias.create_product(user_id, "计数测试")
    try:
        medias.create_item(pid, user_id, "a.mp4", "k1")
        medias.create_item(pid, user_id, "b.mp4", "k2")
        counts = medias.count_items_by_product([pid])
        assert counts[pid] == 2
    finally:
        medias.soft_delete_product(pid)


def test_update_product(user_id):
    pid = medias.create_product(user_id, "更新测试")
    try:
        medias.update_product(pid, name="新名字", archived=1)
        p = medias.get_product(pid)
        assert p["name"] == "新名字"
        assert p["archived"] == 1
    finally:
        medias.soft_delete_product(pid)


def test_create_product_with_code_and_cover(user_id):
    code = "abc-product-01"
    _hard_delete_by_code(code)
    pid = medias.create_product(
        user_id, "带编码的产品",
        product_code=code,
        cover_object_key="covers/1/x.jpg",
    )
    try:
        p = medias.get_product(pid)
        assert p["product_code"] == code
        assert p["cover_object_key"] == "covers/1/x.jpg"
    finally:
        _hard_delete_by_code(code)


def test_update_product_sets_code_and_cover(user_id):
    code = "updated-slug"
    _hard_delete_by_code(code)
    pid = medias.create_product(user_id, "待更新产品")
    try:
        medias.update_product(
            pid,
            product_code=code,
            cover_object_key="covers/1/new.jpg",
        )
        p = medias.get_product(pid)
        assert p["product_code"] == code
        assert p["cover_object_key"] == "covers/1/new.jpg"
    finally:
        _hard_delete_by_code(code)


def test_get_product_by_code(user_id):
    code = "query-code-1"
    _hard_delete_by_code(code)
    pid = medias.create_product(user_id, "可查编码", product_code=code)
    try:
        p = medias.get_product_by_code(code)
        assert p and p["id"] == pid
        assert medias.get_product_by_code("nope-xxxx") is None
    finally:
        _hard_delete_by_code(code)


def test_update_product_ad_supported_langs(user_id):
    pid = medias.create_product(user_id, "适配语种测试")
    try:
        medias.update_product(pid, ad_supported_langs="de,fr,ja")
        p = medias.get_product(pid)
        assert p["ad_supported_langs"] == "de,fr,ja"
    finally:
        medias.soft_delete_product(pid)


def test_parse_ad_supported_langs():
    assert medias.parse_ad_supported_langs(None) == []
    assert medias.parse_ad_supported_langs("") == []
    assert medias.parse_ad_supported_langs("de,fr, ja") == ["de", "fr", "ja"]
    assert medias.parse_ad_supported_langs(" DE , FR ") == ["de", "fr"]


def test_add_detail_image_records_translate_provenance(monkeypatch):
    captured = {}

    monkeypatch.setattr(medias, "_next_detail_image_sort_order", lambda product_id, lang: 3)

    def fake_execute(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return 88

    monkeypatch.setattr(medias, "execute", fake_execute)

    image_id = medias.add_detail_image(
        101,
        "de",
        "1/medias/101/de_1.png",
        content_type="image/png",
        origin_type="image_translate",
        source_detail_image_id=11,
        image_translate_task_id="img-task-1",
    )

    assert image_id == 88
    assert "origin_type" in captured["sql"]
    assert "source_detail_image_id" in captured["sql"]
    assert "image_translate_task_id" in captured["sql"]
    assert captured["args"] == (
        101, "de", 3, "1/medias/101/de_1.png", "image/png", None, None, None,
        "image_translate", 11, "img-task-1",
    )


def test_replace_detail_images_for_lang_recreates_rows_with_provenance(monkeypatch):
    deleted = []
    inserted = []

    monkeypatch.setattr(
        medias,
        "soft_delete_detail_images_by_lang",
        lambda product_id, lang: deleted.append((product_id, lang)) or 1,
        raising=False,
    )

    next_id = {"value": 500}

    def fake_add_detail_image(product_id, lang, object_key, **kwargs):
        next_id["value"] += 1
        inserted.append((product_id, lang, object_key, kwargs))
        return next_id["value"]

    monkeypatch.setattr(medias, "add_detail_image", fake_add_detail_image)

    new_ids = medias.replace_detail_images_for_lang(
        101,
        "de",
        [
            {
                "object_key": "1/medias/101/de_1.png",
                "content_type": "image/png",
                "origin_type": "image_translate",
                "source_detail_image_id": 11,
                "image_translate_task_id": "img-task-1",
            },
            {
                "object_key": "1/medias/101/de_2.png",
                "content_type": "image/png",
                "origin_type": "image_translate",
                "source_detail_image_id": 12,
                "image_translate_task_id": "img-task-1",
            },
        ],
    )

    assert deleted == [(101, "de")]
    assert new_ids == [501, 502]
    assert inserted[0][2] == "1/medias/101/de_1.png"
    assert inserted[0][3]["origin_type"] == "image_translate"
    assert inserted[0][3]["source_detail_image_id"] == 11
    assert inserted[1][3]["image_translate_task_id"] == "img-task-1"
