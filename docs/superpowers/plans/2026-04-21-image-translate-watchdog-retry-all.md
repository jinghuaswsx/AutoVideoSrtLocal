# 图片翻译超时兜底 + 全部重跑按钮 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让图片翻译详情页的"重试按钮"在 runner 线程卡死 30 分钟后自动解锁，并新增「全部重跑」按钮兜底整任务重置。

**Architecture:** Runner 层把 `_running_tasks` 从 `set` 升级为带 `instance_id + last_heartbeat` 的 `dict`，`is_running()` 依据 30 分钟心跳超时判定活跃；Runtime 层在关键点调 heartbeat，slot 被抢占时 raise `_WatchdogTakeover` 让旧线程退出；新路由 `POST /retry-all` 强制重置所有 item（含 done）+ 删旧 dst；前端加按钮 + 二次确认。

**Tech Stack:** Flask、threading、Python stdlib `time.monotonic` / `uuid`、pytest、原生 JS。

**Spec:** `docs/superpowers/specs/2026-04-21-image-translate-watchdog-retry-all-design.md`

---

## 文件结构

- **Modify**
  - `web/services/image_translate_runner.py`：数据结构、`is_running`、`_touch_heartbeat`、`start`、`run` finally
  - `appcore/image_translate_runtime.py`：构造函数 `heartbeat` 参数、`_beat` 方法、`_WatchdogTakeover` 异常、3 处心跳调用点、外层捕获
  - `web/routes/image_translate.py`：新增 `api_retry_all()` 路由
  - `web/templates/image_translate_detail.html`：进度卡片按钮组
  - `web/templates/_image_translate_scripts.html`：`retryAllBtn` 渲染 + 点击事件
  - `web/templates/_image_translate_styles.html`：`.it-retry-all` CSS
  - `tests/test_image_translate_routes.py`：`is_running` 超时 + `/retry-all` 测试
  - `tests/test_image_translate_runtime.py`：heartbeat 回调 + takeover 语义

- **不动**：`web/app.py`、`appcore/task_state.py`、DB schema、state_json 结构

---

## Task 1: Runner 加 watchdog（数据结构 + `is_running` 超时 + `_touch_heartbeat` + `start` instance）

**Files:**
- Modify: `web/services/image_translate_runner.py`
- Test: `tests/test_image_translate_runner.py`（若不存在则 Create）

- [ ] **Step 1.1: 确认测试文件是否存在**

Run: `ls tests/test_image_translate_runner.py 2>/dev/null && echo EXISTS || echo NEW`

若 `NEW`，Step 1.2 需要从零建文件（加 `from __future__ import annotations` 等）。

- [ ] **Step 1.2: 写失败测试**

在 `tests/test_image_translate_runner.py` 末尾追加（若文件不存在先 Create 空文件）：

