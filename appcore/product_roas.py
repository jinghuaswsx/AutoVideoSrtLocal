from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


FEE_RATE = Decimal("0.10")


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("value must be numeric") from exc


def _break_even_roas(
    price: Decimal | None,
    purchase: Decimal | None,
    packet_cost: Decimal | None,
) -> float | None:
    if price is None or purchase is None or packet_cost is None:
        return None
    available_ad_spend = price * (Decimal("1") - FEE_RATE) - purchase - packet_cost
    if available_ad_spend <= 0:
        return None
    return float(price / available_ad_spend)


def calculate_break_even_roas(
    *,
    purchase_price: Any,
    estimated_packet_cost: Any,
    actual_packet_cost: Any,
    standalone_price: Any,
) -> dict[str, float | str | None]:
    purchase = decimal_or_none(purchase_price)
    estimated_packet = decimal_or_none(estimated_packet_cost)
    actual_packet = decimal_or_none(actual_packet_cost)
    price = decimal_or_none(standalone_price)

    estimated_roas = _break_even_roas(price, purchase, estimated_packet)
    actual_roas = _break_even_roas(price, purchase, actual_packet)
    use_actual = actual_packet is not None

    return {
        "estimated_roas": estimated_roas,
        "actual_roas": actual_roas,
        "effective_basis": "actual" if use_actual else "estimated",
        "effective_roas": actual_roas if use_actual else estimated_roas,
    }
