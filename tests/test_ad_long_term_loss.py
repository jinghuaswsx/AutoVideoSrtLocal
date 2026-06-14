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