```python
from __future__ import annotations

import time

import pytest


@pytest.fixture(autouse=True)
def _reset_runner_state():
    """每个测试后清 _running_tasks，避免互相污染。"""
    from web.services import image_translate_runner as r
    yield
    with r._running_tasks_lock:
        r._running_tasks.clear()


def test_is_running_false_when_no_slot():
    from web.services import image_translate_runner as r
    assert r.is_running("no-such-task") is False


def test_is_running_true_when_slot_fresh(monkeypatch):
    from web.services import image_translate_runner as r
    now = [1000.0]
    monkeypatch.setattr(r.time, "monotonic", lambda: now[0])
    with r._running_tasks_lock:
        r._running_tasks["t1"] = {"instance": "inst-a", "last": now[0]}
    now[0] = 1000.0 + 100  # 100s 后
    assert r.is_running("t1") is True


def test_is_running_false_when_slot_expired(monkeypatch):
    from web.services import image_translate_runner as r
    now = [1000.0]
    monkeypatch.setattr(r.time, "monotonic", lambda: now[0])
    with r._running_tasks_lock:
        r._running_tasks["t1"] = {"instance": "inst-a", "last": now[0]}
    now[0] = 1000.0 + r._WATCHDOG_TIMEOUT_SEC  # 恰好等于阈值
    assert r.is_running("t1") is False
    now[0] = 1000.0 + r._WATCHDOG_TIMEOUT_SEC + 1
    assert r.is_running("t1") is False


def test_touch_heartbeat_matching_instance(monkeypatch):
    from web.services import image_translate_runner as r
    now = [1000.0]
    monkeypatch.setattr(r.time, "monotonic", lambda: now[0])
    with r._running_tasks_lock:
        r._running_tasks["t1"] = {"instance": "inst-a", "last": 1000.0}
    now[0] = 1500.0
    assert r._touch_heartbeat("t1", "inst-a") is True
    assert r._running_tasks["t1"]["last"] == 1500.0


def test_touch_heartbeat_wrong_instance_returns_false():
    from web.services import image_translate_runner as r
    with r._running_tasks_lock:
        r._running_tasks["t1"] = {"instance": "inst-new", "last": 9999.0}
    assert r._touch_heartbeat("t1", "inst-old") is False
    assert r._running_tasks["t1"]["last"] == 9999.0  # 没动


def test_touch_heartbeat_missing_slot_returns_false():
    from web.services import image_translate_runner as r
    assert r._touch_heartbeat("t-missing", "any") is False


def test_start_returns_false_when_active_slot_exists(monkeypatch):
    """runtime 线程实际不跑（monkeypatch 掉 thread.start）。"""
    from web.services import image_translate_runner as r
    now = [1000.0]
    monkeypatch.setattr(r.time, "monotonic", lambda: now[0])
    with r._running_tasks_lock:
        r._running_tasks["t1"] = {"instance": "inst-existing", "last": 1000.0}
    now[0] = 1500.0  # 仍活跃
    # 阻止真的建 thread
    monkeypatch.setattr(r.threading, "Thread", lambda target, daemon: type("T", (), {"start": lambda self: None})())
    assert r.start("t1", user_id=1) is False


def test_start_preempts_expired_slot_with_new_instance(monkeypatch):
    from web.services import image_translate_runner as r
    now = [1000.0]
    monkeypatch.setattr(r.time, "monotonic", lambda: now[0])
    with r._running_tasks_lock:
        r._running_tasks["t1"] = {"instance": "inst-zombie", "last": 1000.0}
    now[0] = 1000.0 + r._WATCHDOG_TIMEOUT_SEC + 1  # 已僵尸
    monkeypatch.setattr(r.threading, "Thread", lambda target, daemon: type("T", (), {"start": lambda self: None})())
    monkeypatch.setattr(r, "ImageTranslateRuntime", lambda **kw: type("Rt", (), {"start": lambda self, tid: None})())
    # store.get 不被真正读取（因 Thread.start 是 no-op）
    assert r.start("t1", user_id=1) is True
    slot = r._running_tasks["t1"]
    assert slot["instance"] != "inst-zombie"
    assert slot["last"] == now[0]
```

- [ ] **Step 1.3: 跑测试确认失败**

Run: `pytest tests/test_image_translate_runner.py -v`
Expected: FAIL（`_WATCHDOG_TIMEOUT_SEC` / `_touch_heartbeat` / `_running_tasks` 的新 dict 结构 / `time` 导入都不存在）

- [ ] **Step 1.4: 实现 —— 重写 `web/services/image_translate_runner.py`**

全文替换为：

