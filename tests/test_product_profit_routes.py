"""产品盈亏 web 路由测试（Tab ① 列表 + xlsx 导出）。

不依赖真实数据库——直接 monkeypatch `web.routes.product_profit_report.ppl.generate_list`
来注入 mock 返回值。
"""
from __future__ import annotations

from datetime import date


# ---------------------------------------------------------------------------
# /list.json
# ---------------------------------------------------------------------------
def test_list_json_200_default_dates(authed_client_no_db, monkeypatch):
    """无日期参数 → 默认本月，200 返回 rows + summary。"""
    captured: dict = {}

    def fake_generate_list(*, date_from, date_to, country):
        captured["date_from"] = date_from
        captured["date_to"] = date_to
        captured["country"] = country
        return {
            "rows": [],
            "summary": {
                "product_count": 0,
                "total_orders": 0,
                "total_revenue_usd": 0.0,
                "total_profit_usd": 0.0,
                "overall_roas": None,
            },
        }

    monkeypatch.setattr(
        "web.routes.product_profit_report.ppl.generate_list",
        fake_generate_list,
    )

    resp = authed_client_no_db.get("/order-analytics/product-profit/list.json")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "rows" in data
    assert "summary" in data
    # 默认 date_from = 本月 1 号，date_to = 今天
    today = date.today()
    assert captured["date_from"] == today.replace(day=1)
    assert captured["date_to"] == today
    assert captured["country"] is None


def test_list_json_invalid_date_range_400(authed_client_no_db):
    """date_from > date_to → 400。"""
    resp = authed_client_no_db.get(
        "/order-analytics/product-profit/list.json"
        "?date_from=2026-06-01&date_to=2026-05-01"
    )
    assert resp.status_code == 400


def test_list_json_passes_country_param(authed_client_no_db, monkeypatch):
    """country 参数透传给 generate_list；空串 / 'all' 转 None 由 generate_list 自己判定。"""
    seen: dict = {}

    def fake_generate_list(*, date_from, date_to, country):
        seen["country"] = country
        return {"rows": [], "summary": {"product_count": 0, "total_orders": 0,
                                         "total_revenue_usd": 0.0, "total_profit_usd": 0.0,
                                         "overall_roas": None}}

    monkeypatch.setattr(
        "web.routes.product_profit_report.ppl.generate_list",
        fake_generate_list,
    )

    resp = authed_client_no_db.get(
        "/order-analytics/product-profit/list.json?country=VN"
    )
    assert resp.status_code == 200
    assert seen["country"] == "VN"

    # 空串 → None
    resp = authed_client_no_db.get(
        "/order-analytics/product-profit/list.json?country="
    )
    assert resp.status_code == 200
    assert seen["country"] is None


# ---------------------------------------------------------------------------
# /list.xlsx
# ---------------------------------------------------------------------------
def test_list_xlsx_200_returns_xlsx(authed_client_no_db, monkeypatch):
    """正常请求 → 返回 xlsx 字节流（PK magic header）。"""
    fake_report = {
        "rows": [
            {
                "product_id": 1, "product_code": "ABC", "name": "Test",
                "order_count": 2, "revenue_usd": 100.0,
                "shipping_cost_usd": 6.0, "shipping_pct": 0.06,
                "purchase_usd": 20.0, "purchase_pct": 0.2,
                "ad_cost_usd": 16.0, "ad_pct": 0.16,
                "roas": 6.25, "profit_usd": 53.0, "profit_pct": 0.53,
                "cost_completeness": "ok",
            },
        ],
        "summary": {
            "product_count": 1, "total_orders": 2,
            "total_revenue_usd": 100.0, "total_profit_usd": 53.0,
            "overall_roas": 6.25,
        },
    }

    monkeypatch.setattr(
        "web.routes.product_profit_report.ppl.generate_list",
        lambda **kwargs: fake_report,
    )

    resp = authed_client_no_db.get(
        "/order-analytics/product-profit/list.xlsx"
        "?date_from=2026-05-01&date_to=2026-05-07"
    )
    assert resp.status_code == 200
    assert resp.mimetype == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    body = resp.get_data()
    assert body[:4] == b"PK\x03\x04"
    assert len(body) > 500
    cd = resp.headers.get("Content-Disposition", "")
    assert "product_profit_list_2026-05-01_2026-05-07.xlsx" in cd


def test_list_xlsx_invalid_date_range_400(authed_client_no_db):
    """xlsx 端点也校验日期顺序。"""
    resp = authed_client_no_db.get(
        "/order-analytics/product-profit/list.xlsx"
        "?date_from=2026-06-01&date_to=2026-05-01"
    )
    assert resp.status_code == 400


