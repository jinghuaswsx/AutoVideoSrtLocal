"""实时大盘数据缓存层。

实时大盘数据约每 20 分钟更新一次（Meta 广告快照 + 店小秘订单同步周期），
在数据未更新期间，所有请求直接命中缓存，避免重复执行昂贵的聚合查询。

缓存失效策略（两层）：
1. **短窗口保护**（60 秒）：60 秒内重复请求直接返回缓存，不做任何检查。
   ─ 覆盖场景：切 Tab、刷新、多卡片并发。
2. **新鲜度检查**（60–1800 秒）：超 60 秒后，用轻量 MAX(id) 查询判断
   ``roi_realtime_daily_snapshots`` 是否有新快照；未变则继续复用缓存。
3. **硬 TTL**（1800 秒 / 30 分钟）：超过 30 分钟一律重新计算。

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

# ── 可调参数 ──────────────────────────────────────────
_MIN_RECHECK_SECONDS = 60      # 60 秒内不检查新鲜度
_MAX_AGE_SECONDS = 1800        # 30 分钟硬过期
_MAX_ENTRIES = 200             # 最多缓存条目数，防内存膨胀

# ── 内部存储 ──────────────────────────────────────────
# key → (store_time, freshness_marker, result)
_store: dict[str, tuple[float, str, Any]] = {}
_lock = threading.Lock()

# 新鲜度标记缓存：避免每次都查 DB
_freshness_cache: tuple[float, str] = (0.0, "")
_FRESHNESS_TTL = 10  # 新鲜度标记自身缓存 10 秒


def make_cache_key(params: dict[str, Any]) -> str:
    """将请求参数归一化为稳定缓存键。

    忽略 None / 空字符串的参数，确保不同顺序产生相同的 key。
    """
    filtered = sorted(
        (k, str(v))
        for k, v in params.items()
        if v is not None and str(v).strip() != ""
    )
    raw = json.dumps(filtered, sort_keys=True, ensure_ascii=False)
    return "rt:" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def get_freshness_marker() -> str:
    """获取最新数据版本标记（轻量 DB 查询）。

    查 ``roi_realtime_daily_snapshots`` 的 ``MAX(id)`` + ``MAX(snapshot_at)``，
    以及 ``dianxiaomi_order_lines`` 的 ``MAX(id)`` 作为联合新鲜度标记。
    这两个 MAX 查询走索引，耗时 < 1ms。
    """
    global _freshness_cache
    now = time.time()
    cached_time, cached_marker = _freshness_cache
    if now - cached_time < _FRESHNESS_TTL and cached_marker:
        return cached_marker

    try:
        from appcore.order_analytics import query as oa_query
        # 快照新鲜度
        snap_rows = oa_query(
            "SELECT MAX(id) AS max_id, MAX(snapshot_at) AS max_snap "
            "FROM roi_realtime_daily_snapshots"
        )
        snap_part = ""
        if snap_rows and snap_rows[0]:
            snap_part = f"{snap_rows[0].get('max_id')}:{snap_rows[0].get('max_snap')}"

        # 订单新鲜度
        order_rows = oa_query(
            "SELECT MAX(id) AS max_id FROM dianxiaomi_order_lines"
        )
        order_part = ""
        if order_rows and order_rows[0]:
            order_part = f"{order_rows[0].get('max_id')}"

        marker = f"s:{snap_part}|o:{order_part}"
    except Exception:
        log.debug("freshness marker query failed, using empty", exc_info=True)
        marker = ""

    _freshness_cache = (now, marker)
    return marker


def get(key: str, freshness_marker: str) -> Any | None:
    """从缓存读取结果。

    返回 None 表示缓存未命中或已过期，调用方需重新计算。
    """
    with _lock:
        entry = _store.get(key)
        if entry is None:
            return None
        store_time, stored_marker, result = entry
        age = time.time() - store_time

        # 硬 TTL
        if age > _MAX_AGE_SECONDS:
            del _store[key]
            log.debug("cache EXPIRED (hard TTL) key=%s age=%.0fs", key, age)
            return None

        # 短窗口保护：60 秒内直接返回
        if age <= _MIN_RECHECK_SECONDS:
            log.debug("cache HIT (fast path) key=%s age=%.0fs", key, age)
            return result

        # 新鲜度检查：数据没变则继续复用
        if stored_marker and stored_marker == freshness_marker:
            log.debug(
                "cache HIT (freshness ok) key=%s age=%.0fs",
                key, age,
            )
            return result

        # 新鲜度变了，失效
        del _store[key]
        log.debug(
            "cache INVALIDATED (data changed) key=%s age=%.0fs",
            key, age,
        )
        return None


def put(key: str, result: Any, freshness_marker: str) -> None:
    """将计算结果写入缓存。"""
    with _lock:
        _store[key] = (time.time(), freshness_marker, result)
        # 懒清理：超过 MAX_ENTRIES 时淘汰最旧条目
        if len(_store) > _MAX_ENTRIES:
            _prune_oldest_locked()
        log.debug("cache PUT key=%s entries=%d", key, len(_store))


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
            "ages_seconds": {
                k: round(now - t, 1) for k, (t, _, _) in _store.items()
            },
            "max_age_seconds": _MAX_AGE_SECONDS,
            "min_recheck_seconds": _MIN_RECHECK_SECONDS,
        }


def _prune_oldest_locked() -> None:
    """在锁内淘汰最旧的条目，保持容量在 MAX_ENTRIES 以内。"""
    if len(_store) <= _MAX_ENTRIES:
        return
    # 按 store_time 排序，删除最旧的 20%
    by_age = sorted(_store.items(), key=lambda kv: kv[1][0])
    to_remove = max(1, len(by_age) // 5)
    for k, _ in by_age[:to_remove]:
        del _store[k]
    log.debug("cache pruned %d oldest entries, remaining=%d", to_remove, len(_store))
