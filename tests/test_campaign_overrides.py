"""campaign-product 人工配对兜底测试。"""
from __future__ import annotations

import pytest

from appcore import order_analytics as oa
from appcore.order_analytics.campaign_overrides import (
    apply_override_to_history,
    create_override,
    list_overrides,
    list_unmatched_campaigns,
    remove_override,
    resolve_override,
)


def test_list_unmatched_campaigns_aggregates_by_normalized_code(monkeypatch):
    """聚合按 normalized_campaign_code，按 spend 降序。"""
    rows_returned = [
        {"normalized_campaign_code": "plastic-slide", "days": 60, "spend": 8755.22, "sample_campaign_name": "plastic-slide"},
        {"normalized_campaign_code": "bone-conduction", "days": 96, "spend": 7358.29, "sample_campaign_name": "bone-conduction"},
    ]

    def fake_query(sql, args=()):
        assert "product_id IS NULL" in sql
        return rows_returned

    monkeypatch.setattr(oa, "query", fake_query)

    result = list_unmatched_campaigns(lookback_days=90, limit=50)
    assert len(result) == 2
    assert result[0]["spend"] == 8755.22


def test_create_override_inserts_and_applies_to_history(monkeypatch):
    captured = {"sqls": [], "args": []}

    def fake_execute(sql, args=()):
        captured["sqls"].append(sql)
        captured["args"].append(args)
        return 1

    monkeypatch.setattr(oa, "execute", fake_execute)

    def fake_query_one(sql, args=()):
        # product 查找
        return {"id": 316, "product_code": "sonic-lens-refresher-rjc"}

    monkeypatch.setattr(oa, "query_one", fake_query_one)

    create_override(
        normalized_campaign_code="plastic-slide-paper-clips",
        product_id=316,
        reason="实际投的就是这个产品",
        created_by="admin",
    )
    # 应有 INSERT campaign_product_overrides + UPDATE meta_ad_daily_campaign_metrics
    assert any("INSERT INTO campaign_product_overrides" in s for s in captured["sqls"])
    assert any("UPDATE meta_ad_daily_campaign_metrics" in s for s in captured["sqls"])


def test_create_override_validates_product_exists(monkeypatch):
    monkeypatch.setattr(oa, "query_one", lambda sql, args=(): None)
    monkeypatch.setattr(oa, "execute", lambda sql, args=(): None)

    with pytest.raises(ValueError, match="product_id"):
        create_override(
            normalized_campaign_code="abc", product_id=99999,
            reason="", created_by="admin",
        )


def test_resolve_override_returns_product_id(monkeypatch):
    """resolve_override(campaign_code) 用于同步流程优先查 override 表。"""
    # SQL 用 alias `o.product_id AS id` + JOIN media_products 拿 name
    monkeypatch.setattr(oa, "query_one",
                        lambda sql, args=(): {"id": 316, "product_code": "sonic-lens-refresher-rjc",
                                              "name": "Sonic Lens Refresher"})
    result = resolve_override("plastic-slide-paper-clips")
    assert result == {"id": 316, "product_code": "sonic-lens-refresher-rjc",
                      "name": "Sonic Lens Refresher"}


def test_resolve_override_returns_none_for_unmapped(monkeypatch):
    monkeypatch.setattr(oa, "query_one", lambda sql, args=(): None)
    assert resolve_override("unknown-campaign") is None


def test_list_overrides_returns_all(monkeypatch):
    monkeypatch.setattr(oa, "query", lambda sql, args=(): [
        {"id": 1, "normalized_campaign_code": "a", "product_id": 316,
         "product_code": "p1", "reason": "x", "created_by": "admin",
         "created_at": "2026-05-04 10:00:00", "updated_at": "2026-05-04 10:00:00"},
    ])
    result = list_overrides()
    assert len(result) == 1
    assert result[0]["product_id"] == 316


def test_remove_override_deletes_and_unlinks_history(monkeypatch):
    captured = {"sqls": []}

    def fake_query_one(sql, args=()):
        return {"normalized_campaign_code": "plastic-slide-paper-clips"}

    def fake_execute(sql, args=()):
        captured["sqls"].append(sql)
        return 1

    monkeypatch.setattr(oa, "query_one", fake_query_one)
    monkeypatch.setattr(oa, "execute", fake_execute)

    remove_override(override_id=1)
    # 应同时删 override + 把对应 meta_ad_daily_campaign_metrics 的 product_id 清空
    assert any("DELETE FROM campaign_product_overrides" in s for s in captured["sqls"])


def test_apply_override_to_history_updates_meta_table(monkeypatch):
    captured = {}

    def fake_execute(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return 5

    monkeypatch.setattr(oa, "execute", fake_execute)

    rows = apply_override_to_history(
        normalized_campaign_code="plastic-slide-paper-clips",
        product_id=316,
        product_code="sonic-lens-refresher-rjc",
    )
    assert "UPDATE meta_ad_daily_campaign_metrics" in captured["sql"]
    assert "product_id" in captured["sql"]
