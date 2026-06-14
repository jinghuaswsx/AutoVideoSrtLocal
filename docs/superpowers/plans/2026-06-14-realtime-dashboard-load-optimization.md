# 实时大盘加载优化 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让实时大盘今天/昨天/本周/首次进入不再「整块加载不出来」——缓存按收盘分层修复历史误伤、后台预热让默认视图秒开、前端 allSettled 让偶发慢不连累整块。

**Architecture:** 三组件独立可测：(1) `realtime_cache` 增加 `is_open_day` 维度，closed 区间走纯时间 TTL 不被全局 marker 误伤；(2) 新增 APScheduler 预热任务，按前端日历口径预算 today/yesterday/thisWeek/lastWeek 的 4 个 scope 写入同一 cache_key；(3) 前端顶部卡片 4 请求各自独立 AbortController + Promise.allSettled。不重构后端聚合（方案 B 不在本计划）。

**Tech Stack:** Python 3.12 / Flask / pymysql+PooledDB / APScheduler(BackgroundScheduler) / 原生 JS / pytest。

**Spec:** `docs/superpowers/specs/2026-06-14-realtime-dashboard-load-optimization-design.md`

---

## File Structure

| 文件 | 动作 | 责任 |
|---|---|---|
| `appcore/order_analytics/realtime_cache.py` | Modify | `get/put` 增加 `is_open_day`；closed 走时间 TTL |
| `web/routes/order_analytics.py` | Modify | `realtime_overview` 算 `is_open_day`；提取 `_compute_realtime_overview_cached` 公共入口 |
| `appcore/order_analytics/realtime_warmup.py` | Create | 前端日历 range 解析 + 预热 tick 逻辑（纯逻辑，可单测） |
| `appcore/order_analytics/realtime_warmup_scheduler.py` | Create | `register(scheduler)` 挂 APScheduler interval 任务 |
| `appcore/scheduled_tasks.py` | Modify | `TASK_DEFINITIONS` 增加 `realtime_overview_warmup` 登记 |
| `appcore/scheduler.py` | Modify | `get_scheduler()` 增加 `realtime_warmup_scheduler.register` |
| `web/templates/order_analytics.html` | Modify | `loadRealtimeTopCards` 改独立 controller + allSettled |
| `tests/test_realtime_cache.py` | Create | 缓存分层单测 |
| `tests/test_realtime_warmup.py` | Create | range 解析对拍 + 预热到期/串行单测 |

---

## Task 1: 缓存按 is_open_day 分层

**Files:**
- Modify: `appcore/order_analytics/realtime_cache.py`
- Test: `tests/test_realtime_cache.py`

- [ ] **Step 1: 写失败测试**

`tests/test_realtime_cache.py`:
```python
import time
import pytest
from appcore.order_analytics import realtime_cache


@pytest.fixture(autouse=True)
def _clean():
    realtime_cache.invalidate_all()
    yield
    realtime_cache.invalidate_all()


def test_closed_range_not_invalidated_by_global_marker():
    realtime_cache.put("k_closed", {"v": 1}, "marker_v1", is_open_day=False)
    got = realtime_cache.get("k_closed", "marker_v2_changed", is_open_day=False)
    assert got == {"v": 1}


def test_closed_range_expires_after_ttl(monkeypatch):
    base = time.time()
    monkeypatch.setattr(time, "time", lambda: base)
    realtime_cache.put("k_closed", {"v": 1}, "m", is_open_day=False)
    monkeypatch.setattr(time, "time", lambda: base + realtime_cache._CLOSED_TTL_SECONDS + 1)
    assert realtime_cache.get("k_closed", "m", is_open_day=False) is None


def test_open_range_marker_change_invalidates_after_window(monkeypatch):
    base = time.time()
    monkeypatch.setattr(time, "time", lambda: base)
    realtime_cache.put("k_open", {"v": 1}, "m1", is_open_day=True)
    monkeypatch.setattr(time, "time", lambda: base + realtime_cache._MIN_RECHECK_SECONDS + 1)
    assert realtime_cache.get("k_open", "m2", is_open_day=True) is None
    realtime_cache.put("k_open2", {"v": 2}, "m1", is_open_day=True)
    monkeypatch.setattr(time, "time", lambda: base + realtime_cache._MIN_RECHECK_SECONDS + 1)
    assert realtime_cache.get("k_open2", "m1", is_open_day=True) == {"v": 2}


def test_open_range_fast_path_within_window(monkeypatch):
    base = time.time()
    monkeypatch.setattr(time, "time", lambda: base)
    realtime_cache.put("k_open", {"v": 1}, "m1", is_open_day=True)
    monkeypatch.setattr(time, "time", lambda: base + 5)
    assert realtime_cache.get("k_open", "different_marker", is_open_day=True) == {"v": 1}
```