```python
"""图片翻译后台任务管理器（线程启动 + 重启恢复 + watchdog 超时）。"""
from __future__ import annotations

import json
import threading
import time
import uuid

from appcore.db import query as db_query
from appcore.events import EventBus
from appcore.image_translate_runtime import ImageTranslateRuntime, _WatchdogTakeover
from web.extensions import socketio

# 心跳超时：runner 线程若 30 分钟未刷新心跳，视为僵尸，is_running() 返回 False
_WATCHDOG_TIMEOUT_SEC = 1800.0

# task_id → {"instance": str (uuid), "last": float (time.monotonic())}
_running_tasks: dict[str, dict] = {}
_running_tasks_lock = threading.Lock()


def _make_socketio_handler(task_id: str):
    def handler(event):
        socketio.emit(event.type, event.payload, room=task_id)
    return handler


def is_running(task_id: str) -> bool:
    with _running_tasks_lock:
        slot = _running_tasks.get(task_id)
        if not slot:
            return False
        if time.monotonic() - slot["last"] >= _WATCHDOG_TIMEOUT_SEC:
            return False
        return True


def _touch_heartbeat(task_id: str, instance_id: str) -> bool:
    """由 runtime 线程调用；slot 不存在或 instance 不匹配（被抢占）返回 False。"""
    with _running_tasks_lock:
        slot = _running_tasks.get(task_id)
        if not slot or slot["instance"] != instance_id:
            return False
        slot["last"] = time.monotonic()
        return True


def start(task_id: str, user_id: int | None = None) -> bool:
    now = time.monotonic()
    with _running_tasks_lock:
        slot = _running_tasks.get(task_id)
        if slot and now - slot["last"] < _WATCHDOG_TIMEOUT_SEC:
            return False
        instance_id = str(uuid.uuid4())
        _running_tasks[task_id] = {"instance": instance_id, "last": now}

    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runtime = ImageTranslateRuntime(
        bus=bus,
        user_id=user_id,
        heartbeat=lambda: _touch_heartbeat(task_id, instance_id),
    )

    def run():
        try:
            runtime.start(task_id)
        except _WatchdogTakeover:
            # 被新 runner 抢占，静默退出，不动 slot
            pass
        finally:
            with _running_tasks_lock:
                slot = _running_tasks.get(task_id)
                if slot and slot["instance"] == instance_id:
                    _running_tasks.pop(task_id, None)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return True


def resume_inflight_tasks() -> list[str]:
    """服务重启时扫描未完成的 image_translate 任务并重新拉起。"""
    restored: list[str] = []
    try:
        rows = db_query(
            """
            SELECT id, user_id, status, state_json
            FROM projects
            WHERE type='image_translate'
              AND deleted_at IS NULL
              AND status IN ('queued','running')
            ORDER BY created_at ASC
            """,
            (),
        )
    except Exception:
        return restored

    for row in rows:
        tid = (row.get("id") or "").strip()
        if not tid or is_running(tid):
            continue
        state_json = row.get("state_json") or ""
        try:
            state = json.loads(state_json) if state_json else None
        except Exception:
            state = None
        if not state or state.get("type") != "image_translate":
            continue
        items = state.get("items") or []
        if items and all(it.get("status") in {"done", "failed"} for it in items):
            continue
        if start(tid, user_id=row.get("user_id")):
            restored.append(tid)
    return restored
```

**注意**：此步**同时**引入了对 `ImageTranslateRuntime._WatchdogTakeover` 的 import，这个类 Task 2 才会定义。所以 Task 1 的测试会在 import 阶段失败。解决：在 `appcore/image_translate_runtime.py` 临时 stub 一个占位异常，Task 2 再补全。

**Step 1.4a：在 `appcore/image_translate_runtime.py` 文件顶部、`_MAX_ATTEMPTS` 常量之前插入占位：**

```python
class _WatchdogTakeover(Exception):
    """slot 已被新 runner 抢占，旧 runtime 线程应退出（Task 2 完整实现）。"""
```

（Task 2 会把这个类的定义扩展为正式的，并添加 `_beat` / heartbeat 参数等。此处先放占位保证 import 通。）

- [ ] **Step 1.5: 跑 runner 测试确认通过**

Run: `pytest tests/test_image_translate_runner.py -v`
Expected: 所有 8 个测试 PASS

- [ ] **Step 1.6: 跑路由测试确认向后兼容**

Run: `pytest tests/test_image_translate_routes.py -v`
Expected: 全绿（`is_running` 对外行为等价；老测试 monkeypatch 的位置未变）

- [ ] **Step 1.7: commit**

```bash
git add web/services/image_translate_runner.py appcore/image_translate_runtime.py tests/test_image_translate_runner.py
git commit -m "feat(image-translate): runner 引入 30 分钟 watchdog + instance_id 心跳"
```

---

## Task 2: Runtime 带心跳 + Watchdog 接管语义

