# 图片翻译超时兜底 + 全部重跑按钮 设计

**日期**：2026-04-21
**所属模块**：`web/services/image_translate_runner.py` · `appcore/image_translate_runtime.py` · `web/routes/image_translate.py` · `web/templates/image_translate_detail.html` · `web/templates/_image_translate_scripts.html` · `web/templates/_image_translate_styles.html`

---

## 背景与问题

**现状（2026-04-20 已上线的"重试按钮改版"）**：

- 后端 `_state_payload()` 暴露 `is_running`，由 `web/services/image_translate_runner.is_running(task_id)` 提供（进程内存集合 `_running_tasks` 的 `in` 查询）。
- `POST /api/image-translate/<id>/retry/<idx>`：runner 不活跃时支持任意状态重试。
- `POST /api/image-translate/<id>/retry-unfinished`：runner 不活跃时把所有非 `done` item 重置为 `pending` 并重启。
- 详情页「重试未完成的图片」主按钮 + 单图「重试/重新生成」按钮均以 `!is_running` 启用。

**未解决的漏洞（本次要修）**：

1. **runner 线程本身卡死时无法恢复**
   当 gunicorn 主进程仍在、但 runner 线程在 gemini 调用上 hang 住（网络栈阻塞、上游不响应但 TCP 不断开），`_running_tasks` 里该 task_id 常驻，`is_running=true` 常驻，前端所有重试按钮被永久禁用，用户唯一出路是「删任务重建」。

2. **没有"全部重跑"兜底按钮**
   `/retry-unfinished` 只重置非 `done` item。若用户想把整个任务（包括已完成但自己不满意的）全部重跑一遍，当前只能一张张点「重新生成」。

## 非目标（明确不做）

- **不扩大启动期 `resume_inflight_tasks()` 扫描范围**。按 2026-04-17 宿主机 watchdog 重启 VM 事故以及用户 memory「图片翻译不自动恢复」的要求，所有"自动恢复"一律走手动按钮。
- **不做服务器侧的"30 分钟到了自动把 task 标 failed"**。只在 `is_running()` 的判据里加超时（前端按钮解锁），**不主动改 DB 状态**。DB 状态的推进仍然只由 runner 线程自己完成。
- **不做取消运行中 runner 的接口**。Python 线程无法强制取消；我们只靠新 runner 接管 + 旧 runner 自然结束后的结果被丢弃。
- 不改 `/retry-unfinished`、`/retry-failed`、`/retry/<idx>` 的已有行为。

---

## 方案概览

三块改动：

1. **Runner 加 watchdog**：进程内存状态从 `set[str]` 升级为 `dict[task_id, SlotInfo]`，SlotInfo 含 `instance_id` + `last_heartbeat`。`is_running(task_id)` 的判据从"是否 in set"改为"slot 存在且 `now - last < 30min`"。
2. **Runtime 带心跳**：`ImageTranslateRuntime` 接收 `heartbeat` 回调，在关键处理点（开始、每 item、每次 attempt、gemini 调用前）调用一次。若 `heartbeat()` 返回 False（slot 已被新 runner 抢占），raise `_WatchdogTakeover` 让旧 runner 线程退出，避免竞态写入。
3. **新增 `/retry-all` + 前端按钮**：路由语义上是"强制重置所有 item（包括 done）+ 删所有 dst + 重启 runner"；前端加显眼的次级按钮（危险色描边 + 二次确认）。

---

## ① Runner 改造（`web/services/image_translate_runner.py`）

### 数据结构

```python
import time
import uuid

# slot 生命周期：start() 占据 → runtime 线程持续 heartbeat → finally 释放
# 心跳超时：任何 slot 超过 _WATCHDOG_TIMEOUT_SEC 秒未刷新即视为僵尸
_WATCHDOG_TIMEOUT_SEC = 1800.0  # 30 min

_running_tasks: dict[str, dict] = {}  # task_id → {"instance": str, "last": float(monotonic)}
_running_tasks_lock = threading.Lock()
```

### 公开 API

- `is_running(task_id) -> bool`
  - slot 不存在 → False
  - slot 存在但 `now - last >= 30min` → False（视为僵尸；不立即清理 slot，等旧线程自己走 finally 或被新 runner 抢占）
  - 否则 → True

- `start(task_id, user_id=None) -> bool`
  - 若 `is_running(task_id)` 真（活跃 slot） → 返回 False（保持现有语义）
  - 否则：生成新 `instance_id = uuid4()`，`_running_tasks[task_id] = {"instance": instance_id, "last": monotonic()}`，启动 runtime 线程并传入 heartbeat 回调。旧 slot（若是僵尸）被新值直接覆盖。

### 内部 API（仅给 runtime 的 heartbeat 用）

```python
def _touch_heartbeat(task_id: str, instance_id: str) -> bool:
    """刷新心跳。若当前 slot 已不是 instance_id（被新 runner 抢占），返回 False。"""
    with _running_tasks_lock:
        slot = _running_tasks.get(task_id)
        if not slot or slot["instance"] != instance_id:
            return False
        slot["last"] = time.monotonic()
        return True
```

