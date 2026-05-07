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