- [ ] **Step 2: 运行验证失败**

Run: `python3 -m pytest tests/test_realtime_cache.py -q`
Expected: FAIL（`get()/put()` 不接受 `is_open_day` → TypeError）

- [ ] **Step 3: 实现分层**

`realtime_cache.py` 常量区（`_MAX_AGE_SECONDS = 1800` 附近）增加：
```python
_CLOSED_TTL_SECONDS = 1800     # 收盘区间纯时间 TTL（30 分钟），不受全局 marker 影响
```

替换 `get` 函数为：
```python
def get(key: str, freshness_marker: str, is_open_day: bool = True) -> Any | None:
    """从缓存读取结果。

    is_open_day=False（收盘区间）：纯时间 TTL，不与全局 marker 比较，
    避免历史缓存被「今天的新订单」误伤。
    is_open_day=True（含今天）：60s 短窗口 + marker 新鲜度检查。
    """
    with _lock:
        entry = _store.get(key)
        if entry is None:
            return None
        store_time, stored_marker, result = entry
        age = time.time() - store_time

        if not is_open_day:
            if age > _CLOSED_TTL_SECONDS:
                del _store[key]
                log.debug("cache EXPIRED (closed TTL) key=%s age=%.0fs", key, age)
                return None
            log.debug("cache HIT (closed) key=%s age=%.0fs", key, age)
            return result

        if age > _MAX_AGE_SECONDS:
            del _store[key]
            log.debug("cache EXPIRED (hard TTL) key=%s age=%.0fs", key, age)
            return None
        if age <= _MIN_RECHECK_SECONDS:
            log.debug("cache HIT (fast path) key=%s age=%.0fs", key, age)
            return result
        if stored_marker and stored_marker == freshness_marker:
            log.debug("cache HIT (freshness ok) key=%s age=%.0fs", key, age)
            return result
        del _store[key]
        log.debug("cache INVALIDATED (data changed) key=%s age=%.0fs", key, age)
        return None
```

替换 `put` 签名（存储不变，向后兼容）：
```python
def put(key: str, result: Any, freshness_marker: str, is_open_day: bool = True) -> None:
    """将计算结果写入缓存。is_open_day 仅由读取方使用，此处只存储。"""
    with _lock:
        _store[key] = (time.time(), freshness_marker, result)
        if len(_store) > _MAX_ENTRIES:
            _prune_oldest_locked()
        log.debug("cache PUT key=%s entries=%d open=%s", key, len(_store), is_open_day)
```

- [ ] **Step 4: 运行验证通过**

Run: `python3 -m pytest tests/test_realtime_cache.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add appcore/order_analytics/realtime_cache.py tests/test_realtime_cache.py
git commit -m "feat(realtime-cache): add is_open_day layering so closed ranges aren't invalidated by today's orders"
```

---

## Task 2: route 计算 is_open_day + 提取统一计算入口

**Files:**
- Modify: `web/routes/order_analytics.py:1361-1399`

- [ ] **Step 1: 提取公共入口并接缓存分层**

