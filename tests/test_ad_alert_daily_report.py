from __future__ import annotations

from datetime import date


def _item(code: str, *, spend: float, roas: float | None, loss_days: int):
    from appcore import ad_alerts

    metric = ad_alerts.HighLossMetric(
        spend_usd=spend,
        purchase_value_usd=0.0,
        result_count=0,
        roas=roas,
        estimated_loss=-spend,
    )
    return ad_alerts.HighLossAdItem(
        code=code,
        name=f"Ad {code}",
        ad_account_id="1234",
        ad_account_name="newjoyloo",
        country="DE",
        product_id=10,
        product_code="glow-rjc",
        product_name="Glow Product",
        product_main_image=None,
        first_active_date="2026-06-01",
        last_active_date="2026-06-12",
        active_days=5,
        consecutive_loss_days=loss_days,
        detail_url="/order-analytics?tab=ads",
        metrics={"last_7d": metric},
    )


def test_build_report_text_lists_ads_and_share_url():
    from appcore import ad_alert_daily_report

    text = ad_alert_daily_report.build_report_text(
        date(2026, 6, 12),
        [
            _item("ad-1", spend=120.0, roas=0.33, loss_days=3),
            _item("ad-2", spend=80.0, roas=None, loss_days=1),
        ],
        "https://example.com/ad-alerts/share/high-loss?token=x",
    )

    assert "06-12" in text
    assert "高亏损广告" in text
    assert "Ad ad-1" in text
    assert "$120" in text
    assert "0.33" in text
    assert "连续亏损 3 天" in text
    assert "https://example.com/ad-alerts/share/high-loss?token=x" in text


def test_daily_report_task_registered_for_scheduled_tasks_ui():
    from appcore import ad_alert_daily_report, scheduled_tasks

    assert scheduled_tasks.is_known_task(ad_alert_daily_report.TASK_CODE)

    definition = scheduled_tasks.get_task_definition(ad_alert_daily_report.TASK_CODE)

    assert definition["name"] == "广告预警每日飞书推送"
    assert definition["source_type"] == "apscheduler"
    assert definition["source_ref"] == ad_alert_daily_report.TASK_CODE
    assert definition["runner"] == "appcore.ad_alert_daily_report.tick_once"
    assert definition["log_table"] == "scheduled_task_runs"
    assert definition["default_enabled"] is False


def test_tick_once_skips_when_feishu_disabled(monkeypatch):
    from appcore import ad_alert_daily_report, feishu_alerts, scheduled_tasks

    monkeypatch.setattr(scheduled_tasks, "start_run", lambda code: 11)
    finished: dict[str, object] = {}
    monkeypatch.setattr(
        scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: finished.update(run_id=run_id, **kwargs),
    )
    monkeypatch.setattr(
        feishu_alerts,
        "load_config",
        lambda: feishu_alerts.FeishuAlertConfig(False, "", "", ""),
    )

    summary = ad_alert_daily_report.tick_once()

    assert summary == {"skipped": "feishu_disabled"}
    assert finished["status"] == "success"
    assert finished["summary"] == {"skipped": "feishu_disabled"}


def test_build_long_loss_report_text():
    from datetime import date as _date

    from appcore import ad_alert_daily_report as report
    from appcore import ad_long_term_loss as ltl

    items = [ltl.LongTermLossItem(
        product_id=1, product_code="P1", product_name="品1", product_main_image=None,
        spend_7d=800.0, profit_7d=-200.0, loss_7d=200.0, profit_30d=-50.0, loss_ratio=None,
        verdict="long_term_net_loss", active_days=28, consecutive_loss_days=3,
        first_active_date="2026-05-01", has_estimated_cost=True, detail_url="",
    )]
    text = report.build_long_loss_report_text(_date(2026, 6, 14), items)
    assert "长期亏损品" in text
    assert "品1" in text
    assert "200" in text
    assert "连续亏损 3 天" in text


