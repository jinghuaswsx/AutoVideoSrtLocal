from appcore import ad_long_term_loss as ltl


def test_get_ltl_config_defaults(monkeypatch):
    monkeypatch.setattr(ltl.system_settings, "get_setting", lambda key: None)
    cfg = ltl.get_ltl_config()
    assert cfg["long_days"] == 30
    assert cfg["recent_days"] == 7
    assert cfg["loss_ratio"] == 0.10
    assert cfg["min_active_days"] == 10
    assert cfg["min_spend_7d"] == 50.0
    assert cfg["min_loss_7d"] == 20.0
    assert cfg["est_cost_rate"] == 0.08
    assert cfg["est_shipping_rate"] == 0.17


def test_get_ltl_config_reads_override(monkeypatch):
    overrides = {"ad_alert_ltl_loss_ratio": "0.2", "ad_alert_ltl_min_active_days": "14"}
    monkeypatch.setattr(ltl.system_settings, "get_setting", lambda key: overrides.get(key))
    cfg = ltl.get_ltl_config()
    assert cfg["loss_ratio"] == 0.2
    assert cfg["min_active_days"] == 14
    assert cfg["long_days"] == 30  # 未覆盖项回落默认


def test_judge_recent_not_loss_is_skipped():
    v = ltl.judge_long_term_loss(profit_7d=5.0, profit_30d=-100.0, loss_ratio=0.10)
    assert v.alert is False
    assert v.verdict is None


def test_judge_long_term_net_loss_alerts():
    v = ltl.judge_long_term_loss(profit_7d=-30.0, profit_30d=-10.0, loss_ratio=0.10)
    assert v.alert is True
    assert v.verdict == "long_term_net_loss"
    assert v.loss_7d == 30.0
    assert v.loss_ratio is None


def test_judge_erodes_profit_over_threshold():
    # 近7天亏100，近30天赚500 → 20% > 10% → 报
    v = ltl.judge_long_term_loss(profit_7d=-100.0, profit_30d=500.0, loss_ratio=0.10)
    assert v.alert is True
    assert v.verdict == "erodes_profit"
    assert v.loss_7d == 100.0
    assert round(v.loss_ratio, 4) == 0.2


def test_judge_small_loss_is_volatility():
    # 近7天亏10，近30天赚500 → 2% <= 10% → 放行
    v = ltl.judge_long_term_loss(profit_7d=-10.0, profit_30d=500.0, loss_ratio=0.10)
    assert v.alert is False
    assert v.verdict is None
    assert round(v.loss_ratio, 4) == 0.02


def test_judge_ratio_boundary_equal_is_volatility():
    # 恰好等于阈值 → 不报（用 > 严格大于）
    v = ltl.judge_long_term_loss(profit_7d=-50.0, profit_30d=500.0, loss_ratio=0.10)
    assert v.alert is False


from datetime import date
from decimal import Decimal


