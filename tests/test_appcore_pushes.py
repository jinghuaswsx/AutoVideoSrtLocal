import uuid
from datetime import datetime, timedelta

import pytest
from appcore import medias, pushes
from appcore.db import query_one, execute as db_execute


class _JsonPostResponse:
    def __init__(self, status_code=200, text='{"ok":true}', ok=True):
        self.status_code = status_code
        self.text = text
        self.ok = ok


def test_post_json_payload_success(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers")
        captured["timeout"] = kwargs.get("timeout")
        return _JsonPostResponse(201, "created", True)

    monkeypatch.setattr("appcore.pushes.requests.post", fake_post)

    result = pushes.post_json_payload(
        "https://downstream.example/push",
        {"mode": "create"},
        headers={"X-Test": "1"},
        timeout=9,
    )

    assert captured == {
        "url": "https://downstream.example/push",
        "json": {"mode": "create"},
        "headers": {"X-Test": "1"},
        "timeout": 9,
    }
    assert result == {
        "ok": True,
        "upstream_status": 201,
        "response_body": "created",
        "response_body_full": "created",
    }


def test_post_json_payload_network_error(monkeypatch):
    import requests as _requests

    def boom(url, **kwargs):
        raise _requests.ConnectionError("connection refused")

    monkeypatch.setattr("appcore.pushes.requests.post", boom)

    result = pushes.post_json_payload(
        "https://downstream.example/push",
        {"mode": "create"},
    )

    assert result["ok"] is False
    assert result["error"] == "downstream_unreachable"
    assert result["detail"] == "connection refused"
    assert result["response_body_full"] is None


@pytest.fixture
def user_id():
    row = query_one("SELECT id FROM users ORDER BY id ASC LIMIT 1")
    assert row, "No users in DB"
    return row["id"]


@pytest.fixture
def product_with_item(user_id):
    code = f"push-test-{uuid.uuid4().hex[:8]}"
    pid = medias.create_product(user_id, "推送测试产品")
    medias.update_product(
        pid,
        product_code=code,
        ad_supported_langs="de,fr",
        shopify_image_status_json={
            "de": {
                "replace_status": "confirmed",
                "link_status": "normal",
            },
        },
    )
    item_id = medias.create_item(
        pid, user_id, filename="demo.mp4", object_key="u/1/m/1/demo.mp4",
        cover_object_key="u/1/m/1/cover.jpg",
        file_size=12345, duration_seconds=10.5, lang="de",
    )
    medias.replace_copywritings(pid, [{"title": "T", "body": "B"}], lang="de")
    # 默认插一条合规英文文案，compute_readiness 的 has_push_texts 才为 True。
    # 需要测 has_push_texts=False 的用例自己覆盖/删除这条。
    medias.replace_copywritings(
        pid,
        [{"body": "标题: T\n文案: M\n描述: D"}],
        lang="en",
    )
    yield pid, item_id
    medias.soft_delete_product(pid)


def test_compute_readiness_all_satisfied(product_with_item):
    pid, item_id = product_with_item
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    r = pushes.compute_readiness(item, product)
    assert r == {
        "is_listed": True,
        "has_object": True,
        "has_cover": True,
        "has_copywriting": True,
        "lang_supported": True,
        "has_push_texts": True,
        "shopify_image_confirmed": True,
        "shopify_image_reason": "",
    }


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


def test_compute_readiness_english_lang_supported_without_ad_supported_langs(monkeypatch):
    monkeypatch.setattr(
        "appcore.pushes.query_one",
        lambda sql, args: {"ok": 1}
        if "media_copywritings" in sql or "media_push_logs" in sql
        else None,
    )
    monkeypatch.setattr(
        "appcore.pushes._has_valid_en_push_texts",
        lambda product_id: True,
    )
    item = {
        "id": 10,
        "product_id": 20,
        "lang": "en",
        "object_key": "video.mp4",
        "cover_object_key": "cover.jpg",
    }
    product = {"id": 20, "ad_supported_langs": "", "listing_status": "上架"}
    r = pushes.compute_readiness(item, product)
    assert r["has_copywriting"] is True
    assert r["lang_supported"] is True
    assert r["shopify_image_confirmed"] is True


def test_compute_readiness_applies_active_push_rework_overrides(monkeypatch):
    monkeypatch.setattr("appcore.pushes.medias.is_product_listed", lambda product: True)
    monkeypatch.setattr("appcore.pushes.medias.parse_ad_supported_langs", lambda value: {"de"})
    monkeypatch.setattr(
        "appcore.pushes.query_one",
        lambda sql, args: {"ok": 1} if "media_copywritings" in sql else None,
    )
    monkeypatch.setattr("appcore.pushes._has_valid_en_push_texts", lambda product_id: True)
    monkeypatch.setattr(
        "appcore.pushes.shopify_image_tasks.is_confirmed_for_push",
        lambda product, lang: (True, ""),
    )
    monkeypatch.setattr(
        "appcore.pushes.shopify_image_tasks.domain_statuses_for_push",
        lambda product, lang: [],
    )
    monkeypatch.setattr(
        "appcore.tasks.active_push_rework_readiness_keys",
        lambda task_id: ["has_object", "has_push_texts"] if task_id == 44 else [],
        raising=False,
    )

    readiness = pushes.compute_readiness(
        {
            "id": 10,
            "task_id": 44,
            "product_id": 20,
            "lang": "de",
            "object_key": "video.mp4",
            "cover_object_key": "cover.jpg",
        },
        {"id": 20, "ad_supported_langs": "de", "listing_status": "上架"},
    )

    assert readiness["has_object"] is False
    assert readiness["has_push_texts"] is False
    assert readiness["has_cover"] is True
    assert readiness["has_copywriting"] is True
    assert pushes.is_ready(readiness) is False


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


def test_compute_status_from_readiness_uses_prefetched_failed_log(monkeypatch):
    def fail_query_one(sql, args=()):
        raise AssertionError("prefetched failed log ids should avoid per-row query")

    monkeypatch.setattr("appcore.pushes.query_one", fail_query_one)

    readiness = {
        "is_listed": True,
        "has_object": True,
        "has_cover": True,
        "has_copywriting": True,
        "lang_supported": True,
        "has_push_texts": True,
        "shopify_image_confirmed": True,
    }
    status = pushes.compute_status_from_readiness(
        {"latest_push_id": 5, "pushed_at": None},
        {},
        readiness,
        context={"failed_latest_push_ids": {5}},
    )

    assert status == pushes.STATUS_FAILED


def test_compute_readiness_uses_push_list_context_without_db(monkeypatch):
    def fail_query_one(sql, args=()):
        raise AssertionError("prefetched readiness context should avoid per-row query")

    monkeypatch.setattr("appcore.pushes.query_one", fail_query_one)
    monkeypatch.setattr(
        "appcore.pushes.shopify_image_tasks.is_confirmed_for_push",
        lambda product, lang: (True, ""),
    )
    monkeypatch.setattr(
        "appcore.pushes.shopify_image_tasks.domain_statuses_for_push",
        lambda product, lang: [],
    )

    readiness = pushes.compute_readiness(
        {
            "id": 11,
            "product_id": 7,
            "task_id": 99,
            "lang": "de",
            "object_key": "videos/demo.mp4",
            "cover_object_key": "covers/demo.jpg",
        },
        {"id": 7, "ad_supported_langs": "de"},
        context={
            "copywriting_langs": {(7, "de")},
            "valid_push_text_product_ids": {7},
            "rework_readiness_by_task_id": {99: {"has_cover"}},
        },
    )

    assert readiness["has_copywriting"] is True
    assert readiness["has_push_texts"] is True
    assert readiness["has_cover"] is False


def test_build_push_list_context_prefetches_status_inputs(monkeypatch):
    calls = []

    def fake_query(sql, args=()):
        normalized = " ".join(sql.split())
        calls.append(normalized)
        if normalized.startswith("SELECT DISTINCT product_id, lang FROM media_copywritings"):
            return [{"product_id": 7, "lang": "de"}]
        if normalized.startswith("SELECT product_id, body FROM media_copywritings"):
            return [
                {"product_id": 7, "body": "标题: T\n文案: M\n描述: D"},
                {"product_id": 8, "body": "invalid"},
            ]
        if normalized.startswith("SELECT id, status FROM media_push_logs"):
            return [{"id": 5, "status": "failed"}, {"id": 6, "status": "success"}]
        if normalized.startswith("SELECT id, status FROM tasks"):
            return [{"id": 99, "status": "assigned"}, {"id": 100, "status": "done"}]
        if normalized.startswith("SELECT task_id, payload_json FROM task_events"):
            return [{"task_id": 99, "payload_json": '{"issue_keys":["has_cover"]}'}]
        raise AssertionError(f"unexpected query: {normalized}")

    monkeypatch.setattr("appcore.pushes.query", fake_query)

    context = pushes.build_push_list_context([
        {"product_id": 7, "lang": "de", "latest_push_id": 5, "task_id": 99},
        {"product_id": 8, "lang": "fr", "latest_push_id": 6, "task_id": 100},
    ])

    assert context["copywriting_langs"] == {(7, "de")}
    assert context["valid_push_text_product_ids"] == {7}
    assert context["failed_latest_push_ids"] == {5}
    assert context["rework_readiness_by_task_id"] == {99: {"has_cover"}}
    assert len(calls) == 5


def test_refresh_push_status_cache_rows_upserts_computed_entries(monkeypatch):
    row = {
        "id": 77,
        "product_id": 7,
        "task_id": 99,
        "lang": "de",
        "pushed_at": None,
        "latest_push_id": None,
        "skip_push": 0,
        "product_name": "Demo",
        "product_code": "demo-rjc",
        "ad_supported_langs": "de",
        "listing_status": "上架",
    }
    context = {"copywriting_langs": {(7, "de")}}
    readiness = {
        "has_object": True,
        "has_cover": True,
        "has_copywriting": True,
        "lang_supported": True,
        "has_push_texts": True,
        "shopify_image_confirmed": True,
    }
    upserts = []

    monkeypatch.setattr(pushes, "build_push_list_context", lambda rows: context)
    monkeypatch.setattr(pushes, "compute_readiness", lambda item, product, **kwargs: readiness)
    monkeypatch.setattr(
        pushes,
        "compute_status_from_readiness",
        lambda item, product, provided, **kwargs: "pending",
    )
    monkeypatch.setattr(pushes, "_upsert_push_status_cache_entries", lambda entries: upserts.extend(entries))

    cache_map = pushes.refresh_push_status_cache_rows([row])

    assert set(cache_map) == {77}
    assert cache_map[77]["status"] == "pending"
    assert cache_map[77]["readiness"] is readiness
    assert len(upserts) == 1
    entry = upserts[0]
    assert entry["item_id"] == 77
    assert entry["product_id"] == 7
    assert entry["task_id"] == 99
    assert entry["lang"] == "de"
    assert entry["status"] == "pending"
    assert entry["readiness"] is readiness


def test_status_cache_for_rows_uses_fresh_cache_without_recompute(monkeypatch):
    now = datetime.now()
    cached = {
        77: {
            "item_id": 77,
            "status": "pending",
            "readiness": {"has_object": True},
            "computed_at": now,
        }
    }

    monkeypatch.setattr(pushes, "get_push_status_cache_map", lambda item_ids: cached)
    monkeypatch.setattr(
        pushes,
        "refresh_push_status_cache_rows",
        lambda rows: (_ for _ in ()).throw(AssertionError("fresh cache should not refresh")),
    )

    result = pushes.status_cache_for_rows(
        [{"id": 77, "product_id": 7}],
        max_age_seconds=300,
    )

    assert result == cached


def test_status_cache_for_rows_refreshes_missing_and_stale_rows(monkeypatch):
    now = datetime.now()
    rows = [
        {"id": 77, "product_id": 7},
        {"id": 78, "product_id": 8},
    ]
    refreshed = {
        77: {
            "item_id": 77,
            "status": "pending",
            "readiness": {"has_object": True},
            "computed_at": now,
        },
        78: {
            "item_id": 78,
            "status": "not_ready",
            "readiness": {"has_object": False},
            "computed_at": now,
        },
    }

    monkeypatch.setattr(
        pushes,
        "get_push_status_cache_map",
        lambda item_ids: {
            77: {
                "item_id": 77,
                "status": "pending",
                "readiness": {"old": True},
                "computed_at": now - timedelta(seconds=301),
            }
        },
    )
    monkeypatch.setattr(
        pushes,
        "refresh_push_status_cache_rows",
        lambda stale_rows: {int(row["id"]): refreshed[int(row["id"])] for row in stale_rows},
    )

    result = pushes.status_cache_for_rows(rows, max_age_seconds=300)

    assert result == refreshed


def test_refresh_push_status_cache_for_item_refreshes_single_joined_row(monkeypatch):
    row = {"id": 77, "product_id": 7, "lang": "de"}
    seen = {}

    def fake_refresh(rows):
        seen["rows"] = rows
        return {77: {"status": "pending"}}

    monkeypatch.setattr(pushes, "_get_push_row_for_status_cache", lambda item_id: row)
    monkeypatch.setattr(pushes, "refresh_push_status_cache_rows", fake_refresh)

    result = pushes.refresh_push_status_cache_for_item(77)

    assert seen["rows"] == [row]
    assert result == {77: {"status": "pending"}}


def test_push_state_writes_refresh_status_cache_for_item(monkeypatch):
    executed = []
    refreshed = []

    def fake_execute(sql, args=()):
        executed.append((sql, args))
        return 123 if sql.startswith("INSERT INTO media_push_logs") else 1

    monkeypatch.setattr(pushes, "execute", fake_execute)
    monkeypatch.setattr(
        pushes,
        "_refresh_push_status_cache_for_item_safely",
        lambda item_id: refreshed.append(item_id),
    )

    log_id = pushes.record_push_success(
        item_id=77,
        operator_user_id=1,
        payload={"ok": True},
        response_body="ok",
    )
    pushes.record_push_failure(
        item_id=78,
        operator_user_id=1,
        payload={"ok": False},
        error_message="timeout",
        response_body=None,
    )
    pushes.reset_push_state(79)
    pushes.mark_skip_push(80, 1)
    pushes.unmark_skip_push(81)

    assert log_id == 123
    assert refreshed == [77, 78, 79, 80, 81]
    assert len(executed) == 7


import requests


def test_probe_ad_url_success(monkeypatch):
    class FakeResp:
        status_code = 200
    monkeypatch.setattr(
        "appcore.pushes.requests.head",
        lambda url, timeout, allow_redirects: FakeResp(),
    )
    ok, err = pushes.probe_ad_url("https://example.com/x")
    assert ok is True
    assert err is None


def test_probe_ad_url_404(monkeypatch):
    class FakeResp:
        status_code = 404
    monkeypatch.setattr(
        "appcore.pushes.requests.head",
        lambda url, timeout, allow_redirects: FakeResp(),
    )
    ok, err = pushes.probe_ad_url("https://example.com/x")
    assert ok is False
    assert "404" in err


def test_probe_ad_url_timeout(monkeypatch):
    def boom(url, timeout, allow_redirects):
        raise requests.Timeout("timed out")
    monkeypatch.setattr("appcore.pushes.requests.head", boom)
    ok, err = pushes.probe_ad_url("https://example.com/x")
    assert ok is False
    assert "timed out" in err.lower() or "timeout" in err.lower()


def test_build_product_link():
    import config
    original = config.AD_URL_TEMPLATE
    config.AD_URL_TEMPLATE = "https://x.com/{lang}/p/{product_code}"
    try:
        assert pushes.build_product_link("de", "abc") == "https://x.com/de/p/abc"
    finally:
        config.AD_URL_TEMPLATE = original


def test_resolve_product_page_url_prefers_localized_link():
    product = {
        "product_code": "gold-foil-naturalization-display-rjc",
        "localized_links_json": {
            "de": "https://newjoyloo.com/de/products/gold-foil-naturalization-display-rjc-special",
        },
    }

    assert pushes.resolve_product_page_url("de", product) == (
        "https://newjoyloo.com/de/products/gold-foil-naturalization-display-rjc-special"
    )


def test_resolve_product_page_url_falls_back_to_default_template():
    product = {"product_code": "led-bubble-blaster-rjc"}

    assert pushes.resolve_product_page_url("en", product) == (
        "https://newjoyloo.com/products/led-bubble-blaster-rjc"
    )
    assert pushes.resolve_product_page_url("fr", product) == (
        "https://newjoyloo.com/fr/products/led-bubble-blaster-rjc"
    )


def test_build_item_payload_basic(monkeypatch, product_with_item):
    import config
    pid, item_id = product_with_item
    monkeypatch.setattr(config, "LOCAL_SERVER_BASE_URL", "http://local.test")
    monkeypatch.setattr(
        "appcore.pushes.medias.list_enabled_language_codes",
        lambda: ["en", "de", "fr", "es", "pt", "ja", "it"],
    )
    monkeypatch.setattr(config, "AD_URL_TEMPLATE",
                        "https://example.com/{lang}/products/{product_code}")

    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    payload = pushes.build_item_payload(item, product)

    assert payload["mode"] == "create"
    assert payload["author"] == "蔡靖华"
    assert payload["push_admin"] == "蔡靖华"
    assert len(payload["videos"]) == 1
    assert payload["videos"][0]["url"].startswith("http://local.test/medias/obj/")
    assert payload["videos"][0]["image_url"].startswith("http://local.test/medias/obj/")
    # 英语 + 6 条非英文链接
    assert len(payload["product_links"]) == 7
    assert f"https://newjoyloo.com/products/{product['product_code']}" in payload["product_links"]
    for link in payload["product_links"]:
        assert product["product_code"] in link


def test_build_item_payload_includes_english_product_link_without_db(monkeypatch):
    monkeypatch.setattr("appcore.pushes.medias.is_product_listed", lambda product: True)
    monkeypatch.setattr(
        "appcore.pushes.medias.list_enabled_language_codes",
        lambda: ["en", "de"],
    )
    monkeypatch.setattr(
        "appcore.pushes.resolve_product_page_urls",
        lambda lang, product: [{
            "url": (
                f"https://newjoyloo.com/products/{product['product_code']}"
                if lang == "en"
                else f"https://newjoyloo.com/de/products/{product['product_code']}"
            )
        }],
    )
    monkeypatch.setattr(
        "appcore.pushes.resolve_push_texts",
        lambda product_id: [{"title": "T", "message": "M", "description": "D"}],
    )
    monkeypatch.setattr(
        "appcore.pushes.build_media_public_url",
        lambda key: f"https://local/{key}" if key else None,
    )

    payload = pushes.build_item_payload(
        {
            "id": 1,
            "product_id": 10,
            "lang": "de",
            "display_name": "demo.mp4",
            "filename": "demo.mp4",
            "file_size": 123,
            "object_key": "video.mp4",
            "cover_object_key": "cover.jpg",
        },
        {
            "id": 10,
            "name": "Demo",
            "product_code": "demo-rjc",
            "importance": 3,
            "selling_points": "",
            "listing_status": "上架",
        },
    )

    assert payload["product_links"] == [
        "https://newjoyloo.com/products/demo-rjc",
        "https://newjoyloo.com/de/products/demo-rjc",
    ]


def test_resolve_localized_text_payload_returns_first_current_lang_copy(monkeypatch):
    monkeypatch.setattr(
        "appcore.pushes.query_one",
        lambda sql, args: {
            "title": "德语标题",
            "body": "德语正文",
            "description": "德语描述",
        },
    )
    monkeypatch.setattr("appcore.pushes.medias.get_language_name", lambda code: "德语")

    payload = pushes.resolve_localized_text_payload({
        "product_id": 123,
        "lang": "de",
    })

    assert payload == {
        "title": "德语标题",
        "message": "德语正文",
        "description": "德语描述",
        "lang": "德语",
    }


def test_resolve_localized_text_payload_parses_labeled_body(monkeypatch):
    monkeypatch.setattr(
        "appcore.pushes.query_one",
        lambda sql, args: {
            "title": "",
            "body": (
                "标题：  Ready. Aim. LAUNCH! 🌪️\n"
                "文案:\n"
                "Experience the thrill! 🤩 Instant mechanical launch. Durable & crash-proof. "
                "The coolest gift for ages 3+.\n"
                "描述: Fly High Today ✈️"
            ),
            "description": "",
        },
    )
    monkeypatch.setattr("appcore.pushes.medias.get_language_name", lambda code: "法语")

    payload = pushes.resolve_localized_text_payload({
        "product_id": 123,
        "lang": "fr",
    })

    assert payload == {
        "title": "Ready. Aim. LAUNCH! 🌪️",
        "message": (
            "Experience the thrill! 🤩 Instant mechanical launch. Durable & crash-proof. "
            "The coolest gift for ages 3+."
        ),
        "description": "Fly High Today ✈️",
        "lang": "法语",
    }


def test_resolve_localized_text_payload_returns_none_when_copy_missing(monkeypatch):
    monkeypatch.setattr("appcore.pushes.query_one", lambda sql, args: None)

    payload = pushes.resolve_localized_text_payload({
        "product_id": 123,
        "lang": "fr",
    })

    assert payload is None


def test_build_localized_texts_request_wraps_single_text(monkeypatch):
    monkeypatch.setattr(
        "appcore.pushes.resolve_localized_texts_payload",
        lambda item: [
            {
                "title": "de1",
                "message": "de2",
                "description": "de3",
                "lang": "德语",
            },
            {
                "title": "fr1",
                "message": "fr2",
                "description": "fr3",
                "lang": "法语",
            },
        ],
    )

    body = pushes.build_localized_texts_request({
        "product_id": 123,
        "lang": "fr",
    })

    assert body == {
        "texts": [
            {
                "title": "de1",
                "message": "de2",
                "description": "de3",
                "lang": "德语",
            },
            {
                "title": "fr1",
                "message": "fr2",
                "description": "fr3",
                "lang": "法语",
            }
        ]
    }


def test_build_localized_texts_request_returns_empty_array_when_text_missing(monkeypatch):
    monkeypatch.setattr("appcore.pushes.resolve_localized_texts_payload", lambda item: [])

    body = pushes.build_localized_texts_request({
        "product_id": 123,
        "lang": "fr",
    })

    assert body == {"texts": []}


def test_build_localized_texts_request_returns_empty_array_when_text_incomplete(monkeypatch):
    monkeypatch.setattr(
        "appcore.pushes.resolve_localized_texts_payload",
        lambda item: [],
    )

    body = pushes.build_localized_texts_request({
        "product_id": 123,
        "lang": "fr",
    })

    assert body == {"texts": []}


def test_resolve_localized_texts_payload_returns_all_enabled_first_rows_including_english(monkeypatch):
    monkeypatch.setattr(
        "appcore.pushes.query",
        lambda sql, args: [
            {
                "lang": "en",
                "title": "EN 标题",
                "body": "EN 文案",
                "description": "EN 描述",
            },
            {
                "lang": "fr",
                "title": "",
                "body": "标题: FR 标题\n文案: FR 文案\n描述: FR 描述",
                "description": "",
            },
            {
                "lang": "fr",
                "title": "ignored",
                "body": "ignored",
                "description": "ignored",
            },
            {
                "lang": "de",
                "title": "DE 标题",
                "body": "DE 文案",
                "description": "DE 描述",
            },
            {
                "lang": "it",
                "title": "IT 标题",
                "body": "IT 文案",
                "description": "",
            },
        ],
    )
    monkeypatch.setattr(
        "appcore.pushes.medias.list_languages",
        lambda: [
            {"code": "en"},
            {"code": "de"},
            {"code": "fr"},
            {"code": "it"},
        ],
    )
    monkeypatch.setattr(
        "appcore.pushes.medias.get_language_name",
        lambda code: {"en": "英语", "de": "德语", "fr": "法语", "it": "意大利语"}.get(code, code),
    )

    payload = pushes.resolve_localized_texts_payload({"product_id": 123})

    assert payload == [
        {
            "title": "EN 标题",
            "message": "EN 文案",
            "description": "EN 描述",
            "lang": "英语",
        },
        {
            "title": "DE 标题",
            "message": "DE 文案",
            "description": "DE 描述",
            "lang": "德语",
        },
        {
            "title": "FR 标题",
            "message": "FR 文案",
            "description": "FR 描述",
            "lang": "法语",
        },
    ]


def test_record_success_and_reset(product_with_item):
    pid, item_id = product_with_item
    log_id = pushes.record_push_success(
        item_id=item_id, operator_user_id=1,
        payload={"a": 1}, response_body="ok",
    )
    assert log_id > 0
    it = medias.get_item(item_id)
    assert it["pushed_at"] is not None
    assert it["latest_push_id"] == log_id

    pushes.reset_push_state(item_id)
    it2 = medias.get_item(item_id)
    assert it2["pushed_at"] is None
    assert it2["latest_push_id"] is None
    # 历史保留
    row = query_one("SELECT COUNT(*) AS c FROM media_push_logs WHERE item_id=%s", (item_id,))
    assert row["c"] == 1


def test_record_failure_does_not_mark_pushed(product_with_item):
    pid, item_id = product_with_item
    log_id = pushes.record_push_failure(
        item_id=item_id, operator_user_id=1,
        payload={"a": 1}, error_message="boom", response_body=None,
    )
    it = medias.get_item(item_id)
    assert it["pushed_at"] is None
    assert it["latest_push_id"] == log_id


def test_list_logs(product_with_item):
    pid, item_id = product_with_item
    pushes.record_push_failure(item_id=item_id, operator_user_id=1,
                               payload={}, error_message="e1", response_body=None)
    pushes.record_push_success(item_id=item_id, operator_user_id=1,
                               payload={}, response_body="ok")
    logs = pushes.list_item_logs(item_id)
    assert len(logs) == 2
    # 按时间倒序
    assert logs[0]["status"] == "success"
    assert logs[1]["status"] == "failed"


def test_list_items_for_push_default(product_with_item):
    pid, item_id = product_with_item
    rows, total = pushes.list_items_for_push(offset=0, limit=20)
    assert total >= 1
    assert any(r["id"] == item_id for r in rows)


def test_list_items_for_push_filter_by_lang(product_with_item):
    pid, item_id = product_with_item
    rows, total = pushes.list_items_for_push(langs=["fr"], offset=0, limit=20)
    # 我们的 item 是 de，过滤 fr 应该不包含
    assert all(r["id"] != item_id for r in rows)


def test_list_items_for_push_filter_by_keyword(product_with_item):
    pid, item_id = product_with_item
    db_execute("UPDATE media_items SET display_name='UNIQUEMARKER' WHERE id=%s", (item_id,))
    rows, _ = pushes.list_items_for_push(keyword="UNIQUEMARKER", offset=0, limit=20)
    assert len(rows) == 1
    assert rows[0]["id"] == item_id


def test_list_items_for_push_selects_product_ai_review_fields(monkeypatch):
    captured = {}

    monkeypatch.setattr("appcore.pushes.query_one", lambda sql, args: {"c": 0})
    monkeypatch.setattr(
        "appcore.pushes.medias._media_product_owner_name_expr",
        lambda: "u.username",
    )

    def fake_query(sql, args):
        captured["sql"] = sql
        captured["args"] = args
        return []

    monkeypatch.setattr("appcore.pushes.query", fake_query)

    rows, total = pushes.list_items_for_push(offset=0, limit=20)

    assert rows == []
    assert total == 0
    sql = captured["sql"]
    assert "p.remark" in sql
    assert "p.ai_score" in sql
    assert "p.ai_evaluation_result" in sql
    assert "p.ai_evaluation_detail" in sql
    assert "p.listing_status" in sql


def test_list_items_for_push_selects_product_owner_name(monkeypatch):
    captured = {}

    monkeypatch.setattr("appcore.pushes.query_one", lambda sql, args: {"c": 0})
    monkeypatch.setattr(
        "appcore.pushes.medias._media_product_owner_name_expr",
        lambda: "u.username",
    )

    def fake_query(sql, args):
        captured["sql"] = sql
        captured["args"] = args
        return []

    monkeypatch.setattr("appcore.pushes.query", fake_query)

    rows, total = pushes.list_items_for_push(offset=0, limit=20)

    assert rows == []
    assert total == 0
    sql = captured["sql"]
    assert "u.username AS owner_name" in sql
    assert "LEFT JOIN users u ON u.id = p.user_id" in sql


def test_list_items_for_push_does_not_exclude_english(monkeypatch):
    captured = {"count_sql": "", "list_sql": ""}

    def fake_query_one(sql, args):
        captured["count_sql"] = sql
        return {"c": 0}

    def fake_query(sql, args):
        captured["list_sql"] = sql
        return []

    monkeypatch.setattr("appcore.pushes.query_one", fake_query_one)
    monkeypatch.setattr("appcore.pushes.query", fake_query)
    monkeypatch.setattr(
        "appcore.pushes.medias._media_product_owner_name_expr",
        lambda: "u.username",
    )

    rows, total = pushes.list_items_for_push(offset=0, limit=20)

    assert rows == []
    assert total == 0
    assert "i.lang <> 'en'" not in captured["count_sql"]
    assert "i.lang <> 'en'" not in captured["list_sql"]


def test_list_items_for_push_filter_by_owner_id(monkeypatch):
    captured = {}

    monkeypatch.setattr("appcore.pushes.query_one", lambda sql, args: {"c": 0})
    monkeypatch.setattr(
        "appcore.pushes.medias._media_product_owner_name_expr",
        lambda: "u.username",
    )

    def fake_query(sql, args):
        captured["sql"] = sql
        captured["args"] = args
        return []

    monkeypatch.setattr("appcore.pushes.query", fake_query)

    rows, total = pushes.list_items_for_push(owner_id=42, offset=0, limit=20)

    assert rows == []
    assert total == 0
    assert "p.user_id = %s" in captured["sql"]
    assert 42 in captured["args"]


def test_list_items_for_push_filter_by_audit_result(monkeypatch):
    captured = {}

    monkeypatch.setattr("appcore.pushes.query_one", lambda sql, args: {"c": 0})
    monkeypatch.setattr(
        "appcore.pushes.medias._media_product_owner_name_expr",
        lambda: "u.username",
    )

    def fake_query(sql, args):
        captured["sql"] = sql
        captured["args"] = args
        return []

    monkeypatch.setattr("appcore.pushes.query", fake_query)

    rows, total = pushes.list_items_for_push(
        audit_result="部分适合推广",
        offset=0,
        limit=20,
    )

    assert rows == []
    assert total == 0
    assert "p.ai_evaluation_result = %s" in captured["sql"]
    assert "部分适合推广" in captured["args"]


def test_list_items_for_push_sorts_by_created_at_asc(monkeypatch):
    captured = {}

    monkeypatch.setattr("appcore.pushes.query_one", lambda sql, args: {"c": 0})
    monkeypatch.setattr(
        "appcore.pushes.medias._media_product_owner_name_expr",
        lambda: "u.username",
    )

    def fake_query(sql, args):
        captured["sql"] = sql
        captured["args"] = args
        return []

    monkeypatch.setattr("appcore.pushes.query", fake_query)

    rows, total = pushes.list_items_for_push(sort="created_at_asc", offset=0, limit=20)

    assert rows == []
    assert total == 0
    assert "ORDER BY i.created_at ASC, i.id ASC" in captured["sql"]


# ---------- resolve_push_texts ----------


def test_resolve_push_texts_returns_parsed(product_with_item):
    pid, _item_id = product_with_item
    body = "标题: Ready\n文案: Do it\n描述: Go"
    medias.replace_copywritings(pid, [{"body": body}], lang="en")
    texts = pushes.resolve_push_texts(pid)
    assert texts == [{"title": "Ready", "message": "Do it", "description": "Go", "lang": "英语 EN"}]


def test_resolve_push_texts_missing_raises(product_with_item):
    pid, _ = product_with_item
    medias.replace_copywritings(pid, [], lang="en")  # 清掉 fixture 里的默认英文文案
    with pytest.raises(pushes.CopywritingMissingError):
        pushes.resolve_push_texts(pid)


def test_resolve_push_texts_parse_error(product_with_item):
    pid, _ = product_with_item
    medias.replace_copywritings(
        pid, [{"body": "随便一段没有标签的中文"}], lang="en",
    )
    with pytest.raises(pushes.CopywritingParseError):
        pushes.resolve_push_texts(pid)


# ---------- build_item_payload ----------


def test_build_item_payload_uses_real_texts(product_with_item):
    pid, item_id = product_with_item
    body = "标题: Ready. Aim. LAUNCH!\n文案: Experience the thrill.\n描述: Fly High Today"
    medias.replace_copywritings(pid, [{"body": body}], lang="en")
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    payload = pushes.build_item_payload(item, product)
    assert payload["texts"] == [
        {
            "title": "Ready. Aim. LAUNCH!",
            "message": "Experience the thrill.",
            "description": "Fly High Today",
            "lang": "英语 EN",
        }
    ]


def test_build_item_payload_raises_when_no_en_copy(product_with_item):
    pid, item_id = product_with_item
    medias.replace_copywritings(pid, [], lang="en")  # 清掉 fixture 里的默认英文文案
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    with pytest.raises(pushes.CopywritingMissingError):
        pushes.build_item_payload(item, product)


# ---------- has_push_texts 就绪项 ----------


def test_compute_readiness_has_push_texts_true(product_with_item):
    pid, item_id = product_with_item
    # fixture 默认插了合规英文文案
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    r = pushes.compute_readiness(item, product)
    assert r["has_push_texts"] is True


def test_compute_readiness_has_push_texts_false_when_no_en(product_with_item):
    pid, item_id = product_with_item
    medias.replace_copywritings(pid, [], lang="en")
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    r = pushes.compute_readiness(item, product)
    assert r["has_push_texts"] is False


def test_compute_readiness_has_push_texts_false_when_unparseable(product_with_item):
    pid, item_id = product_with_item
    medias.replace_copywritings(pid, [{"body": "no labels here"}], lang="en")
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    r = pushes.compute_readiness(item, product)
    assert r["has_push_texts"] is False


def test_compute_status_not_ready_without_push_texts(product_with_item):
    pid, item_id = product_with_item
    medias.replace_copywritings(pid, [], lang="en")
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    assert pushes.compute_status(item, product) == pushes.STATUS_NOT_READY