**Files:**
- Modify: `appcore/image_translate_runtime.py`
- Test: `tests/test_image_translate_runtime.py`

- [ ] **Step 2.1: 写失败测试**

在 `tests/test_image_translate_runtime.py` 末尾追加（若文件不存在先 Create 空文件 + `from __future__ import annotations`）：

```python
def test_runtime_beat_raises_when_heartbeat_returns_false():
    from appcore.image_translate_runtime import ImageTranslateRuntime, _WatchdogTakeover
    from appcore.events import EventBus
    rt = ImageTranslateRuntime(bus=EventBus(), heartbeat=lambda: False)
    try:
        rt._beat()
    except _WatchdogTakeover:
        return
    raise AssertionError("expected _WatchdogTakeover")


def test_runtime_beat_noop_when_heartbeat_returns_true():
    from appcore.image_translate_runtime import ImageTranslateRuntime
    from appcore.events import EventBus
    rt = ImageTranslateRuntime(bus=EventBus(), heartbeat=lambda: True)
    rt._beat()  # 不应抛


def test_runtime_beat_noop_when_heartbeat_none():
    from appcore.image_translate_runtime import ImageTranslateRuntime
    from appcore.events import EventBus
    rt = ImageTranslateRuntime(bus=EventBus())
    rt._beat()  # 默认 heartbeat 固定返回 True → 不抛


def test_runtime_start_propagates_watchdog_takeover(monkeypatch):
    """runtime.start 循环中 heartbeat 被踢下线时，抛出 _WatchdogTakeover 让上层处理。"""
    from appcore.image_translate_runtime import ImageTranslateRuntime, _WatchdogTakeover
    from appcore.events import EventBus
    from web import store

    task = {
        "id": "t-takeover",
        "type": "image_translate",
        "status": "queued",
        "steps": {},
        "items": [{"idx": 0, "filename": "a.jpg", "src_tos_key": "s/a",
                    "dst_tos_key": "", "status": "pending", "attempts": 0, "error": ""}],
        "progress": {"total": 1, "done": 0, "failed": 0, "running": 0},
    }
    monkeypatch.setattr(store, "get", lambda tid: task)
    monkeypatch.setattr(store, "update", lambda *a, **kw: None)

    calls = {"n": 0}
    def hb():
        calls["n"] += 1
        return False  # 第一次心跳就被踢

    rt = ImageTranslateRuntime(bus=EventBus(), heartbeat=hb)
    try:
        rt.start("t-takeover")
    except _WatchdogTakeover:
        assert calls["n"] >= 1
        return
    raise AssertionError("expected _WatchdogTakeover")
```

- [ ] **Step 2.2: 跑测试确认失败**

Run: `pytest tests/test_image_translate_runtime.py -k "beat or watchdog_takeover" -v`
Expected: FAIL（`_beat`、`heartbeat` 构造参数、`start` 里的 `_beat` 调用都不存在）

- [ ] **Step 2.3: 实现**

修改 `appcore/image_translate_runtime.py`：

**Step 2.3a：扩展 `_WatchdogTakeover`**（Task 1 已加的占位不用动）。

**Step 2.3b：改 `ImageTranslateRuntime.__init__` 签名 + 成员**：

```python
from typing import Callable

class ImageTranslateRuntime:
    def __init__(
        self,
        *,
        bus: EventBus,
        user_id: int | None = None,
        heartbeat: Callable[[], bool] | None = None,
    ) -> None:
        self.bus = bus
        self.user_id = user_id
        self._heartbeat = heartbeat or (lambda: True)
        self._rate_limit_hits: deque[float] = deque()

    def _beat(self) -> None:
        if not self._heartbeat():
            raise _WatchdogTakeover()
```

**Step 2.3c：在 `start(self, task_id)` 的如下位置插入 `self._beat()`**：

- **位置 A**：`task["status"] = "running"` 之前（`task = store.get(...)`; 判空之后）。
- **位置 B**：`_process_one(task, task_id, idx)` 调用之前（for 循环内，`if items[idx]["status"] in {"done", "failed"}: continue` 之后）。

