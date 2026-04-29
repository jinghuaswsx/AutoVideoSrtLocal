from datetime import datetime

import pytest


def test_meta_daily_final_business_date_uses_16_bj_cutover():
    from tools import meta_daily_final_sync

    assert (
        meta_daily_final_sync.completed_meta_business_date(datetime(2026, 4, 30, 15, 59, 59)).isoformat()
        == "2026-04-28"
    )
    assert (
        meta_daily_final_sync.completed_meta_business_date(datetime(2026, 4, 30, 16, 0, 0)).isoformat()
        == "2026-04-29"
    )


def test_roi_meta_realtime_channel_aliases():
    from tools import roi_hourly_sync

    assert roi_hourly_sync._normalize_meta_sync_channel(None) == "browser"
    assert roi_hourly_sync._normalize_meta_sync_channel("ads_manager") == "browser"
    assert roi_hourly_sync._normalize_meta_sync_channel("graph_api") == "api"
    assert roi_hourly_sync._normalize_meta_sync_channel("off") == "none"
    with pytest.raises(ValueError, match="Unsupported Meta sync channel"):
        roi_hourly_sync._normalize_meta_sync_channel("spreadsheet")


def test_roi_meta_api_purchase_metric_prefers_known_action_types():
    from tools import roi_hourly_sync

    assert roi_hourly_sync._extract_purchase_metric([
        {"action_type": "link_click", "value": "9"},
        {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "3"},
        {"action_type": "purchase.custom", "value": "99"},
    ]) == 3.0
    assert roi_hourly_sync._extract_purchase_metric([
        {"action_type": "custom_purchase_event", "value": "7"},
    ]) == 7.0