def test_load_window_metrics_uses_real_and_estimated_cost(monkeypatch):
    business_date = date(2026, 6, 14)
    cfg = ltl.get_ltl_config()

    order_rows = [
        {
            "product_id": 100, "product_code": "P100", "product_name": "完备品",
            "product_main_image": None,
            "revenue_7d": 1000.0, "fee_7d": 30.0, "purchase_7d": 200.0,
            "shipping_7d": 150.0, "rr_7d": 10.0,
            "revenue_30d": 5000.0, "fee_30d": 150.0, "purchase_30d": 1000.0,
            "shipping_30d": 750.0, "rr_30d": 50.0,
            "has_estimated": 0,
            "first_active_date": date(2026, 5, 1), "last_active_date": business_date,
        },
        {
            "product_id": 200, "product_code": "P200", "product_name": "缺成本品",
            "product_main_image": None,
            "revenue_7d": 1000.0, "fee_7d": 30.0, "purchase_7d": 80.0,
            "shipping_7d": 170.0, "rr_7d": 10.0,
            "revenue_30d": 4000.0, "fee_30d": 120.0, "purchase_30d": 320.0,
            "shipping_30d": 680.0, "rr_30d": 40.0,
            "has_estimated": 1,
            "first_active_date": date(2026, 5, 10), "last_active_date": business_date,
        },
    ]
    active_rows = [
        {"product_id": 100, "active_days": 28},
        {"product_id": 200, "active_days": 20},
    ]

    def fake_query(sql, params=None):
        if "FROM order_profit_lines" in sql:
            return order_rows
        if "active_days" in sql:
            return active_rows
        return []

    monkeypatch.setattr(ltl, "query", fake_query)
    monkeypatch.setattr(ltl, "ensure_open_day_profit_lines_fresh", lambda a, b: None)
    monkeypatch.setattr(
        ltl, "_load_ad_spend",
        lambda d_from, d_to, country=None: (
            {100: Decimal("700"), 200: Decimal("900")}
            if (d_to - d_from).days <= cfg["recent_days"]
            else {100: Decimal("3500"), 200: Decimal("3600")}
        ),
    )

    metrics = ltl._load_window_metrics(business_date, cfg)
    m100 = metrics[100]
    # profit_7d = 1000 - 30 - 200 - 150 - 10 - 700 = -90
    assert round(m100.profit_7d, 2) == -90.0
    # profit_30d = 5000 - 150 - 1000 - 750 - 50 - 3500 = -450
    assert round(m100.profit_30d, 2) == -450.0
    assert m100.spend_7d == 700.0
    assert m100.active_days == 28
    assert m100.has_estimated_cost is False
    assert metrics[200].has_estimated_cost is True


def _wm(pid, profit_7d, profit_30d, spend_7d, active_days, has_est=False):
    return ltl.WindowMetric(
        product_id=pid, product_code=f"P{pid}", product_name=f"品{pid}",
        product_main_image=None, revenue_7d=1000.0, profit_7d=profit_7d,
        revenue_30d=5000.0, profit_30d=profit_30d, spend_7d=spend_7d,
        active_days=active_days, has_estimated_cost=has_est,
        first_active_date=date(2026, 5, 1), last_active_date=date(2026, 6, 14),
    )


def test_get_products_filters_sorts_and_excludes_new(monkeypatch):
    business_date = date(2026, 6, 14)
    metrics = {
        1: _wm(1, profit_7d=-200.0, profit_30d=-50.0, spend_7d=800.0, active_days=28),
        2: _wm(2, profit_7d=-100.0, profit_30d=300.0, spend_7d=500.0, active_days=28),
        3: _wm(3, profit_7d=-10.0, profit_30d=500.0, spend_7d=600.0, active_days=28),
        4: _wm(4, profit_7d=-300.0, profit_30d=-100.0, spend_7d=900.0, active_days=3),
        5: _wm(5, profit_7d=-25.0, profit_30d=-5.0, spend_7d=30.0, active_days=28),
    }
    monkeypatch.setattr(ltl, "current_meta_business_date", lambda: business_date)
    monkeypatch.setattr(ltl, "_load_window_metrics", lambda bd, cfg: metrics)
    monkeypatch.setattr(ltl, "_attach_consecutive_loss_days", lambda items, bd, cfg: None)
    monkeypatch.setattr(ltl.system_settings, "get_setting", lambda key: None)

    from appcore import ad_alert_actions
    monkeypatch.setattr(ad_alert_actions, "get_actions", lambda scope, keys: {})

    bd, items = ltl.get_long_term_loss_products(limit=10)
    ids = [it.product_id for it in items]
    assert ids == [1, 2]  # 3波动/4新品/5小额 排除；按 spend_7d 降序 1(800)>2(500)
    assert items[0].verdict == "long_term_net_loss"
    assert items[1].verdict == "erodes_profit"
    assert items[0].loss_7d == 200.0