**Step 2.3d：在 `start` 的外层 `try`/`except` 链加 `_WatchdogTakeover` 捕获**：

把现有的：

```python
try:
    for idx in range(len(items)):
        if items[idx]["status"] in {"done", "failed"}:
            continue
        self._process_one(task, task_id, idx)
except _CircuitOpen as exc:
    circuit_msg = str(exc) or "上游持续限流，已熔断"
    logger.warning(...)
    self._abort_remaining_items(task, task_id, circuit_msg)
```

改为：

```python
try:
    for idx in range(len(items)):
        if items[idx]["status"] in {"done", "failed"}:
            continue
        self._beat()
        self._process_one(task, task_id, idx)
except _CircuitOpen as exc:
    circuit_msg = str(exc) or "上游持续限流，已熔断"
    logger.warning(
        "[image_translate] circuit breaker opened for task %s: %s",
        task_id, circuit_msg,
    )
    self._abort_remaining_items(task, task_id, circuit_msg)
except _WatchdogTakeover:
    # 被新 runner 接管，直接让异常冒出；调用方（runner.run()）捕获并静默退出。
    raise
```

**Step 2.3e：在 `_process_one` 开头（`attempts = 0` 之前）与 **gemini 调用前**加 `self._beat()`**：

```python
def _process_one(self, task: dict, task_id: str, idx: int) -> None:
    item = task["items"][idx]
    item["status"] = "running"
    _update_progress(task)
    store.update(task_id, items=task["items"], progress=task["progress"])
    self._emit_item(task_id, item)

    attempts = 0
    while attempts < _MAX_ATTEMPTS:
        attempts += 1
        item["attempts"] = attempts
        src_path = ""
        dst_path = ""
        try:
            self._beat()  # <<< 新增
            # 1. 下载原图 ...
            ...
            # 2. 调 gemini_image
            out_bytes, out_mime = gemini_image.generate_image(...)
            ...
```

- [ ] **Step 2.4: 跑测试**

Run: `pytest tests/test_image_translate_runtime.py -v`
Expected: 新增的 4 个测试 PASS，老测试（若有）不回归。

- [ ] **Step 2.5: 跑 runner 测试确认依赖更新后仍绿**

Run: `pytest tests/test_image_translate_runner.py -v`
Expected: 全绿

- [ ] **Step 2.6: commit**

```bash
git add appcore/image_translate_runtime.py tests/test_image_translate_runtime.py
git commit -m "feat(image-translate): runtime 带心跳回调 + WatchdogTakeover 退出语义"
```

---

## Task 3: 路由 `POST /retry-all`

**Files:**
- Modify: `web/routes/image_translate.py`
- Test: `tests/test_image_translate_routes.py`

- [ ] **Step 3.1: 写失败测试**

在 `tests/test_image_translate_routes.py` 末尾追加：

