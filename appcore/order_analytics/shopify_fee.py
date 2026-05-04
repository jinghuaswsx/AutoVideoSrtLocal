"""Shopify Payments 4 档手续费计算（策略 C 前向预估 + 策略 B 反推校验）。

详细规则见 docs/superpowers/specs/2026-05-04-shopify-payments-fee-rules.md

费率结构（3 个独立组件叠加）：
  base_rate                = 2.5% × amount + $0.30           （所有交易）
  cross_border_rate        = +1.0% × amount                  （发卡国 ≠ 店铺所在国）
  currency_conversion_rate = +1.5% × amount                  （结账币 ≠ 结算币）

四档：
  A 美元本土卡         2.5% + $0.30
  B 美元国际卡         3.5% + $0.30
  C 非美元本土卡       4.0% + $0.30
  D 非美元国际卡       5.0% + $0.30
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any


BASE_RATE = Decimal("0.025")
FIXED_FEE = Decimal("0.30")
CROSS_BORDER_RATE = Decimal("0.010")
CURRENCY_CONVERSION_RATE = Decimal("0.015")

DEFAULT_STORE_COUNTRY = "US"
DEFAULT_SETTLEMENT_CURRENCY = "USD"

# 容差：处理 Shopify 内部 banker's rounding 与 ROUND_HALF_UP 在 .5 边界 ±$0.01 差异
RECONCILIATION_TOLERANCE = Decimal("0.02")


# 国家 → 客户结账币种映射（策略 C 用）
# 业务假设：多数客户用本地货币结账。店小秘 raw_order_json 没有
# presentment_currency 字段，所以从 buyerCountry 反推。
COUNTRY_TO_CURRENCY: dict[str, str] = {
    # 欧元区（19 国 + 克罗地亚 2023 加入共 20 国）
    "AT": "EUR", "BE": "EUR", "CY": "EUR", "DE": "EUR", "EE": "EUR",
    "ES": "EUR", "FI": "EUR", "FR": "EUR", "GR": "EUR", "HR": "EUR",
    "IE": "EUR", "IT": "EUR", "LT": "EUR", "LU": "EUR", "LV": "EUR",
    "MT": "EUR", "NL": "EUR", "PT": "EUR", "SI": "EUR", "SK": "EUR",
    # 主要单独货币
    "GB": "GBP", "US": "USD", "AU": "AUD", "CA": "CAD", "NZ": "NZD",
    "CH": "CHF", "SE": "SEK", "NO": "NOK", "DK": "DKK", "PL": "PLN",
    "JP": "JPY", "MX": "MXN", "BR": "BRL", "SG": "SGD", "HK": "HKD",
    "KR": "KRW", "IN": "INR", "ZA": "ZAR", "AE": "AED", "IL": "ILS",
    "TR": "TRY", "CZ": "CZK", "HU": "HUF", "RO": "RON", "BG": "BGN",
}


def infer_presentment_currency_from_country(country: str | None) -> str:
    """根据 buyerCountry 推断客户结账时的货币（策略 C）。

    未知或缺失国家 → fallback 到店铺结算币种 USD。

    Args:
        country: ISO 2-letter 国家码（大小写不敏感）

    Returns:
        ISO 4217 currency code，如 'EUR', 'GBP', 'USD'
    """
    if not country:
        return DEFAULT_SETTLEMENT_CURRENCY
    return COUNTRY_TO_CURRENCY.get(country.strip().upper(), DEFAULT_SETTLEMENT_CURRENCY)


def estimate_fee_for_buyer_country(
    amount: Any,
    buyer_country: str | None,
    *,
    settlement_currency: str = DEFAULT_SETTLEMENT_CURRENCY,
    store_country: str = DEFAULT_STORE_COUNTRY,
) -> dict[str, Any]:
    """便利函数：从 dianxiaomi 订单行的 buyer_country 估算 Shopify 手续费。

    本店首版假设（策略 C）：
      1. card_country = buyer_country（多数客户用本地卡）
      2. presentment_currency = COUNTRY_TO_CURRENCY[buyer_country]
         （多数客户用本地货币结账）

    buyer_country 缺失时退化为"未知卡 + USD 结账" → Tier B_estimated。
    未来 CSV 反推校验可据此校准国家假设。
    """
    if not buyer_country:
        return calculate_shopify_fee(
            amount=amount,
            presentment_currency=settlement_currency,
            card_country=None,
            settlement_currency=settlement_currency,
            store_country=store_country,
        )
    presentment_currency = infer_presentment_currency_from_country(buyer_country)
    return calculate_shopify_fee(
        amount=amount,
        presentment_currency=presentment_currency,
        card_country=buyer_country,
        settlement_currency=settlement_currency,
        store_country=store_country,
    )


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _round2(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def classify_tier(
    presentment_currency: str,
    card_country: str | None,
    *,
    settlement_currency: str = DEFAULT_SETTLEMENT_CURRENCY,
    store_country: str = DEFAULT_STORE_COUNTRY,
) -> str:
    """返回 'A' | 'B' | 'C' | 'D'。card_country=None 视作国际卡（保守估算）。"""
    needs_conversion = presentment_currency.upper() != settlement_currency.upper()
    if card_country is None:
        is_cross_border = True
    else:
        is_cross_border = card_country.upper() != store_country.upper()
    if not is_cross_border and not needs_conversion:
        return "A"
    if is_cross_border and not needs_conversion:
        return "B"
    if not is_cross_border and needs_conversion:
        return "C"
    return "D"


def calculate_shopify_fee(
    amount: Any,
    presentment_currency: str,
    card_country: str | None = None,
    *,
    settlement_currency: str = DEFAULT_SETTLEMENT_CURRENCY,
    store_country: str = DEFAULT_STORE_COUNTRY,
) -> dict[str, Any]:
    """计算 Shopify Payments 单笔交易手续费、净到账、tier、费率分解。

    Args:
        amount: 交易金额（结算币种，通常 USD）
        presentment_currency: 客户结账时的币种
        card_country: 发卡国 ISO 2-letter；None 表示未知，按保守估算（国际卡）
        settlement_currency: 店铺结算币种，默认 USD
        store_country: 店铺所在国，默认 US

    Returns:
        {amount, fee, net, tier, rate_breakdown}
        tier 在 card_country=None 时附 _estimated 后缀
    """
    amount_d = _to_decimal(amount)
    needs_conversion = presentment_currency.upper() != settlement_currency.upper()
    if card_country is None:
        is_cross_border = True
        tier_suffix = "_estimated"
    else:
        is_cross_border = card_country.upper() != store_country.upper()
        tier_suffix = ""

    rate = BASE_RATE
    if is_cross_border:
        rate += CROSS_BORDER_RATE
    if needs_conversion:
        rate += CURRENCY_CONVERSION_RATE

    fee_d = amount_d * rate + FIXED_FEE
    fee = _round2(fee_d)
    net = _round2(amount_d - Decimal(str(fee)))

    base_tier = classify_tier(
        presentment_currency,
        card_country,
        settlement_currency=settlement_currency,
        store_country=store_country,
    )
    tier = base_tier + tier_suffix

    return {
        "amount": float(amount_d),
        "fee": fee,
        "net": net,
        "tier": tier,
        "rate_breakdown": {
            "base_rate": float(BASE_RATE),
            "cross_border_rate": float(CROSS_BORDER_RATE) if is_cross_border else 0.0,
            "currency_conversion_rate": float(CURRENCY_CONVERSION_RATE) if needs_conversion else 0.0,
            "total_percentage_rate": float(rate),
            "fixed_fee": float(FIXED_FEE),
        },
    }


def estimate_net_income(
    amount: Any,
    presentment_currency: str,
    card_country: str | None = None,
    *,
    settlement_currency: str = DEFAULT_SETTLEMENT_CURRENCY,
    store_country: str = DEFAULT_STORE_COUNTRY,
) -> float:
    """扣完 Shopify 手续费后的预估到账金额（结算币种）。"""
    return calculate_shopify_fee(
        amount=amount,
        presentment_currency=presentment_currency,
        card_country=card_country,
        settlement_currency=settlement_currency,
        store_country=store_country,
    )["net"]


def verify_fee(
    amount: Any,
    actual_fee: Any,
    presentment_currency: str,
    *,
    settlement_currency: str = DEFAULT_SETTLEMENT_CURRENCY,
) -> dict[str, Any]:
    """根据真实 fee 反推卡来源 + 校验是否符合标准费率。

    用于策略 B（CSV 校验回路）：判断某笔交易是否被多扣或少扣，
    或反推 card_origin 用于策略 C 参数校准。

    Returns:
        命中标准费率（domestic 或 international）：
            {card_origin, matches_standard: True, diff}
        既不是 domestic 也不是 international（容差外）：
            {card_origin: 'unknown', matches_standard: False,
             expected_domestic, expected_international, actual}
    """
    amount_d = _to_decimal(amount)
    fee_d = _to_decimal(actual_fee)
    needs_conversion = presentment_currency.upper() != settlement_currency.upper()
    base_rate = BASE_RATE + (CURRENCY_CONVERSION_RATE if needs_conversion else Decimal("0"))

    expected_domestic_d = (amount_d * base_rate + FIXED_FEE).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    expected_international_d = (
        amount_d * (base_rate + CROSS_BORDER_RATE) + FIXED_FEE
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    diff_dom = abs(fee_d - expected_domestic_d)
    diff_intl = abs(fee_d - expected_international_d)

    if diff_dom <= RECONCILIATION_TOLERANCE and diff_dom <= diff_intl:
        return {
            "card_origin": "domestic",
            "matches_standard": True,
            "diff": float(fee_d - expected_domestic_d),
        }
    if diff_intl <= RECONCILIATION_TOLERANCE:
        return {
            "card_origin": "international",
            "matches_standard": True,
            "diff": float(fee_d - expected_international_d),
        }
    return {
        "card_origin": "unknown",
        "matches_standard": False,
        "expected_domestic": float(expected_domestic_d),
        "expected_international": float(expected_international_d),
        "actual": float(fee_d),
    }
