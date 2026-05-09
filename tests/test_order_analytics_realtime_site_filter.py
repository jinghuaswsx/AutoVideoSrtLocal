"""实时大盘店铺筛选回归。

锚点：docs/superpowers/specs/2026-05-09-realtime-dashboard-store-filter.md

覆盖：
1. 默认（不传 site_code）行为与现状一致：scope.stores 为双店、SQL 中保留
   ``site_code IN ('newjoy', 'omurio')`` 字面量。
2. 单店筛选：scope.stores 收窄、SQL 改为 ``site_code IN ('newjoy')``、不再查询
   ``roi_realtime_daily_snapshots`` 与 ``roi_daily_roas_nodes``。
3. 路由层 site_code 白名单校验。
"""
from __future__ import annotations

from datetime import date, datetime

from appcore import order_analytics as oa
from appcore.order_analytics import realtime as realtime_oa


# ── 单元测试：默认 / 单店 SQL 渲染 ─────────────────────────────────────


def _stub_meta_ad_accounts(monkeypatch, site_to_account: dict[str, tuple[str, ...]]):
    from appcore import meta_ad_accounts

    monkeypatch.setattr(
        meta_ad_accounts,
        "site_account_map",
        lambda *, enabled_only=False: site_to_account,
    )


def test_default_site_codes_render_legacy_sql(monkeypatch):
    calls: list[tuple[str, tuple]] = []

    def fake_query(sql, args=()):
        calls.append((sql, args))
        # 模拟无快照 / 无明细 / 无广告
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        "2026-05-09",
        now=datetime(2026, 5, 9, 14, 0),
    )

    # 默认仍走双店字面量，向后兼容现有快照测试期望
    assert any("site_code IN ('newjoy', 'omurio')" in sql for sql, _ in calls)
    assert result["scope"]["stores"] == ["newjoy", "omurio"]


def test_single_site_filter_narrows_sql_and_scope(monkeypatch):
    calls: list[tuple[str, tuple]] = []

    def fake_query(sql, args=()):
        calls.append((sql, args))
        return []

    monkeypatch.setattr(oa, "query", fake_query)
    _stub_meta_ad_accounts(monkeypatch, {"newjoy": ("1861285821213497",)})

    result = oa.get_realtime_roas_overview(
        "2026-05-09",
        now=datetime(2026, 5, 9, 14, 0),
        site_codes=["newjoy"],
    )

    # SQL 收窄为单店
    assert any("site_code IN ('newjoy')" in sql for sql, _ in calls)
    assert not any("site_code IN ('newjoy', 'omurio')" in sql for sql, _ in calls)

    # 不再触碰双店预聚合表
    assert not any("FROM roi_realtime_daily_snapshots" in sql for sql, _ in calls)
    assert not any("FROM roi_daily_roas_nodes" in sql for sql, _ in calls)

    # scope 反映真实筛选
    assert result["scope"]["stores"] == ["newjoy"]
    # 单店时 roas_points 走 24 个空槽（保持响应 schema 不变）
    assert len(result["roas_points"]) == 24
    assert all(point["true_roas"] is None for point in result["roas_points"])
    assert all(point["order_count"] == 0 for point in result["roas_points"])


def test_single_site_filter_limits_ads_account_id(monkeypatch):
    calls: list[tuple[str, tuple]] = []

    def fake_query(sql, args=()):
        calls.append((sql, args))
        return []

    monkeypatch.setattr(oa, "query", fake_query)
    _stub_meta_ad_accounts(monkeypatch, {"omurio": ("99999",)})

    oa.get_realtime_roas_overview(
        "2026-05-09",
        now=datetime(2026, 5, 9, 14, 0),
        site_codes=["omurio"],
    )

    ads_calls = [
        (sql, args)
        for sql, args in calls
        if "FROM meta_ad_daily_campaign_metrics" in sql
    ]
    assert ads_calls, "expected ads query to fire on live-calc path"
    assert any("ad_account_id IN" in sql for sql, _ in ads_calls)
    assert any("99999" in tuple(args) for _, args in ads_calls)


