from __future__ import annotations

from datetime import datetime

import pytest


def test_target_key_builders_normalize_inputs():
    from appcore import ad_alert_actions

    assert ad_alert_actions.high_loss_target_key("act_123", "120210") == "123:120210"
    assert ad_alert_actions.high_loss_target_key("123", " 120210 ") == "123:120210"
    assert ad_alert_actions.language_target_key(45, "DE") == "45:de"


def test_set_action_upserts_and_validates(monkeypatch):
    from appcore import ad_alert_actions

    captured: dict[str, object] = {}

    def fake_execute(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return 1

    monkeypatch.setattr(ad_alert_actions, "execute", fake_execute)

    result = ad_alert_actions.set_action(
        ad_alert_actions.SCOPE_HIGH_LOSS,
        "123:120210",
        "resolved",
        note="已在 Meta 后台关停",
        operator_user_id=7,
    )

    assert "INSERT INTO ad_alert_actions" in captured["sql"]
    assert "ON DUPLICATE KEY UPDATE" in captured["sql"]
    assert captured["args"] == (
        "high_loss", "123:120210", "resolved", "已在 Meta 后台关停", 7,
    )
    assert result == {
        "scope": "high_loss",
        "target_key": "123:120210",
        "action": "resolved",
        "note": "已在 Meta 后台关停",
        "operator_user_id": 7,
    }

    with pytest.raises(ValueError):
        ad_alert_actions.set_action("bad_scope", "k", "resolved")
    with pytest.raises(ValueError):
        ad_alert_actions.set_action(
            ad_alert_actions.SCOPE_LANGUAGE, "45:de", "bad_action"
        )
    with pytest.raises(ValueError):
        ad_alert_actions.set_action(ad_alert_actions.SCOPE_LANGUAGE, "", "resolved")


def test_clear_action_deletes_row(monkeypatch):
    from appcore import ad_alert_actions

    captured: dict[str, object] = {}

    def fake_execute(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return 1

    monkeypatch.setattr(ad_alert_actions, "execute", fake_execute)

    assert ad_alert_actions.clear_action(
        ad_alert_actions.SCOPE_LANGUAGE, "45:de"
    ) is True
    assert "DELETE FROM ad_alert_actions" in captured["sql"]
    assert captured["args"] == ("language", "45:de")


def test_get_actions_batches_and_maps(monkeypatch):
    from appcore import ad_alert_actions

    captured: dict[str, object] = {}

    def fake_query(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return [
            {
                "target_key": "123:120210",
                "action": "resolved",
                "note": "done",
                "operator_user_id": 7,
                "updated_at": datetime(2026, 6, 12, 10, 0, 0),
            }
        ]

    monkeypatch.setattr(ad_alert_actions, "query", fake_query)

    result = ad_alert_actions.get_actions(
        ad_alert_actions.SCOPE_HIGH_LOSS, ["123:120210", "456:888"]
    )

    assert "FROM ad_alert_actions" in captured["sql"]
    assert captured["args"] == ("high_loss", "123:120210", "456:888")
    assert set(result.keys()) == {"123:120210"}
    assert result["123:120210"]["action"] == "resolved"
    assert result["123:120210"]["updated_at"] == "2026-06-12T10:00:00"

    assert ad_alert_actions.get_actions(ad_alert_actions.SCOPE_HIGH_LOSS, []) == {}


def test_long_term_loss_scope_is_valid():
    from appcore import ad_alert_actions

    assert "long_term_loss" in ad_alert_actions.VALID_SCOPES
    assert ad_alert_actions.SCOPE_LONG_TERM_LOSS == "long_term_loss"


def test_long_term_loss_target_key_uses_product_id():
    from appcore import ad_alert_actions

    assert ad_alert_actions.long_term_loss_target_key(123) == "product:123"
