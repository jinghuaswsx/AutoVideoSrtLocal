from __future__ import annotations

from decimal import Decimal
import importlib

import pytest


@pytest.fixture
def pricing_module(monkeypatch):
    module = importlib.import_module("appcore.pricing")
    module = importlib.reload(module)
    module.invalidate_cache()
    return module


def test_compute_cost_tokens_uses_input_and_output_rates(pricing_module, monkeypatch):
    rows = [{
        "provider": "gemini_aistudio",
        "model": "gemini-2.5-flash",
        "units_type": "tokens",
        "unit_input_cny": Decimal("0.001"),
        "unit_output_cny": Decimal("0.002"),
        "unit_flat_cny": None,
    }]
    monkeypatch.setattr(pricing_module, "query", lambda sql: rows)

    cost, source = pricing_module.compute_cost_cny(
        provider="gemini_aistudio",
        model="gemini-2.5-flash",
        units_type="tokens",
        input_tokens=1000,
        output_tokens=500,
        request_units=None,
    )

    assert cost == Decimal("2.000000")
    assert source == "pricebook"


@pytest.mark.parametrize(
    ("units_type", "request_units", "unit_flat_cny", "expected"),
    [
        ("chars", 1000, Decimal("0.00016"), Decimal("0.160000")),
        ("seconds", 15, Decimal("0.014"), Decimal("0.210000")),
        ("images", 2, Decimal("0.2652"), Decimal("0.530400")),
    ],
)
def test_compute_cost_flat_units(pricing_module, monkeypatch, units_type, request_units, unit_flat_cny, expected):
    rows = [{
        "provider": "provider_x",
        "model": "*",
        "units_type": units_type,
        "unit_input_cny": None,
        "unit_output_cny": None,
        "unit_flat_cny": unit_flat_cny,
    }]
    monkeypatch.setattr(pricing_module, "query", lambda sql: rows)

    cost, source = pricing_module.compute_cost_cny(
        provider="provider_x",
        model="arbitrary-model",
        units_type=units_type,
        input_tokens=None,
        output_tokens=None,
        request_units=request_units,
    )

    assert cost == expected
    assert source == "pricebook"


def test_exact_match_beats_wildcard(pricing_module, monkeypatch):
    rows = [
        {
            "provider": "gemini_aistudio",
            "model": "*",
            "units_type": "tokens",
            "unit_input_cny": Decimal("0.1"),
            "unit_output_cny": Decimal("0.1"),
            "unit_flat_cny": None,
        },
        {
            "provider": "gemini_aistudio",
            "model": "gemini-2.5-flash",
            "units_type": "tokens",
            "unit_input_cny": Decimal("0.001"),
            "unit_output_cny": Decimal("0.002"),
            "unit_flat_cny": None,
        },
    ]
    monkeypatch.setattr(pricing_module, "query", lambda sql: rows)

    cost, source = pricing_module.compute_cost_cny(
        provider="gemini_aistudio",
        model="gemini-2.5-flash",
        units_type="tokens",
        input_tokens=1,
        output_tokens=1,
        request_units=None,
    )

    assert cost == Decimal("0.003000")
    assert source == "pricebook"


def test_wildcard_match_is_used_when_exact_missing(pricing_module, monkeypatch):
    rows = [{
        "provider": "elevenlabs",
        "model": "*",
        "units_type": "chars",
        "unit_input_cny": None,
        "unit_output_cny": None,
        "unit_flat_cny": Decimal("0.000165"),
    }]
    monkeypatch.setattr(pricing_module, "query", lambda sql: rows)

    cost, source = pricing_module.compute_cost_cny(
        provider="elevenlabs",
        model="custom_voice_xxx",
        units_type="chars",
        input_tokens=None,
        output_tokens=None,
        request_units=1000,
    )

    assert cost == Decimal("0.165000")
    assert source == "pricebook"


def test_missing_price_returns_unknown(pricing_module, monkeypatch):
    monkeypatch.setattr(pricing_module, "query", lambda sql: [])

    cost, source = pricing_module.compute_cost_cny(
        provider="missing_provider",
        model="missing_model",
        units_type="tokens",
        input_tokens=1,
        output_tokens=1,
        request_units=None,
    )

    assert cost is None
    assert source == "unknown"


def test_missing_token_values_return_unknown(pricing_module, monkeypatch):
    rows = [{
        "provider": "gemini_aistudio",
        "model": "gemini-2.5-flash",
        "units_type": "tokens",
        "unit_input_cny": Decimal("0.001"),
        "unit_output_cny": Decimal("0.002"),
        "unit_flat_cny": None,
    }]
    monkeypatch.setattr(pricing_module, "query", lambda sql: rows)

    cost, source = pricing_module.compute_cost_cny(
        provider="gemini_aistudio",
        model="gemini-2.5-flash",
        units_type="tokens",
        input_tokens=None,
        output_tokens=500,
        request_units=None,
    )

    assert cost is None
    assert source == "unknown"


def test_cache_skips_second_db_query(pricing_module, monkeypatch):
    calls = []

    def fake_query(sql):
        calls.append(sql)
        return [{
            "provider": "gemini_aistudio",
            "model": "gemini-2.5-flash",
            "units_type": "tokens",
            "unit_input_cny": Decimal("0.001"),
            "unit_output_cny": Decimal("0.002"),
            "unit_flat_cny": None,
        }]

    monkeypatch.setattr(pricing_module, "query", fake_query)

    first = pricing_module.compute_cost_cny(
        provider="gemini_aistudio",
        model="gemini-2.5-flash",
        units_type="tokens",
        input_tokens=1,
        output_tokens=1,
        request_units=None,
    )
    second = pricing_module.compute_cost_cny(
        provider="gemini_aistudio",
        model="gemini-2.5-flash",
        units_type="tokens",
        input_tokens=2,
        output_tokens=1,
        request_units=None,
    )

    assert first == (Decimal("0.003000"), "pricebook")
    assert second == (Decimal("0.004000"), "pricebook")
    assert len(calls) == 1


