from tools.tabcut_crawler import client, runner


def test_extract_items_handles_video_and_trpc_shapes():
    video_payload = {"result": {"total": 2, "data": [{"videoId": "v1"}]}}
    trpc_payload = {
        "result": {
            "data": {
                "code": "200",
                "result": {"total": 3, "data": [{"itemId": "i1"}]},
            }
        }
    }

    assert client.extract_items(video_payload) == [{"videoId": "v1"}]
    assert client.extract_total(video_payload, 0) == 2
    assert client.extract_items(trpc_payload) == [{"itemId": "i1"}]
    assert client.extract_total(trpc_payload, 0) == 3


def test_sanitize_payload_strips_signed_video_urls():
    payload = {
        "videoId": "v1",
        "videoUrl": "https://cdn.example/a.mp4?auth_key=secret",
        "children": [{"videoPlayUrl": "https://cdn.example/b.mp4?auth_key=secret"}],
    }

    sanitized = client.sanitize_payload(payload)

    assert "videoUrl" not in sanitized
    assert "videoPlayUrl" not in sanitized["children"][0]


def test_throttled_client_waits_between_requests():
    calls = []
    sleeps = []
    now = [1000.0]

    def fake_fetch(method, url, *, params=None, json_body=None):
        calls.append((method, url, params, json_body))
        return {"result": {"data": []}}

    api = client.TabcutApiClient(
        fetch_fn=fake_fetch,
        sleep_fn=sleeps.append,
        monotonic_fn=lambda: now[0],
        min_interval_seconds=3.0,
    )

    api.request_json("GET", "https://example.test/a")
    now[0] = 1001.0
    api.request_json("GET", "https://example.test/b")

    assert len(calls) == 2
    assert sleeps == [2.0]


def test_recent_plan_collects_daily_weekly_monthly_video_rankings_at_1000_each():
    dates = [f"202605{day:02d}" for day in range(11, 0, -1)]

    plan = runner.build_recent7_plan(dates)

    video_sources = [item for item in plan if item.kind == "video"]
    assert [item.source for item in video_sources] == [
        "video_1d_play",
        "video_1d_sales",
        "video_7d_play",
        "video_7d_sales",
        "video_30d_play",
        "video_30d_sales",
    ]
    for rank_day in (1, 7, 30):
        rank_sources = [item for item in video_sources if f"rankDay={rank_day}" in item.url_for_page(1)]
        assert len(rank_sources) == 2
        assert all(item.pages * item.page_size >= 1000 for item in rank_sources)
    goods_sources = [item.source for item in plan if item.source.startswith("goods_daily_")]
    assert goods_sources == [f"goods_daily_{date}" for date in dates]