在 `realtime_overview` 函数之前新增 module 级 helper：
```python
def _compute_realtime_overview_cached(date_text, kwargs, *, cache_params):
    """实时大盘 overview 的统一「缓存读→算→写」入口，route 与后台预热共用。

    返回 (payload_dict, cache_state)，cache_state ∈ {"HIT","MISS"}。
    """
    from appcore.order_analytics import realtime_cache
    from appcore.order_analytics._helpers import current_meta_business_date

    end_text = cache_params.get("end_date") or cache_params.get("date")
    is_open_day = True
    if end_text:
        try:
            end_d = date.fromisoformat(str(end_text))
            is_open_day = end_d >= current_meta_business_date()
        except ValueError:
            is_open_day = True

    cache_key = realtime_cache.make_cache_key(cache_params)
    freshness_marker = realtime_cache.get_freshness_marker()
    cached = realtime_cache.get(cache_key, freshness_marker, is_open_day)
    if cached is not None:
        return cached, "HIT"

    result = oa.get_realtime_roas_overview(date_text, **kwargs)
    result = _attach_realtime_data_quality(result)
    safe_result = _json_safe(result)
    realtime_cache.put(cache_key, safe_result, freshness_marker, is_open_day)
    return safe_result, "MISS"
```
确保文件顶部已 `from datetime import date`（若无则添加）。

替换 `realtime_overview` 内 1361-1399 的缓存+try 段为：
```python
    cache_params = {
        "date": date_text, "start_date": start_date, "end_date": end_date,
        "include_details": include_details, "include_profit_summary": include_profit_summary,
        "product_id": kwargs.get("product_id"), "site_code": site_code_text,
        "product_launch_scope": product_launch_scope,
        "product_launch_window_days": kwargs.get("product_launch_window_days"),
        "page": kwargs.get("page"), "page_size": kwargs.get("page_size"),
        "order_page": kwargs.get("order_page"), "order_page_size": kwargs.get("order_page_size"),
    }
    try:
        payload, state = _compute_realtime_overview_cached(date_text, kwargs, cache_params=cache_params)
        resp = _json_response(payload)
        resp.headers["X-Realtime-Cache"] = state
        return resp
    except ValueError as exc:
        return _json_response(error="invalid_date", detail=str(exc)), 400
    except Exception as exc:
        log.exception("realtime roas overview query failed: %s", exc)
        return _json_response(error="internal_error", detail=str(exc)), 500
```

- [ ] **Step 2: 验证既有 route 测试不回归**

Run: `python3 -m pytest tests/test_order_analytics_tab_routes.py tests/test_order_analytics_realtime_site_filter.py -q`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add web/routes/order_analytics.py
git commit -m "refactor(realtime): single cache entry _compute_realtime_overview_cached with is_open_day"
```

---

## Task 3: 前端日历 range 解析（后端复刻，周一起算）

**Files:**
- Create: `appcore/order_analytics/realtime_warmup.py`
- Test: `tests/test_realtime_warmup.py`

- [ ] **Step 1: 写失败测试（对拍前端定义）**

`tests/test_realtime_warmup.py`:
```python
from datetime import date
from appcore.order_analytics import realtime_warmup as rw


def test_today_and_yesterday():
    t = date(2026, 6, 10)  # 周三
    assert rw.resolve_meta_calendar_range("today", t) == (date(2026, 6, 10), date(2026, 6, 10))
    assert rw.resolve_meta_calendar_range("yesterday", t) == (date(2026, 6, 9), date(2026, 6, 9))


def test_this_week_monday_start_sunday_end():
    t = date(2026, 6, 10)  # 周三 → 周一 6-08，周日 6-14
    assert rw.resolve_meta_calendar_range("thisWeek", t) == (date(2026, 6, 8), date(2026, 6, 14))


def test_last_week():
    t = date(2026, 6, 10)
    assert rw.resolve_meta_calendar_range("lastWeek", t) == (date(2026, 6, 1), date(2026, 6, 7))


def test_monday_itself():
    t = date(2026, 6, 8)  # 周一
    assert rw.resolve_meta_calendar_range("thisWeek", t) == (date(2026, 6, 8), date(2026, 6, 14))
```

- [ ] **Step 2: 运行验证失败**

Run: `python3 -m pytest tests/test_realtime_warmup.py -q`
Expected: FAIL（module 不存在）

- [ ] **Step 3: 实现 range 解析**

`appcore/order_analytics/realtime_warmup.py`:
```python
"""实时大盘 overview 后台预热。

range 解析必须与前端 order_analytics.html 的 orderAnalyticsMetaCalendar
逐字一致：**周一起算**（不可复用 weekly_ai_report 的周日起算），thisWeek.end
为本周日（可能为未来日期）。否则 cache_key 不匹配，预热白做。
"""
from __future__ import annotations