```python
def test_retry_all_resets_every_item_including_done(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from web.services import image_translate_runner
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    from web import store
    task = store.get(tid)
    # 构造：done / failed / pending 都有
    task["items"] = [
        {"idx": 0, "filename": "a.jpg", "src_tos_key": "s/a", "dst_tos_key": "d/a",
         "status": "done", "attempts": 1, "error": ""},
        {"idx": 1, "filename": "b.jpg", "src_tos_key": "s/b", "dst_tos_key": "d/b-old",
         "status": "failed", "attempts": 3, "error": "timeout"},
        {"idx": 2, "filename": "c.jpg", "src_tos_key": "s/c", "dst_tos_key": "",
         "status": "pending", "attempts": 0, "error": ""},
    ]
    task["progress"] = {"total": 3, "done": 1, "failed": 1, "running": 0}
    task["status"] = "done"
    deleted: list[str] = []
    monkeypatch.setattr(r.tos_clients, "delete_object", lambda k: deleted.append(k))
    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: False)
    monkeypatch.setattr(r, "_start_runner", lambda tid_, uid: True)
    monkeypatch.setattr(store, "update", lambda *a, **kw: None)

    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/retry-all")
    assert resp.status_code == 202
    data = resp.get_json()
    assert data["reset"] == 3
    for it in task["items"]:
        assert it["status"] == "pending"
        assert it["attempts"] == 0
        assert it["error"] == ""
        assert it["dst_tos_key"] == ""
    assert sorted(deleted) == ["d/a", "d/b-old"]
    assert task["progress"] == {"total": 3, "done": 0, "failed": 0, "running": 0}
    assert task["status"] == "queued"


def test_retry_all_409_when_runner_active(authed_client_no_db, monkeypatch):
    from web.services import image_translate_runner
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=True)
    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: True)
    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/retry-all")
    assert resp.status_code == 409
    assert "正在跑" in resp.get_json().get("error", "")


def test_retry_all_409_when_no_items(authed_client_no_db, monkeypatch):
    from web.services import image_translate_runner
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    from web import store
    task = store.get(tid)
    task["items"] = []
    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: False)
    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/retry-all")
    assert resp.status_code == 409


def test_retry_all_tolerates_delete_object_failure(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from web.services import image_translate_runner
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=True)
    from web import store
    task = store.get(tid)
    task["items"][0]["dst_tos_key"] = "d/exists"

    def boom(_k): raise RuntimeError("tos down")
    monkeypatch.setattr(r.tos_clients, "delete_object", boom)
    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: False)
    monkeypatch.setattr(r, "_start_runner", lambda tid_, uid: True)
    monkeypatch.setattr(store, "update", lambda *a, **kw: None)

    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/retry-all")
    assert resp.status_code == 202  # delete 失败不阻断
    assert task["items"][0]["status"] == "pending"
    assert task["items"][0]["dst_tos_key"] == ""
```

- [ ] **Step 3.2: 跑测试确认失败**

Run: `pytest tests/test_image_translate_routes.py -k "retry_all" -v`
Expected: 4 FAIL（路由不存在，404）

- [ ] **Step 3.3: 实现 —— 在 `web/routes/image_translate.py` 的 `api_retry_unfinished` 之后追加**

```python
@bp.route("/api/image-translate/<task_id>/retry-all", methods=["POST"])
@login_required
def api_retry_all(task_id: str):
    """把该任务**所有** item（含 done）全部重置为 pending，删所有旧 dst，重启 runner。

    与 /retry-unfinished 的区别：**也重置 done 项**。"""
    task = _get_owned_task(task_id)
    if image_translate_runner.is_running(task_id):
        return jsonify({"error": "任务正在跑，等跑完再重试"}), 409
    items = task.get("items") or []
    if not items:
        return jsonify({"error": "任务没有图片"}), 409
    for item in items:
        old_dst = (item.get("dst_tos_key") or "").strip()
        if old_dst:
            try:
                tos_clients.delete_object(old_dst)
            except Exception:
                pass
        item["status"] = "pending"
        item["attempts"] = 0
        item["error"] = ""
        item["dst_tos_key"] = ""
    total = len(items)
    task["progress"] = {"total": total, "done": 0, "failed": 0, "running": 0}
    task["status"] = "queued"
    store.update(
        task_id,
        items=items,
        progress=task["progress"],
        status="queued",
    )
    _start_runner(task_id, current_user.id)
    return jsonify({"task_id": task_id, "reset": total, "status": "queued"}), 202
```

- [ ] **Step 3.4: 跑新测试**

Run: `pytest tests/test_image_translate_routes.py -k "retry_all" -v`
Expected: 4 PASS

- [ ] **Step 3.5: 跑整个路由测试文件确认不回归**

Run: `pytest tests/test_image_translate_routes.py -v`
Expected: 全绿

- [ ] **Step 3.6: commit**

```bash
git add web/routes/image_translate.py tests/test_image_translate_routes.py
git commit -m "feat(image-translate): 新增 /retry-all 路由（重置所有 item 含 done）"
```

---

## Task 4: 详情页 HTML + CSS

**Files:**
- Modify: `web/templates/image_translate_detail.html`
- Modify: `web/templates/_image_translate_styles.html`

- [ ] **Step 4.1: 编辑 `web/templates/image_translate_detail.html`**

