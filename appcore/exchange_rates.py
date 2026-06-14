"""USD/CNY daily exchange rate archive and lookup.

Docs-anchor: docs/superpowers/specs/2026-06-06-usd-cny-daily-exchange-rate-design.md
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterable
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from appcore.db import execute, query, query_one

log = logging.getLogger(__name__)

BEIJING_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_TOLERANCE_RATIO = Decimal("0.05")
FALLBACK_WINDOW_DAYS = 30
FALLBACK_CALCULATION_METHOD = "daily_archive_30d_average"
FALLBACK_LOGIC_DESCRIPTION = (
    "最近 30 天已归档 USD/CNY 基准汇率的算术平均值；"
    "缺当天基准时优先使用该值；无样本时退回系统配置汇率"
)
FRANKFURTER_LATEST_URL = "https://api.frankfurter.app/latest?from=USD&to=CNY"
FRANKFURTER_HISTORICAL_URL = "https://api.frankfurter.app/{date}?from=USD&to=CNY"
# 向后兼容：旧引用仍可用
FRANKFURTER_URL = FRANKFURTER_LATEST_URL
OPEN_ER_API_URL = "https://open.er-api.com/v6/latest/USD"
FLOATRATES_URL = "https://www.floatrates.com/daily/usd.json"


class ExchangeRateValidationError(RuntimeError):
    """Raised when cross validation rejects a fetched exchange rate."""

    def __init__(self, message: str, *, summary: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.summary = summary or {}


@dataclass(frozen=True)
class RateQuote:
    source: str
    rate: Decimal
    source_date: date | None
    fetched_at: datetime
    raw: dict[str, Any]

    def as_summary(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "rate": float(_q6(self.rate)),
            "source_date": self.source_date.isoformat() if self.source_date else None,
        }


@dataclass(frozen=True)
class ExchangeRateLookup:
    rate: Decimal
    source: str
    rate_date: date | None = None
    source_id: int | None = None

    def cost_basis(self) -> dict[str, Any]:
        return {
            "exchange_rate_source": self.source,
            "exchange_rate_date": self.rate_date.isoformat() if self.rate_date else None,
            "exchange_rate_source_id": self.source_id,
        }


def _q6(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _q8(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


def _positive_decimal(value: Any, *, label: str) -> Decimal:
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if dec <= 0:
        raise ValueError(f"{label} must be positive")
    return dec


def _parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value)[:10])


def _parse_http_date(value: Any) -> date | None:
    if not value:
        return None
    return parsedate_to_datetime(str(value)).date()


def _today_beijing() -> date:
    return datetime.now(BEIJING_TZ).date()


def _http_get_json(url: str, *, timeout_seconds: int = 15) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "AutoVideoSrtLocal/1.0"})
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - fixed public API URLs
        body = response.read().decode("utf-8")
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError("exchange rate response must be a JSON object")
    return parsed


def fetch_frankfurter_usd_cny(
    *, rate_date: date | None = None, get_json: Callable[[str], dict[str, Any]] | None = None
) -> RateQuote:
    url = (
        FRANKFURTER_HISTORICAL_URL.format(date=rate_date.isoformat())
        if rate_date is not None
        else FRANKFURTER_LATEST_URL
    )
    data = (get_json or _http_get_json)(url)
    base = str(data.get("base") or "").upper()
    if base != "USD":
        raise ValueError(f"frankfurter base must be USD, got {base!r}")
    rates = data.get("rates") or {}
    rate = _positive_decimal(rates.get("CNY"), label="frankfurter rates.CNY")
    source_date = _parse_iso_date(data.get("date"))
    return RateQuote(
        source="frankfurter",
        rate=rate,
        source_date=source_date,
        fetched_at=datetime.now(BEIJING_TZ),
        raw={
            "base": data.get("base"),
            "date": data.get("date"),
            "rates": {"CNY": rates.get("CNY")},
        },
    )


def fetch_open_er_api_usd_cny(
    *, get_json: Callable[[str], dict[str, Any]] | None = None
) -> RateQuote:
    data = (get_json or _http_get_json)(OPEN_ER_API_URL)
    if str(data.get("result") or "").lower() != "success":
        raise ValueError("open_er_api result must be success")
    base = str(data.get("base_code") or "").upper()
    if base != "USD":
        raise ValueError(f"open_er_api base_code must be USD, got {base!r}")
    rates = data.get("rates") or {}
    rate = _positive_decimal(rates.get("CNY"), label="open_er_api rates.CNY")
    source_date = _parse_http_date(data.get("time_last_update_utc"))
    return RateQuote(
        source="open_er_api",
        rate=rate,
        source_date=source_date,
        fetched_at=datetime.now(BEIJING_TZ),
        raw={
            "result": data.get("result"),
            "provider": data.get("provider"),
            "base_code": data.get("base_code"),
            "time_last_update_utc": data.get("time_last_update_utc"),
            "rates": {"CNY": rates.get("CNY")},
        },
    )


def fetch_floatrates_usd_cny(
    *, get_json: Callable[[str], dict[str, Any]] | None = None
) -> RateQuote:
    data = (get_json or _http_get_json)(FLOATRATES_URL)
    cny = data.get("cny") or {}
    code = str(cny.get("code") or cny.get("alphaCode") or "").upper()
    if code != "CNY":
        raise ValueError(f"floatrates quote must be CNY, got {code!r}")
    rate = _positive_decimal(cny.get("rate"), label="floatrates cny.rate")
    source_date = _parse_http_date(cny.get("date"))
    return RateQuote(
        source="floatrates",
        rate=rate,
        source_date=source_date,
        fetched_at=datetime.now(BEIJING_TZ),
        raw={
            "code": cny.get("code"),
            "alphaCode": cny.get("alphaCode"),
            "date": cny.get("date"),
            "rate": cny.get("rate"),
        },
    )


def relative_diff_ratio(primary_rate: Decimal, validator_rate: Decimal) -> Decimal:
    average = (primary_rate + validator_rate) / Decimal("2")
    if average <= 0:
        return Decimal("999")
    return abs(primary_rate - validator_rate) / average


def max_relative_diff_ratio(quotes: list[RateQuote]) -> Decimal:
    max_diff = Decimal("0")
    for left_index, left in enumerate(quotes):
        for right in quotes[left_index + 1:]:
            max_diff = max(max_diff, relative_diff_ratio(left.rate, right.rate))
    return max_diff


def validate_cross_rates(
    primary: RateQuote,
    validators: list[RateQuote],
    *,
    tolerance_ratio: Decimal = DEFAULT_TOLERANCE_RATIO,
) -> dict[str, Any]:
    quotes = [primary, *validators]
    if len(quotes) < 3:
        raise ExchangeRateValidationError(
            "USD/CNY exchange rate validation requires at least three sources",
            summary={
                "quotes": [quote.as_summary() for quote in quotes],
                "tolerance_ratio": float(_q8(tolerance_ratio)),
            },
        )
    diff = max_relative_diff_ratio(quotes)
    summary = {
        "primary": primary.as_summary(),
        "validators": [quote.as_summary() for quote in validators],
        "quotes": [quote.as_summary() for quote in quotes],
        "max_relative_diff_ratio": float(_q8(diff)),
        "tolerance_ratio": float(_q8(tolerance_ratio)),
    }
    if diff > tolerance_ratio:
        raise ExchangeRateValidationError(
            "USD/CNY exchange rate cross validation failed",
            summary=summary,
        )
    return {
        **summary,
        "usd_to_cny": float(_q6(primary.rate)),
    }


def _upsert_validated_rate(
    *,
    rate_date: date,
    primary: RateQuote,
    validators: list[RateQuote],
    max_relative_diff: Decimal,
    tolerance_ratio: Decimal,
    source_run_id: int | None = None,
) -> int:
    return int(execute(
        """
        INSERT INTO usd_cny_daily_exchange_rates (
          rate_date, usd_to_cny,
          primary_source, primary_rate, primary_source_date,
          validator_quotes_json,
          max_relative_diff_ratio, tolerance_ratio,
          source_payload_json, source_run_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          usd_to_cny=VALUES(usd_to_cny),
          primary_source=VALUES(primary_source),
          primary_rate=VALUES(primary_rate),
          primary_source_date=VALUES(primary_source_date),
          validator_quotes_json=VALUES(validator_quotes_json),
          max_relative_diff_ratio=VALUES(max_relative_diff_ratio),
          tolerance_ratio=VALUES(tolerance_ratio),
          source_payload_json=VALUES(source_payload_json),
          source_run_id=VALUES(source_run_id),
          synced_at=NOW(),
          updated_at=NOW()
        """,
        (
            rate_date,
            _q6(primary.rate),
            primary.source,
            _q6(primary.rate),
            primary.source_date,
            json.dumps(
                [quote.as_summary() for quote in validators],
                ensure_ascii=False,
                default=str,
            ),
            _q8(max_relative_diff),
            _q8(tolerance_ratio),
            json.dumps(
                {
                    "primary": primary.raw,
                    "validators": [
                        {"source": quote.source, "payload": quote.raw}
                        for quote in validators
                    ],
                },
                ensure_ascii=False,
                default=str,
            ),
            source_run_id,
        ),
    ))


def sync_usd_cny_daily_rate(
    *,
    rate_date: date | None = None,
    tolerance_ratio: Decimal = DEFAULT_TOLERANCE_RATIO,
    source_run_id: int | None = None,
    primary_fetcher: Callable[[], RateQuote] = fetch_frankfurter_usd_cny,
    validator_fetchers: tuple[Callable[[], RateQuote], ...] = (
        fetch_open_er_api_usd_cny,
        fetch_floatrates_usd_cny,
    ),
) -> dict[str, Any]:
    effective_date = rate_date or _today_beijing()
    primary = primary_fetcher()
    validators = [fetcher() for fetcher in validator_fetchers]
    diff = max_relative_diff_ratio([primary, *validators])
    summary = validate_cross_rates(
        primary,
        validators,
        tolerance_ratio=tolerance_ratio,
    )
    row_id = _upsert_validated_rate(
        rate_date=effective_date,
        primary=primary,
        validators=validators,
        max_relative_diff=diff,
        tolerance_ratio=tolerance_ratio,
        source_run_id=source_run_id,
    )
    return {
        "rate_date": effective_date.isoformat(),
        "usd_to_cny": float(_q6(primary.rate)),
        **summary,
        "row_id": row_id,
    }


def backfill_usd_cny_daily_rate(
    *,
    rate_date: date,
    fetcher: Callable[[date], RateQuote] | None = None,
    source_run_id: int | None = None,
) -> dict[str, Any]:
    """单源历史回填：open_er_api / floatrates 无历史端点，三源交叉校验不适用于历史日期，
    故仅用 frankfurter 历史值写入，validators 置空、max_diff=0，标注 single_source_historical。
    写入同一张 usd_cny_daily_exchange_rates，下游按 daily_archive 读取。
    """
    fetch = fetcher or (lambda d: fetch_frankfurter_usd_cny(rate_date=d))
    primary = fetch(rate_date)
    row_id = _upsert_validated_rate(
        rate_date=rate_date,
        primary=primary,
        validators=[],
        max_relative_diff=Decimal("0"),
        tolerance_ratio=DEFAULT_TOLERANCE_RATIO,
        source_run_id=source_run_id,
    )
    return {
        "rate_date": rate_date.isoformat(),
        "usd_to_cny": float(_q6(primary.rate)),
        "primary": primary.as_summary(),
        "validators": [],
        "sample_status": "single_source_historical",
        "row_id": row_id,
    }


def manual_rate_lookup(rate: Any) -> ExchangeRateLookup:
    return ExchangeRateLookup(
        rate=_positive_decimal(rate, label="manual rmb_per_usd"),
        source="manual_override",
    )


def _configured_setting_lookup(rate: Any | None = None) -> ExchangeRateLookup:
    if rate is None:
        from appcore.product_roas import get_configured_rmb_per_usd

        rate = get_configured_rmb_per_usd()
    return ExchangeRateLookup(
        rate=_positive_decimal(rate, label="configured fallback rmb_per_usd"),
        source="configured_fallback",
    )


def configured_fallback_lookup(rate: Any | None = None) -> ExchangeRateLookup:
    """Return the effective fallback lookup.

    With no explicit legacy rate, analytics uses the dynamic 30-day average
    fallback first. The old system setting remains the final guardrail.
    """
    if rate is None:
        dynamic = get_current_usd_cny_fallback_lookup()
        if dynamic is not None:
            return dynamic
    return _configured_setting_lookup(rate)


def _date_from_db(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(str(value)[:10])


def _lookup_from_row(row: dict[str, Any]) -> ExchangeRateLookup:
    return ExchangeRateLookup(
        rate=_positive_decimal(row.get("usd_to_cny"), label="usd_to_cny"),
        source="daily_archive",
        rate_date=_date_from_db(row.get("rate_date")),
        source_id=int(row["id"]) if row.get("id") is not None else None,
    )


def _fallback_lookup_from_row(row: dict[str, Any]) -> ExchangeRateLookup:
    return ExchangeRateLookup(
        rate=_positive_decimal(row.get("usd_to_cny"), label="fallback usd_to_cny"),
        source="fallback_30d_average",
        rate_date=_date_from_db(row.get("fallback_date")),
        source_id=int(row["id"]) if row.get("id") is not None else None,
    )


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return value


def list_usd_cny_daily_rates(*, limit: int = 30) -> list[dict[str, Any]]:
    """Return recent validated baseline rates for the admin JSON endpoint."""
    safe_limit = max(1, min(365, int(limit or 30)))
    rows = query(
        """
        SELECT id, rate_date, usd_to_cny,
               primary_source, primary_rate, primary_source_date,
               validator_quotes_json, max_relative_diff_ratio, tolerance_ratio,
               synced_at, source_run_id
        FROM usd_cny_daily_exchange_rates
        ORDER BY rate_date DESC
        LIMIT %s
        """,
        (safe_limit,),
    )
    out: list[dict[str, Any]] = []
    for row in rows or []:
        out.append({
            "id": int(row["id"]) if row.get("id") is not None else None,
            "rate_date": _date_from_db(row.get("rate_date")).isoformat()
            if row.get("rate_date") is not None else None,
            "usd_to_cny": float(row.get("usd_to_cny") or 0),
            "primary": {
                "source": row.get("primary_source"),
                "rate": float(row.get("primary_rate") or 0),
                "source_date": _date_from_db(row.get("primary_source_date")).isoformat()
                if row.get("primary_source_date") is not None else None,
            },
            "validators": _json_value(row.get("validator_quotes_json")) or [],
            "max_relative_diff_ratio": float(row.get("max_relative_diff_ratio") or 0),
            "tolerance_ratio": float(row.get("tolerance_ratio") or 0),
            "synced_at": row.get("synced_at").isoformat()
            if hasattr(row.get("synced_at"), "isoformat") else row.get("synced_at"),
            "source_run_id": int(row["source_run_id"]) if row.get("source_run_id") is not None else None,
        })
    return out


def _datetime_or_text(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _fallback_row_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]) if row.get("id") is not None else None,
        "fallback_date": _date_from_db(row.get("fallback_date")).isoformat()
        if row.get("fallback_date") is not None else None,
        "usd_to_cny": float(row.get("usd_to_cny") or 0),
        "window_start": _date_from_db(row.get("window_start")).isoformat()
        if row.get("window_start") is not None else None,
        "window_end": _date_from_db(row.get("window_end")).isoformat()
        if row.get("window_end") is not None else None,
        "sample_count": int(row.get("sample_count") or 0),
        "source_rate_ids": _json_value(row.get("source_rate_ids_json")) or [],
        "calculation_method": row.get("calculation_method") or FALLBACK_CALCULATION_METHOD,
        "logic": FALLBACK_LOGIC_DESCRIPTION,
        "synced_at": _datetime_or_text(row.get("synced_at")),
        "updated_at": _datetime_or_text(row.get("updated_at")),
        "source_run_id": int(row["source_run_id"]) if row.get("source_run_id") is not None else None,
    }


def refresh_usd_cny_fallback_rate(
    *,
    fallback_date: date | None = None,
    window_days: int = FALLBACK_WINDOW_DAYS,
    source_run_id: int | None = None,
) -> dict[str, Any]:
    """Upsert the dynamic fallback from recent validated daily archives."""
    effective_date = fallback_date or _today_beijing()
    safe_window_days = max(1, int(window_days or FALLBACK_WINDOW_DAYS))
    window_start = effective_date - timedelta(days=safe_window_days - 1)
    rows = query(
        """
        SELECT id, rate_date, usd_to_cny
        FROM usd_cny_daily_exchange_rates
        WHERE rate_date BETWEEN %s AND %s
        ORDER BY rate_date ASC
        """,
        (window_start, effective_date),
    )
    if not rows:
        raise RuntimeError(
            "cannot refresh USD/CNY fallback rate: no validated daily archive samples"
        )
    total = sum((_positive_decimal(row.get("usd_to_cny"), label="usd_to_cny") for row in rows), Decimal("0"))
    average = total / Decimal(len(rows))
    source_ids = [int(row["id"]) for row in rows if row.get("id") is not None]
    execute(
        """
        INSERT INTO usd_cny_fallback_exchange_rates (
          fallback_date, usd_to_cny,
          window_start, window_end, sample_count, source_rate_ids_json,
          calculation_method, source_run_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          usd_to_cny=VALUES(usd_to_cny),
          window_start=VALUES(window_start),
          window_end=VALUES(window_end),
          sample_count=VALUES(sample_count),
          source_rate_ids_json=VALUES(source_rate_ids_json),
          calculation_method=VALUES(calculation_method),
          source_run_id=VALUES(source_run_id),
          synced_at=NOW(),
          updated_at=NOW()
        """,
        (
            effective_date,
            _q6(average),
            window_start,
            effective_date,
            len(rows),
            json.dumps(source_ids, ensure_ascii=False),
            FALLBACK_CALCULATION_METHOD,
            source_run_id,
        ),
    )
    row = query_one(
        """
        SELECT id, fallback_date, usd_to_cny, window_start, window_end,
               sample_count, source_rate_ids_json, calculation_method,
               synced_at, updated_at, source_run_id
        FROM usd_cny_fallback_exchange_rates
        WHERE fallback_date = %s
        """,
        (effective_date,),
    )
    if row:
        return _fallback_row_summary(row)
    return {
        "fallback_date": effective_date.isoformat(),
        "usd_to_cny": float(_q6(average)),
        "window_start": window_start.isoformat(),
        "window_end": effective_date.isoformat(),
        "sample_count": len(rows),
        "source_rate_ids": source_ids,
        "calculation_method": FALLBACK_CALCULATION_METHOD,
        "logic": FALLBACK_LOGIC_DESCRIPTION,
        "source_run_id": source_run_id,
    }


def get_current_usd_cny_fallback_lookup() -> ExchangeRateLookup | None:
    try:
        row = query_one(
            """
            SELECT id, fallback_date, usd_to_cny
            FROM usd_cny_fallback_exchange_rates
            ORDER BY fallback_date DESC
            LIMIT 1
            """
        )
    except Exception:
        log.warning("failed to read usd_cny_fallback_exchange_rates", exc_info=True)
        return None
    if not row:
        return None
    return _fallback_lookup_from_row(row)


def list_usd_cny_fallback_rates(*, limit: int = 30) -> list[dict[str, Any]]:
    safe_limit = max(1, min(365, int(limit or 30)))
    rows = query(
        """
        SELECT id, fallback_date, usd_to_cny, window_start, window_end,
               sample_count, source_rate_ids_json, calculation_method,
               synced_at, updated_at, source_run_id
        FROM usd_cny_fallback_exchange_rates
        ORDER BY fallback_date DESC
        LIMIT %s
        """,
        (safe_limit,),
    )
    return [_fallback_row_summary(row) for row in rows or []]


def exchange_rate_admin_payload(*, limit: int = 30) -> dict[str, Any]:
    safe_limit = max(1, min(365, int(limit or 30)))
    fallback_history = list_usd_cny_fallback_rates(limit=safe_limit)
    return {
        "limit": safe_limit,
        "rows": list_usd_cny_daily_rates(limit=safe_limit),
        "current_fallback": fallback_history[0] if fallback_history else None,
        "fallback_history": fallback_history,
        "fallback_logic": {
            "calculation_method": FALLBACK_CALCULATION_METHOD,
            "window_days": FALLBACK_WINDOW_DAYS,
            "description": FALLBACK_LOGIC_DESCRIPTION,
            "fallback_order": [
                "daily_archive",
                "fallback_30d_average",
                "configured_fallback",
            ],
        },
    }


def get_usd_to_cny_for_date(
    rate_date: date | None,
    *,
    fallback_rate: Any | None = None,
) -> ExchangeRateLookup:
    if rate_date is None:
        return configured_fallback_lookup(fallback_rate)
    try:
        row = query_one(
            "SELECT id, rate_date, usd_to_cny "
            "FROM usd_cny_daily_exchange_rates "
            "WHERE rate_date = %s",
            (rate_date,),
        )
    except Exception:
        log.warning("failed to read usd_cny_daily_exchange_rates", exc_info=True)
        row = None
    if row:
        return _lookup_from_row(row)
    return configured_fallback_lookup(fallback_rate)


def get_usd_to_cny_map(
    rate_dates: Iterable[date],
    *,
    fallback_rate: Any | None = None,
) -> dict[date, ExchangeRateLookup]:
    unique_dates = [d for d in dict.fromkeys(rate_dates) if d is not None]
    if not unique_dates:
        return {}
    rows: list[dict[str, Any]] = []
    try:
        placeholders = ",".join(["%s"] * len(unique_dates))
        rows = query(
            "SELECT id, rate_date, usd_to_cny "
            f"FROM usd_cny_daily_exchange_rates WHERE rate_date IN ({placeholders})",
            tuple(unique_dates),
        )
    except Exception:
        log.warning("failed to read usd_cny_daily_exchange_rates map", exc_info=True)

    by_date: dict[date, ExchangeRateLookup] = {}
    for row in rows or []:
        row_date = _date_from_db(row.get("rate_date"))
        if row_date is not None:
            by_date[row_date] = _lookup_from_row(row)

    fallback = configured_fallback_lookup(fallback_rate)
    for d in unique_dates:
        by_date.setdefault(d, fallback)
    return by_date