def test_tick_once_skips_when_no_ads(monkeypatch):
    from appcore import ad_alert_daily_report, ad_alerts, feishu_alerts, scheduled_tasks

    monkeypatch.setattr(scheduled_tasks, "start_run", lambda code: 12)
    monkeypatch.setattr(scheduled_tasks, "finish_run", lambda run_id, **kwargs: None)
    monkeypatch.setattr(
        feishu_alerts,
        "load_config",
        lambda: feishu_alerts.FeishuAlertConfig(True, "a", "s", "c"),
    )
    monkeypatch.setattr(
        ad_alerts, "get_high_loss_ads",
        lambda **kwargs: (date(2026, 6, 12), []),
    )
    from appcore import ad_long_term_loss
    monkeypatch.setattr(
        ad_long_term_loss, "get_long_term_loss_products",
        lambda **kwargs: (date(2026, 6, 12), []),
    )
    sent: list[str] = []
    monkeypatch.setattr(
        feishu_alerts, "send_text_message",
        lambda text, **kwargs: sent.append(text) or {"ok": True},
    )

    summary = ad_alert_daily_report.tick_once()

    assert summary == {"skipped": "no_high_loss_ads"}
    assert sent == []


def test_tick_once_sends_report_with_share_link(monkeypatch):
    from appcore import ad_alert_daily_report, ad_alerts, feishu_alerts, scheduled_tasks

    monkeypatch.setattr(scheduled_tasks, "start_run", lambda code: 13)
    finished: dict[str, object] = {}
    monkeypatch.setattr(
        scheduled_tasks,
        "finish_run",
        lambda run_id, **kwargs: finished.update(run_id=run_id, **kwargs),
    )
    monkeypatch.setattr(
        feishu_alerts,
        "load_config",
        lambda: feishu_alerts.FeishuAlertConfig(True, "a", "s", "c"),
    )
    captured_kwargs: dict[str, object] = {}

    def fake_get_high_loss_ads(**kwargs):
        captured_kwargs.update(kwargs)
        return date(2026, 6, 12), [_item("ad-1", spend=120.0, roas=0.33, loss_days=3)]

    monkeypatch.setattr(ad_alerts, "get_high_loss_ads", fake_get_high_loss_ads)
    from appcore import ad_long_term_loss
    monkeypatch.setattr(
        ad_long_term_loss, "get_long_term_loss_products",
        lambda **kwargs: (date(2026, 6, 12), []),
    )
    monkeypatch.setattr(
        ad_alert_daily_report, "_share_secret_key", lambda: "test-secret"
    )
    monkeypatch.setattr(
        ad_alert_daily_report, "_share_base_url", lambda: "https://example.com"
    )
    sent: list[str] = []
    monkeypatch.setattr(
        feishu_alerts, "send_text_message",
        lambda text, **kwargs: sent.append(text) or {"ok": True, "message_id": "m1"},
    )

    summary = ad_alert_daily_report.tick_once()

    assert captured_kwargs.get("limit") == 10
    assert summary["sent"] is True
    assert summary["ad_count"] == 1
    assert len(sent) == 1
    assert "Ad ad-1" in sent[0]
    assert "https://example.com/ad-alerts/share/high-loss?token=" in sent[0]
    assert finished["status"] == "success"


def test_tick_once_pushes_long_loss_when_high_loss_empty(monkeypatch):
    """高额亏损为空时，长期亏损品推送仍独立发出（两者口径不同）。"""
    from appcore import ad_alert_daily_report, ad_alerts, ad_long_term_loss, feishu_alerts, scheduled_tasks

    monkeypatch.setattr(scheduled_tasks, "start_run", lambda code: 14)
    monkeypatch.setattr(scheduled_tasks, "finish_run", lambda run_id, **kwargs: None)
    monkeypatch.setattr(
        feishu_alerts, "load_config",
        lambda: feishu_alerts.FeishuAlertConfig(True, "a", "s", "c"),
    )
    monkeypatch.setattr(
        ad_alerts, "get_high_loss_ads", lambda **kwargs: (date(2026, 6, 12), [])
    )
    ll_item = ad_long_term_loss.LongTermLossItem(
        product_id=1, product_code="P1", product_name="品1", product_main_image=None,
        spend_7d=800.0, profit_7d=-200.0, loss_7d=200.0, profit_30d=-50.0, loss_ratio=None,
        verdict="long_term_net_loss", active_days=28, consecutive_loss_days=3,
        first_active_date="2026-05-01", has_estimated_cost=True, detail_url="",
    )
    monkeypatch.setattr(
        ad_long_term_loss, "get_long_term_loss_products",
        lambda **kwargs: (date(2026, 6, 14), [ll_item]),
    )
    sent: list[str] = []
    monkeypatch.setattr(
        feishu_alerts, "send_text_message",
        lambda text, **kwargs: sent.append(text) or {"ok": True},
    )

    summary = ad_alert_daily_report.tick_once()

    assert summary == {"skipped": "no_high_loss_ads"}
    assert len(sent) == 1
    assert "长期亏损品" in sent[0]
    assert "品1" in sent[0]
