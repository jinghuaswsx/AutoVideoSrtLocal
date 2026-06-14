from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable

from config import Config

from .shopify_fee import (
    estimate_fee_for_buyer_country,
    infer_presentment_currency_from_country,
)
from .shopify_fee_dynamic import (
    FIXED_FEE_PER_ORDER,
    load_best_fee_rate_snapshot,
    region_for_presentment_currency,
    source_prefix_for_store_code,
)


FEE_SOURCE_ACTUAL_PAYMENT = "actual_payment"
FEE_SOURCE_DYNAMIC_REGION_RATE = "dynamic_region_rate"
FEE_SOURCE_STRATEGY_C_FALLBACK = "strategy_c_fallback"
FEE_SOURCE_LEGACY_STRATEGY_C = "legacy_strategy_c"
STRATEGY_VERSION = "dynamic_shopify_fee_v1"


def _facade():
    return sys.modules[__package__]


def query(*args, **kwargs):
    return _facade().query(*args, **kwargs)


def _round_money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


_DYNAMIC_FEE_TOGGLE_KEY = "shopify_dynamic_fee_enabled"
_TOGGLE_CACHE_TTL = 30.0
_toggle_lock = threading.Lock()
_toggle_cache = {"value": None, "fetched_at": 0.0, "primed": False}


def _read_dynamic_fee_toggle() -> str | None:
    """读 system_settings.shopify_dynamic_fee_enabled，进程内缓存 30s。

    DB 异常返回 None（回退 env/config），不抛错。热路径（每单调用），缓存避免每单查 DB。
    """
    now = time.monotonic()
    with _toggle_lock:
        if _toggle_cache["primed"] and now - _toggle_cache["fetched_at"] < _TOGGLE_CACHE_TTL:
            return _toggle_cache["value"]
    try:
        from appcore.settings import get_setting
        value = get_setting(_DYNAMIC_FEE_TOGGLE_KEY)
    except Exception:
        value = None
    with _toggle_lock:
        _toggle_cache.update(value=value, fetched_at=now, primed=True)
    return value


def invalidate_dynamic_fee_toggle_cache() -> None:
    """保存设置后由路由调用，立即失效本进程缓存（其他 worker 靠 TTL 收敛）。"""
    with _toggle_lock:
        _toggle_cache["primed"] = False


def _parse_effective_at() -> datetime | None:
    raw = os.getenv("SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT")
    if raw is None:
        raw = getattr(Config, "SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT", None)
    raw = str(raw or "").strip()
    if not raw:
        return None

    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def is_dynamic_fee_effective(order_time: datetime | None) -> bool:
    toggle = _read_dynamic_fee_toggle()
    if toggle == "0":
        return False  # UI 显式关闭 → 全部策略 C
    if toggle == "1":
        return order_time is not None  # UI 显式开启 → 全量真实优先（忽略 env/config 日期）
    # toggle 未设 → 回退现有 env/config effective_at 逻辑
    effective_at = _parse_effective_at()
    if effective_at is None or order_time is None:
        return False

    comparable = order_time
    if comparable.tzinfo is not None:
        comparable = comparable.astimezone(timezone.utc).replace(tzinfo=None)
    return comparable >= effective_at


def _source_csv_filter_for_store(site_code: str | None) -> tuple[str | None, tuple[Any, ...]]:
    prefix = source_prefix_for_store_code(site_code)
    if not prefix:
        return None, ()
    return " AND LEFT(LOWER(source_csv), %s) = %s", (len(prefix), prefix)


def _preferred_order_names(order_names: Iterable[str | None]) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for raw in order_names:
        name = str(raw or "").strip()
        if not name:
            continue
        candidates = [name, name[1:] if name.startswith("#") else f"#{name}"]
        for candidate in candidates:
            if candidate and candidate not in seen:
                seen.add(candidate)
                values.append(candidate)
    return values


def _load_actual_payment_fee(
    order_names: Iterable[str | None],
    *,
    site_code: str | None,
) -> dict[str, Any] | None:
    names = _preferred_order_names(order_names)
    if not names:
        return None

    placeholders = ", ".join(["%s"] * len(names))
    source_filter, source_params = _source_csv_filter_for_store(site_code)
    if source_filter is None:
        return None
    rows = query(
        f"""
        SELECT
            order_name,
            SUM(ABS(fee_usd)) AS fee_usd,
            GROUP_CONCAT(id ORDER BY id) AS transaction_ids
        FROM shopify_payments_transactions
        WHERE type = 'charge'
          AND order_name IN ({placeholders})
          {source_filter}
        GROUP BY order_name
        """,
        tuple(names) + source_params,
    )
    if not rows:
        return None

    rows_by_name = {str(row.get("order_name") or ""): row for row in rows}
    for name in names:
        row = rows_by_name.get(name)
        if row is None:
            continue
        fee = _to_decimal(row.get("fee_usd"))
        if fee <= 0:
            continue
        transaction_ids = [
            part.strip()
            for part in str(row.get("transaction_ids") or "").split(",")
            if part.strip()
        ]
        return {"fee_usd": _round_money(fee), "transaction_ids": transaction_ids}
    return None


