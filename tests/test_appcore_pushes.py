import uuid

import pytest
from appcore import medias, pushes
from appcore.db import query_one, execute as db_execute


@pytest.fixture
def user_id():
    row = query_one("SELECT id FROM users ORDER BY id ASC LIMIT 1")
    assert row, "No users in DB"
    return row["id"]


@pytest.fixture
def product_with_item(user_id):
    code = f"push-test-{uuid.uuid4().hex[:8]}"
    pid = medias.create_product(user_id, "推送测试产品")
    medias.update_product(pid, product_code=code, ad_supported_langs="de,fr")
    item_id = medias.create_item(
        pid, user_id, filename="demo.mp4", object_key="u/1/m/1/demo.mp4",
        cover_object_key="u/1/m/1/cover.jpg",
        file_size=12345, duration_seconds=10.5, lang="de",
    )
    medias.replace_copywritings(pid, [{"title": "T", "body": "B"}], lang="de")
    yield pid, item_id
    medias.soft_delete_product(pid)


def test_compute_readiness_all_satisfied(product_with_item):
    pid, item_id = product_with_item
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    r = pushes.compute_readiness(item, product)
    assert r == {"has_object": True, "has_cover": True, "has_copywriting": True, "lang_supported": True}


def test_compute_readiness_missing_cover(product_with_item):
    pid, item_id = product_with_item
    db_execute("UPDATE media_items SET cover_object_key=NULL WHERE id=%s", (item_id,))
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    r = pushes.compute_readiness(item, product)
    assert r["has_cover"] is False
    assert r["has_object"] is True
    assert r["has_copywriting"] is True
    assert r["lang_supported"] is True


def test_compute_readiness_lang_not_supported(product_with_item):
    pid, item_id = product_with_item
    medias.update_product(pid, ad_supported_langs="fr")  # 没有 de
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    r = pushes.compute_readiness(item, product)
    assert r["lang_supported"] is False


def test_compute_status_pushed(product_with_item):
    pid, item_id = product_with_item
    db_execute("UPDATE media_items SET pushed_at=NOW() WHERE id=%s", (item_id,))
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    assert pushes.compute_status(item, product) == "pushed"


def test_compute_status_failed(product_with_item):
    pid, item_id = product_with_item
    log_id = db_execute(
        "INSERT INTO media_push_logs (item_id, operator_user_id, status, request_payload, error_message) "
        "VALUES (%s, %s, 'failed', %s, %s)",
        (item_id, 1, "{}", "timeout"),
    )
    db_execute("UPDATE media_items SET latest_push_id=%s WHERE id=%s", (log_id, item_id))
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    assert pushes.compute_status(item, product) == "failed"


def test_compute_status_pending(product_with_item):
    pid, item_id = product_with_item
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    assert pushes.compute_status(item, product) == "pending"


def test_compute_status_not_ready(product_with_item):
    pid, item_id = product_with_item
    db_execute("UPDATE media_items SET cover_object_key=NULL WHERE id=%s", (item_id,))
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    assert pushes.compute_status(item, product) == "not_ready"