from datetime import date, timedelta


def _start_of_week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def resolve_meta_calendar_range(range_name: str, today: date) -> tuple[date, date]:
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
```

- [ ] **Step 4: 运行验证通过**

Run: `python3 -m pytest tests/test_realtime_warmup.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add appcore/order_analytics/realtime_warmup.py tests/test_realtime_warmup.py
git commit -m "feat(realtime-warmup): meta-calendar range resolver aligned to frontend (Monday-start)"
```

---

## Task 4: 预热 tick（到期判定 + 串行执行）

**Files:**
- Modify: `appcore/order_analytics/realtime_warmup.py`
- Test: `tests/test_realtime_warmup.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_realtime_warmup.py`:
```python
def test_warmup_targets_intervals():
    targets = {(t.range_name, t.scope): t.interval_seconds for t in rw.WARMUP_TARGETS}
    assert targets[("today", "global")] == 45
    assert targets[("today", "new")] == 150
    assert targets[("thisWeek", "global")] == 45
    assert targets[("yesterday", "global")] == 1200
    assert targets[("lastWeek", "unmatched")] == 1200
    assert all(t.range_name in {"today", "yesterday", "thisWeek", "lastWeek"} for t in rw.WARMUP_TARGETS)


def test_due_targets_respects_last_run():
    import appcore.order_analytics.realtime_warmup as m
    m._last_run.clear()
    now = 1000.0
    assert m._due_targets(now), "首次全部到期"
    m._last_run[("today", "global")] = now
    due2 = {(t.range_name, t.scope) for t in m._due_targets(now + 10)}
    assert ("today", "global") not in due2
    assert ("today", "new") in due2


def test_run_tick_serial_calls_warm_one(monkeypatch):
    import appcore.order_analytics.realtime_warmup as m
    m._last_run.clear()
    calls = []
    monkeypatch.setattr(m, "_warm_one", lambda t: calls.append((t.range_name, t.scope)))
    monkeypatch.setattr(m, "_now", lambda: 5000.0)
    m.run_warmup_tick()
    assert ("today", "global") in calls
    assert len(calls) == len(m.WARMUP_TARGETS)
```

- [ ] **Step 2: 运行验证失败**

Run: `python3 -m pytest tests/test_realtime_warmup.py -q`
Expected: FAIL（`WARMUP_TARGETS`/`_due_targets`/`run_warmup_tick` 未定义）

- [ ] **Step 3: 实现预热 tick**

追加到 `appcore/order_analytics/realtime_warmup.py`:
```python
import logging
import threading
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

_OPEN_RANGES = ("today", "thisWeek")
_CLOSED_RANGES = ("yesterday", "lastWeek")


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
```
> 注：`_warm_one` 的 `cache_params` 必须与 Task 2 route 顶部卡片默认视图（无 site/product、window=7、include_profit_summary=True、include_details=False）逐字对应；改 route 的 cache_params 字段时同步改这里。

- [ ] **Step 4: 运行验证通过**

Run: `python3 -m pytest tests/test_realtime_warmup.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add appcore/order_analytics/realtime_warmup.py tests/test_realtime_warmup.py
git commit -m "feat(realtime-warmup): tiered due-based warmup tick (serial, reuses route cache entry)"
```

---

## Task 5: 调度注册 + 任务登记

**Files:**
- Create: `appcore/order_analytics/realtime_warmup_scheduler.py`
- Modify: `appcore/scheduled_tasks.py`、`appcore/scheduler.py`

- [ ] **Step 1: 新建 register 模块**

`appcore/order_analytics/realtime_warmup_scheduler.py`:
```python
"""把实时大盘 overview 预热挂到 Web 进程 APScheduler。"""
from __future__ import annotations

from appcore import scheduled_tasks
from appcore.order_analytics.realtime_warmup import run_warmup_tick


