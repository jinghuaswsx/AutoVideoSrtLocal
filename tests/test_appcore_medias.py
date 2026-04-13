import pytest
from appcore import medias
from appcore.db import query_one


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