### runtime 线程的 finally 清理

```python
def run():
    try:
        runtime.start(task_id)
    except _WatchdogTakeover:
        # 被新 runner 接管，不动 slot（slot 已是新 instance），静默退出
        pass
    finally:
        with _running_tasks_lock:
            slot = _running_tasks.get(task_id)
            if slot and slot["instance"] == instance_id:
                _running_tasks.pop(task_id, None)
```

关键点：**只有自己的 instance_id 匹配时才 pop**，否则已被新 runner 替换，不应清掉别人的 slot。

### `resume_inflight_tasks()` 保留

函数体不动。调用 `start()`，其 `is_running()` 判据已包含超时，自动兼容。

---

## ② Runtime 改造（`appcore/image_translate_runtime.py`）

### 构造函数

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
        self._rate_limit_hits = deque()
```

### 心跳调用点（最小增量）

增加私有方法 `_beat()`：

```python
class _WatchdogTakeover(Exception):
    """slot 已被新 runner 抢占，旧 runtime 线程应退出。"""


def _beat(self) -> None:
    if not self._heartbeat():
        raise _WatchdogTakeover()
```

调用点（3 处就够）：

1. `ImageTranslateRuntime.start()` 开头，`task.get(...)` 之后、for 循环之前。
2. `_process_one()` 开头，在 `item["status"] = "running"` 之前。
3. `_process_one()` 的 `while attempts < _MAX_ATTEMPTS:` 循环内、**`gemini_image.generate_image(...)` 调用之前**。这一处最关键——gemini 卡住时 watchdog 的唯一判据就是这里不再调。

### `_WatchdogTakeover` 的外层捕获

改 `start(task_id)` 的结构：

```python
def start(self, task_id: str) -> None:
    task = store.get(task_id)
    if not task or task.get("type") != "image_translate":
        return

    self._beat()  # 占位心跳

    task["status"] = "running"
    # ... 老逻辑 ...

    circuit_msg = ""
    try:
        for idx in range(len(items)):
            if items[idx]["status"] in {"done", "failed"}:
                continue
            self._process_one(task, task_id, idx)
    except _CircuitOpen as exc:
        # 老逻辑不动
        ...
    except _WatchdogTakeover:
        # 被新 runner 接管，直接退出，不做 finalize / store.update
        raise

    # ... 老的 finalize 逻辑 ...
```

注意 `_WatchdogTakeover` 必须在 `_CircuitOpen` 之后捕获，让两个异常清晰分离；遇 takeover 直接 `raise` 让 runner 层的 `run()` 处理。

---

## ③ 路由改造（`web/routes/image_translate.py`）

新增 `POST /api/image-translate/<task_id>/retry-all`：

```python
@bp.route("/api/image-translate/<task_id>/retry-all", methods=["POST"])
@login_required
def api_retry_all(task_id: str):
    """把该任务**所有** item（含 done）全部重置为 pending，删所有旧 dst，重启 runner。

    与 /retry-unfinished 的区别：**也重置 done 项**。
    依赖 is_running() 互斥；runner 活跃时（心跳未超 30 min）返回 409。"""
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

**不改**：`/retry/<idx>`、`/retry-unfinished`、`/retry-failed`。三者的 `is_running()` 判据自动受益于 30min 超时（语义等价，无需改动）。

---

## ④ 前端改造

### HTML (`image_translate_detail.html`)

进度卡片头部两个按钮并排（次要按钮在主按钮左侧）：

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

### JS (`_image_translate_scripts.html`)

- 在 `retryUnfinishedBtn` 声明下方追加 `var retryAllBtn = document.getElementById("itRetryAll");`
- `renderProgress(state)` 里同时处理两个按钮：
  - 显示条件：`total > 0`（只要任务有图就显示「全部重跑」，不管 done 数；与「重试未完成」的 `done < total` 区分）
  - 启用条件：`!isRunning`
  - 置 `title = isRunning ? "任务正在跑，等跑完再重试" : ""`
- 点击事件：弹 `confirm("将重置所有图片（包括已完成的），原有结果会被删除，确认？")`，取消则 return；确认后 `POST /api/image-translate/<tid>/retry-all`，失败 alert，成功 refresh。

### CSS (`_image_translate_styles.html`)

新增 `.it-retry-all` 样式：危险色描边次级按钮，高度对齐主按钮（40px），视觉上明显但不抢戏。

```css
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

---

## 数据流

**场景 A：runner 线程真卡死在 gemini**

```
t=0:  runner 开始，heartbeat(t=0)
t=60: _process_one 第 2 张，heartbeat(t=60)
t=80: gemini 调用 hang 住，heartbeat(t=80) 之后不再刷新
...
t=1880 (31 min后): 用户刷新详情页 → is_running() 检查 now(1880)-last(80)=1800 >= 30min → 返回 False
                 → state.is_running = false → 按钮解锁
用户点「全部重跑」→ POST /retry-all → is_running=false → 接受
  → 重置所有 item → start() → _running_tasks[tid] 被新 instance_id 覆盖
  → 启动新 runtime 线程