def test_list_xlsx_filename_includes_country_when_specified(
    authed_client_no_db, monkeypatch,
):
    """选具体国家（country=vn）→ filename 含大写国家代码 VN_；不传 / 'all' → 不含。"""
    fake_report = {
        "rows": [],
        "summary": {
            "product_count": 0, "total_orders": 0,
            "total_revenue_usd": 0.0, "total_profit_usd": 0.0,
            "overall_roas": None,
        },
    }
    monkeypatch.setattr(
        "web.routes.product_profit_report.ppl.generate_list",
        lambda **kwargs: fake_report,
    )

    # 选具体国家（小写传入也归一为大写）
    resp = authed_client_no_db.get(
        "/order-analytics/product-profit/list.xlsx"
        "?date_from=2026-05-01&date_to=2026-05-07&country=vn"
    )
    assert resp.status_code == 200
    cd = resp.headers.get("Content-Disposition", "")
    assert "product_profit_list_VN_2026-05-01_2026-05-07.xlsx" in cd

    # 不传 country → 不含国家段
    resp = authed_client_no_db.get(
        "/order-analytics/product-profit/list.xlsx"
        "?date_from=2026-05-01&date_to=2026-05-07"
    )
    assert resp.status_code == 200
    cd = resp.headers.get("Content-Disposition", "")
    assert "product_profit_list_2026-05-01_2026-05-07.xlsx" in cd
    assert "_VN_" not in cd
    assert "_ALL_" not in cd

    # country=all → 视作全部，不含国家段
    resp = authed_client_no_db.get(
        "/order-analytics/product-profit/list.xlsx"
        "?date_from=2026-05-01&date_to=2026-05-07&country=all"
    )
    assert resp.status_code == 200
    cd = resp.headers.get("Content-Disposition", "")
    assert "product_profit_list_2026-05-01_2026-05-07.xlsx" in cd
    assert "_ALL_" not in cd


# ---------------------------------------------------------------------------
# /countries.json — Tab 3 国家胶囊
# ---------------------------------------------------------------------------
def test_countries_json_returns_gb_plus_enabled_language_countries(
    authed_client_no_db, monkeypatch,
):
    """国家看板胶囊固定含英国，后续按启用小语种主国家补足，最多 9 个。"""
    monkeypatch.setattr(
        "web.routes.product_profit_report.medias.list_enabled_languages_kv",
        lambda: [
            ("en", "英语"), ("de", "德语"), ("fr", "法语"), ("es", "西班牙语"),
            ("it", "意大利语"), ("ja", "日语"), ("pt", "葡萄牙语"),
            ("nl", "荷兰语"), ("sv", "瑞典语"), ("fi", "芬兰语"),
        ],
    )

    resp = authed_client_no_db.get("/order-analytics/product-profit/countries.json")
    assert resp.status_code == 200
    countries = resp.get_json()["countries"]
    assert len(countries) == 9
    assert countries[0] == {"country": "GB", "lang": "en", "label": "英国"}
    assert {"country": "DE", "lang": "de", "label": "德国"} in countries
    assert {"country": "FR", "lang": "fr", "label": "法国"} in countries


# ---------------------------------------------------------------------------
# /ads.json — Tab 4 广告明细
# ---------------------------------------------------------------------------
def _fake_ads_report():
    return {
        "accounts": [
            {
                "ad_account_id": "act_111",
                "label": "Newjoyloo",
                "spend_usd": 50.0,
                "result_count": 10,
                "impressions": 1000,
                "clicks": 100,
                "attributed_revenue_usd": 200.0,
                "roas": 4.0,
            }
        ],
        "campaigns": [
            {
                "normalized_campaign_code": "CMP_A",
                "campaign_name": "Campaign A",
                "ad_account_id": "act_111",
                "ad_account_name": "Newjoyloo",
                "spend_usd": 50.0,
                "result_count": 10,
                "impressions": 1000,
                "clicks": 100,
                "ctr": 0.1,
                "cpc": 0.5,
                "purchase_value_usd": 180.0,
                "roas_meta": 3.6,
                "attributed_order_count": 5,
                "attributed_revenue_usd": 200.0,
                "roas": 4.0,
                "profit_contribution_usd": 80.0,
            }
        ],
        "daily": [
            {"date": "2026-05-01", "spend_usd": 50.0, "revenue_usd": 200.0}
        ],
        "unmatched": [],
    }


def test_ads_json_missing_product_id_400(authed_client_no_db):
    """缺 product_id → 400。"""
    resp = authed_client_no_db.get("/order-analytics/product-profit/ads.json")
    assert resp.status_code == 400


def test_ads_json_invalid_product_id_400(authed_client_no_db):
    """非整数 product_id → 400。"""
    resp = authed_client_no_db.get(
        "/order-analytics/product-profit/ads.json?product_id=abc"
    )
    assert resp.status_code == 400


def test_ads_json_invalid_date_range_400(authed_client_no_db):
    """date_from > date_to → 400。"""
    resp = authed_client_no_db.get(
        "/order-analytics/product-profit/ads.json"
        "?product_id=1&date_from=2026-06-01&date_to=2026-05-01"
    )
    assert resp.status_code == 400


