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
