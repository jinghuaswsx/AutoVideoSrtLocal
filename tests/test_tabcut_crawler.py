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


def test_recent7_plan_collects_video_rankday7_and_seven_goods_days():
    dates = ["20260511", "20260510", "20260509", "20260508", "20260507", "20260506", "20260505"]

    plan = runner.build_recent7_plan(dates)

    assert plan[0].source == "video_7d_play"
    assert "rankDay=7" in plan[0].url_for_page(1)
    goods_sources = [item.source for item in plan if item.source.startswith("goods_daily_")]
    assert goods_sources == [f"goods_daily_{date}" for date in dates]
    assert all(item.pages == 5 for item in plan)