把进度卡片这一段（第 37-46 行）：

```html
<section class="card">
  <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px">
    <h2 style="margin:0">进度</h2>
    <button id="itRetryUnfinished" class="btn btn-primary it-retry-main" type="button" hidden>重试未完成的图片</button>
  </div>
  <div id="itProgress" class="it-progress">
    <span id="itProgressText">{{ state.progress.done }} / {{ state.progress.total }} 完成，{{ state.progress.failed }} 失败</span>
    <div class="it-progress-bar"><div id="itProgressFill"></div></div>
  </div>
</section>
```

改为：

```html
<section class="card">
  <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px">
    <h2 style="margin:0">进度</h2>
    <div style="display:flex;gap:10px;align-items:center">
      <button id="itRetryAll" class="btn it-retry-all" type="button" hidden>全部重跑</button>
      <button id="itRetryUnfinished" class="btn btn-primary it-retry-main" type="button" hidden>重试未完成的图片</button>
    </div>
  </div>
  <div id="itProgress" class="it-progress">
    <span id="itProgressText">{{ state.progress.done }} / {{ state.progress.total }} 完成，{{ state.progress.failed }} 失败</span>
    <div class="it-progress-bar"><div id="itProgressFill"></div></div>
  </div>
</section>
```

- [ ] **Step 4.2: 编辑 `web/templates/_image_translate_styles.html`**

在 `</style>` 之前、`.it-retry-item:disabled { ... }` 规则之后追加：

```css
/* 全部重跑按钮：危险色描边次级按钮，与主按钮等高 */
.it-retry-all {
  height: 40px;
  padding: 8px 18px;
  font-size: 14px;
  font-weight: 600;
  border-radius: 8px;
  border: 1.5px solid var(--danger, #dc2626);
  background: #fff;
  color: var(--danger, #dc2626);
  cursor: pointer;
  transition: background-color 120ms, color 120ms, box-shadow 120ms;
}
.it-retry-all:hover:not(:disabled) {
  background: rgba(220, 38, 38, 0.08);
}
.it-retry-all:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
```

- [ ] **Step 4.3: 手工 diff 确认**

Run: `git diff web/templates/image_translate_detail.html web/templates/_image_translate_styles.html`
Expected: 仅上述两处修改。

- [ ] **Step 4.4: commit**

```bash
git add web/templates/image_translate_detail.html web/templates/_image_translate_styles.html
git commit -m "style(image-translate): 进度卡片增加「全部重跑」按钮 + 危险色样式"
```

---

## Task 5: 详情页 JS —— 绑定 `retryAllBtn`

**Files:**
- Modify: `web/templates/_image_translate_scripts.html`

- [ ] **Step 5.1: 编辑**

在 `web/templates/_image_translate_scripts.html` 的 `image-translate-detail` 分支内：

**5.1a** — 在 `var retryUnfinishedBtn = document.getElementById("itRetryUnfinished");` 下方添加：

```js
var retryAllBtn = document.getElementById("itRetryAll");
```

**5.1b** — 把 `renderProgress(state)` 函数整体替换为：

```js
function renderProgress(state){
  var p = state.progress || {total:0, done:0, failed:0};
  progressText.textContent = p.done + " / " + p.total + " 完成，" + p.failed + " 失败";
  var pct = p.total ? Math.round((p.done + p.failed) / p.total * 100) : 0;
  progressFill.style.width = pct + "%";
  var total = p.total || 0;
  var done = p.done || 0;
  var isRunning = state.is_running === true;
  if (retryUnfinishedBtn) {
    var showUnfinished = total > 0 && done < total;
    retryUnfinishedBtn.hidden = !showUnfinished;
    retryUnfinishedBtn.disabled = isRunning;
    retryUnfinishedBtn.title = isRunning ? "任务正在跑，等跑完再重试" : "";
  }
  if (retryAllBtn) {
    var showAll = total > 0;
    retryAllBtn.hidden = !showAll;
    retryAllBtn.disabled = isRunning;
    retryAllBtn.title = isRunning ? "任务正在跑，等跑完再重试" : "";
  }
}
```