def test_gemini_vertex_falls_back_to_aistudio_exact(pricing_module, monkeypatch):
    """Vertex 精确查询未命中时回落到 AI Studio 同名精确价，避免两边各维护一份。"""
    rows = [{
        "provider": "gemini_aistudio",
        "model": "gemini-3.1-pro-preview",
        "units_type": "tokens",
        "unit_input_cny": Decimal("0.0000578"),
        "unit_output_cny": Decimal("0.0002312"),
        "unit_flat_cny": None,
    }]
    monkeypatch.setattr(pricing_module, "query", lambda sql: rows)

    cost, source = pricing_module.compute_cost_cny(
        provider="gemini_vertex",
        model="gemini-3.1-pro-preview",
        units_type="tokens",
        input_tokens=1000,
        output_tokens=1000,
        request_units=None,
    )

    assert cost == Decimal("0.289000")
    assert source == "pricebook"


def test_gemini_aistudio_falls_back_to_vertex_exact(pricing_module, monkeypatch):
    """对称方向：AI Studio 未命中时回落到 Vertex 同名精确价。"""
    rows = [{
        "provider": "gemini_vertex",
        "model": "gemini-3-pro-image-preview",
        "units_type": "images",
        "unit_input_cny": None,
        "unit_output_cny": None,
        "unit_flat_cny": Decimal("0.2652"),
    }]
    monkeypatch.setattr(pricing_module, "query", lambda sql: rows)

    cost, source = pricing_module.compute_cost_cny(
        provider="gemini_aistudio",
        model="gemini-3-pro-image-preview",
        units_type="images",
        input_tokens=None,
        output_tokens=None,
        request_units=1,
    )

    assert cost == Decimal("0.265200")
    assert source == "pricebook"


def test_gemini_pair_exact_beats_self_wildcard(pricing_module, monkeypatch):
    """对家精确应优先于自家通配——通配是真正的最后兜底。"""
    rows = [
        {
            "provider": "gemini_vertex",
            "model": "*",
            "units_type": "tokens",
            "unit_input_cny": Decimal("0.1"),
            "unit_output_cny": Decimal("0.1"),
            "unit_flat_cny": None,
        },
        {
            "provider": "gemini_aistudio",
            "model": "gemini-3.1-pro-preview",
            "units_type": "tokens",
            "unit_input_cny": Decimal("0.001"),
            "unit_output_cny": Decimal("0.002"),
            "unit_flat_cny": None,
        },
    ]
    monkeypatch.setattr(pricing_module, "query", lambda sql: rows)

    cost, source = pricing_module.compute_cost_cny(
        provider="gemini_vertex",
        model="gemini-3.1-pro-preview",
        units_type="tokens",
        input_tokens=1,
        output_tokens=1,
        request_units=None,
    )

    # 用对家精确 0.001 + 0.002，而不是自家通配 0.1 + 0.1
    assert cost == Decimal("0.003000")
    assert source == "pricebook"


def test_non_gemini_provider_does_not_cross_fallback(pricing_module, monkeypatch):
    """回落只对 Gemini 这一对成立，其他 provider 不应跨家查询。"""
    rows = [{
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-4.6",
        "units_type": "tokens",
        "unit_input_cny": Decimal("0.001"),
        "unit_output_cny": Decimal("0.002"),
        "unit_flat_cny": None,
    }]
    monkeypatch.setattr(pricing_module, "query", lambda sql: rows)

    cost, source = pricing_module.compute_cost_cny(
        provider="gemini_aistudio",
        model="anthropic/claude-sonnet-4.6",
        units_type="tokens",
        input_tokens=1,
        output_tokens=1,
        request_units=None,
    )

    assert cost is None
    assert source == "unknown"


def test_invalidate_cache_forces_reload(pricing_module, monkeypatch):
    calls = []

    def fake_query(sql):
        calls.append(sql)
        return [{
            "provider": "gemini_vertex",
            "model": "gemini-3.1-pro-preview",
            "units_type": "tokens",
            "unit_input_cny": Decimal("0.001"),
            "unit_output_cny": Decimal("0.002"),
            "unit_flat_cny": None,
        }]

    monkeypatch.setattr(pricing_module, "query", fake_query)

    pricing_module.compute_cost_cny(
        provider="gemini_vertex",
        model="gemini-3.1-pro-preview",
        units_type="tokens",
        input_tokens=1,
        output_tokens=1,
        request_units=None,
    )
    pricing_module.invalidate_cache()
    pricing_module.compute_cost_cny(
        provider="gemini_vertex",
        model="gemini-3.1-pro-preview",
        units_type="tokens",
        input_tokens=1,
        output_tokens=1,
        request_units=None,
    )

    assert len(calls) == 2


def test_config_exposes_usd_to_cny(monkeypatch):
    monkeypatch.setenv("AUTOVIDEOSRT_DISABLE_DOTENV", "1")

    import config

    cfg = importlib.reload(config)
    assert cfg.USD_TO_CNY == 6.8