def register(scheduler) -> None:
    scheduled_tasks.add_controlled_job(
        scheduler, "realtime_overview_warmup", run_warmup_tick,
        "interval", seconds=15, id="realtime_overview_warmup",
    )
```

- [ ] **Step 2: TASK_DEFINITIONS 登记**

`appcore/scheduled_tasks.py` 的 `TASK_DEFINITIONS` 新增一条（紧挨其它 apscheduler 任务）：
```python
    "realtime_overview_warmup": {
        "code": "realtime_overview_warmup",
        "name": "实时大盘 overview 预热",
        "description": (
            "每 15s tick，按前端 Meta 日历口径预算 today/yesterday/thisWeek/lastWeek 的 "
            "global/new/old/unmatched 四个 scope 并写入实时缓存，使用户首次打开命中缓存秒开。"
            "open 区间 global 45s/其余 150s，closed 区间 1200s。"
            "Spec: docs/superpowers/specs/2026-06-14-realtime-dashboard-load-optimization-design.md"
        ),
        "schedule": "每 15s tick（分级：open-global 45s / open-其余 150s / closed 1200s）",
        "source_type": "apscheduler",
        "source_label": "Web 进程 APScheduler",
        "source_ref": "appcore/order_analytics/realtime_warmup_scheduler.py",
        "runner": "appcore/order_analytics/realtime_warmup.py::run_warmup_tick",
        "deployment": "随 Web 进程启动",
        "log_table": "scheduled_task_runs",
    },
```

- [ ] **Step 3: get_scheduler 注册**

`appcore/scheduler.py` 的 `get_scheduler()` 内，仿照 `weekly_roas_report.register(_scheduler)` 增加：
```python
        from appcore.order_analytics import realtime_warmup_scheduler
        realtime_warmup_scheduler.register(_scheduler)
```

- [ ] **Step 4: 写并运行登记测试**

追加到 `tests/test_realtime_warmup.py`:
```python
def test_task_registered_in_definitions():
    from appcore import scheduled_tasks
    assert "realtime_overview_warmup" in scheduled_tasks.TASK_DEFINITIONS
    t = scheduled_tasks.TASK_DEFINITIONS["realtime_overview_warmup"]
    assert t["source_type"] == "apscheduler"
    assert t["runner"].endswith("run_warmup_tick")
```
Run: `python3 -m pytest tests/test_realtime_warmup.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add appcore/order_analytics/realtime_warmup_scheduler.py appcore/scheduled_tasks.py appcore/scheduler.py tests/test_realtime_warmup.py
git commit -m "feat(realtime-warmup): register APScheduler interval job + TASK_DEFINITIONS entry"
```

---

## Task 6: 前端 allSettled 解耦

**Files:**
- Modify: `web/templates/order_analytics.html`（`loadRealtimeTopCards` 7984-8040）

- [ ] **Step 1: 改 4 请求各自独立 controller + allSettled**

把 `loadRealtimeTopCards` 内 `var controller = createRealtimeController('top');`（7986）起、至原 `.finally(...)` 结束的整段，替换为：
```javascript
    clearRealtimeController('top', realtimeState.topCardsController);
    setRealtimeScopeCardsLoading();
    var scopes = ['global', 'new', 'old', 'unmatched'];
    var jobs = scopes.map(function(scope) {
      var c = (typeof AbortController !== 'undefined') ? new AbortController() : null;
      return fetchRealtimeScopeSummary(baseParams, scope, c)
        .then(function(data) { return { scope: scope, ok: true, data: data || {} }; })
        .catch(function(err) { return { scope: scope, ok: false, err: err }; });
    });
    Promise.all(jobs).then(function(results) {
      if (!isRealtimeRequestCurrent('top', requestSeq)) return;
      var byScope = {};
      results.forEach(function(r) { byScope[r.scope] = r; });
      ['new', 'old', 'unmatched'].forEach(function(scope) {
        var r = byScope[scope];
        if (r && r.ok) { renderRealtimeScopeSummary(scope, r.data); }
        else if (r && !isRealtimeAbortError(r.err)) { setRealtimeScopeCardError(scope, r.err.message); }
      });
      var g = byScope.global, n = byScope['new'], o = byScope.old, u = byScope.unmatched;
      if (g && g.ok) {
        var globalData = g.data;
        if (n && n.ok && o && o.ok && u && u.ok) {
          globalData = reconcileRealtimeGlobalScopeProfit(globalData, n.data, o.data, u.data);
        }
        if (typeof window.renderDataQualityBar === 'function') {
          window.renderDataQualityBar(globalData && globalData.data_quality);
        }
        renderRealtimeScopeSummary('global', globalData);
        renderRealtimeFreshness(globalData);
      } else if (g && !isRealtimeAbortError(g.err)) {
        setRealtimeScopeCardError('global', g.err.message);
      }
    });
