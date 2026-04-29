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


def test_update_product_link_check_tasks_json(monkeypatch):
    payload = {
        "de": {
            "task_id": "task-de-1",
            "status": "review_ready",
            "link_url": "https://newjoyloo.com/de/products/demo",
            "checked_at": "2026-04-19T22:10:00",
            "summary": {
                "overall_decision": "unfinished",
                "pass_count": 3,
                "replace_count": 1,
                "review_count": 0,
            },
        }
    }
    captured = {}

    def fake_execute(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return 1

    monkeypatch.setattr(medias, "execute", fake_execute)

    medias.update_product(7, link_check_tasks_json=payload)

    assert "link_check_tasks_json=%s" in captured["sql"]
    assert '"task-de-1"' in captured["args"][0]
    assert captured["args"][-1] == 7


def test_update_product_shopifyid_normalizes_numeric_string(monkeypatch):
    captured = {}

    def fake_execute(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return 1

    monkeypatch.setattr(medias, "execute", fake_execute)

    medias.update_product(12, shopifyid="8560559554733")

    assert "shopifyid=%s" in captured["sql"]
    assert captured["args"] == ("8560559554733", 12)


def test_update_product_shopifyid_normalizes_blank_to_none(monkeypatch):
    captured = {}

    def fake_execute(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return 1

    monkeypatch.setattr(medias, "execute", fake_execute)

    medias.update_product(18, shopifyid="   ")

    assert "shopifyid=%s" in captured["sql"]
    assert captured["args"] == (None, 18)


def test_update_product_shopifyid_rejects_non_digits():
    with pytest.raises(ValueError, match="shopifyid 必须是纯数字字符串"):
        medias.update_product(21, shopifyid="abc-123")


def test_update_item_display_name_updates_only_display_name(monkeypatch):
    captured = {}

    def fake_execute(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return 1

    monkeypatch.setattr(medias, "execute", fake_execute)

    medias.update_item_display_name(77, "new-video-name.mp4")

    assert captured["sql"] == "UPDATE media_items SET display_name=%s WHERE id=%s"
    assert captured["args"] == ("new-video-name.mp4", 77)


def test_parse_link_check_tasks_json_handles_str_dict_and_none():
    assert medias.parse_link_check_tasks_json(None) == {}
    assert medias.parse_link_check_tasks_json("") == {}
    assert medias.parse_link_check_tasks_json({"de": {"task_id": "x"}}) == {"de": {"task_id": "x"}}
    assert medias.parse_link_check_tasks_json('{"de":{"task_id":"x"}}') == {"de": {"task_id": "x"}}


def test_list_products_orders_by_created_at_desc(monkeypatch):
    captured = {}

    def fake_query_one(sql, args=()):
        if "information_schema.COLUMNS" in sql:
            return None
        captured["count_sql"] = sql
        captured["count_args"] = args
        return {"c": 0}

    def fake_query(sql, args=()):
        captured["list_sql"] = sql
        captured["list_args"] = args
        return []

    monkeypatch.setattr(medias, "query_one", fake_query_one)
    monkeypatch.setattr(medias, "query", fake_query)

    rows, total = medias.list_products(None, archived=False, offset=20, limit=20)

    assert rows == []
    assert total == 0
    assert "ORDER BY p.created_at DESC, p.id DESC" in captured["list_sql"]
    assert captured["list_args"][-2:] == (20, 20)


def test_list_products_matches_numeric_keyword_by_mk_id(monkeypatch):
    captured = {}

    def fake_query_one(sql, args=()):
        if "information_schema.COLUMNS" in sql:
            return None
        captured["count_sql"] = sql
        captured["count_args"] = args
        return {"c": 0}

    def fake_query(sql, args=()):
        captured["list_sql"] = sql
        captured["list_args"] = args
        return []

    monkeypatch.setattr(medias, "query_one", fake_query_one)
    monkeypatch.setattr(medias, "query", fake_query)

    rows, total = medias.list_products(None, keyword="12345", archived=False, offset=0, limit=20)

    assert rows == []
    assert total == 0
    assert "(p.name LIKE %s OR p.product_code LIKE %s OR p.id=%s OR p.mk_id=%s)" in captured["count_sql"]
    assert captured["count_args"] == (0, "%12345%", "%12345%", 12345, 12345)
    assert captured["list_args"][:-2] == captured["count_args"]


def test_list_products_matches_numeric_keyword_by_product_id(monkeypatch):
    captured = {}

    def fake_query_one(sql, args=()):
        if "information_schema.COLUMNS" in sql:
            return None
        captured["count_sql"] = sql
        captured["count_args"] = args
        return {"c": 1}

    def fake_query(sql, args=()):
        captured["list_sql"] = sql
        captured["list_args"] = args
        return [{"id": 537536}]

    monkeypatch.setattr(medias, "query_one", fake_query_one)
    monkeypatch.setattr(medias, "query", fake_query)

    rows, total = medias.list_products(None, keyword="537536", archived=False, offset=0, limit=20)

    assert rows == [{"id": 537536}]
    assert total == 1
    assert "p.id=%s" in captured["count_sql"]
    assert captured["count_args"] == (0, "%537536%", "%537536%", 537536, 537536)
    assert captured["list_args"][:-2] == captured["count_args"]


def test_list_products_prefers_users_xingming_when_available(monkeypatch):
    captured = {}

    def fake_query_one(sql, args=()):
        if "information_schema.COLUMNS" in sql:
            return {"ok": 1}
        captured["count_sql"] = sql
        captured["count_args"] = args
        return {"c": 0}

    def fake_query(sql, args=()):
        captured["list_sql"] = sql
        captured["list_args"] = args
        return []

    monkeypatch.setattr(medias, "query_one", fake_query_one)
    monkeypatch.setattr(medias, "query", fake_query)

    rows, total = medias.list_products(None, archived=False, offset=0, limit=20)

    assert rows == []
    assert total == 0
    assert "LEFT JOIN users u ON u.id = p.user_id" in captured["list_sql"]
    assert "COALESCE(NULLIF(TRIM(u.xingming), ''), u.username) AS owner_name" in captured["list_sql"]


def test_list_products_falls_back_to_username_when_xingming_column_missing(monkeypatch):
    captured = {}

    def fake_query_one(sql, args=()):
        if "information_schema.COLUMNS" in sql:
            return None
        captured["count_sql"] = sql
        captured["count_args"] = args
        return {"c": 0}

    def fake_query(sql, args=()):
        captured["list_sql"] = sql
        captured["list_args"] = args
        return []

    monkeypatch.setattr(medias, "query_one", fake_query_one)
    monkeypatch.setattr(medias, "query", fake_query)

    rows, total = medias.list_products(None, archived=False, offset=0, limit=20)

    assert rows == []
    assert total == 0
    assert "LEFT JOIN users u ON u.id = p.user_id" in captured["list_sql"]
    assert "u.username AS owner_name" in captured["list_sql"]
    assert "u.xingming" not in captured["list_sql"]


# ==================== 负责人 / 项目归属迁移 ====================

import uuid  # noqa: E402
from appcore import users as appusers  # noqa: E402


def _has_xingming_column() -> bool:
    return bool(query_one(
        "SELECT 1 AS ok FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME='users' "
        "AND COLUMN_NAME='xingming'"
    ))


@pytest.fixture
def ephemeral_users():
    """创建两个临时 user 作为 owner_a / owner_b，测试结束后硬删除。"""
    uname_a = f"pytest_owner_a_{uuid.uuid4().hex[:10]}"
    uname_b = f"pytest_owner_b_{uuid.uuid4().hex[:10]}"
    uid_a = appusers.create_user(uname_a, "pw")
    uid_b = appusers.create_user(uname_b, "pw")
    if _has_xingming_column():
        db_execute("UPDATE users SET xingming=%s WHERE id=%s", ("乙同学", uid_b))
    try:
        yield uid_a, uid_b
    finally:
        db_execute("DELETE FROM users WHERE id IN (%s, %s)", (uid_a, uid_b))


def test_list_active_users_excludes_inactive(ephemeral_users):
    uid_a, uid_b = ephemeral_users
    appusers.set_active(uid_a, False)
    try:
        rows = medias.list_active_users()
        ids = [r["id"] for r in rows]
        assert uid_b in ids
        assert uid_a not in ids
    finally:
        appusers.set_active(uid_a, True)


def test_list_active_users_prefers_chinese_name(ephemeral_users):
    _, uid_b = ephemeral_users
    if not _has_xingming_column():
        pytest.skip("users.xingming column not present in this DB")
    rows = medias.list_active_users()
    b_entry = next((r for r in rows if r["id"] == uid_b), None)
    assert b_entry is not None
    assert b_entry["display_name"] == "乙同学"


def test_get_user_display_name(ephemeral_users):
    _, uid_b = ephemeral_users
    name = medias.get_user_display_name(uid_b)
    assert name  # 至少是 username 或 xingming
    if _has_xingming_column():
        assert name == "乙同学"


def test_get_user_display_name_unknown_user():
    assert medias.get_user_display_name(999_999_999) == ""


def test_update_product_owner_syncs_all_tables(ephemeral_users):
    uid_a, uid_b = ephemeral_users
    pid = medias.create_product(uid_a, "换归属测试")
    try:
        medias.create_item(pid, uid_a, "a.mp4", f"{uid_a}/medias/{pid}/a.mp4")
        medias.create_item(pid, uid_a, "b.mp4", f"{uid_a}/medias/{pid}/b.mp4")
        medias.create_raw_source(
            pid, uid_a,
            display_name="原始",
            video_object_key=f"{uid_a}/medias/{pid}/raw.mp4",
            cover_object_key=f"{uid_a}/medias/{pid}/raw.jpg",
        )

        medias.update_product_owner(pid, uid_b)

        prod = medias.get_product(pid)
        assert prod["user_id"] == uid_b
        items = medias.list_items(pid)
        assert items and all(it["user_id"] == uid_b for it in items)
        raws = medias.list_raw_sources(pid)
        assert raws and all(rs["user_id"] == uid_b for rs in raws)
    finally:
        medias.soft_delete_product(pid)


def test_update_product_owner_skips_soft_deleted_rows(ephemeral_users):
    uid_a, uid_b = ephemeral_users
    pid = medias.create_product(uid_a, "软删跳过测试")
    try:
        item_live = medias.create_item(pid, uid_a, "live.mp4", f"{uid_a}/medias/{pid}/live.mp4")
        item_gone = medias.create_item(pid, uid_a, "gone.mp4", f"{uid_a}/medias/{pid}/gone.mp4")
        db_execute("UPDATE media_items SET deleted_at=NOW() WHERE id=%s", (item_gone,))

        medias.update_product_owner(pid, uid_b)

        live_row = query_one("SELECT user_id FROM media_items WHERE id=%s", (item_live,))
        gone_row = query_one("SELECT user_id FROM media_items WHERE id=%s", (item_gone,))
        assert live_row["user_id"] == uid_b
        assert gone_row["user_id"] == uid_a  # 软删行保留旧归属
    finally:
        medias.soft_delete_product(pid)
        db_execute("DELETE FROM media_items WHERE id=%s", (item_gone,))


def test_update_product_owner_rejects_unknown_user(ephemeral_users):
    uid_a, _ = ephemeral_users
    pid = medias.create_product(uid_a, "未知用户拒绝")
    try:
        with pytest.raises(ValueError):
            medias.update_product_owner(pid, 999_999_999)
    finally:
        medias.soft_delete_product(pid)


def test_update_product_owner_rejects_inactive_user(ephemeral_users):
    uid_a, uid_b = ephemeral_users
    pid = medias.create_product(uid_a, "停用用户拒绝")
    appusers.set_active(uid_b, False)
    try:
        with pytest.raises(ValueError):
            medias.update_product_owner(pid, uid_b)
    finally:
        appusers.set_active(uid_b, True)
        medias.soft_delete_product(pid)


def test_update_product_owner_rejects_unknown_product(ephemeral_users):
    _, uid_b = ephemeral_users
    with pytest.raises(ValueError):
        medias.update_product_owner(999_999_999, uid_b)
