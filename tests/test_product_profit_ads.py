"""产品广告明细聚合测试（Tab ④ 数据源）。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from appcore.order_analytics import product_profit_ads as ppa


def test_generate_ads_report_empty():
    """无广告数据 → 返回空列表 + 空 daily / unmatched / accounts。"""
    with patch.object(ppa, "_load_campaign_metrics", return_value=[]), \
         patch.object(ppa, "_load_match_map", return_value={}), \
         patch.object(ppa, "_load_attributed_orders", return_value={}):
        result = ppa.generate_ads_report(
            product_id=100,
            date_from=date(2026, 5, 1),
            date_to=date(2026, 5, 7),
        )
    assert result["accounts"] == []
    assert result["campaigns"] == []
    assert result["unmatched"] == []
    assert result["daily"] == []


def test_generate_ads_report_matched_campaign_aggregates():
    """campaign 在表里 product_id 已回填 → campaigns 列表 1 行 + accounts 1 行 + daily 1 行。"""
    fake_metrics = [
        {
            "report_date": date(2026, 5, 5),
            "ad_account_id": "2110407576446225",
            "ad_account_name": "newjoyloo",
            "normalized_campaign_code": "abc-rjc",
            "campaign_name": "ABC-rjc",
            "product_id": 100,
            "matched_product_code": "abc",
            "spend_usd": Decimal("8.00"),
            "result_count": 5,
            "purchase_value_usd": Decimal("60.00"),
            "roas_purchase": Decimal("7.50"),
        },
    ]
    fake_attributed = {
        date(2026, 5, 5): {
            "revenue": Decimal("50"),
            "purchase": Decimal("10"),
            "shipping": Decimal("3"),
            "reserve": Decimal("0.50"),
            "order_count": 1,
        },
    }
    with patch.object(ppa, "_load_campaign_metrics", return_value=fake_metrics), \
         patch.object(ppa, "_load_match_map", return_value={}), \
         patch.object(ppa, "_load_attributed_orders", return_value=fake_attributed):
        result = ppa.generate_ads_report(
            product_id=100,
            date_from=date(2026, 5, 1),
            date_to=date(2026, 5, 7),
        )

    assert len(result["campaigns"]) == 1
    c = result["campaigns"][0]
    assert c["normalized_campaign_code"] == "abc-rjc"
    assert c["campaign_name"] == "ABC-rjc"
    assert c["ad_account_id"] == "2110407576446225"
    assert c["spend_usd"] == 8.0
    assert c["result_count"] == 5
    # ROAS = 归属收入 / 花费 = 50 / 8（用订单归属收入，不用 Meta 自报 purchase_value）
    assert c["roas"] == 50.0 / 8.0
    # 100% 占比 → 归属订单数 = 总订单数 1
    assert c["attributed_order_count"] == 1
    # profit = 50 - 8 - (10 + 3 + 0.5) = 28.5
    assert c["profit_contribution_usd"] == 28.5

    assert len(result["accounts"]) == 1
    a = result["accounts"][0]
    assert a["ad_account_id"] == "2110407576446225"
    assert a["label"] == "newjoyloo"
    assert a["spend_usd"] == 8.0

    assert len(result["daily"]) == 1
    d = result["daily"][0]
    assert d["date"] == "2026-05-05"
    assert d["spend_usd"] == 8.0
    assert d["revenue_usd"] == 50.0

    assert result["unmatched"] == []


def test_generate_ads_report_unmatched_goes_to_unmatched_bucket():
    """campaign 没有 product_id 且 resolve_ad_product_match 也匹配不上 → 进 unmatched。"""
    fake_metrics = [
        {
            "report_date": date(2026, 5, 5),
            "ad_account_id": "2110407576446225",
            "ad_account_name": "newjoyloo",
            "normalized_campaign_code": "mystery",
            "campaign_name": "Mystery",
            "product_id": None,
            "matched_product_code": None,
            "spend_usd": Decimal("5.00"),
            "result_count": 0,
            "purchase_value_usd": Decimal("0"),
            "roas_purchase": None,
        },
    ]
    with patch.object(ppa, "_load_campaign_metrics", return_value=fake_metrics), \
         patch.object(ppa, "_load_match_map", return_value={"mystery": None}), \
         patch.object(ppa, "_load_attributed_orders", return_value={}):
        result = ppa.generate_ads_report(
            product_id=100,
            date_from=date(2026, 5, 1),
            date_to=date(2026, 5, 7),
        )
    assert result["campaigns"] == []
    assert result["accounts"] == []
    assert result["daily"] == []
    assert len(result["unmatched"]) == 1
    u = result["unmatched"][0]
    assert u["normalized_campaign_code"] == "mystery"
    assert u["campaign_name"] == "Mystery"
    assert u["spend_usd"] == 5.0


def test_generate_ads_report_resolve_recovers_unmatched_to_current_product():
    """campaign product_id=NULL 但 resolve_ad_product_match 命中当前产品 → 仍归当前产品。"""
    fake_metrics = [
        {
            "report_date": date(2026, 5, 5),
            "ad_account_id": "2110407576446225",
            "ad_account_name": "newjoyloo",
            "normalized_campaign_code": "abc-rjc",
            "campaign_name": "ABC-rjc",
            "product_id": None,         # 还没回填
            "matched_product_code": None,
            "spend_usd": Decimal("4"),
            "result_count": 1,
            "purchase_value_usd": Decimal("12"),
            "roas_purchase": Decimal("3"),
        },
    ]
    fake_attributed = {
        date(2026, 5, 5): {
            "revenue": Decimal("40"),
            "purchase": Decimal("8"),
            "shipping": Decimal("2"),
            "reserve": Decimal("0.40"),
            "order_count": 1,
        },
    }
    with patch.object(ppa, "_load_campaign_metrics", return_value=fake_metrics), \
         patch.object(ppa, "_load_match_map", return_value={"abc-rjc": 100}), \
         patch.object(ppa, "_load_attributed_orders", return_value=fake_attributed):
        result = ppa.generate_ads_report(
            product_id=100,
            date_from=date(2026, 5, 1),
            date_to=date(2026, 5, 7),
        )
    assert len(result["campaigns"]) == 1
    assert result["unmatched"] == []
    c = result["campaigns"][0]
    assert c["normalized_campaign_code"] == "abc-rjc"
    assert c["spend_usd"] == 4.0


def test_generate_ads_report_other_product_excluded_silently():
    """campaign 已经匹配到另一个 product_id → 既不进 campaigns 也不进 unmatched。"""
    fake_metrics = [
        {
            "report_date": date(2026, 5, 5),
            "ad_account_id": "2110407576446225",
            "ad_account_name": "newjoyloo",
            "normalized_campaign_code": "xyz-rjc",
            "campaign_name": "XYZ-rjc",
            "product_id": 200,                  # 别的产品
            "matched_product_code": "xyz",
            "spend_usd": Decimal("6"),
            "result_count": 2,
            "purchase_value_usd": Decimal("18"),
            "roas_purchase": Decimal("3"),
        },
    ]
    with patch.object(ppa, "_load_campaign_metrics", return_value=fake_metrics), \
         patch.object(ppa, "_load_match_map", return_value={}), \
         patch.object(ppa, "_load_attributed_orders", return_value={}):
        result = ppa.generate_ads_report(
            product_id=100,
            date_from=date(2026, 5, 1),
            date_to=date(2026, 5, 7),
        )
    assert result["campaigns"] == []
    assert result["unmatched"] == []
    assert result["accounts"] == []
    assert result["daily"] == []


def test_generate_ads_report_attributed_orders_split_by_spend_pro_rata():
    """两个 campaign 同产品 → 归属订单数按 spend 比例分摊（不是每行都展示总数）。"""
    fake_metrics = [
        {
            "report_date": date(2026, 5, 5),
            "ad_account_id": "2110407576446225",
            "ad_account_name": "newjoyloo",
            "normalized_campaign_code": "abc-rjc",
            "campaign_name": "ABC-rjc",
            "product_id": 100,
            "matched_product_code": "abc",
            "spend_usd": Decimal("9"),       # 75%
            "result_count": 3,
            "purchase_value_usd": Decimal("27"),
            "roas_purchase": Decimal("3"),
        },
        {
            "report_date": date(2026, 5, 5),
            "ad_account_id": "2110407576446225",
            "ad_account_name": "newjoyloo",
            "normalized_campaign_code": "abc2-rjc",
            "campaign_name": "ABC2-rjc",
            "product_id": 100,
            "matched_product_code": "abc",
            "spend_usd": Decimal("3"),       # 25%
            "result_count": 1,
            "purchase_value_usd": Decimal("9"),
            "roas_purchase": Decimal("3"),
        },
    ]
    fake_attributed = {
        date(2026, 5, 5): {
            "revenue": Decimal("100"),
            "purchase": Decimal("20"),
            "shipping": Decimal("6"),
            "reserve": Decimal("1"),
            "order_count": 4,
        },
    }
    with patch.object(ppa, "_load_campaign_metrics", return_value=fake_metrics), \
         patch.object(ppa, "_load_match_map", return_value={}), \
         patch.object(ppa, "_load_attributed_orders", return_value=fake_attributed):
        result = ppa.generate_ads_report(
            product_id=100,
            date_from=date(2026, 5, 1),
            date_to=date(2026, 5, 7),
        )
    assert len(result["campaigns"]) == 2
    # 已按 spend 倒序排
    big = result["campaigns"][0]
    small = result["campaigns"][1]
    assert big["spend_usd"] == 9.0
    assert small["spend_usd"] == 3.0
    # 归属订单 4 单按 spend 比例分摊：9 / 12 → 3 单，3 / 12 → 1 单
    assert big["attributed_order_count"] == 3
    assert small["attributed_order_count"] == 1
    # 归属收入：100 × 9/12 = 75；100 × 3/12 = 25
    assert big["attributed_revenue_usd"] == 75.0
    assert small["attributed_revenue_usd"] == 25.0
    # 同 ad_account → accounts 仅 1 行
    assert len(result["accounts"]) == 1
    assert result["accounts"][0]["spend_usd"] == 12.0
