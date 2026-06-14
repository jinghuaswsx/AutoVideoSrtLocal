"""实时大盘 + 新品投放分析 overview 后台预热（touch 续期 + 陈旧才重算 + 快/慢双线）。

核心设计（2026-06-14 二次修复，实测坐实「点昨天 30s 超时、出不来数据」事故）：
1. **touch 续期，陈旧才重算**：命中且数据未超 TTL → 只 ``realtime_cache.touch`` 续期
   （一次轻量 UPDATE，不重算）；数据超 TTL 没重算过、或缓存被清 → 才重算一次。
   把"防过期"与"重算更新数据"分开。
   —— 上一版每轮都 ``force_refresh`` 重算，fast 单轮 16s > 15s 间隔，预热在
   ``workers=1`` 单进程里几乎一刻不停地重算、霸占 GIL + DB，把用户请求饿死到 30s 超时
   （APScheduler 日志：run_warmup_fast "maximum number of running instances reached"）。
2. **快/慢分离**：今天/昨天实时大盘走快线 ``run_warmup_fast``（15s），周月 + npl + 子Tab
   走慢线 ``run_warmup_slow``（30s），互不阻塞。

为什么不靠加 worker 进程摊负载：``deploy/gunicorn.conf.py`` 的 ``workers=1`` 是有意的
（in-process Socket.IO rooms / in-memory task state / 本预热 APScheduler 都依赖单进程）。
所以预热必须自身轻量。

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
# 上次"重算"时间（touch 续期不更新它），决定数据是否陈旧到该重算。与 _last_run（调度节流）分开。
_last_refresh: dict[tuple[str, str, str], float] = {}
_lock = threading.Lock()


def _now() -> float:
    return time.monotonic()


def _due(targets: list[WarmupTarget], now: float) -> list[WarmupTarget]:
    return [
        t for t in targets
        if now - _last_run.get((t.range_name, t.module, t.scope), 0.0) >= t.interval_seconds
    ]


def _warm_one(target: WarmupTarget) -> None:
    """对单个目标：命中且数据未陈旧 → touch 续期；陈旧/缺失 → 重算。cache_key 与 route 逐字一致。"""
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
    from web.routes.order_analytics import _overview_cache_ttl
    from appcore.order_analytics import realtime_cache

    cache_key = realtime_cache.make_cache_key(cache_params)
    ttl = _overview_cache_ttl(cache_params)
    key_t = (target.range_name, target.module, target.scope)
    # 数据超过 TTL 没重算过、或缓存已被清 → 重算更新（force）；否则只 touch 续期
    # （一次轻量 UPDATE，不重算、不占 GIL/DB）。这样预热"防过期"几乎零成本、
    # "重算更新"降到每 TTL 一次，不再每轮重算把单进程 worker 拖到用户请求 30s 超时。
    stale = (_now() - _last_refresh.get(key_t, 0.0)) >= ttl
    if stale or not realtime_cache.touch(cache_key, ttl):
        _compute_realtime_overview_cached(None, kwargs, cache_params=cache_params, force_refresh=True)
        _last_refresh[key_t] = _now()


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
