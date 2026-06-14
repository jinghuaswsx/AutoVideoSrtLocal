"""实时大盘 / 新品投放分析 overview 跨进程缓存（MySQL 表）。

历史教训（2026-06-14，实测坐实）：曾用进程内存 dict（``_store``），生产 gunicorn
多 worker 各有独立缓存、预热只填一个进程，用户并发请求（一次点 4 个 scope）分散到
不同 worker，落到未填充的 worker 就 MISS、现算十几秒。改用 MySQL 表
``roi_realtime_overview_cache`` 让所有 worker 共享同一份缓存——预热填一次，全体命中。

per-range TTL 存为 ``expires_at``（put 时 = NOW + ttl，get 时按 ``expires_at > NOW`` 过滤）：
- 单日含今天（today）：``TTL_SINGLE_DAY_OPEN`` 60s
- 多日含今天（本周/本月）：``TTL_MULTI_DAY_OPEN`` 660s
- 历史已收盘区间：``TTL_CLOSED`` 1800s

所有 DB 操作都吞异常容错——缓存层故障绝不能让 dashboard 崩。

Docs-anchor: 本文件即为规范。
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from appcore.db import query, execute

log = logging.getLogger(__name__)

# ── 可调 TTL（秒）──────────────────────────────────────
TTL_SINGLE_DAY_OPEN = 60       # 单日含今天（today），配 15s 预热
TTL_MULTI_DAY_OPEN = 660       # 多日含今天（本周/本月），配 10min 预热
TTL_CLOSED = 1800             # 历史已收盘区间：30 分钟
_DEFAULT_TTL = 60

_TABLE = "roi_realtime_overview_cache"


def make_cache_key(params: dict[str, Any]) -> str:
    """将请求参数归一化为稳定缓存键（忽略 None / 空字符串）。"""
    filtered = sorted(
        (k, str(v))
        for k, v in params.items()
        if v is not None and str(v).strip() != ""
    )
    raw = json.dumps(filtered, sort_keys=True, ensure_ascii=False)
    return "rt:" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def get(key: str, ttl_seconds: float | None = None) -> Any | None:
    """跨进程读缓存。未命中或已过期返回 None。ttl_seconds 仅为兼容签名，不参与判定。"""
    try:
        rows = query(
            f"SELECT payload FROM {_TABLE} WHERE cache_key=%s AND expires_at > NOW(3)",
            (key,),
        )
    except Exception:
        log.debug("realtime cache get failed key=%s", key, exc_info=True)
        return None
    if not rows:
        return None
    try:
        return json.loads(rows[0]["payload"])
    except Exception:
        log.debug("realtime cache payload decode failed key=%s", key, exc_info=True)
        return None


def put(key: str, result: Any, ttl_seconds: float = _DEFAULT_TTL) -> None:
    """跨进程写缓存，expires_at = NOW + ttl_seconds。"""
    try:
        payload = json.dumps(result, ensure_ascii=False, default=str)
        execute(
            f"REPLACE INTO {_TABLE} (cache_key, payload, expires_at) "
            f"VALUES (%s, %s, NOW(3) + INTERVAL %s SECOND)",
            (key, payload, int(ttl_seconds)),
        )
    except Exception:
        log.warning("realtime cache put failed key=%s", key, exc_info=True)


def touch(key: str, ttl_seconds: float = _DEFAULT_TTL) -> bool:
    """仅续期（延长 expires_at），不重算 payload；命中续期返回 True，缓存不存在返回 False。

    预热专用，把"防过期"与"重算更新数据"彻底分开：数据还新时只 touch 续期（一次轻量
    UPDATE）。否则每轮预热都强制重算（force_refresh），在 workers=1 单进程里持续霸占
    GIL + DB 连接，把用户请求饿死到 30s 超时——2026-06-14 实测坐实的事故根因
    （APScheduler 日志 run_warmup_fast "maximum number of running instances reached"）。
    """
    try:
        affected = execute(
            f"UPDATE {_TABLE} SET expires_at = NOW(3) + INTERVAL %s SECOND WHERE cache_key=%s",
            (int(ttl_seconds), key),
        )
        return bool(affected)
    except Exception:
        log.debug("realtime cache touch failed key=%s", key, exc_info=True)
        return False


def prune_expired() -> int:
    """删除已过期行，防止表膨胀。返回删除行数。"""
    try:
        return execute(f"DELETE FROM {_TABLE} WHERE expires_at < NOW(3)")
    except Exception:
        log.debug("realtime cache prune failed", exc_info=True)
        return 0


def invalidate_all() -> None:
    """清空缓存表（可被外部数据同步流程调用）。"""
    try:
        execute(f"DELETE FROM {_TABLE}")
    except Exception:
        log.warning("realtime cache invalidate failed", exc_info=True)


def get_freshness_marker() -> str:
    """已废弃（纯 TTL 模型不再用全局 marker）。保留空实现兼容旧的 stats 调试接口。"""
    return ""


def stats() -> dict[str, Any]:
    """返回缓存统计信息，供调试接口使用。"""
    try:
        rows = query(
            f"SELECT COUNT(*) AS total, "
            f"SUM(CASE WHEN expires_at > NOW(3) THEN 1 ELSE 0 END) AS live FROM {_TABLE}"
        )
        if rows:
            return {
                "backend": "mysql",
                "table": _TABLE,
                "entries": int(rows[0]["total"] or 0),
                "live": int(rows[0]["live"] or 0),
            }
    except Exception:
        log.debug("realtime cache stats failed", exc_info=True)
    return {"backend": "mysql", "table": _TABLE, "entries": 0, "live": 0}
