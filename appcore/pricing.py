from __future__ import annotations

from decimal import Decimal
import time
from typing import Literal

from appcore.db import query


_CACHE_TTL = 60
_PRECISION = Decimal("0.000001")
_cache: dict[str, object] = {"expire": 0.0, "data": {}}


def _as_decimal(value) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _load_prices() -> dict[tuple[str, str], dict]:
    now = time.time()
    expire = float(_cache.get("expire", 0.0) or 0.0)
    if now < expire:
        return _cache["data"]  # type: ignore[return-value]

    rows = query(
        """
        SELECT provider, model, units_type,
               unit_input_cny, unit_output_cny, unit_flat_cny
        FROM ai_model_prices
        """
    )
    data = {(row["provider"], row["model"]): row for row in rows}
    _cache["data"] = data
    _cache["expire"] = now + _CACHE_TTL
    return data


# AI Studio、Vertex 和 Vertex ADC 对同名 Gemini 模型定价相同，这里维护一次即可
# 查找顺序：自家精确 → 同组精确 → 自家通配 → 同组通配
_GEMINI_PRICING_GROUP = ("gemini_aistudio", "gemini_vertex", "gemini_vertex_adc")


def _lookup(provider: str, model: str) -> dict | None:
    data = _load_prices()
    peer_providers = []
    if provider in _GEMINI_PRICING_GROUP:
        peer_providers = [p for p in _GEMINI_PRICING_GROUP if p != provider]
    keys: list[tuple[str, str]] = [(provider, model)]
    keys.extend((peer, model) for peer in peer_providers)
    keys.append((provider, "*"))
    keys.extend((peer, "*") for peer in peer_providers)
    for key in keys:
        row = data.get(key)
        if row is not None:
            return row
    return None


def compute_cost_cny(
    *,
    provider: str,
    model: str,
    units_type: str,
    input_tokens: int | None,
    output_tokens: int | None,
    request_units: int | None,
) -> tuple[Decimal | None, Literal["pricebook", "unknown"]]:
    row = _lookup(provider, model)
    if not row:
        return None, "unknown"

    try:
        if units_type == "tokens":
            unit_input = _as_decimal(row.get("unit_input_cny"))
            unit_output = _as_decimal(row.get("unit_output_cny"))
            if unit_input is None or unit_output is None:
                return None, "unknown"
            if input_tokens is None or output_tokens is None:
                return None, "unknown"
            cost = Decimal(input_tokens) * unit_input + Decimal(output_tokens) * unit_output
            return cost.quantize(_PRECISION), "pricebook"

        unit_flat = _as_decimal(row.get("unit_flat_cny"))
        if unit_flat is None or request_units is None:
            return None, "unknown"
        cost = Decimal(request_units) * unit_flat
        return cost.quantize(_PRECISION), "pricebook"
    except Exception:
        return None, "unknown"


def invalidate_cache() -> None:
    _cache["expire"] = 0.0