def test_single_site_open_day_uses_realtime_ads_when_daily_final_missing(monkeypatch):
    """单店筛选的 open day 必须读实时表，而不是日终表空值。"""
    calls: list[tuple[str, tuple]] = []
    target = date(2026, 5, 9)
    latest_at = datetime(2026, 5, 10, 0, 40)

    def fake_query(sql, args=()):
        calls.append((sql, args))
        if (
            "FROM meta_ad_realtime_daily_campaign_metrics" in sql
            and "MAX(snapshot_at) AS latest_at" in sql
        ):
            return [{"ad_account_id": "1253003326160754", "latest_at": latest_at}]
        if (
            "FROM meta_ad_realtime_daily_campaign_metrics" in sql
            and "campaign_id, campaign_name" in sql
        ):
            return [{
                "ad_account_id": "1253003326160754",
                "ad_account_name": "Omurio",
                "campaign_id": "cmp_1",
                "campaign_name": "Omurio spend",
                "normalized_campaign_code": "omurio-spend",
                "result_count": 0,
                "spend_usd": 64.77,
                "purchase_value_usd": 0,
                "impressions": 1000,
                "clicks": 12,
            }]
        return []

    monkeypatch.setattr(oa, "query", fake_query)
    _stub_meta_ad_accounts(monkeypatch, {"omurio": ("1253003326160754",)})
    monkeypatch.setattr(realtime_oa, "_get_realtime_order_details", lambda *a, **kw: [])
    monkeypatch.setattr(realtime_oa, "_get_realtime_order_profit_details", lambda *a, **kw: [])
    monkeypatch.setattr(realtime_oa, "_get_realtime_product_sales_stats", lambda *a, **kw: [])
    monkeypatch.setattr(realtime_oa, "_get_daily_campaigns", lambda *a, **kw: [])

    result = oa.get_realtime_roas_overview(
        target.isoformat(),
        now=datetime(2026, 5, 10, 0, 56),
        site_codes=["omurio"],
    )

    assert result["scope"]["stores"] == ["omurio"]
    assert result["scope"]["ad_source"] == "meta_ad_realtime_daily_campaign_metrics"
    assert result["summary"]["ad_spend"] == 64.77
    assert any(
        "FROM meta_ad_realtime_daily_campaign_metrics" in sql
        for sql, _ in calls
    )


def test_invalid_site_code_falls_back_to_default(monkeypatch):
    """非法 site_code 在 normalize 阶段被丢弃，回退到默认双店。"""
    calls: list[tuple[str, tuple]] = []

    def fake_query(sql, args=()):
        calls.append((sql, args))
        return []

    monkeypatch.setattr(oa, "query", fake_query)

    result = oa.get_realtime_roas_overview(
        "2026-05-09",
        now=datetime(2026, 5, 9, 14, 0),
        site_codes=["__not_a_real_store__"],
    )

    assert result["scope"]["stores"] == ["newjoy", "omurio"]
    assert any("site_code IN ('newjoy', 'omurio')" in sql for sql, _ in calls)


# ── 路由层：site_code 白名单 ────────────────────────────────────────


def test_route_rejects_unknown_site_code(authed_client_no_db, monkeypatch):
    response = authed_client_no_db.get(
        "/order-analytics/realtime-overview?site_code=smartgearx"
    )
    assert response.status_code == 400
    body = response.get_json()
    assert body.get("error") == "invalid_param"


def test_route_passes_site_code_to_overview(authed_client_no_db, monkeypatch):
    captured: dict = {}

    def fake_overview(date_text, **kwargs):
        captured.update(kwargs)
        captured["date_text"] = date_text
        return {"summary": {}, "scope": {"stores": kwargs.get("site_codes")}}

    monkeypatch.setattr(
        "web.routes.order_analytics.oa.get_realtime_roas_overview",
        fake_overview,
    )
    monkeypatch.setattr(
        "web.routes.order_analytics._attach_realtime_data_quality",
        lambda result: result,
    )

    response = authed_client_no_db.get(
        "/order-analytics/realtime-overview?site_code=newjoy"
    )

    assert response.status_code == 200
    assert captured.get("site_codes") == ["newjoy"]


def test_route_default_omits_site_codes(authed_client_no_db, monkeypatch):
    captured: dict = {}

    def fake_overview(date_text, **kwargs):
        captured.update(kwargs)
        captured["date_text"] = date_text
        return {"summary": {}, "scope": {"stores": ["newjoy", "omurio"]}}

    monkeypatch.setattr(
        "web.routes.order_analytics.oa.get_realtime_roas_overview",
        fake_overview,
    )
    monkeypatch.setattr(
        "web.routes.order_analytics._attach_realtime_data_quality",
        lambda result: result,
    )

    response = authed_client_no_db.get("/order-analytics/realtime-overview")

    assert response.status_code == 200
    assert "site_codes" not in captured
