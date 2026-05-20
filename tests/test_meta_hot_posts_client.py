from tools.meta_hot_posts import client


def test_throttled_wedev_client_waits_at_least_three_seconds_between_requests():
    calls = []
    sleeps = []
    now = [100.0]

    def fake_fetch(method, url, *, params=None, headers=None):
        calls.append((method, url, params, headers))
        return {"data": {"items": [], "total": 0, "size": 30}}

    api = client.MetaHotPostsClient(
        base_url="https://os.wedev.vip",
        fetch_fn=fake_fetch,
        headers_fn=lambda: {"Authorization": "Bearer token"},
        min_interval_seconds=1.0,
        sleep_fn=sleeps.append,
        monotonic_fn=lambda: now[0],
    )

    api.fetch_page(page=1)
    now[0] = 101.0
    api.fetch_page(page=2)

    assert len(calls) == 2
    assert calls[0][1] == "https://os.wedev.vip/api/spy/hot/posts"
    assert calls[1][2]["page"] == 2
    assert sleeps == [2.0]


def test_fetch_page_detects_wedev_expired_credentials():
    api = client.MetaHotPostsClient(
        base_url="https://os.wedev.vip",
        fetch_fn=lambda *a, **k: {"data": None, "is_guest": True, "message": "登录已失效"},
        headers_fn=lambda: {"Cookie": "sid=abc"},
        sleep_fn=lambda _: None,
    )

    try:
        api.fetch_page(page=1)
    except client.WedevCredentialsExpiredError as exc:
        assert "登录已失效" in str(exc)
    else:
        raise AssertionError("expected WedevCredentialsExpiredError")


def test_normalize_hot_post_derives_links_and_card_fields():
    row = {
        "id": 99,
        "page_id": "page1",
        "post_id": "post1",
        "bm_page_id": "bm1",
        "product_url": "example.com/products/a",
        "creation_time": "2026-05-08T12:35:00Z",
        "last_synced_at": "2026-05-12T09:07:00Z",
        "likes": 100,
        "comments": 8,
        "shares": 5,
        "latest_likes": 120,
        "latest_comments": 9,
        "latest_shares": 6,
        "sync_period_likes": 20,
        "sync_period_hours": 39.21,
        "message": "<p>Hello</p>",
        "video": "https://facebook.com/video",
        "image": "",
    }

    normalized = client.normalize_hot_post(row)

    assert normalized["wedev_post_id"] == 99
    assert normalized["post_url"] == "https://facebook.com/page1/posts/post1"
    assert "view_all_page_id=bm1" in normalized["ad_library_url"]
    assert normalized["product_url"] == "https://example.com/products/a"
    assert normalized["card_metrics"]["latest_comments"] == 9
    assert normalized["raw_json"]["id"] == 99


def test_normalize_hot_post_captures_wedev_pushed_marker():
    for key, value in (
        ("is_pushed", True),
        ("pushed", 1),
        ("push_status", "pushed"),
        ("status", "已推送"),
    ):
        normalized = client.normalize_hot_post({"id": 99, key: value})

        assert normalized["is_pushed"] is True


def test_normalize_hot_post_treats_wedev_selected_marker_as_pushed():
    for row in (
        {"id": 99, "selected_at": "2026-05-13T17:29:50+08:00"},
        {"id": 99, "select": {"id": 10000000}},
        {"id": 99, "select": {"is_done": 11}},
    ):
        normalized = client.normalize_hot_post(row)

        assert normalized["is_pushed"] is True


def test_normalize_hot_post_does_not_treat_numeric_generic_status_as_pushed():
    normalized = client.normalize_hot_post({"id": 99, "status": 1})

    assert normalized["is_pushed"] is False


def test_normalize_hot_post_accepts_millisecond_timestamps():
    normalized = client.normalize_hot_post(
        {
            "id": 100,
            "creation_time": 1778803200000,
            "last_synced_at": 1778806800000,
        }
    )

    assert normalized["creation_time"] == "2026-05-15 00:00:00"
    assert normalized["last_synced_at"] == "2026-05-15 01:00:00"
