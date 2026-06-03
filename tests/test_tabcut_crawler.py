from urllib.parse import unquote

import pytest

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


def test_tabcut_login_credentials_resolve_from_environment_aliases():
    credentials = client.resolve_tabcut_login_credentials(
        {
            "TABCUT_USERNAME": "legacy@example.com",
            "TABCUT_PASSWORD": "legacy-secret",
            "TABCUT_LOGIN_ACCOUNT": "tabcut@example.com",
            "TABCUT_LOGIN_PASSWORD": "secret",
        }
    )

    assert credentials == client.TabcutLoginCredentials(
        username="tabcut@example.com",
        password="secret",
    )


def test_classify_tabcut_login_state_detects_guest_and_human_required():
    assert (
        client.classify_tabcut_login_state(
            "https://www.tabcut.com/zh-CN/workbench",
            "游客模式，仅展示部分内容 登录 / 注册",
        )
        == "login_required"
    )
    assert (
        client.classify_tabcut_login_state(
            "https://www.tabcut.com/zh-CN/workbench",
            "安全验证 请输入验证码",
        )
        == "needs_human"
    )
    assert client.classify_tabcut_login_state("https://www.tabcut.com/zh-CN/workbench", "TABCUT 工作台") == "logged_in"


def test_ensure_tabcut_login_on_page_requires_credentials_for_guest():
    class FakeLocator:
        def inner_text(self, timeout=None):
            return "游客模式，仅展示部分内容 登录 / 注册"

    class FakePage:
        url = "https://www.tabcut.com/zh-CN/workbench"

        def locator(self, selector):
            assert selector == "body"
            return FakeLocator()

    with pytest.raises(RuntimeError, match="TABCUT_LOGIN_ACCOUNT"):
        client.ensure_tabcut_login_on_page(FakePage(), credentials=None)


def test_cdp_fetcher_checks_tabcut_login_once_before_fetch():
    login_calls = []

    class FakePage:
        url = "https://www.tabcut.com/zh-CN/workbench"

        def evaluate(self, script, args):
            return {"result": {"data": [{"url": args["url"]}]}}

    fetcher = client._CdpFetcher(
        "http://127.0.0.1:9227",
        login_fn=lambda page: login_calls.append(page),
    )
    fetcher._page = FakePage()

    fetcher("GET", "https://www.tabcut.com/api/a")
    fetcher("GET", "https://www.tabcut.com/api/b")

    assert login_calls == [fetcher._page]


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


def test_goods_ranking_url_maps_hot_new_and_periods():
    hot_month = unquote(
        client.goods_ranking_url(
            biz_date="20260531",
            rank_kind="hot",
            rank_period="30d",
            page_no=2,
            page_size=100,
        )
    ).replace("+", " ")
    new_week = unquote(
        client.goods_ranking_url(
            biz_date="20260531",
            rank_kind="new",
            rank_period="7d",
            page_no=1,
            page_size=100,
        )
    ).replace("+", " ")

    assert '"rankType": 3' in hot_month
    assert '"orderType": "1"' in hot_month
    assert '"rankType": 2' in new_week
    assert '"orderType": "2"' in new_week


def test_build_goods_ranking_plan_collects_hot_and_new_daily_weekly_monthly():
    plan = runner.build_goods_ranking_plan("20260531", pages=3, page_size=100)

    assert [source.source for source in plan] == [
        "goods_hot_1d",
        "goods_hot_7d",
        "goods_hot_30d",
        "goods_new_1d",
        "goods_new_7d",
        "goods_new_30d",
    ]
    assert all(source.kind == "goods" for source in plan)
    assert all(source.biz_date == "20260531" for source in plan)
    assert all(source.pages == 3 for source in plan)
    assert all(source.page_size == 100 for source in plan)
    decoded_month_url = unquote(plan[2].url_for_page(1)).replace("+", " ")
    assert '"rankType": 3' in decoded_month_url
    assert '"orderType": "1"' in decoded_month_url


