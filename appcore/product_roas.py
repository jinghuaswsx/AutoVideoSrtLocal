from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any


log = logging.getLogger(__name__)

FEE_RATE = Decimal("0.10")
DEFAULT_RMB_PER_USD = Decimal("6.83")
RMB_PER_USD_SETTING_KEY = "material_roas_rmb_per_usd"


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("value must be numeric") from exc


def normalize_rmb_per_usd(value: Any) -> Decimal:
    rate = decimal_or_none(value)
    if rate is None or rate <= 0:
        return DEFAULT_RMB_PER_USD
    return rate


def validate_rmb_per_usd(value: Any) -> Decimal:
    rate = decimal_or_none(value)
    if rate is None or rate <= 0:
        raise ValueError("RMB/USD 汇率必须是正数")
    return rate


def format_decimal(value: Any) -> str:
    decimal_value = normalize_rmb_per_usd(value)
    text = format(decimal_value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def get_configured_rmb_per_usd() -> Decimal:
    try:
        from appcore import settings as system_settings

        return normalize_rmb_per_usd(system_settings.get_setting(RMB_PER_USD_SETTING_KEY))
    except Exception:
        log.warning("[product-roas] failed to read RMB/USD rate setting", exc_info=True)
        return DEFAULT_RMB_PER_USD


def _break_even_roas(
    price: Decimal | None,
    shipping_fee: Decimal | None,
    purchase: Decimal | None,
    packet_cost: Decimal | None,
    rmb_per_usd: Decimal,
) -> float | None:
    if price is None or purchase is None or packet_cost is None:
        return None
    revenue = price + (shipping_fee or Decimal("0"))
    purchase_usd = purchase / rmb_per_usd
    packet_cost_usd = packet_cost / rmb_per_usd
    available_ad_spend = revenue * (Decimal("1") - FEE_RATE) - purchase_usd - packet_cost_usd
    if available_ad_spend <= 0:
        return None
    return float(revenue / available_ad_spend)


def calculate_break_even_roas(
    *,
    purchase_price: Any,
    estimated_packet_cost: Any,
    actual_packet_cost: Any,
    standalone_price: Any,
    standalone_shipping_fee: Any = None,
    rmb_per_usd: Any = DEFAULT_RMB_PER_USD,
) -> dict[str, float | str | None]:
    purchase = decimal_or_none(purchase_price)
    estimated_packet = decimal_or_none(estimated_packet_cost)
    actual_packet = decimal_or_none(actual_packet_cost)
    price = decimal_or_none(standalone_price)
    shipping_fee = decimal_or_none(standalone_shipping_fee)
    rate = normalize_rmb_per_usd(rmb_per_usd)

    estimated_roas = _break_even_roas(price, shipping_fee, purchase, estimated_packet, rate)
    actual_roas = _break_even_roas(price, shipping_fee, purchase, actual_packet, rate)
    use_actual = actual_packet is not None

    return {
        "estimated_roas": estimated_roas,
        "actual_roas": actual_roas,
        "effective_basis": "actual" if use_actual else "estimated",
        "effective_roas": actual_roas if use_actual else estimated_roas,
        "rmb_per_usd": float(rate),
    }
