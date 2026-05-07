"""产品盈亏报表服务层单测：拆分手续费 + 按 site 重算广告费 + 4-sheet xlsx 生成。

不依赖真实数据库——用 monkeypatch 替换 query / query_one 注入 mock 数据。
"""
from __future__ import annotations

import io
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from appcore.order_analytics import product_profit_report as ppr


def test_payments_csv_import_sanitizes_filename_and_decodes_gbk(authed_client_no_db, monkeypatch):
    captured = {}

    def fake_import_payments_csv(stream, *, source_csv):
        captured["content"] = stream.read()
        captured["source_csv"] = source_csv
        return {"inserted": 1, "updated": 0}

    monkeypatch.setattr(
        "web.routes.product_profit_report.import_payments_csv",
        fake_import_payments_csv,
    )

    resp = authed_client_no_db.post(
        "/order-analytics/product-profit/payments_csv/import",
        data={
            "store_code": "newjoyloo",
            "file": (io.BytesIO("订单金额\n100".encode("gbk")), "..\\..\\payments.csv"),
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["filename"] == "payments.csv"
    assert payload["source_csv"] == "newjoyloo__payments.csv"
    assert captured["source_csv"] == "newjoyloo__payments.csv"
    assert captured["content"] == "订单金额\n100"


# ---------------------------------------------------------------------------
# 1. _split_shopify_fee 拆分公式
# ---------------------------------------------------------------------------
def test_split_fee_us_buyer_only_base_rate():
    """美国本土卡 + USD 结账 → 只有 base_rate（2.5%），没有国际/转换费。"""
    result = ppr._split_shopify_fee(
        line_amount_usd=100.0,
        total_fee_usd=2.50,
        buyer_country="US",
    )
    assert result["intl_fee"] == 0.0
    assert result["conv_fee"] == 0.0
    assert result["base_fee"] == 2.50


def test_split_fee_gb_buyer_has_intl_and_conversion():
    """英国买家 → cross_border (1%) + currency_conversion (1.5%) 都触发。"""
    # base 2.5% + cb 1% + cc 1.5% = 5%
    # 100 USD * 5% = 5.0 fee
    result = ppr._split_shopify_fee(
        line_amount_usd=100.0,
        total_fee_usd=5.0,
        buyer_country="GB",
    )
    assert result["intl_fee"] == pytest.approx(1.0, abs=0.01)
    assert result["conv_fee"] == pytest.approx(1.5, abs=0.01)
    assert result["base_fee"] == pytest.approx(2.5, abs=0.01)
    # 三项之和必须严格等于合计
    assert result["base_fee"] + result["intl_fee"] + result["conv_fee"] == pytest.approx(5.0, abs=0.001)


def test_split_fee_ca_buyer_intl_and_conversion():
    """加拿大买家 → CAD 结账，cross_border + currency_conversion 都触发。"""
    result = ppr._split_shopify_fee(
        line_amount_usd=200.0,
        total_fee_usd=10.0,  # 5% rate * 200 = 10
        buyer_country="CA",
    )
    assert result["intl_fee"] > 0
    assert result["conv_fee"] > 0
    assert result["base_fee"] + result["intl_fee"] + result["conv_fee"] == pytest.approx(10.0, abs=0.001)


def test_split_fee_zero_amount():
    """金额或 fee 为 0 → 全部 0。"""
    assert ppr._split_shopify_fee(0, 0, "US") == {"base_fee": 0.0, "intl_fee": 0.0, "conv_fee": 0.0}
    assert ppr._split_shopify_fee(100, 0, "US") == {"base_fee": 0.0, "intl_fee": 0.0, "conv_fee": 0.0}


# ---------------------------------------------------------------------------
# 2. _recalc_ad_cost 按 site → account 分账
# ---------------------------------------------------------------------------
def test_recalc_ad_cost_newjoy_uses_newjoyloo_account():
    """newjoy 站订单 → 只摊 Newjoyloo (2110407576446225) 账户的 spend。"""
    line = {
        "site_code": "newjoy",
        "business_date": date(2026, 4, 15),
        "quantity": 2,
    }
    site_units = {(date(2026, 4, 15), "newjoy"): 10}  # 当日 newjoy 站 10 件
    account_spend = {
        (date(2026, 4, 15), "2110407576446225"): 100.0,  # Newjoyloo $100
        (date(2026, 4, 15), "1253003326160754"): 500.0,  # Omurio $500（不应该影响 newjoy 行）
    }
    ad_cost = ppr._recalc_ad_cost(line, site_units, account_spend)
    # 100 * 2/10 = 20，绝不能等于 500*2/10=100 或 (100+500)*2/10=120
    assert ad_cost == pytest.approx(20.0, abs=0.01)


def test_recalc_ad_cost_omurio_uses_omurio_account():
    """omurio 站订单 → 只摊 Omurio (1253003326160754) 账户的 spend。"""
    line = {
        "site_code": "omurio",
        "business_date": date(2026, 4, 15),
        "quantity": 1,
    }
    site_units = {(date(2026, 4, 15), "omurio"): 4}
    account_spend = {
        (date(2026, 4, 15), "2110407576446225"): 999.0,
        (date(2026, 4, 15), "1253003326160754"): 200.0,
    }
    ad_cost = ppr._recalc_ad_cost(line, site_units, account_spend)
    assert ad_cost == pytest.approx(50.0, abs=0.01)  # 200 * 1/4


def test_recalc_ad_cost_uses_meta_account_store_mapping(monkeypatch):
    """同一店铺绑定多个广告户时，广告费按该店铺多个账户 spend 合计分摊。"""
    monkeypatch.setattr(
        ppr,
        "meta_ad_accounts",
        SimpleNamespace(site_account_map=lambda enabled_only=True: {"newjoy": ("111", "222")}),
        raising=False,
    )
    line = {
        "site_code": "newjoy",
        "business_date": date(2026, 4, 15),
        "quantity": 3,
    }
    site_units = {(date(2026, 4, 15), "newjoy"): 6}
    account_spend = {
        (date(2026, 4, 15), "111"): 20.0,
        (date(2026, 4, 15), "222"): 40.0,
        (date(2026, 4, 15), "333"): 999.0,
    }

    ad_cost = ppr._recalc_ad_cost(line, site_units, account_spend)

    assert ad_cost == pytest.approx(30.0, abs=0.01)


def test_product_profit_mapping_includes_disabled_accounts(monkeypatch):
    """产品盈亏历史分摊使用全部配置账户，不受 enabled 同步开关影响。"""
    calls = []

    def fake_site_account_map(*, enabled_only=True):
        calls.append(enabled_only)
        return {"newjoy": ("2110407576446225",)}

    monkeypatch.setattr(
        ppr,
        "meta_ad_accounts",
        SimpleNamespace(site_account_map=fake_site_account_map),
        raising=False,
    )

    ad_cost = ppr._recalc_ad_cost(
        {"site_code": "newjoy", "business_date": date(2026, 4, 15), "quantity": 1},
        {(date(2026, 4, 15), "newjoy"): 2},
        {(date(2026, 4, 15), "2110407576446225"): 50.0},
    )

    assert calls == [False]
    assert ad_cost == pytest.approx(25.0, abs=0.01)


def test_recalc_ad_cost_zero_when_no_units():
    """当日该站 0 单 → 防御除零，返回 0。"""
    line = {"site_code": "newjoy", "business_date": date(2026, 4, 15), "quantity": 1}
    assert ppr._recalc_ad_cost(line, {}, {(date(2026, 4, 15), "2110407576446225"): 100.0}) == 0.0


def test_recalc_ad_cost_zero_when_unknown_site():
    """未知站点（不在 1:1 映射里）→ 返回 0，不爆。"""
    line = {"site_code": "unknown_site", "business_date": date(2026, 4, 15), "quantity": 1}
    assert ppr._recalc_ad_cost(line, {(date(2026, 4, 15), "unknown_site"): 5}, {}) == 0.0


# ---------------------------------------------------------------------------
# 3. generate_report 端到端（mock 数据）
# ---------------------------------------------------------------------------
def _setup_mock_db(monkeypatch, *, lines, site_units, account_spend, real_fees=None, product_meta=None):
    """统一的 monkeypatch helper：替换 sub-module facade 透传到的 query/query_one。"""
    real_fees = real_fees or {}
    product_meta = product_meta or {"id": 427, "product_code": "fully-automatic-water-blaster-rjc", "name": "ARP9 电动水枪"}

    import sys
    pkg_mod = sys.modules["appcore.order_analytics"]

    def fake_query(sql, params=None):
        s = sql.upper()
        if "FROM ORDER_PROFIT_LINES OPL" in s and "JOIN DIANXIAOMI_ORDER_LINES" in s:
            return lines
        if "FROM DIANXIAOMI_ORDER_LINES DOL" in s and "GROUP BY DATE" in s:
            return [
                {"d": d, "site_code": site, "units": units}
                for (d, site), units in site_units.items()
            ]
        if "FROM META_AD_DAILY_CAMPAIGN_METRICS" in s and "GROUP BY REPORT_DATE" in s:
            return [
                {"report_date": d, "ad_account_id": acc, "spend": spend}
                for (d, acc), spend in account_spend.items()
            ]
        if "FROM SHOPIFY_PAYMENTS_TRANSACTIONS" in s:
            return [{"order_name": k, "fee": v} for k, v in real_fees.items()]
        return []

    def fake_query_one(sql, params=None):
        s = sql.upper()
        if "FROM MEDIA_PRODUCTS" in s:
            return product_meta
        return None

    monkeypatch.setattr(pkg_mod, "query", fake_query)
    monkeypatch.setattr(pkg_mod, "query_one", fake_query_one)


def test_generate_report_end_to_end(monkeypatch):
    """模拟 4 单（2 newjoy + 2 omurio）→ 检验三维聚合 + 修正利润 + 站点完整口径。"""
    d = date(2026, 4, 15)
    lines = [
        # newjoy 2 单
        {
            "dxm_order_line_id": 1, "business_date": d, "paid_at": datetime(2026, 4, 15, 10),
            "buyer_country": "US", "line_amount_usd": 50.0, "shipping_allocated_usd": 5.0,
            "revenue_usd": 55.0, "shopify_fee_usd": 1.65, "purchase_usd": 8.0,
            "shipping_cost_usd": 5.0, "return_reserve_usd": 0.55, "profit_old_usd": 30.0,
            "shopify_tier": "A", "status": "ok",
            "dxm_package_id": "P1", "extended_order_id": "#1001", "site_code": "newjoy",
            "product_sku": "SKU-A", "product_display_sku": "SKU-A", "product_name": "ARP9",
            "quantity": 1, "unit_price": 50.0, "line_amount_native": 50.0,
            "order_amount_native": 55.0, "order_currency": "USD", "platform": "Shopify",
        },
        {
            "dxm_order_line_id": 2, "business_date": d, "paid_at": datetime(2026, 4, 15, 11),
            "buyer_country": "GB", "line_amount_usd": 60.0, "shipping_allocated_usd": 6.0,
            "revenue_usd": 66.0, "shopify_fee_usd": 3.30, "purchase_usd": 8.0,
            "shipping_cost_usd": 5.0, "return_reserve_usd": 0.66, "profit_old_usd": 25.0,
            "shopify_tier": "D", "status": "ok",
            "dxm_package_id": "P2", "extended_order_id": "#1002", "site_code": "newjoy",
            "product_sku": "SKU-A", "product_display_sku": "SKU-A", "product_name": "ARP9",
            "quantity": 1, "unit_price": 60.0, "line_amount_native": 60.0,
            "order_amount_native": 66.0, "order_currency": "GBP", "platform": "Shopify",
        },
        # omurio 2 单
        {
            "dxm_order_line_id": 3, "business_date": d, "paid_at": datetime(2026, 4, 15, 12),
            "buyer_country": "US", "line_amount_usd": 50.0, "shipping_allocated_usd": 5.0,
            "revenue_usd": 55.0, "shopify_fee_usd": 1.65, "purchase_usd": 8.0,
            "shipping_cost_usd": 5.0, "return_reserve_usd": 0.55, "profit_old_usd": 30.0,
            "shopify_tier": "A", "status": "ok",
            "dxm_package_id": "P3", "extended_order_id": "#2001", "site_code": "omurio",
            "product_sku": "SKU-A", "product_display_sku": "SKU-A", "product_name": "ARP9",
            "quantity": 1, "unit_price": 50.0, "line_amount_native": 50.0,
            "order_amount_native": 55.0, "order_currency": "USD", "platform": "Shopify",
        },
        {
            "dxm_order_line_id": 4, "business_date": d, "paid_at": datetime(2026, 4, 15, 13),
            "buyer_country": "CA", "line_amount_usd": 70.0, "shipping_allocated_usd": 7.0,
            "revenue_usd": 77.0, "shopify_fee_usd": 3.85, "purchase_usd": 8.0,
            "shipping_cost_usd": 5.0, "return_reserve_usd": 0.77, "profit_old_usd": 35.0,
            "shopify_tier": "D", "status": "ok",
            "dxm_package_id": "P4", "extended_order_id": "#2002", "site_code": "omurio",
            "product_sku": "SKU-A", "product_display_sku": "SKU-A", "product_name": "ARP9",
            "quantity": 1, "unit_price": 70.0, "line_amount_native": 70.0,
            "order_amount_native": 77.0, "order_currency": "CAD", "platform": "Shopify",
        },
    ]
    site_units = {
        (d, "newjoy"): 2,   # 2 件
        (d, "omurio"): 2,   # 2 件
    }
    account_spend = {
        (d, "2110407576446225"): 50.0,    # Newjoyloo $50 → newjoy 站每件 $25
        (d, "1253003326160754"): 100.0,   # Omurio $100 → omurio 站每件 $50
    }
    _setup_mock_db(monkeypatch, lines=lines, site_units=site_units, account_spend=account_spend)

    report = ppr.generate_report(product_id=427, date_from=d, date_to=d)

    # === 订单明细：每行包含 7 列 + ad_cost_recalc ===
    assert len(report["orders"]) == 4
    o1 = report["orders"][0]  # newjoy US line
    assert o1["site"] == "newjoyloo"  # 完整口径
    assert o1["ad_cost_recalc_usd"] == pytest.approx(25.0)  # 50 * 1/2
    assert o1["intl_card_fee_usd"] == 0  # US buyer
    assert o1["currency_conv_fee_usd"] == 0  # USD presentment

    o3 = report["orders"][2]  # omurio US
    assert o3["site"] == "Omurio"
    assert o3["ad_cost_recalc_usd"] == pytest.approx(50.0)  # 100 * 1/2

    o4 = report["orders"][3]  # omurio CA
    assert o4["intl_card_fee_usd"] > 0   # 加拿大卡 → 国际信用卡费
    assert o4["currency_conv_fee_usd"] > 0  # CAD 结账 → 货币转换费
    # 三项之和 = 合计 fee
    assert (o4["shopify_base_fee_usd"] + o4["intl_card_fee_usd"]
            + o4["currency_conv_fee_usd"]) == pytest.approx(3.85, abs=0.01)

    # === 每日聚合：1 天，4 单 ===
    assert len(report["daily"]) == 1
    daily = report["daily"][0]
    assert daily["business_date"] == d
    assert daily["orders"] == 4
    # ad_cost 总和 = $50 (newjoy 部分) + $100 (omurio 部分) = $150
    assert daily["ad_cost_usd"] == pytest.approx(150.0, abs=0.01)

    # === 按国家：US (2 单 across 2 sites) + GB (newjoy) + CA (omurio) ===
    countries = {(c["buyer_country"], c["site"]): c for c in report["by_country"]}
    assert ("US", "newjoyloo") in countries
    assert ("US", "Omurio") in countries
    assert ("GB", "newjoyloo") in countries
    assert ("CA", "Omurio") in countries

    # === 站点切片：newjoyloo + Omurio 各一行 ===
    sites = {s["site"]: s for s in report["by_site"]}
    assert "newjoyloo" in sites and "Omurio" in sites
    assert sites["newjoyloo"]["ad_cost_usd"] == pytest.approx(50.0)
    assert sites["Omurio"]["ad_cost_usd"] == pytest.approx(100.0)

    # === 总账：4 单，广告费 $150，product_code 完整 ===
    total = report["total"]
    assert total["orders"] == 4
    assert total["ad_cost_usd"] == pytest.approx(150.0)
    assert total["product_code"] == "fully-automatic-water-blaster-rjc"
    assert total["real_fee_coverage_pct"] == 0.0  # 没传 real_fees


def test_generate_report_incomplete_row_keeps_revenue_blanks_costs(monkeypatch):
    """incomplete 行（含估算值）：UI 收到估算成本 + estimated_fields 标识。

    业务背景：缺采购价 / 物流成本时，calculate_line_profit 用 fallback 比例估算成本
    （purchase = revenue × 10%, shipping = revenue × 20%）；report 层把估算值带出 +
    标注 cost_basis_source / estimated_fields，前端据此渲染 "估算" 标签。
    """
    d = date(2026, 4, 15)
    lines = [
        # 一行 ok（基线）
        {
            "dxm_order_line_id": 1, "business_date": d, "paid_at": datetime(2026, 4, 15, 10),
            "buyer_country": "US", "line_amount_usd": 50.0, "shipping_allocated_usd": 5.0,
            "revenue_usd": 55.0, "shopify_fee_usd": 1.65, "purchase_usd": 8.0,
            "shipping_cost_usd": 5.0, "return_reserve_usd": 0.55, "profit_old_usd": 30.0,
            "shopify_tier": "A", "status": "ok",
            "missing_fields": "[]", "cost_basis": '{"estimated_fields": []}',
            "dxm_package_id": "P1", "extended_order_id": "#1001", "site_code": "newjoy",
            "product_sku": "SKU-A", "product_display_sku": "SKU-A", "product_name": "ARP9",
            "quantity": 1, "unit_price": 50.0, "line_amount_native": 50.0,
            "order_amount_native": 55.0, "order_currency": "USD", "platform": "Shopify",
        },
        # 一行 incomplete-estimated（缺采购价，DB 已经存了估算值）
        {
            "dxm_order_line_id": 2, "business_date": d, "paid_at": datetime(2026, 4, 15, 11),
            "buyer_country": "GB",
            "line_amount_usd": 60.0, "shipping_allocated_usd": 6.0, "revenue_usd": 66.0,
            "shopify_fee_usd": 3.30,
            "purchase_usd": 6.6,   # = 66 × 10% 估算值
            "shipping_cost_usd": 5.0,
            "return_reserve_usd": 0.66,
            "profit_old_usd": None,
            "shopify_tier": "D", "status": "incomplete",
            "missing_fields": '["purchase_price"]',
            "cost_basis": '{"estimated_fields": ["purchase"], "purchase_fallback_ratio": 0.10}',
            "dxm_package_id": "P2", "extended_order_id": "#1002", "site_code": "newjoy",
            "product_sku": "SKU-B", "product_display_sku": "SKU-B", "product_name": "Insect Set",
            "quantity": 1, "unit_price": 60.0, "line_amount_native": 60.0,
            "order_amount_native": 66.0, "order_currency": "GBP", "platform": "Shopify",
        },
    ]
    site_units = {(d, "newjoy"): 2}
    account_spend = {(d, "2110407576446225"): 20.0}
    _setup_mock_db(monkeypatch, lines=lines, site_units=site_units, account_spend=account_spend)

    report = ppr.generate_report(product_id=427, date_from=d, date_to=d)

    o_ok, o_inc = report["orders"][0], report["orders"][1]
    # ok 行：cost_basis_source = real，estimated_fields 空
    assert o_ok["cost_basis_source"] == "real"
    assert o_ok["estimated_fields"] == []
    assert o_ok["purchase_cost_usd"] == pytest.approx(8.0)

    # incomplete 行：估算值有数字，标记 partial_estimated（仅 purchase 估算）
    assert o_inc["status"] == "incomplete"
    assert o_inc["revenue_usd"] == pytest.approx(66.0)
    assert o_inc["purchase_cost_usd"] == pytest.approx(6.6)  # 估算值带出
    assert o_inc["shipping_cost_usd"] == pytest.approx(5.0)
    assert o_inc["profit_usd"] is not None
    assert o_inc["cost_basis_source"] == "partial_estimated"
    assert o_inc["estimated_fields"] == ["purchase"]
    assert o_inc["ad_cost_recalc_usd"] == pytest.approx(10.0)

    # 总账：incomplete 行也参与 sum
    assert report["total"]["revenue_usd"] == pytest.approx(121.0)  # 55 + 66
    assert report["total"]["purchase_usd"] == pytest.approx(14.6)   # 8 + 6.6
    assert report["total"]["incomplete_lines"] == 1
    assert report["total"]["incomplete_pct"] == 50.0  # 1 / 2
    assert report["total"]["fallback_purchase_ratio_pct"] == 10.0
    assert report["total"]["fallback_shipping_ratio_pct"] == 20.0


def test_generate_xlsx_produces_valid_bytes(monkeypatch):
    """端到端验证 Excel 生成：能产出 4-sheet xlsx 字节流。"""
    d = date(2026, 4, 15)
    lines = [{
        "dxm_order_line_id": 1, "business_date": d, "paid_at": datetime(2026, 4, 15, 10),
        "buyer_country": "US", "line_amount_usd": 50.0, "shipping_allocated_usd": 5.0,
        "revenue_usd": 55.0, "shopify_fee_usd": 1.65, "purchase_usd": 8.0,
        "shipping_cost_usd": 5.0, "return_reserve_usd": 0.55, "profit_old_usd": 30.0,
        "shopify_tier": "A", "status": "ok",
        "dxm_package_id": "P1", "extended_order_id": "#1001", "site_code": "newjoy",
        "product_sku": "SKU-A", "product_display_sku": "SKU-A", "product_name": "ARP9",
        "quantity": 1, "unit_price": 50.0, "line_amount_native": 50.0,
        "order_amount_native": 55.0, "order_currency": "USD", "platform": "Shopify",
    }]
    _setup_mock_db(
        monkeypatch,
        lines=lines,
        site_units={(d, "newjoy"): 1},
        account_spend={(d, "2110407576446225"): 10.0},
    )

    report = ppr.generate_report(product_id=427, date_from=d, date_to=d)
    xlsx_bytes = ppr.generate_xlsx(report)

    # xlsx 是 zip-based 格式，magic bytes 是 PK\x03\x04
    assert xlsx_bytes[:4] == b"PK\x03\x04"
    assert len(xlsx_bytes) > 1000  # 4 sheet 至少几 KB


# ---------------------------------------------------------------------------
# 4. site 完整口径
# ---------------------------------------------------------------------------
def test_site_full_name_mapping():
    assert ppr._site_full("newjoy") == "newjoyloo"
    assert ppr._site_full("omurio") == "Omurio"
    assert ppr._site_full(None) == "(未知)"
    assert ppr._site_full("unknown_x") == "unknown_x"  # fallback 原样