t=1881: 旧 gemini 调用突然返回 → 旧 runtime 进入 _process_one 下一步 → 调 self._beat()
      → _touch_heartbeat(tid, 旧 instance) → slot instance 不匹配 → 返回 False
      → raise _WatchdogTakeover → 旧 runtime.start() 退出，不做 store.update
      → 旧 runner 线程的 finally：检查 slot instance ≠ 自己 → 不 pop slot（保留新 runner 的）
新 runner 正常跑，DB 最终一致。
```

**场景 B：runner 正常跑完**

```
heartbeat 每 10~30s 刷新一次 → is_running=true 持续 → 用户看到按钮禁用 → 跑完后 finally pop slot
→ is_running=false → 按钮解锁。
```

**场景 C：主进程重启**

```
进程死亡 → _running_tasks 清空 → resume_inflight_tasks() 走 start() → 新 slot + 新 runner
或：若 resume 未触发（db 状态不符合），用户打开详情页 → is_running=false → 按钮可见。
```

---

## 并发边界

- `_running_tasks_lock` 覆盖所有读写。
- heartbeat / is_running / start 三个 API 在 lock 内**不**持有长时间操作（只做 dict 读写）。
- runtime 线程的 `_beat()` 通过 callback 调 `_touch_heartbeat`，锁粒度极小。
- 旧 runner 线程在 gemini hang 期间完全不持锁，不会阻塞主进程。

---

## 错误处理

- `delete_object` 失败：`try/except` 包住，仅 log warning，不阻断重置（与现有 `/retry-unfinished` 一致）。
- `start_runner()` 并发冲突：接口层先查 `is_running()`，若竞态下 `start()` 返回 False 则是"另一端刚 start"——接受 202 不是最准，但不会导致 DB 错乱。此场景下可额外在 `_start_runner` 返回 False 时返回 409，但本次不做，保持与 `/retry-unfinished` 一致。
- 旧 runtime 抛 `_WatchdogTakeover`：runner 层 `run()` 捕获，不 log error（正常流程）。

---

## 测试策略

### 单测新增

1. **`web/services/image_translate_runner`**
   - `is_running(tid)` 当 slot 存在且 `last` 距今 <30min → True
   - `is_running(tid)` 当 slot 存在但 `last` 距今 >=30min → False
   - `_touch_heartbeat(tid, instance)` 正确 instance → True 且 `last` 被刷新
   - `_touch_heartbeat(tid, instance)` 错误 instance → False，`last` 不变
   - `start(tid)` 在当前 slot 已超时时能成功抢占，新 instance 不同
   - `start(tid)` 在当前 slot 活跃时返回 False

2. **`web/routes/image_translate`**
   - `POST /retry-all` 正常：所有 item（含 done）重置为 pending + dst 清空 + 原 dst 被调 delete_object，progress={total,done=0,failed=0,running=0}，status=queued，返回 202 + reset=total
   - `POST /retry-all` 在 `is_running=true` 时返回 409
   - `POST /retry-all` 在 items=[] 时返回 409
   - 新建测试：冻结 `time.monotonic`，验证 `is_running` 在 30min 边界翻转后对 `/retry-unfinished`、`/retry/<idx>`、`/retry-all` 的放行

### 冒烟（跑存量测试全绿）

- `pytest tests/test_image_translate_routes.py -v` 全绿（老测试不回归）
- `pytest tests/test_image_translate_runtime.py -v` 全绿（runtime 改造后 heartbeat 默认 lambda 不影响）

### 手工验证

场景 A/B/C/D（见 plan 末尾 Task 9）。

---

## 触碰文件

- **Modify**
  - `web/services/image_translate_runner.py`（数据结构 + is_running + start + _touch_heartbeat）
  - `appcore/image_translate_runtime.py`（构造函数加 heartbeat + _beat + _WatchdogTakeover + 3 处调用点）
  - `web/routes/image_translate.py`（新路由 `/retry-all`）
  - `web/templates/image_translate_detail.html`（按钮 HTML）
  - `web/templates/_image_translate_scripts.html`（新按钮绑定 + 渲染）
  - `web/templates/_image_translate_styles.html`（`.it-retry-all` 样式）
  - `tests/test_image_translate_routes.py`（新路由 + is_running 超时）
  - `tests/test_image_translate_runtime.py`（heartbeat 回调语义）

- **不动**
  - `web/app.py`（startup recovery 保持）
  - `appcore/task_state.py`
  - DB schema、state_json 结构
  - `/retry/<idx>`、`/retry-unfinished`、`/retry-failed` 路由体

---

## 回滚方案

- 三步独立 revert：
  1. revert UI 改动（HTML/JS/CSS）——前端按钮消失，后端仍可用
  2. revert 路由新增 `/retry-all`——恢复到老接口
  3. revert runner + runtime 超时改动——恢复 set 实现，`is_running` 回到"纯 in 查询"
- DB schema 不变、state_json 不变，老任务无需迁移。
