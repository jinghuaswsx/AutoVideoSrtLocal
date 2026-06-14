"""实时大盘 + 新品投放分析 overview 后台预热。

range 解析与前端 order_analytics.html 的 orderAnalyticsMetaCalendar 逐字一致
（**周一起算**；月度同前端 new Date(y, m, 1)..new Date(y, m+1, 0)）。

覆盖 6 个范围（today/yesterday/本周/上周/本月/上月，**不含年度**），分两档频率：
- today / yesterday：15s
- 本周 / 上周 / 本月 / 上月：600s（10 分钟）

两个模块（cache_key 不同，各自预热）：
- realtime：实时大盘顶部卡片，4 scope（global/new/old/unmatched），无 details / 分页。
- npl：新品投放分析，3 scope（new/old/unmatched），带 include_details + 分页（window=7）。

预热写入的 kwargs / cache_params 必须与 web.routes.order_analytics 的顶部卡片 /
新品投放分析默认视图逐字对应，否则 cache_key 不匹配、预热白做。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, timedelta

from appcore.order_analytics._helpers import current_meta_business_date

log = logging.getLogger(__name__)


def _start_of_week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def resolve_meta_calendar_range(range_name: str, today: date) -> tuple[date, date]:
    """复刻前端 resolveRange 的 today/yesterday/本周/上周/本月/上月 边界。"""
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
    if range_name == "thisMonth":
        start = today.replace(day=1)
        nxt = start.replace(year=start.year + 1, month=1) if start.month == 12 \
            else start.replace(month=start.month + 1)
        return start, nxt - timedelta(days=1)
    if range_name == "lastMonth":
        last_prev = today.replace(day=1) - timedelta(days=1)
        return last_prev.replace(day=1), last_prev
    raise ValueError(f"unsupported warmup range: {range_name}")


# ── 预热目标矩阵 ──────────────────────────────────────
_FAST_RANGES = ("today", "yesterday")
_SLOW_RANGES = ("thisWeek", "lastWeek", "thisMonth", "lastMonth")
_FAST_INTERVAL = 15
_SLOW_INTERVAL = 600
_REALTIME_SCOPES = ("global", "new", "old", "unmatched")
_NPL_SCOPES = ("new", "old", "unmatched")


@dataclass(frozen=True)
class WarmupTarget:
    range_name: str
    module: str            # "realtime" / "npl"
    scope: str             # "global"/"new"/"old"/"unmatched"
    interval_seconds: int


def _build_targets() -> list[WarmupTarget]:
    out: list[WarmupTarget] = []
    for ranges, interval in ((_FAST_RANGES, _FAST_INTERVAL), (_SLOW_RANGES, _SLOW_INTERVAL)):
        for r in ranges:
            for s in _REALTIME_SCOPES:
                out.append(WarmupTarget(r, "realtime", s, interval))
            for s in _NPL_SCOPES:
                out.append(WarmupTarget(r, "npl", s, interval))
    return out


WARMUP_TARGETS = _build_targets()
_last_run: dict[tuple[str, str, str], float] = {}
_lock = threading.Lock()


def _now() -> float:
    return time.monotonic()


def _due_targets(now: float) -> list[WarmupTarget]:
    return [
        t for t in WARMUP_TARGETS
        if now - _last_run.get((t.range_name, t.module, t.scope), 0.0) >= t.interval_seconds
    ]


def _warm_one(target: WarmupTarget) -> None:
    """对单个目标现算并写入与 route 完全一致的 cache_key。"""
    from web.routes.order_analytics import _compute_realtime_overview_cached

    today = current_meta_business_date()
    start, end = resolve_meta_calendar_range(target.range_name, today)
    scope = None if target.scope == "global" else target.scope
    window = 7
    si, ei = start.isoformat(), end.isoformat()
    if target.module == "npl":
        kwargs = {
            "start_date": si, "end_date": ei,
            "include_details": True, "include_profit_summary": True,
            "product_launch_window_days": window,
            "order_page": 1, "order_page_size": 30, "page": 1, "page_size": 30,
        }
        if scope:
            kwargs["product_launch_scope"] = scope
        cache_params = {
            "date": None, "start_date": si, "end_date": ei,
            "include_details": True, "include_profit_summary": True,
            "product_id": None, "site_code": "",
            "product_launch_scope": scope, "product_launch_window_days": window,
            "page": 1, "page_size": 30, "order_page": 1, "order_page_size": 30,
        }
    else:
        kwargs = {
            "start_date": si, "end_date": ei,
            "include_details": False, "include_profit_summary": True,
            "product_launch_window_days": window,
        }
        if scope:
            kwargs["product_launch_scope"] = scope
        cache_params = {
            "date": None, "start_date": si, "end_date": ei,
            "include_details": False, "include_profit_summary": True,
            "product_id": None, "site_code": "",
            "product_launch_scope": scope, "product_launch_window_days": window,
            "page": None, "page_size": None, "order_page": None, "order_page_size": None,
        }
    _compute_realtime_overview_cached(None, kwargs, cache_params=cache_params)


def run_warmup_tick() -> None:
    """APScheduler 每 ~15s 调用；串行预热到期的 (range, module, scope)。"""
    now = _now()
    with _lock:
        targets = _due_targets(now)
    for t in targets:
        try:
            _warm_one(t)
        except Exception:
            log.warning("realtime warmup failed range=%s module=%s scope=%s",
                        t.range_name, t.module, t.scope, exc_info=True)
        with _lock:
            _last_run[(t.range_name, t.module, t.scope)] = _now()
