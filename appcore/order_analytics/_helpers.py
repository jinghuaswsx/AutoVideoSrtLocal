"""通用 helpers：日期解析、金额规范化、ROAS 计算等。

由 ``appcore.order_analytics`` package 在 PR 1.1b 从单文件抽出；
原 ``order_analytics.py`` 中的函数体逐字符保留，行为不变。

子模块（``dianxiaomi.py`` / ``shopify_orders.py`` 等）通过
``from ._helpers import _XXX`` 调用；``__init__.py`` 用显式 re-export
把它们带回 ``appcore.order_analytics`` 命名空间，保持调用方与测试的
``oa._money(...)`` 与 ``monkeypatch.setattr(oa, "_money", ...)`` 仍可工作。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from ._constants import META_ATTRIBUTION_TIMEZONE, _SHOP_TS_FMT


def _safe_decimal_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return round(float(str(value).replace(",", "").strip()), 2)
    except (TypeError, ValueError):
        return None


def _parse_dianxiaomi_ts(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp = timestamp / 1000
        try:
            return datetime.fromtimestamp(timestamp).replace(microsecond=0)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return _parse_dianxiaomi_ts(int(text))
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        candidate = text[:19] if fmt.endswith("%S") else text[:10]
        try:
            return datetime.strptime(candidate, fmt)
        except ValueError:
            continue
    return None


def _combined_link_text(*values: Any) -> str:
    return " ".join(str(value or "") for value in values).lower()


def _canonical_product_handle(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if "/products/" in text:
        text = text.split("/products/", 1)[1]
    text = text.split("?", 1)[0].split("#", 1)[0].strip("/")
    if not text:
        return None
    if text.endswith("-rjc"):
        text = text[:-4]
    return text or None


def _json_dumps_for_db(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _parse_shopify_ts(ts_str: str) -> datetime | None:
    """解析 Shopify 时间戳 '2026-04-22 23:00:14 -0700' 为 naive UTC-ish datetime。"""
    ts_str = (ts_str or "").strip()
    if not ts_str:
        return None
    try:
        dt = datetime.strptime(ts_str, _SHOP_TS_FMT)
        # 转为 UTC（去掉时区信息）
        return dt.replace(tzinfo=None) - dt.utcoffset()
    except Exception:
        pass
    # fallback: 只取日期时间部分
    try:
        return datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _safe_int(val: str, default: int = 0) -> int:
    try:
        return int(float((val or "").strip()))
    except (ValueError, TypeError):
        return default


def _safe_float(val: str) -> float | None:
    try:
        return float((val or "").strip())
    except (ValueError, TypeError):
        return None


def _safe_float_default(val: str, default: float = 0.0) -> float:
    parsed = _safe_float(val)
    return default if parsed is None else parsed


def _parse_meta_date(value: str) -> date:
    value = (value or "").strip()
    if not value:
        raise ValueError("Meta 广告报表日期不能为空")
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"无法解析 Meta 广告报表日期：{value}")


def _parse_iso_date_param(value: str, name: str) -> date:
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{name} must be YYYY-MM-DD") from exc


def _money(value: Any) -> float:
    return round(float(value or 0), 2)


def _roas(revenue: float, spend: float) -> float | None:
    if spend <= 0:
        return None
    return round(revenue / spend, 4)


def _revenue_with_shipping(order_revenue: float, shipping_revenue: float) -> float:
    return round(float(order_revenue or 0) + float(shipping_revenue or 0), 2)


def _beijing_now() -> datetime:
    return datetime.now(ZoneInfo(META_ATTRIBUTION_TIMEZONE)).replace(tzinfo=None)


def _business_hour(value: datetime | None, day_start: datetime) -> int | None:
    if not value:
        return None
    hour = int((value - day_start).total_seconds() // 3600)
    return max(0, min(23, hour))


def _compute_pct_change(now, prev) -> float | None:
    """环比百分比。返回 None 表示无法计算（prev=0 且 now>0）。"""
    now_v = float(now or 0)
    prev_v = float(prev or 0)
    if prev_v == 0 and now_v == 0:
        return 0.0
    if prev_v == 0:
        return None
    return round((now_v - prev_v) / prev_v * 100, 2)
