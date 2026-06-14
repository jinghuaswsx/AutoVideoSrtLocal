"""实时大盘 overview 后台预热。

range 解析必须与前端 order_analytics.html 的 orderAnalyticsMetaCalendar
逐字一致：**周一起算**（不可复用 weekly_ai_report 的周日起算），thisWeek.end
为本周日（可能为未来日期）。否则 cache_key 不匹配，预热白做。

预热写入的 kwargs / cache_params 必须与 web.routes.order_analytics.realtime_overview
顶部卡片默认视图（无 site/product、window=7、include_profit_summary=True、
include_details=False）逐字对应；改 route 的 cache_params 字段时同步改这里。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, timedelta

log = logging.getLogger(__name__)

_OPEN_RANGES = ("today", "thisWeek")
_CLOSED_RANGES = ("yesterday", "lastWeek")


def _start_of_week_monday(d: date) -> date:
    # 前端：day = getDay()||7; date - day + 1 → 周一
    return d - timedelta(days=d.weekday())


def resolve_meta_calendar_range(range_name: str, today: date) -> tuple[date, date]:
    """复刻前端 resolveRange 的 today/yesterday/thisWeek/lastWeek 边界。"""
    if range_name == "today":
        return today, today
    if range_name == "yesterday":
        y = today - timedelta(days=1)
        return y, y
    if range_name == "thisWeek":
        start = _start_of_week_monday(today)
        return start, start + timedelta(days=6)
    if range_name == "lastWeek":
        start = _start_of_week_monday(today) - timedelta(days=7)
        return start, start + timedelta(days=6)
    raise ValueError(f"unsupported warmup range: {range_name}")


@dataclass(frozen=True)
class WarmupTarget:
    range_name: str
    scope: str            # "global"/"new"/"old"/"unmatched"
    interval_seconds: int


def _build_targets() -> list[WarmupTarget]:
    out: list[WarmupTarget] = []
    for r in _OPEN_RANGES:
        out.append(WarmupTarget(r, "global", 45))
        for s in ("new", "old", "unmatched"):
            out.append(WarmupTarget(r, s, 150))
    for r in _CLOSED_RANGES:
        for s in ("global", "new", "old", "unmatched"):
            out.append(WarmupTarget(r, s, 1200))
    return out


WARMUP_TARGETS = _build_targets()
_last_run: dict[tuple[str, str], float] = {}
_lock = threading.Lock()


def _now() -> float:
    return time.monotonic()


def _due_targets(now: float) -> list[WarmupTarget]:
    return [
        t for t in WARMUP_TARGETS
        if now - _last_run.get((t.range_name, t.scope), 0.0) >= t.interval_seconds
    ]


def _warm_one(target: WarmupTarget) -> None:
    """对单个 (range, scope) 现算并写入与 route 完全一致的 cache_key。"""
    from appcore.order_analytics._helpers import current_meta_business_date
    from web.routes.order_analytics import _compute_realtime_overview_cached

    today = current_meta_business_date()
    start, end = resolve_meta_calendar_range(target.range_name, today)
    scope = None if target.scope == "global" else target.scope
    window = 7
    kwargs = {
        "start_date": start.isoformat(), "end_date": end.isoformat(),
        "include_details": False, "include_profit_summary": True,
        "product_launch_window_days": window,
    }
    if scope:
        kwargs["product_launch_scope"] = scope
    cache_params = {
        "date": None, "start_date": start.isoformat(), "end_date": end.isoformat(),
        "include_details": False, "include_profit_summary": True,
        "product_id": None, "site_code": "",
        "product_launch_scope": scope, "product_launch_window_days": window,
        "page": None, "page_size": None, "order_page": None, "order_page_size": None,
    }
    _compute_realtime_overview_cached(None, kwargs, cache_params=cache_params)


def run_warmup_tick() -> None:
    """APScheduler 每 ~15s 调用；串行预热到期的 (range, scope)。"""
    now = _now()
    with _lock:
        targets = _due_targets(now)
    for t in targets:
        try:
            _warm_one(t)
        except Exception:
            log.warning("realtime warmup failed range=%s scope=%s", t.range_name, t.scope, exc_info=True)
        with _lock:
            _last_run[(t.range_name, t.scope)] = _now()
