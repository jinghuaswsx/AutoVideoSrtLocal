"""实时大盘 + 新品投放分析 overview 后台预热（快/慢双线 + 强制刷新续期）。

两层关键设计（2026-06-14 修复，实测坐实）：
1. **强制刷新（force_refresh=True）**：预热必须重算并写回、刷新 expires_at；否则命中
   旧缓存就跳过、不续期，缓存到 TTL 被动过期，过期窗口用户请求 MISS、现算十几秒。
2. **快/慢分离**：今天/昨天实时大盘（60s TTL）走快线 ``run_warmup_fast``（15s，独立调度），
   不被周月整月聚合、npl 带明细这些十几秒的重现算串行阻塞；周月 + npl 走慢线
   ``run_warmup_slow``（300s）。否则单串行轮要几分钟，今天/昨天等不到续期就过期了。

range 解析与前端 orderAnalyticsMetaCalendar 逐字一致（周一起算；月度同前端）。

Docs-anchor: docs/superpowers/specs/2026-06-14-realtime-dashboard-load-optimization-design.md
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


# ── 预热目标矩阵（分频）────────────────────────────────
_REALTIME_SCOPES = ("global", "new", "old", "unmatched")
_NPL_SCOPES = ("new", "old", "unmatched")
_FAST_INTERVAL = 15       # 今天/昨天实时大盘（TTL 60s）
_NPL_INTERVAL = 120       # 今天/昨天 npl（带明细，TTL 660s）
_SLOW_INTERVAL = 300      # 周/月（TTL 660s/1800s）


@dataclass(frozen=True)
class WarmupTarget:
    range_name: str
    module: str            # "realtime" / "npl"
    scope: str             # "global"/"new"/"old"/"unmatched"
    interval_seconds: int


def _build_targets() -> list[WarmupTarget]:
    out: list[WarmupTarget] = []
    for r in ("today", "yesterday"):
        for s in _REALTIME_SCOPES:
            out.append(WarmupTarget(r, "realtime", s, _FAST_INTERVAL))
        for s in _NPL_SCOPES:
            out.append(WarmupTarget(r, "npl", s, _NPL_INTERVAL))
        out.append(WarmupTarget(r, "subtab", "global", _NPL_INTERVAL))
    for r in ("thisWeek", "lastWeek", "thisMonth", "lastMonth"):
        for s in _REALTIME_SCOPES:
            out.append(WarmupTarget(r, "realtime", s, _SLOW_INTERVAL))
        for s in _NPL_SCOPES:
            out.append(WarmupTarget(r, "npl", s, _SLOW_INTERVAL))
        out.append(WarmupTarget(r, "subtab", "global", _SLOW_INTERVAL))
    return out


WARMUP_TARGETS = _build_targets()
# 快线：今天/昨天实时大盘——必须高频续期、绝不被周月/npl 的重现算阻塞
FAST_TARGETS = [t for t in WARMUP_TARGETS
                if t.range_name in ("today", "yesterday") and t.module == "realtime"]
SLOW_TARGETS = [t for t in WARMUP_TARGETS if t not in FAST_TARGETS]

_last_run: dict[tuple[str, str, str], float] = {}
_lock = threading.Lock()


def _now() -> float:
    return time.monotonic()


def _due(targets: list[WarmupTarget], now: float) -> list[WarmupTarget]:
    return [
        t for t in targets
        if now - _last_run.get((t.range_name, t.module, t.scope), 0.0) >= t.interval_seconds
    ]


def _warm_one(target: WarmupTarget) -> None:
    """对单个目标强制重算并写入与 route 完全一致的 cache_key（force_refresh）。"""
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
    elif target.module == "subtab":
        # 实时大盘子 Tab（ROAS走势/订单明细/产品销量/广告计划）：
        # include_details + 分页，无 scope / 无 window / 无 profit_summary，逐字匹配前端 loadRealtimeSubTabs
        kwargs = {
            "start_date": si, "end_date": ei,
            "include_details": True,
            "order_page": 1, "order_page_size": 30, "page": 1, "page_size": 30,
        }
        cache_params = {
            "date": None, "start_date": si, "end_date": ei,
            "include_details": True, "include_profit_summary": False,
            "product_id": None, "site_code": "",
            "product_launch_scope": None, "product_launch_window_days": None,
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
    _compute_realtime_overview_cached(None, kwargs, cache_params=cache_params, force_refresh=True)


def _run(targets: list[WarmupTarget]) -> None:
    now = _now()
    with _lock:
        due = _due(targets, now)
    for t in due:
        try:
            _warm_one(t)
        except Exception:
            log.warning("realtime warmup failed range=%s module=%s scope=%s",
                        t.range_name, t.module, t.scope, exc_info=True)
        with _lock:
            _last_run[(t.range_name, t.module, t.scope)] = _now()


def run_warmup_fast() -> None:
    """快线：今天/昨天实时大盘高频强制续期（独立调度，不被周月/npl 重活阻塞）。"""
    _run(FAST_TARGETS)


def run_warmup_slow() -> None:
    """慢线：周/月 + 新品投放分析低频强制续期。"""
    _run(SLOW_TARGETS)
