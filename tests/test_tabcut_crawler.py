from urllib.parse import unquote

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
    goods_sources = [item for item in plan if item.kind == "goods"]
    assert len(goods_sources) == len(dates) * len(runner.TARGET_GOODS_CATEGORIES)
    assert {item.source for item in goods_sources} == {
        "goods_cat_11",
        "goods_cat_12",
        "goods_cat_13",
        "goods_cat_16",
        "goods_cat_20",
        "goods_cat_21",
        "goods_cat_25",
        "goods_cat_26",
        "goods_cat_27",
    }
    assert all(item.pages == 1 for item in goods_sources)
    assert all(item.page_size == 50 for item in goods_sources)
    assert '"categoryId": "11"' in unquote(goods_sources[0].url_for_page(1)).replace("+", " ")


def test_goods_ranking_url_accepts_category_id_for_top50_category_page():
    url = client.goods_ranking_url(
        biz_date="20260511",
        category_id=25,
        page_no=1,
        page_size=50,
    )
    decoded = unquote(url).replace("+", " ")

    assert '"bizDate": "20260511"' in decoded
    assert '"categoryId": "25"' in decoded
    assert '"pageNo": 1' in decoded
    assert '"pageSize": 50' in decoded