**5.1c** — 在 `retryUnfinishedBtn` 的点击事件块之后（`if (retryUnfinishedBtn) { ... }` 闭合后）追加：

```js
if (retryAllBtn) {
  retryAllBtn.onclick = function(){
    if (!confirm("将重置所有图片（包括已完成的），原有结果会被删除，确认？")) return;
    retryAllBtn.disabled = true;
    fetch("/api/image-translate/"+taskId+"/retry-all",{method:"POST",credentials:"same-origin"})
      .then(function(r){ return r.json().then(function(d){ return {ok:r.ok, body:d}; }); })
      .then(function(res){
        if (!res.ok) { alert(res.body && res.body.error || "全部重跑失败"); retryAllBtn.disabled = false; return; }
        refresh();
      })
      .catch(function(){ retryAllBtn.disabled = false; });
  };
}
```

- [ ] **Step 5.2: 手工 diff 确认**

Run: `git diff web/templates/_image_translate_scripts.html`
Expected: 仅上述三处变更。

- [ ] **Step 5.3: commit**

```bash
git add web/templates/_image_translate_scripts.html
git commit -m "feat(image-translate): 前端绑定「全部重跑」按钮（二次确认 + 调 /retry-all）"
```

---

## Task 6: 集成冒烟 + 手工验证

- [ ] **Step 6.1: 跑图片翻译相关全部测试**

Run: `pytest tests/test_image_translate_routes.py tests/test_image_translate_runner.py tests/test_image_translate_runtime.py -v`
Expected: 全绿。

- [ ] **Step 6.2: 跑 web 路由测试**

Run: `pytest tests/test_web_routes.py -v`
Expected: 全绿（若红需判断是否与本次改动相关，非相关则不处理）。

- [ ] **Step 6.3: 启动本地 dev 服务（若未启动；否则跳过让用户验）**

按项目惯例启动 Flask dev server（略）。

- [ ] **Step 6.4: 手工验证场景**

**场景 A：正常任务跑完后**
- 全部 done：「重试未完成的图片」按钮隐藏（done==total），「全部重跑」按钮仍可见。
- 点「全部重跑」→ 弹确认框 → 确认 → 202 → 所有 dst_key 被删 → runner 重跑每张。

**场景 B：服务重启后 DB 卡在 running**
- 手动把某任务 `state_json.status` 改 `running` 且某 item.status=`running`；刷新。
- `is_running=false`（内存里无此 task）→ 两个按钮都可见可点。

**场景 C：runner 真卡住 30 分钟（模拟）**
- 在 Python REPL 里手动 `_running_tasks["x"] = {"instance": "fake", "last": time.monotonic() - 1900}`（已过 30min）。
- 访问详情页 → `is_running=false` → 按钮可见且可点 → 调 `/retry-all`/`/retry-unfinished` 均能触发 start()，新 instance 覆盖旧 slot。

**场景 D：runner 正在跑**
- 刚提交新任务 → runner 活跃 → 两个按钮 disabled + tooltip。

- [ ] **Step 6.5: 对照 spec 验收要求**

对照 `docs/superpowers/specs/2026-04-21-image-translate-watchdog-retry-all-design.md`：
- ① runner 数据结构升级 → Task 1 ✓
- ② `is_running` 30min 超时语义 → Task 1 ✓
- ③ runtime heartbeat 接管 → Task 2 ✓
- ④ `/retry-all` 路由 + 409 / 409 / 202 行为 → Task 3 ✓
- ⑤ 前端按钮 + 二次确认 + 样式 → Task 4 + 5 ✓
- ⑥ 不自动 resume / 不改 DB schema → 未触及 `resume_inflight_tasks` 主体、无 migration ✓

- [ ] **Step 6.6: 最终微调 commit（若有）**

若手工验证发现 JS/CSS 细节问题，追加单独 commit；否则无需。

---

## 备注：回滚

每个 task 一个 commit，可独立 revert：

- Task 5（JS）→ Task 4（HTML/CSS）→ Task 3（路由） → Task 2（runtime） → Task 1（runner）

DB schema、state_json 不变；老任务无需迁移。