```
> `baseParams` 已在该函数上方（8007）定义。删除原单 controller + `Promise.all([...4 fetch...]).then().catch().finally()` 整段。

- [ ] **Step 2: 新增单 scope 错误渲染辅助函数**

在 `setRealtimeScopeCardsError`（8168）之后新增：
```javascript
  function setRealtimeScopeCardError(scope, message) {
    var prefix = realtimeScopePrefix(scope);
    ['Revenue','Shipping','RevenueWithShipping','Spend','Roas','MetaRoas','PurchaseCost',
     'LogisticsCost','ShopifyFee','GlobalBreakEvenRoas','ProfitDeduction','OrderCount','Units','Profit']
      .forEach(function(s){ setRealtimeProfitText(prefix + s, '-'); });
    setRealtimeProfitText(prefix + 'RevenueSub', '加载失败');
    setRealtimeProfitText(prefix + 'ProfitSub', message || '加载失败');
    setRealtimeProfitText(realtimeScopeSourceId(scope), '加载失败：' + (message || '查询失败'));
  }
```

- [ ] **Step 3: Commit**

```bash
git add web/templates/order_analytics.html
git commit -m "fix(realtime-frontend): per-scope controllers + allSettled so one slow scope no longer fails the whole top row"
```

---

## Task 7: 回归 + 线上 Playwright 复测

- [ ] **Step 1: 后端回归（order_analytics CLAUDE.md 硬规则集）**

Run:
```bash
python3 -m pytest tests/test_order_analytics_realtime_site_filter.py \
  tests/test_order_analytics_true_roas.py tests/test_order_analytics_data_quality.py \
  tests/test_order_profit_aggregation.py tests/test_order_analytics_ads.py \
  tests/test_product_profit_report.py tests/characterization/test_order_analytics_baseline.py \
  tests/test_realtime_cache.py tests/test_realtime_warmup.py -q
```
Expected: 全 PASS（个别用例若依赖不可用 DB fixture，记录并改跑 `python3 scripts/pytest_related.py --base origin/master --run`）

- [ ] **Step 2: 前端 5 场景复测**

Run: `cd /tmp && timeout 200 python3 /tmp/rt_test2.py 2>&1 | grep -vE "^\[" | head -40`
Expected: 5 场景全部渲染金额；某 scope 慢时其余卡片不再整块 `-`；命中预热的请求 `X-Realtime-Cache: HIT`。

- [ ] **Step 3: 终验汇报**

汇总：全量是否跳过 + 理由、实际运行的 focused tests、Playwright 复测前后对比。

---

## Self-Review

- **Spec 覆盖**：§4 缓存分层→Task1/2；§5 预热(range/参数/频率/调度)→Task3/4/5；§6 前端 allSettled→Task6；§8 测试→各 Task + Task7。无遗漏。
- **占位符**：无 TBD/TODO；每个代码步给出完整代码。
- **类型一致**：`get/put(... is_open_day)`、`_compute_realtime_overview_cached(date_text, kwargs, *, cache_params)`、`resolve_meta_calendar_range`、`WARMUP_TARGETS/_due_targets/run_warmup_tick/_warm_one`、`setRealtimeScopeCardError` 跨 Task 命名一致。
- **依赖**：Task4 `_warm_one` import Task2 定义的 `_compute_realtime_overview_cached`；预热 cache_params 与 route 顶部卡片默认视图逐字对齐（已注记）。
