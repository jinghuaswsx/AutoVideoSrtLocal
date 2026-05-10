from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from appcore import meta_ad_accounts


def _load_backfill_module():
    spec = importlib.util.spec_from_file_location(
        "_test_meta_ads_backfill_columns",
        Path(__file__).resolve().parents[1] / "scripts" / "run_meta_ads_backfill_range.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_current_accounts_get_account_specific_default_column_presets():
    """Docs-anchor: docs/superpowers/specs/2026-05-09-ads-purchase-value-order-fallback-design.md."""
    cases = [
        ("newjoyloo", "1861285821213497", "1680560372975676"),
        ("Omurio", "1253003326160754", "1645951873103193"),
        ("newjoyloo_old", "2110407576446225", "1658418688523178"),
    ]

    for code, account_id, expected_preset in cases:
        account = meta_ad_accounts._coerce_account({
            "code": code,
            "account_id": account_id,
            "business_id": "biz",
            "csv_prefix": code,
            "store_codes": ["newjoy" if "newjoy" in code.lower() else "omurio"],
            "enabled": True,
        })

        assert account is not None
        assert account.column_preset == expected_preset


def test_known_accounts_replace_unsafe_saved_column_presets():
    cases = [
        ("newjoyloo", "1861285821213497", "111", "1680560372975676"),
        ("newjoyloo", "1861285821213497", "PERFORMANCE", "1680560372975676"),
        ("Omurio", "1253003326160754", "1111", "1645951873103193"),
        ("Omurio", "1253003326160754", "1658418688523178", "1645951873103193"),
        ("newjoyloo_old", "2110407576446225", "1111", "1658418688523178"),
    ]

    for code, account_id, saved_value, expected_preset in cases:
        account = meta_ad_accounts._coerce_account({
            "code": code,
            "account_id": account_id,
            "business_id": "biz",
            "csv_prefix": code,
            "store_codes": ["newjoy" if "newjoy" in code.lower() else "omurio"],
            "enabled": True,
            "column_preset": saved_value,
        })

        assert account is not None
        assert account.column_preset == expected_preset


def test_column_preset_choices_expose_ui_labels_and_real_url_params():
    choices = meta_ad_accounts.column_preset_choices()

    by_value = {choice["value"]: choice for choice in choices}
    assert by_value["1680560372975676"]["label"] == "111"
    assert by_value["1680560372975676"]["recommended_account_codes"] == ["newjoyloo"]
    assert by_value["1645951873103193"]["label"] == "1111"
    assert by_value["1645951873103193"]["recommended_account_codes"] == ["Omurio"]
    assert by_value["1658418688523178"]["label"] == "1111"
    assert by_value["1658418688523178"]["recommended_account_codes"] == ["newjoyloo_old"]


def test_daily_final_metrics_reads_meta_result_value_aliases():
    from tools import meta_daily_final_sync

    metrics = meta_daily_final_sync._common_metrics({
        "成效": "4",
        "已花费金额 (USD)": "$70.97",
        "成效价值": "$84.11",
        "成效广告花费回报": "1.19",
    })

    assert metrics["purchase_value_usd"] == 84.11
    assert metrics["roas_purchase"] == 1.19


def test_realtime_metrics_reads_meta_result_value_aliases():
    from tools import roi_hourly_sync

    value = roi_hourly_sync._meta_purchase_value_from_row({
        "成效价值": "$84.11",
        "成效广告花费回报": "1.19",
    })

    assert value == 84.11


def test_export_csv_validation_accepts_result_value_and_roas_aliases(tmp_path):
    module = _load_backfill_module()
    csv_path = tmp_path / "newjoyloo_campaigns_2026-05-09.csv"
    csv_path.write_text(
        "广告系列名称,已花费金额 (USD),成效,成效价值,成效广告花费回报\n"
        "demo,$70.97,4,$84.11,1.19\n",
        encoding="utf-8",
    )

    report = module.validate_export_csv_has_meta_performance_columns(csv_path)

    assert report["ok"] is True
    assert report["missing"] == []


def test_export_csv_validation_rejects_default_performance_columns(tmp_path):
    module = _load_backfill_module()
    csv_path = tmp_path / "Omurio_campaigns_2026-05-09.csv"
    csv_path.write_text(
        "报告开始日期,报告结束日期,广告系列名称,广告系列投放,成效,成效指标,"
        "单次成效费用,已花费金额 (USD),展示次数,覆盖人数,归因设置\n"
        "2026-05-09,2026-05-09,demo,投放中,4,网站购物,$10,$70,2571,2000,点击后7天\n",
        encoding="utf-8",
    )

    with pytest.raises(module.ExportColumnValidationError, match="column_preset"):
        module.validate_export_csv_or_raise(
            csv_path,
            account_id="1253003326160754",
            level="campaigns",
            day="2026-05-09",
            column_preset="PERFORMANCE",
        )
