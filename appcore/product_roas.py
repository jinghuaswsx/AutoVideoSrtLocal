from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any


log = logging.getLogger(__name__)

FEE_RATE = Decimal("0.07")
FALLBACK_SHIPPING_FEE_USD = Decimal("7")
PURCHASE_FALLBACK_RATE = Decimal("0.10")
PACKET_FALLBACK_RATE = Decimal("0.20")
DEFAULT_RMB_PER_USD = Decimal("6.83")
RMB_PER_USD_SETTING_KEY = "material_roas_rmb_per_usd"
STANDALONE_PRICE_CENTS_GUARD_MIN = Decimal("100")
STANDALONE_PRICE_CENTS_TOLERANCE = Decimal("0.01")


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


def match_cents_unit_standalone_price(
    standalone_price: Any,
    sku_prices: list[Any],
) -> Decimal | None:
    """Return the matching SKU price when standalone_price looks like cents."""
    price = decimal_or_none(standalone_price)
    if price is None or price < STANDALONE_PRICE_CENTS_GUARD_MIN:
        return None
    cents_unit_price = price / Decimal("100")
    for raw_sku_price in sku_prices:
        try:
            sku_price = decimal_or_none(raw_sku_price)
        except ValueError:
            continue
        if sku_price is None or sku_price <= 0:
            continue
        if abs(sku_price - cents_unit_price) <= STANDALONE_PRICE_CENTS_TOLERANCE:
            return sku_price
    return None


def get_configured_rmb_per_usd() -> Decimal:
    try:
        from appcore import settings as system_settings

        return normalize_rmb_per_usd(system_settings.get_setting(RMB_PER_USD_SETTING_KEY))
    except Exception:
        log.warning("[product-roas] failed to read RMB/USD rate setting", exc_info=True)
        return DEFAULT_RMB_PER_USD


def _revenue_with_shipping(
    price: Decimal | None,
    shipping_fee: Decimal | None,
) -> Decimal | None:
    if price is None:
        return None
    shipping = shipping_fee if shipping_fee is not None else FALLBACK_SHIPPING_FEE_USD
    return price + shipping


def _break_even_roas_from_usd_costs(
    revenue: Decimal | None,
    purchase_usd: Decimal | None,
    packet_cost_usd: Decimal | None,
) -> float | None:
    if revenue is None or purchase_usd is None or packet_cost_usd is None:
        return None
    available_ad_spend = revenue * (Decimal("1") - FEE_RATE) - purchase_usd - packet_cost_usd
    if available_ad_spend <= 0:
        return None
    return float(revenue / available_ad_spend)


def _fallback_cost_usd(revenue: Decimal | None, rate: Decimal) -> Decimal | None:
    if revenue is None:
        return None
    return revenue * rate


def _effective_basis(purchase: Decimal | None, actual_packet: Decimal | None) -> str:
    if purchase is not None and actual_packet is not None:
        return "actual"
    if purchase is None and actual_packet is None:
        return "estimated"
    return "fallback"


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
    decimal_or_none(estimated_packet_cost)
    actual_packet = decimal_or_none(actual_packet_cost)
    price = decimal_or_none(standalone_price)
    shipping_fee = decimal_or_none(standalone_shipping_fee)
    rate = normalize_rmb_per_usd(rmb_per_usd)

    revenue = _revenue_with_shipping(price, shipping_fee)
    shipping_fee_used = shipping_fee if shipping_fee is not None else FALLBACK_SHIPPING_FEE_USD
    shipping_source = "actual" if shipping_fee is not None else "fallback_7usd"

    estimated_purchase_usd = _fallback_cost_usd(revenue, PURCHASE_FALLBACK_RATE)
    estimated_packet_usd = _fallback_cost_usd(revenue, PACKET_FALLBACK_RATE)
    estimated_roas = _break_even_roas_from_usd_costs(
        revenue,
        estimated_purchase_usd,
        estimated_packet_usd,
    )

    if purchase is not None:
        actual_purchase_usd = purchase / rate
        purchase_source = "actual"
    else:
        actual_purchase_usd = estimated_purchase_usd
        purchase_source = "fallback_10pct"

    if actual_packet is not None:
        actual_packet_usd = actual_packet / rate
        packet_source = "actual"
    else:
        actual_packet_usd = estimated_packet_usd
        packet_source = "fallback_20pct"

    actual_roas = _break_even_roas_from_usd_costs(revenue, actual_purchase_usd, actual_packet_usd)
    effective_basis = _effective_basis(purchase, actual_packet)

    return {
        "estimated_roas": estimated_roas,
        "actual_roas": actual_roas,
        "effective_basis": effective_basis,
        "effective_roas": actual_roas if effective_basis != "estimated" else estimated_roas,
        "shipping_fee_used": float(shipping_fee_used),
        "shipping_source": shipping_source,
        "purchase_source": purchase_source,
        "packet_source": packet_source,
        "rmb_per_usd": float(rate),
    }