def test_ads_json_200_returns_payload(authed_client_no_db, monkeypatch):
    """带 product_id → 200，透传 generate_ads_report 返回值。"""
    captured: dict = {}

    def fake_generate_ads_report(*, product_id, date_from, date_to, country=None):
        captured["product_id"] = product_id
        captured["date_from"] = date_from
        captured["date_to"] = date_to
        captured["country"] = country
        return _fake_ads_report()

    monkeypatch.setattr(
        "web.routes.product_profit_report.ppa.generate_ads_report",
        fake_generate_ads_report,
    )

    resp = authed_client_no_db.get(
        "/order-analytics/product-profit/ads.json"
        "?product_id=42&date_from=2026-05-01&date_to=2026-05-07"
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "accounts" in data
    assert "campaigns" in data
    assert "daily" in data
    assert "unmatched" in data
    assert captured["product_id"] == 42
    assert captured["date_from"] == date(2026, 5, 1)
    assert captured["date_to"] == date(2026, 5, 7)
    assert captured["country"] is None


def test_ads_json_passes_country(authed_client_no_db, monkeypatch):
    """country 参数透传给 generate_ads_report；空串 → None。"""
    seen: dict = {}

    def fake_generate_ads_report(*, product_id, date_from, date_to, country=None):
        seen["country"] = country
        return _fake_ads_report()

    monkeypatch.setattr(
        "web.routes.product_profit_report.ppa.generate_ads_report",
        fake_generate_ads_report,
    )

    resp = authed_client_no_db.get(
        "/order-analytics/product-profit/ads.json?product_id=1&country=VN"
    )
    assert resp.status_code == 200
    assert seen["country"] == "VN"

    resp = authed_client_no_db.get(
        "/order-analytics/product-profit/ads.json?product_id=1&country="
    )
    assert resp.status_code == 200
    assert seen["country"] is None


# ---------------------------------------------------------------------------
# /ads/manual-match — 手动匹配 campaign → product
# ---------------------------------------------------------------------------
def test_manual_match_missing_fields_400(authed_client_no_db):
    """缺 campaign_code / product_id → 400。"""
    # 完全没 body
    resp = authed_client_no_db.post(
        "/order-analytics/product-profit/ads/manual-match",
        json={},
    )
    assert resp.status_code == 400

    # 缺 product_id
    resp = authed_client_no_db.post(
        "/order-analytics/product-profit/ads/manual-match",
        json={"campaign_code": "CMP_X"},
    )
    assert resp.status_code == 400

    # 缺 campaign_code
    resp = authed_client_no_db.post(
        "/order-analytics/product-profit/ads/manual-match",
        json={"product_id": 5},
    )
    assert resp.status_code == 400


def test_manual_match_invalid_product_id_400(authed_client_no_db):
    """product_id 非正整数 → 400。"""
    resp = authed_client_no_db.post(
        "/order-analytics/product-profit/ads/manual-match",
        json={"campaign_code": "CMP_X", "product_id": "not-a-number"},
    )
    assert resp.status_code == 400

    resp = authed_client_no_db.post(
        "/order-analytics/product-profit/ads/manual-match",
        json={"campaign_code": "CMP_X", "product_id": 0},
    )
    assert resp.status_code == 400


def test_manual_match_200_calls_underlying(authed_client_no_db, monkeypatch):
    """合法 body → 200, ok=True，参数被透传。"""
    captured: dict = {}

    def fake_manual_match(normalized_campaign_code, product_id, **kwargs):
        captured["normalized_campaign_code"] = normalized_campaign_code
        captured["product_id"] = product_id
        captured["kwargs"] = kwargs
        return {
            "matched_periodic": 3,
            "matched_daily": 7,
            "product_id": product_id,
            "product_code": "ABC",
            "product_name": "Test Product",
        }

    monkeypatch.setattr(
        "web.routes.product_profit_report.manual_match_meta_ad_campaign",
        fake_manual_match,
    )

    resp = authed_client_no_db.post(
        "/order-analytics/product-profit/ads/manual-match",
        json={"campaign_code": "CMP_X", "product_id": 42},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["matched_periodic"] == 3
    assert data["matched_daily"] == 7
    assert data["product_id"] == 42
    assert data["product_code"] == "ABC"
    assert captured["normalized_campaign_code"] == "CMP_X"
    assert captured["product_id"] == 42


def test_manual_match_delete_calls_remove_override(authed_client_no_db, monkeypatch):
    """产品盈亏广告明细解绑 → 删除人工 override。"""
    captured: dict = {}

    def fake_remove_override(*, override_id):
        captured["override_id"] = override_id
        return {"removed": 1, "normalized_campaign_code": "CMP_X"}

    monkeypatch.setattr(
        "web.routes.product_profit_report.remove_override",
        fake_remove_override,
    )

    resp = authed_client_no_db.delete(
        "/order-analytics/product-profit/ads/manual-match/9"
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["removed"] == 1
    assert data["normalized_campaign_code"] == "CMP_X"
    assert captured["override_id"] == 9


def test_manual_match_underlying_raises_500(authed_client_no_db, monkeypatch):
    """underlying 抛异常 → 500，错误信息回传。"""
    def fake_manual_match(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "web.routes.product_profit_report.manual_match_meta_ad_campaign",
        fake_manual_match,
    )

    resp = authed_client_no_db.post(
        "/order-analytics/product-profit/ads/manual-match",
        json={"campaign_code": "CMP_X", "product_id": 42},
    )
    assert resp.status_code == 500
    data = resp.get_json()
    assert "error" in data
