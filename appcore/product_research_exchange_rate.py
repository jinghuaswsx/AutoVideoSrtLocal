"""Exchange rate provider for product research pricing calculations.

Provides static exchange rates with optional overrides from system_settings.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

# 静态汇率表：1 USD = X target_currency
# 数据需要定期更新，此处为参考值
_DEFAULT_RATES: dict[str, Decimal] = {
    "USD": Decimal("1.0"),
    "EUR": Decimal("0.92"),
    "SEK": Decimal("10.30"),
    "JPY": Decimal("150.0"),
    "GBP": Decimal("0.79"),
    "CNY": Decimal("7.25"),
}

# 反向汇率缓存
_RATE_CACHE: dict[str, dict[str, Decimal]] = {}


class ExchangeRateProvider:
    """提供货币汇率查询和换算，不抛异常，缺失时返回 None。"""

    def get_rate(self, from_currency: str, to_currency: str) -> Decimal | None:
        from_code = str(from_currency or "").strip().upper()
        to_code = str(to_currency or "").strip().upper()
        if not from_code or not to_code:
            return None
        if from_code == to_code:
            return Decimal("1.0")

        cache_key = f"{from_code}:{to_code}"
        if cache_key in _RATE_CACHE:
            return _RATE_CACHE[cache_key]

        rate = self._lookup_rate(from_code, to_code)
        if rate is not None:
            _RATE_CACHE[cache_key] = rate
        return rate

    def convert(self, amount: Decimal | float | int | None, from_currency: str, to_currency: str) -> Decimal | None:
        if amount is None:
            return None
        try:
            dec = Decimal(str(amount))
        except Exception:
            return None
        rate = self.get_rate(from_currency, to_currency)
        if rate is None:
            return None
        return (dec * rate).quantize(Decimal("0.01"))

    def _lookup_rate(self, from_code: str, to_code: str) -> Decimal | None:
        from_usd = self._usd_rate(from_code)
        to_usd = self._usd_rate(to_code)
        if from_usd is None or to_usd is None:
            return None
        try:
            return (to_usd / from_usd).quantize(Decimal("0.000001"))
        except Exception:
            return None

    def _usd_rate(self, currency: str) -> Decimal | None:
        override = _read_setting_override(currency)
        if override is not None:
            return override
        return _DEFAULT_RATES.get(currency)


def _read_setting_override(currency: str) -> Decimal | None:
    try:
        from appcore.settings import get_setting
        key = f"product_research_fx_usd_to_{currency.lower()}"
        value = get_setting(key)
        if value is not None and str(value).strip():
            return Decimal(str(value).strip())
    except Exception:
        pass
    return None


_provider: ExchangeRateProvider | None = None


def get_exchange_rate_provider() -> ExchangeRateProvider:
    global _provider
    if _provider is None:
        _provider = ExchangeRateProvider()
    return _provider