def test_collect_goods_rankings_normalizes_and_persists(monkeypatch, tmp_path):
    persisted_goods = []
    persisted_snapshots = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def fetch_items(self, url):
            decoded = unquote(url)
            source_marker = "new" if '"orderType":"2"' in decoded.replace(" ", "") else "hot"
            period_marker = "30d" if '"rankType":3' in decoded.replace(" ", "") else ("7d" if '"rankType":2' in decoded.replace(" ", "") else "1d")
            return (
                [
                    {
                        "itemId": f"{source_marker}-{period_marker}",
                        "itemName": f"{source_marker} {period_marker}",
                        "rank": 1,
                        "soldCount30d": 6000,
                        "priceList": [{"local": 12.34}],
                    }
                ],
                1,
            )

    monkeypatch.setattr(runner, "TabcutApiClient", FakeClient)
    monkeypatch.setattr(runner.store, "upsert_goods", lambda row: persisted_goods.append(row))
    monkeypatch.setattr(runner.store, "upsert_goods_snapshot", lambda row: persisted_snapshots.append(row))

    summary = runner.collect_goods_rankings(
        cdp_url="http://127.0.0.1:9227",
        output_dir=tmp_path,
        biz_date="2026-05-31",
        pages=1,
        persist=True,
        min_interval_seconds=3.0,
    )

    assert summary["ok"] is True
    assert summary["goods_count"] == 6
    assert {row["source"] for row in persisted_snapshots} == {
        "goods_hot_1d",
        "goods_hot_7d",
        "goods_hot_30d",
        "goods_new_1d",
        "goods_new_7d",
        "goods_new_30d",
    }
    assert len(persisted_goods) == 6
    assert all(row["biz_date"] == "2026-05-31" for row in persisted_snapshots)


def test_analysis_video_search_payload_uses_page_size_100_and_filters():
    payload = client.analysis_video_search_payload(
        page_no=2,
        page_size=100,
        video_create_time_begin="2026-04-12 00:00:00",
        video_create_time_end="2026-05-11 23:59:59",
    )

    assert payload == {
        "pageNo": 2,
        "pageSize": "100",
        "region": "US",
        "sortField": "video_sold_count",
        "videoCreateTimeBegin": "2026-04-12 00:00:00",
        "videoCreateTimeEnd": "2026-05-11 23:59:59",
        "itemVideoFlag": "1",
        "categoryQuery": {"lv1List": [], "lv2List": [], "lv3List": []},
    }


def test_analysis_video_search_source_name_fits_snapshot_column():
    assert runner._analysis_video_search_source("video_sold_count") == "analysis_video_sold_count"
    assert len(runner._analysis_video_search_source("video_sold_count")) <= 32


def test_normalize_goods_row_supports_analysis_video_search_item_fields():
    row = {
        "itemId": "i1",
        "itemName": "Demo product",
        "itemCoverUrl": "https://cdn.example/item.webp",
        "itemSoldCount7d": 123,
        "itemSoldCount30d": 456,
        "itemSoldCountTotal": 789,
        "priceAmount": {"local": 19.99, "region": 19.99},
        "itemTkLv1Name": "Beauty",
        "itemTkLv2Name": "Skin Care",
        "itemTkLv3Name": "Serum",
    }

    normalized = runner.normalize_goods_row(row, source="analysis_video_search")

    assert normalized["item_id"] == "i1"
    assert normalized["item_pic_url"] == "https://cdn.example/item.webp"
    assert normalized["sold_count_7d"] == 123
    assert normalized["sold_count_30d"] == 456
    assert normalized["sold_count_total"] == 789
    assert normalized["price_min"] == 19.99
    assert normalized["price_max"] == 19.99
    assert normalized["category_l1_name"] == "Beauty"
    assert normalized["category_l2_name"] == "Skin Care"
    assert normalized["category_l3_name"] == "Serum"


def test_analysis_video_search_normalizes_videos_goods_and_candidates():
    items = [
        {
            "videoId": "v1",
            "videoCoverUrl": "https://cdn.example/v1.webp",
            "tkVideoUrl": "https://www.tiktok.com/@demo/video/v1",
            "playCountTotal": 100000,
            "likeCountTotal": 1000,
            "shareCountTotal": 500,
            "commentCountTotal": 100,
            "videoSplitSoldCount": 333,
            "videoSplitGmv": {"local": 9999},
            "itemId": "i1",
            "itemName": "Demo product",
            "itemCoverUrl": "https://cdn.example/i1.webp",
            "itemSoldCountTotal": 8888,
            "priceAmount": {"local": 12.34},
        }
    ]

    normalized = runner._normalize_analysis_video_search_items(
        items,
        biz_date="2026-05-11",
        source="analysis_video_search_video_sold_count",
    )

    assert normalized["videos"][0]["video_id"] == "v1"
    assert normalized["goods"][0]["item_id"] == "i1"
    assert normalized["candidates"][0]["video_id"] == "v1"
    assert normalized["candidates"][0]["primary_item_id"] == "i1"
    assert normalized["candidates"][0]["primary_item_price_min"] == 12.34
    assert normalized["candidates"][0]["primary_item_price_max"] == 12.34
    assert normalized["candidates"][0]["goods_sold_count_total"] == 8888
