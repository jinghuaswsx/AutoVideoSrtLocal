"""实时大盘数据缓存层（per-range 纯时间 TTL 模型）。

每个缓存条目带自己的 TTL（由调用方按日期范围决定）：
- 单日含今天（today）：``TTL_SINGLE_DAY_OPEN`` 60s（配 15s 高频预热，数据旧 ≤1 分钟）。
- 多日含今天（本周/本月）：``TTL_MULTI_DAY_OPEN`` ~10 分钟（配 10 分钟预热）。
- 历史已收盘区间：``TTL_CLOSED`` 30 分钟。

不再用全局 freshness marker 失效——预热主动刷新缓存，TTL 控制新鲜度上限，
历史区间也不会被「今天的新订单」误伤（旧模型的根因）。

Docs-anchor: 本文件即为规范，无单独 spec。
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

# ── 可调 TTL（秒）──────────────────────────────────────
TTL_SINGLE_DAY_OPEN = 60       # 单日含今天（today）：配 15s 预热
TTL_MULTI_DAY_OPEN = 660       # 多日含今天（本周/本月）：配 10min 预热，留余量
TTL_CLOSED = 1800             # 历史已收盘区间：30 分钟
_DEFAULT_TTL = 60
_MAX_ENTRIES = 200            # 最多缓存条目数，防内存膨胀

# key → (store_time, ttl_seconds, result)
_store: dict[str, tuple[float, float, Any]] = {}
_lock = threading.Lock()

# 新鲜度标记仍保留（仅供 stats 调试接口展示，不参与失效判定）
_freshness_cache: tuple[float, str] = (0.0, "")
_FRESHNESS_TTL = 10


def make_cache_key(params: dict[str, Any]) -> str:
    """将请求参数归一化为稳定缓存键（忽略 None / 空字符串）。"""
    filtered = sorted(
        (k, str(v))
        for k, v in params.items()
        if v is not None and str(v).strip() != ""
    )
    raw = json.dumps(filtered, sort_keys=True, ensure_ascii=False)
    return "rt:" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def get_freshness_marker() -> str:
    """最新数据版本标记（轻量 DB 查询）。仅供 stats 调试展示，不再参与失效。"""
    global _freshness_cache
    now = time.time()
    cached_time, cached_marker = _freshness_cache
    if now - cached_time < _FRESHNESS_TTL and cached_marker:
        return cached_marker
    try:
        from appcore.order_analytics import query as oa_query
        snap_rows = oa_query(
            "SELECT MAX(id) AS max_id, MAX(snapshot_at) AS max_snap "
            "FROM roi_realtime_daily_snapshots"
        )
        snap_part = ""
        if snap_rows and snap_rows[0]:
            snap_part = f"{snap_rows[0].get('max_id')}:{snap_rows[0].get('max_snap')}"
        order_rows = oa_query("SELECT MAX(id) AS max_id FROM dianxiaomi_order_lines")
        order_part = ""
        if order_rows and order_rows[0]:
            order_part = f"{order_rows[0].get('max_id')}"
        marker = f"s:{snap_part}|o:{order_part}"
    except Exception:
        log.debug("freshness marker query failed, using empty", exc_info=True)
        marker = ""
    _freshness_cache = (now, marker)
    return marker


def get(key: str, ttl_seconds: float | None = None) -> Any | None:
    """从缓存读取结果。age 超过条目自身 TTL（或显式传入的 ttl）即失效。"""
    with _lock:
        entry = _store.get(key)
        if entry is None:
            return None
        store_time, stored_ttl, result = entry
        age = time.time() - store_time
        effective_ttl = ttl_seconds if ttl_seconds is not None else stored_ttl
        if age > effective_ttl:
            del _store[key]
            log.debug("cache EXPIRED key=%s age=%.0fs ttl=%.0fs", key, age, effective_ttl)
            return None
        log.debug("cache HIT key=%s age=%.0fs ttl=%.0fs", key, age, effective_ttl)
        return result


def put(key: str, result: Any, ttl_seconds: float = _DEFAULT_TTL) -> None:
    """将计算结果写入缓存，带该条目的 TTL。"""
    with _lock:
        _store[key] = (time.time(), float(ttl_seconds), result)
        if len(_store) > _MAX_ENTRIES:
            _prune_oldest_locked()
        log.debug("cache PUT key=%s entries=%d ttl=%.0fs", key, len(_store), ttl_seconds)


def invalidate_all() -> None:
    """清除所有缓存条目（可被外部数据同步流程调用）。"""
    global _freshness_cache
    with _lock:
        count = len(_store)
        _store.clear()
        _freshness_cache = (0.0, "")
    if count:
        log.info("realtime cache invalidated: cleared %d entries", count)


def stats() -> dict[str, Any]:
    """返回缓存统计信息，供调试接口使用。"""
    with _lock:
        now = time.time()
        return {
            "entries": len(_store),
            "keys": list(_store.keys()),
            "ages_seconds": {k: round(now - t, 1) for k, (t, _, _) in _store.items()},
            "ttls_seconds": {k: ttl for k, (_, ttl, _) in _store.items()},
            "max_entries": _MAX_ENTRIES,
        }


def _prune_oldest_locked() -> None:
    """在锁内淘汰最旧的条目，保持容量在 MAX_ENTRIES 以内。"""
    if len(_store) <= _MAX_ENTRIES:
        return
    by_age = sorted(_store.items(), key=lambda kv: kv[1][0])
    to_remove = max(1, len(by_age) // 5)
    for k, _ in by_age[:to_remove]:
        del _store[k]
    log.debug("cache pruned %d oldest entries, remaining=%d", to_remove, len(_store))