def _strategy_c_result(
    *,
    amount: Any,
    buyer_country: str | None,
    source: str,
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    estimate = estimate_fee_for_buyer_country(amount, buyer_country)
    presentment_currency = infer_presentment_currency_from_country(buyer_country)
    amount_d = _to_decimal(amount)
    fee_usd = float(estimate["fee"])
    basis: dict[str, Any] = {
        "strategy_version": STRATEGY_VERSION,
        "order_total_revenue_usd": float(amount_d),
        "order_fee_usd": fee_usd,
    }
    if fallback_reason:
        basis["fallback_reason"] = fallback_reason

    return {
        "shopify_fee_usd": fee_usd,
        "shopify_tier": estimate.get("tier"),
        "presentment_currency": presentment_currency,
        "shopify_fee_source": source,
        "shopify_fee_rate": None,
        "shopify_fee_rate_region": region_for_presentment_currency(presentment_currency),
        "shopify_fee_rate_window_start": None,
        "shopify_fee_rate_window_end": None,
        "shopify_fee_basis": basis,
    }


def resolve_shopify_fee_for_order(
    *,
    amount: Any,
    buyer_country: str | None,
    site_code: str | None,
    order_names: Iterable[str | None],
    order_time: datetime | None,
) -> dict[str, Any]:
    if not is_dynamic_fee_effective(order_time):
        return _strategy_c_result(
            amount=amount,
            buyer_country=buyer_country,
            source=FEE_SOURCE_LEGACY_STRATEGY_C,
            fallback_reason="dynamic_fee_not_effective",
        )

    presentment_currency = infer_presentment_currency_from_country(buyer_country)
    region = region_for_presentment_currency(presentment_currency)
    amount_d = _to_decimal(amount)

    actual = _load_actual_payment_fee(order_names, site_code=site_code)
    if actual is not None:
        fee_usd = actual["fee_usd"]
        return {
            "shopify_fee_usd": fee_usd,
            "shopify_tier": FEE_SOURCE_ACTUAL_PAYMENT,
            "presentment_currency": presentment_currency,
            "shopify_fee_source": FEE_SOURCE_ACTUAL_PAYMENT,
            "shopify_fee_rate": None
            if amount_d <= 0
            else float((_to_decimal(fee_usd) / amount_d).quantize(Decimal("0.00000001"))),
            "shopify_fee_rate_region": region,
            "shopify_fee_rate_window_start": None,
            "shopify_fee_rate_window_end": None,
            "shopify_fee_basis": {
                "strategy_version": STRATEGY_VERSION,
                "order_total_revenue_usd": float(amount_d),
                "order_fee_usd": fee_usd,
                "matched_payment_transaction_ids": actual["transaction_ids"],
            },
        }

    snapshot = load_best_fee_rate_snapshot(site_code, region)
    if snapshot is not None:
        fixed_fee = _to_decimal(snapshot.get("fixed_fee_per_order") or FIXED_FEE_PER_ORDER)
        fee = amount_d * _to_decimal(snapshot["variable_rate"]) + fixed_fee
        fee_usd = _round_money(fee)
        return {
            "shopify_fee_usd": fee_usd,
            "shopify_tier": FEE_SOURCE_DYNAMIC_REGION_RATE,
            "presentment_currency": presentment_currency,
            "shopify_fee_source": FEE_SOURCE_DYNAMIC_REGION_RATE,
            "shopify_fee_rate": float(snapshot["effective_rate"]),
            "shopify_fee_rate_region": region,
            "shopify_fee_rate_window_start": snapshot["window_start_date"],
            "shopify_fee_rate_window_end": snapshot["window_end_date"],
            "shopify_fee_basis": {
                "strategy_version": STRATEGY_VERSION,
                "order_total_revenue_usd": float(amount_d),
                "order_fee_usd": fee_usd,
                "fixed_fee_usd": float(fixed_fee),
                "snapshot_id": snapshot["id"],
                "snapshot_sample_status": snapshot.get("sample_status"),
            },
        }

    return _strategy_c_result(
        amount=amount,
        buyer_country=buyer_country,
        source=FEE_SOURCE_STRATEGY_C_FALLBACK,
        fallback_reason="no_actual_payment_or_dynamic_snapshot",
    )
