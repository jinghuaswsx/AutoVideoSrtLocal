from contextlib import contextmanager, nullcontext
from datetime import datetime

import pytest

from appcore import parcel_cost_suggest as mod


def _make_query_rows(rows):
    def fake_query(sql, params=None):
        return list(rows)
    return fake_query


def test_pick_primary_sku_and_shop_returns_top_frequency(monkeypatch):
    monkeypatch.setattr(mod, "query", _make_query_rows([
        {"product_sku": "45697043366061", "dxm_shop_id": "8477915", "cnt": 1340},
    ]))
    sku, shop = mod.pick_primary_sku_and_shop(317)
    assert sku == "45697043366061"
    assert shop == "8477915"


def test_pick_primary_sku_and_shop_raises_when_no_orders(monkeypatch):
    monkeypatch.setattr(mod, "query", _make_query_rows([]))
    with pytest.raises(mod.ParcelCostSuggestError, match="no_orders"):
        mod.pick_primary_sku_and_shop(317)


def test_filter_logistic_fees_keeps_only_target_sku_with_numeric_fee():
    orders = [
        {"productList": [{"displaySku": "45697043366061"}], "logisticFee": 61.606},
        {"productList": [{"productSku": "45697043366061"}], "logisticFee": "51.78"},
        {"productList": [{"sku": "45697043366061"}], "logisticFee": 60},
        {"productList": [{"displaySku": "45697043366061"}], "logisticFee": "--"},
        {"productList": [{"displaySku": "45697043366061"}], "logisticFee": None},
        {"productList": [{"displaySku": "45697043398829"}], "logisticFee": 81.0},
        {"productList": [], "logisticFee": 99.0},
        {"productList": [{"displaySku": "45697043366061"}], "logisticFee": "garbage"},
    ]
    fees = mod.filter_logistic_fees(orders, "45697043366061")
    assert fees == [61.606, 51.78, 60.0]


def test_filter_logistic_fees_handles_malformed_orders():
    orders = [
        {},
        {"productList": "not-a-list", "logisticFee": 1},
        {"productList": [None, "string", {"displaySku": "x"}], "logisticFee": 2},
    ]
    assert mod.filter_logistic_fees(orders, "x") == [2.0]


def test_compute_suggestion_basic_stats():
    out = mod.compute_suggestion([61.606, 61.606, 51.78, 61.606, 70.0])
    assert out["sample_size"] == 5
    assert out["median"] == 61.61
    assert out["min"] == 51.78
    assert out["max"] == 70.0
    assert out["mean"] == pytest.approx(round((61.606 * 3 + 51.78 + 70.0) / 5, 2))


def test_compute_suggestion_empty_list():
    out = mod.compute_suggestion([])
    assert out == {"sample_size": 0, "median": None, "mean": None, "min": None, "max": None}


def test_build_order_payload_window_and_shop_passthrough():
    payload = mod.build_order_payload(
        page_no=3,
        page_size=200,
        shop_id="8477915",
        start_time=datetime(2026, 4, 4),
        end_time=datetime(2026, 5, 2),
    )
    assert payload["pageNo"] == "3"
    assert payload["pageSize"] == "200"
    assert payload["shopId"] == "8477915"
    assert payload["startTime"] == "2026-04-04 00:00:00"
    assert payload["endTime"] == "2026-05-02 23:59:59"
    assert payload["isSearch"] == "1"
    assert payload["orderField"] == "order_pay_time"


def test_fetch_orders_in_window_paginates_until_short_page():
    page_responses = [
        {"code": 0, "data": {"page": {"list": [{"id": str(i), "productList": []} for i in range(200)]}}},
        {"code": 0, "data": {"page": {"list": [{"id": "x1", "productList": []}, {"id": "x2", "productList": []}]}}},
    ]
    calls = []

    def fake_post(page_obj, url, payload):
        calls.append({"page_no": payload["pageNo"], "shopId": payload["shopId"]})
        return page_responses[len(calls) - 1]

    fake_post_handle = fake_post
    orig = mod.post_form_via_page
    try:
        mod.post_form_via_page = fake_post_handle
        orders = mod.fetch_orders_in_window(
            page=object(),
            shop_id="8477915",
            start_time=datetime(2026, 4, 4),
            end_time=datetime(2026, 5, 2),
            max_pages=10,
        )
    finally:
        mod.post_form_via_page = orig
    assert len(orders) == 202
    assert [c["page_no"] for c in calls] == ["1", "2"]


def test_fetch_orders_in_window_raises_on_dxm_error():
    def fake_post(page_obj, url, payload):
        return {"code": 1, "msg": "login_expired"}

    orig = mod.post_form_via_page
    try:
        mod.post_form_via_page = fake_post
        with pytest.raises(mod.ParcelCostSuggestError, match="login_expired"):
            mod.fetch_orders_in_window(
                page=object(),
                shop_id="8477915",
                start_time=datetime(2026, 4, 4),
                end_time=datetime(2026, 5, 2),
            )
    finally:
        mod.post_form_via_page = orig


def test_suggest_parcel_cost_end_to_end(monkeypatch):
    monkeypatch.setattr(mod, "query", _make_query_rows([
        {"product_sku": "45697043366061", "dxm_shop_id": "8477915", "cnt": 1340},
    ]))
    monkeypatch.setattr(mod, "browser_automation_lock", lambda **kw: nullcontext())

    fake_orders = [
        {"productList": [{"displaySku": "45697043366061"}], "logisticFee": 61.606}
        for _ in range(10)
    ] + [
        {"productList": [{"displaySku": "45697043366061"}], "logisticFee": 51.78}
    ]

    def fake_fetch(page, *, shop_id, start_time, end_time, **kw):
        assert shop_id == "8477915"
        # Window respects 2-day settlement delay, plus 30-day lookback
        assert (end_time - start_time).days == 30
        return fake_orders

    monkeypatch.setattr(mod, "fetch_orders_in_window", fake_fetch)

    @contextmanager
    def fake_page_provider():
        yield object()

    fixed_now = datetime(2026, 5, 4, 12, 0, 0)
    out = mod.suggest_parcel_cost(
        317,
        days=30,
        now_func=lambda: fixed_now,
        page_provider=fake_page_provider,
    )
    assert out["product_id"] == 317
    assert out["sku"] == "45697043366061"
    assert out["dxm_shop_id"] == "8477915"
    assert out["lookback_days"] == 30
    assert out["window_end"] == "2026-05-02"
    assert out["window_start"] == "2026-04-02"
    assert out["orders_pulled"] == 11
    assert out["sample_size"] == 11
    assert out["median"] == 61.61
    assert out["min"] == 51.78
    assert out["max"] == 61.61
