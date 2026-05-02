# 优雅停机与后台任务生命周期治理设计

**日期：** 2026-05-01
**作者：** Claude Code（第三者视角根因分析 + 落地方案）
**状态：** Draft，待用户审核
**关联文档：**
- [2026-04-16-interrupted-task-recovery-design.md](2026-04-16-interrupted-task-recovery-design.md)（中断任务自动失败恢复设计）
- [2026-04-22-web-service-tuning-design.md](2026-04-22-web-service-tuning-design.md)（gthread 单 worker 调优）

---

## 1. 摘要

线上 `systemctl restart autovideosrt.service` 时，旧 Gunicorn 进程经常在 `TimeoutStopSec=900s` 后被 systemd `SIGKILL` 强杀。本方案在不引入 Redis/MQ 的前提下，分两阶段解决这个问题：

- **阶段 1（低风险止血）**：补全信号传递链 + 引入进程级取消令牌 + 在长任务循环里加可中断点 + APScheduler 显式 shutdown + 提供"重启前活跃任务检查"探针，并把 `TimeoutStopSec` / `graceful_timeout` 从 900s 收紧到 300s/240s。落地后预期：常规重启在 60s 内完成，异常情况下不超过 5 分钟仍能正常 `systemctl restart`，再不被 `SIGKILL`。
- **阶段 2（结构性演进）**：把长任务从 web 进程内迁出到独立的 `autovideosrt-worker.service`，DB 当作任务队列，web 进程只负责 HTTP/WS + 入队，重启 web 不再影响正在跑的任务。

阶段 1 必须先落地、跑稳一周以上再启动阶段 2；阶段 2 落地后阶段 1 的 cancellation 机制仍保留（worker 进程自己也要支持 graceful 停机）。

> 2026-05-02 合并说明：当前整改分支同时保留 `appcore.ops.active_tasks pre-restart`、`runtime_active_tasks` / `runtime_active_task_snapshots`、停机时 active task 快照和后台“定时任务”登记；部署配置采用本设计的 300s systemd / 240s Gunicorn 停机窗口，并在 `worker_exit` 中先写快照再等待 tracked thread 协作退出。

---

## 2. 背景与现状

### 2.1 部署架构

- systemd unit：[`deploy/autovideosrt.service`](../../../deploy/autovideosrt.service)
  - `WorkingDirectory=/opt/autovideosrt`
  - `ExecStart=.../gunicorn --config /opt/autovideosrt/deploy/gunicorn.conf.py main:app`
  - `Restart=always`、`RestartSec=5`
  - `TimeoutStopSec=900`（注释明说为了等长任务跑完）
- Gunicorn 配置：[`deploy/gunicorn.conf.py`](../../../deploy/gunicorn.conf.py)
  - `worker_class=gthread`、`workers=1`、`threads=32`
  - `timeout=300`（请求级超时）
  - `graceful_timeout=900`（已与 systemd 对齐）
  - `keepalive=10`
- 测试环境同构：`autovideosrt-test.service` + `/opt/autovideosrt-test`，端口 8080。

### 2.2 进程内运行时

- 入口 [`main.py`](../../../main.py) 在 import 阶段创建 Flask app + 启动 `BackgroundScheduler`；当前**没有任何显式 shutdown hook**。
- web 后台任务通过 [`web/background.py`](../../../web/background.py) 的 `start_background_task` 起线程；底层走 `socketio.start_background_task`，因为 `async_mode="threading"`，最终就是 Python `threading.Thread`。
- 长任务统一登记到 [`appcore/runner_lifecycle.py`](../../../appcore/runner_lifecycle.py) 的 `start_tracked_thread(daemon=False)`；活跃任务由 [`appcore/active_tasks.py`](../../../appcore/active_tasks.py) 管理，并通过 [`appcore/task_recovery.py`](../../../appcore/task_recovery.py) 保留兼容入口。
- 任务状态在 [`appcore/task_state.py`](../../../appcore/task_state.py) 内存字典 + DB `projects.state_json` 双写；`web/store.py` 是 `appcore.task_state` 的 alias，确保两端用同一份对象。
- APScheduler 注册的 job：cleanup（小时级）、subtitle_removal_vod、material_evaluation、push_quality_check、product_cover_backfill、tos_backup_job——见 [`appcore/scheduler.py`](../../../appcore/scheduler.py)。

### 2.3 已经存在的中断恢复机制

- [`appcore/task_recovery.recover_all_interrupted_tasks`](../../../appcore/task_recovery.py)：在 [`web/app.py`](../../../web/app.py) `_run_startup_recovery` 里启动时跑一次，扫 `projects.status='running'` 把无活跃任务的记录改成 `interrupted` / `error`。
- [`appcore/bulk_translate_recovery.mark_interrupted_bulk_translate_tasks`](../../../appcore/bulk_translate_recovery.py)：bulk_translate 项目类型专用同等机制。
- 唯一会被启动期"自动续跑"的项目类型是 `image_translate`（已经把任务提交到 APIMART 异步通道，不续跑就会浪费已计费的上游调用），由 `_auto_resume_after_recovery` 调起 `start_image_translate_runner`。
- 即"重启 = 任务中断 + 用户手动 resume" 这条路线**已经是项目共识**，本方案沿用并补全 graceful 部分。

### 2.4 复现路径（推断）

1. 运维执行 `systemctl restart autovideosrt.service`。
2. systemd 给主 Gunicorn 进程发 `SIGTERM`。
3. Gunicorn arbiter 把 `SIGTERM` 转给 worker（Gunicorn 自己会拦截 SIGTERM 并设置 `worker.alive=False`，等 `graceful_timeout=900s`）。
4. worker 不再接受新连接，等 in-flight HTTP 请求结束——但 `socketio.start_background_task` 起的长任务线程**和 HTTP 请求无关**，gunicorn 不会主动等它们。
5. 因为 `start_tracked_thread(daemon=False)`，Python interpreter 在主线程退出时仍会等所有 non-daemon 线程结束 → 进程不退出。
6. APScheduler 的 BackgroundScheduler 也是 daemon=False thread → 进一步阻塞退出。
7. 等到 `TimeoutStopSec=900s`，systemd 发 `SIGKILL` → 强杀，留下 `journalctl` 里的 "Killed by signal 9" + "process didn't exit cleanly"。

也就是说，**当前 SIGKILL 不是因为 15 分钟太短，而是因为根本没人通知后台线程"请退出"——15 分钟等到天荒地老也不会自然结束**。把 `TimeoutStopSec` 拉得再长也只是延迟症状。

---

## 3. 第三者视角根因分析

把 SIGKILL 拆成 4 条独立失因，每条都在阶段 1 里有专门子任务对位：

| # | 失因 | 现状 | 阶段 1 对治 |
|---|------|------|-------------|
| 1 | **信号链路断开** | Gunicorn 拦了 SIGTERM 但只用来停 HTTP 接受；spawned 后台线程没人通知。 | 在 Gunicorn `post_worker_init` hook 里 chain SIGTERM/SIGINT，触发进程级 cancel 事件。 |
| 2 | **任务没有可中断点** | `runtime.py` / `image_translate_runner.py` 等长循环里没有"如果 cancel 就早退"的检查。 | 引入 `appcore/shutdown_coordinator.py` + 每个 runner 在循环顶部检查 `is_shutdown_requested()`。 |
| 3 | **APScheduler 不退出** | scheduler 是 non-daemon thread，停机时没调用 `scheduler.shutdown()`。 | SIGTERM/SIGINT signal handler、`worker_exit` / `atexit` 调 `scheduler.shutdown(wait=False)`。 |
| 4 | **non-daemon spawned thread 阻塞 process exit** | `start_tracked_thread(daemon=False)` 让进程必须等线程结束。 | 任务收到 cancel → 抛 `OperationCancelled` → 在 finally 里 `unregister_active_task` → 线程自然退出。worker_exit 兜底再做一次活跃集合扫描，超时则记 warning（不强杀）。 |

阶段 1 落地后，"重启 = 通知线程取消 → 标 interrupted → 进程退出"这个完整链路成立，常规情况下进程 30~60s 退出，最差也只剩"当前正在执行的单步硬等结束"——比如某个 ffmpeg subprocess 在跑，那就等它跑完。

阶段 2 通过架构隔离从根上解决：web 进程不再持有长任务，重启 web 在秒级完成；worker 进程独立退场，自己内部也跑阶段 1 的 cancellation 协议。

---

## 4. 总体设计原则与目标

### 4.1 设计原则

1. **沿用已有"中断 + 标记 + 用户手动 resume"路线**。不引入"任务级断点恢复"（resume mid-step）这种结构性改动；这是显式的非目标。
2. **不破坏现有任务语义**。所有改动只影响"信号到达 → 退出"这一段；任务正常运行路径不变。
3. **能用现有基础设施就不新建**。`_active_tasks` set、`recover_all_interrupted_tasks`、`CancellationToken` 模式都已存在或在项目里有等价实现，本方案优先复用。
4. **可逐子任务回滚**。每个子任务有独立的 feature flag 或纯环境变量开关，必要时单独关闭不影响其他改动。
5. **阶段 1 和阶段 2 互不依赖**。阶段 2 即使永不落地，阶段 1 也是终态可用方案。

### 4.2 核心目标

- `systemctl restart autovideosrt.service` 不再触发 `SIGKILL`（journalctl 里再无 "killed by signal 9"）。
- 重启窗口 ≤ 60s（90 分位数；最差不超过 5 分钟）。
- 重启后无任务卡 `running` 状态：要么 `done`，要么 `interrupted`，要么 image_translate 这类异步任务自动续跑成 `running`。
- 运维有"重启前活跃任务清单"探针可以人工决定是否延后重启。
- APScheduler 的 jobs 在重启前后状态一致（不重复跑、不漏跑）。

### 4.3 显式非目标

- 不实现任务级断点恢复（resume 当前 step 的中间状态）。
- 不为了"任务跑完才重启"加 systemd ExecStop 等待逻辑；接受"重启 = 中断"。
- 不在阶段 1 引入 Redis / 消息队列 / sticky session。
- 不改 socketio / 内存任务态的双写模型。
- 不改 image_translate / subtitle_removal 等已有自动 resume 的特殊路径。

---

## 5. 阶段划分概览

```
[阶段 1 — 低风险止血，预计 1 周内落地]
  └─ 6 个子任务，全部聚焦"让进程能干净退出"
  └─ 涉及文件 ~10 个，不动 DB schema，不动业务任务流
  └─ 每子任务可独立回滚

       ↓ 跑稳 ≥ 1 周，线上无 SIGKILL，且无 graceful_timeout 命中

[阶段 2 — 结构性 worker 化，预计 2-3 周]
  └─ 拆出 autovideosrt-worker.service
  └─ DB 队列 + 独立 worker 主循环
  └─ 涉及 systemd unit 新增 + Web routes 改"入队不直接起线程"
  └─ 部署期间 web/worker 共存灰度
```

---

## 6. 阶段 1：低风险止血

### 6.1 涉及文件

**新增**
- `appcore/shutdown_coordinator.py`：进程级 `Event` + `is_shutdown_requested()` + `request_shutdown()` + `wait_for_active_tasks(timeout)`。
- `appcore/cancellation.py`：跟 `tools/shopify_image_localizer/cancellation.py` 同形态，但作用域是 web 后台任务（避免 import cycle，不复用那个模块）。提供 `OperationCancelled` 异常 + `throw_if_cancel_requested()` 简便函数。
- `web/routes/admin_runtime.py`：admin 后台 + `/admin/runtime/active-tasks` JSON 接口（探针）。
- `tools/active_tasks_probe.py`：CLI 包装，运维 `ssh + python -m tools.active_tasks_probe` 可读 JSON。

**修改**
- `deploy/gunicorn.conf.py`：补 `post_worker_init` / `worker_exit` hook，把 `graceful_timeout` 从 900 → 240。
- `deploy/autovideosrt.service`：把 `TimeoutStopSec` 从 900 → 300，更新注释。
- `appcore/scheduler.py`：新增 `shutdown_scheduler(wait: bool=False)`。
- `appcore/runner_lifecycle.py`：在 `start_tracked_thread` 启动前检查 `is_shutdown_requested`；启动后捕获 `OperationCancelled` 在 finally 里走正常的 `unregister_active_task`。
- `appcore/task_recovery.py`：增加 `snapshot_active_tasks()` 返回 `[(project_type, task_id, started_at)]` 给探针 API 用。
- `appcore/runtime.py` / `appcore/runtime_v2.py` / `appcore/runtime_sentence_translate.py`：在每个 step 函数入口和长循环顶部 `throw_if_cancel_requested()`；`except OperationCancelled` 时把 task 标 `interrupted` 并 `_emit *_PIPELINE_ERROR` 携带 `cancelled=True` 标志。
- `web/services/image_translate_runner.py` / `multi_pipeline_runner.py` / `omni_pipeline_runner.py` / `bulk_translate_*.py`：在批量 for-loop 顶部加 `throw_if_cancel_requested()`。
- `appcore/scheduled_tasks.py`：scheduler job wrapper 里也加一次 `throw_if_cancel_requested`，避免 cleanup job 在停机时启动。
- `web/app.py`：移除 `_run_startup_recovery` 里的 try-broad except 简化（已经容错，无需改）；本子任务不动它，仅作记号。

**不改**
- 任务 DB schema、任务状态枚举、socketio 事件名、前端代码、用户操作流。

### 6.2 子任务清单

#### 6.2.1 进程级取消协议（shutdown_coordinator + cancellation）

**目标**：提供"全进程一次设置、所有线程都能查"的取消信号。

**设计**：
- `appcore/shutdown_coordinator.py`：
  - 模块级 `_shutdown_event = threading.Event()`。
  - `is_shutdown_requested() -> bool`、`request_shutdown(reason: str)`（幂等）、`wait(timeout: float) -> bool`、`reason() -> str`。
  - `wait_for_active_tasks(timeout: float) -> int`：循环检查 `appcore.task_recovery._active_tasks` 长度；为 0 立即返回 0；否则每 0.5s 轮询一次直到为空或超时；返回退出时仍活跃的任务数（0 = 全部干净退出）。**实现注意**：不能持有 `_active_lock`，否则会跟 `unregister_active_task` 死锁。
  - 启动期可调 `reset()`（仅测试用；prod 不调）。
- `appcore/cancellation.py`：
  - 异常 `OperationCancelled(RuntimeError)`。
  - `throw_if_cancel_requested(reason: str = "")`：内部检查 `shutdown_coordinator.is_shutdown_requested()`，是则 `raise OperationCancelled(...)`。
  - `cancellable_sleep(seconds: float)`：用 `_shutdown_event.wait(seconds)`，被信号唤醒就 `raise`。

**不可逆改动**：无（纯新增模块）。

**风险**：模块循环引用——确保不引用 `appcore.task_state` 之类的高层模块，只引用 `threading`/`logging`。

**回滚**：删除两个新文件 + 把所有 `throw_if_cancel_requested()` 调用注释掉。

#### 6.2.2 Gunicorn signal hook + APScheduler shutdown

**目标**：让 worker 收到 SIGTERM/SIGINT 时真正触发 cancel + 关掉 scheduler。

**设计**：在 `deploy/gunicorn.conf.py` 末尾补：

```python
def post_worker_init(worker):
    """Worker fork 完成后挂钩。"""
    import signal
    from appcore.shutdown_coordinator import request_shutdown
    from appcore.scheduler import shutdown_scheduler

    original_term = signal.getsignal(signal.SIGTERM)
    original_int = signal.getsignal(signal.SIGINT)

    def _term(signum, frame):
        request_shutdown(f"signal={signum}")
        shutdown_scheduler(wait=False)
        if callable(original_term):
            original_term(signum, frame)

    def _int(signum, frame):
        request_shutdown(f"signal={signum}")
        shutdown_scheduler(wait=False)
        if callable(original_int):
            original_int(signum, frame)

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _int)


def worker_exit(server, worker):
    """Worker 即将退出时跑一次扫尾（apscheduler shutdown + 等活跃任务）。"""
    from appcore.shutdown_coordinator import request_shutdown, wait_for_active_tasks
    from appcore.scheduler import shutdown_scheduler

    request_shutdown("worker_exit")
    shutdown_scheduler(wait=False)
    # 给活跃任务最多 200 秒退出窗口；超时不强杀，只记日志，让 systemd 兜底。
    wait_for_active_tasks(timeout=200)
```

**graceful_timeout** 从 900 → 240。具体数值的选择和与 systemd `TimeoutStopSec` 的对位关系见 6.2.6。

**不可逆改动**：gunicorn worker 行为变化。

**风险**：
- signal handler 和 Gunicorn 内部 SIGTERM 处理冲突——通过 chain 调用 `original_term` 规避。
- `wait_for_active_tasks` 实现要避免死锁——用 `Event.wait(timeout)` 不要 `Lock`。

**回滚**：删除 `post_worker_init` / `worker_exit` 两个函数，把 `graceful_timeout` 改回 900。

#### 6.2.3 APScheduler 显式 shutdown

**目标**：`scheduler.shutdown(wait=False)` 在停机时被调用。

**设计**：在 `appcore/scheduler.py` 加：

```python
def shutdown_scheduler(wait: bool = False) -> None:
    global _scheduler
    if _scheduler is None:
        return
    try:
        if _scheduler.running:
            _scheduler.shutdown(wait=wait)
    except Exception:
        log.warning("[scheduler] shutdown failed", exc_info=True)
    finally:
        _scheduler = None
```

`post_worker_init` signal handler 和 `worker_exit` 已经调它（见 6.2.2）；另外 `atexit.register` 兜底。

**风险**：scheduler 内部 worker thread 挂在某个 IO 调用上，`shutdown(wait=False)` 不等它——这是预期行为，让 OS 在进程退出时清理（daemon thread 配合）。

**回滚**：删除 `shutdown_scheduler` 函数 + 取消 `atexit.register` 行。

#### 6.2.4 长任务循环加可中断点

**目标**：在 runner 主循环顶部、长 for-loop 顶部、各 step 入口插 `throw_if_cancel_requested()`。

**优先级矩阵**（按"线程能挂多久"评估，先改高优先级）：

| 文件 | 主要循环 | 单次迭代估算 | 优先级 |
|------|----------|--------------|--------|
| `web/services/image_translate_runner.py` | items 批量循环（最大 1000 张） | 5-30s/张 | 高 |
| `appcore/bulk_translate_runtime.py` 等 bulk_translate | plan items 循环（最大 200+） | 2-10 分钟/项 | 高 |
| `web/services/multi_pipeline_runner.py` | 9 step 主循环 + TTS 段循环 | 单 step 几分钟 | 高 |
| `appcore/runtime.py` PipelineRunner | 8 step 主循环 | 单 step 几分钟 | 高 |
| `appcore/runtime_v2.py` PipelineRunnerV2 | 9 step + 翻译分镜 for-loop | 单 step 几分钟 | 高 |
| `appcore/runtime_sentence_translate.py` | 类似 v2 | | 高 |
| `web/services/omni_pipeline_runner.py` / `de/fr/ja_pipeline_runner.py` | 同 multi | | 中 |
| `web/services/subtitle_removal_runner.py` | 上游异步轮询循环 | 5s/次 | 中 |
| `web/services/translate_lab_runner.py` | 测试模块 | 不在 prod 关键路径 | 低 |
| `web/services/link_check_runner.py` | URL 抓取并发循环 | 几秒/URL | 低 |
| `appcore/scheduled_tasks.py` job wrapper | 每个 scheduler job 入口 | | 低（频次低） |

**插点规范**：
1. 每个 step 函数顶部一行 `throw_if_cancel_requested()`。
2. 长 for-loop 在 `for` 行下一行加 `throw_if_cancel_requested()`。
3. 任何 `time.sleep(...)` 替换为 `cancellable_sleep(...)`（subtitle_removal 上游轮询尤其重要）。
4. `OperationCancelled` 在 runner 顶层 `_run` / `start` 的 except 链里捕获，把 task 标 `interrupted` 并发 `*_PIPELINE_ERROR` event 携带 `cancelled=True`，然后 `return`（不要 raise 出去——会让 thread 留 unhandled exception traceback 在日志里看着像 bug）。

**风险**：
- 漏插点 → 单步 run 时长上限就是该 step 的最慢迭代时间（可控）。
- 在 ffmpeg subprocess 等外部进程调用前后必须加，但 subprocess 本身不可中断；接受单次 ffmpeg 跑完。
- 跟 `tools/shopify_image_localizer/cancellation.py` 同名异类——所以新建 `appcore/cancellation.py` 而不是复用，文档明确两者不要互相 import。

**回滚**：把 `throw_if_cancel_requested()` 调用全删（一个 `git revert` 即可）。`OperationCancelled` 的 except 块保留也无害（永远不会被 raise）。

#### 6.2.5 重启前活跃任务探针

**目标**：运维 `ssh root@host` 一条命令能列出当前正在跑的任务。

**设计**：
- 在 `appcore/task_recovery.py` 加 `snapshot_active_tasks() -> list[dict]`：返回 `_active_tasks` 的副本（`project_type`, `task_id`），并尽量从 `task_state.get(task_id)` 取 `display_name` / `started_at`。
- 新增 `web/routes/admin_runtime.py`：
  - `GET /admin/runtime/active-tasks`：返回 `{shutting_down, active_count, items: [...], scheduler_running, scheduler_jobs}`。需要 `is_admin` 才能访问，绕过 CSRF（GET 接口不需要）。
- 新增 `tools/active_tasks_probe.py`：单独的脚本，能 import `appcore.task_recovery` 直接读 `_active_tasks`（**不**走 HTTP），适合 systemd `ExecStartPre` 或部署脚本调。
- 把 [`deploy/publish.sh`](../../../deploy/publish.sh) 的"远端 pull + restart"那一步前面加：

  ```bash
  ssh ... "curl -s http://127.0.0.1/admin/runtime/active-tasks | python3 -m json.tool"
  ```
  让运维肉眼检查；不强制阻断 restart，只是把信息暴露出来。

**风险**：
- API 暴露后台任务 ID 给 admin——用户已经能在 admin 后台看到。
- `_active_tasks` 是进程内 set，跨 worker 不共享——本项目 workers=1，不是问题。

**回滚**：删除 route 和 CLI 脚本，删除 publish.sh 里的探针 step。

#### 6.2.6 timeout 调整与文档同步

- `deploy/autovideosrt.service` `TimeoutStopSec`: 900 → 300。注释从"等长任务跑完"改成"等任务收到 cancel 信号 + 当前迭代结束"。
- `deploy/gunicorn.conf.py` `graceful_timeout`: 900 → 240。注释同步更新。
- `web/routes/admin_runtime.py` 探针接口 + 部署脚本说明写进 README 部署章节。

**为什么 300/240 这组数**：
- 240s = 200s（让 active task 跑完最后一次迭代 + 标 interrupted）+ 40s（HTTP 请求 + 余量）。
- 300s = 240s + 60s（systemd 兜底缓冲）。
- 不再取 900s 的原因：信号链补全后 60s 就够了；300s 是异常情况兜底；900s 反而会让真正"卡死"的进程被埋藏 15 分钟才发现。
- 极端情况（比如某 ffmpeg 卡死 200s+）systemd 会发 SIGKILL——但这种情况现在反正也跑不完任务，强杀和不强杀结果一致；强杀让重启快、问题暴露快。

### 6.3 风险全景

| 风险 | 缓解 | 触发条件 |
|------|------|----------|
| signal handler 跟 Gunicorn 内部 SIGTERM 冲突 | chain 调用原 handler；测试环境先验证 SIGTERM/SIGINT 都能正常退出 | post_worker_init 没执行成功 |
| `OperationCancelled` 在某些 step 没被捕获，traceback 脏日志 | runner 顶层 except 链兜底；阶段 1 测试覆盖每种 runner | 漏改某个 runner 的顶层 except |
| ffmpeg subprocess 跑超过 240s | 接受这种情况会被 SIGKILL，因为没有更优方案；后续 Phase 2 worker 化再优化 | 超长视频 + 复杂 filter |
| 探针接口被 abuse 高频调用 | 内部接口，限 admin；并把响应缓存 1s | 第三方爬到 |
| 测试环境验证不够 → 线上首次 restart 暴露 bug | 阶段 1 强制要求测试环境跑 ≥ 3 天，并模拟"长任务途中重启"场景 | 紧急发布跳过测试环境 |
| TimeoutStopSec 变短后，老旧版本部署机回滚时仍然 SIGKILL | 灰度发布 + journalctl 跟踪 24h；阶段 1 落地后保留旧 unit 文件备份 | 回滚到无 hook 的旧版本 |

### 6.4 测试方案

**单元测试（pytest）**
- `tests/test_shutdown_coordinator.py`：
  - `request_shutdown` 后 `is_shutdown_requested` 为真。
  - `wait_for_active_tasks` 在 timeout 内退出。
  - `cancellable_sleep` 被信号打断会 raise。
- `tests/test_runner_lifecycle_cancellable.py`：
  - 启动 dummy `start_tracked_thread`，途中 `request_shutdown`，线程能在 N 秒内退出。
  - `OperationCancelled` 触发后 `_active_tasks` 被清。
- `tests/test_admin_runtime_routes.py`：
  - 未登录 401；非 admin 403；admin 返回 200 + 正确 schema。
  - 探针返回的 `items` 反映当前 `_active_tasks`。

**集成测试（手工，测试环境）**
1. 在测试环境（172.30.254.14:8080）启动一个 multi_translate 50 段任务。
2. 等到 step=`tts` 跑到一半（第 10-15 段），`ssh root@172.30.254.14 "systemctl restart autovideosrt-test.service"`。
3. 验证：
   - `journalctl -u autovideosrt-test -n 100 --no-pager`：无 `SIGKILL` / `signal 9` / `process didn't exit`。
   - 重启总耗时 ≤ 60s（实际看 `systemd-analyze stop autovideosrt-test`）。
   - 任务在 DB 里 `status=interrupted`，`steps.tts=interrupted`。
   - 重启后页面能看到"已中断"，按"重新启动"能正常 resume。
4. 同步重复一次 image_translate 100 张图、bulk_translate 30 项的场景。
5. 跑 5 次连续 `restart` 验证稳定性（无残留 zombie 进程）。

**线上验收**
- 灰度发布：`deploy/publish.sh` 推上去后，`ssh + journalctl --since '5m ago' | grep -E 'signal 9|SIGKILL|killed'`，应为空。
- 24 小时观察期，每天扫一次 systemd 日志确认没新增 SIGKILL。

### 6.5 落地顺序建议

每个 commit 单独发布、单独验证，不要打包：

1. **6.2.1 + 6.2.3**（一次 commit）：新增 `shutdown_coordinator.py` / `cancellation.py` + `scheduler.shutdown_scheduler`。纯新增模块，无人调，零风险。
2. **6.2.5 探针**：`admin_runtime.py` route + `tools/active_tasks_probe.py`。先把"看活跃任务"能力上线，方便后续 step 排查问题。
3. **6.2.4 长任务可中断点**：分两步走，先改 image_translate 一个 runner（最高频任务）跑 24h，再批量铺开其他 runner。
4. **6.2.2 Gunicorn signal hook**：上线 hook，但 graceful_timeout 暂不改，先验证 hook 正常触发。
5. **6.2.6 timeout 收紧**：把 `TimeoutStopSec` 900 → 300、`graceful_timeout` 900 → 240，跑 24h 灰度。
6. 验收 + 监控一周。

如果 step 3/4 出问题，前置 step 仍然有效（hook 拿不到 cancellation 协议响应，但至少 scheduler 会 shutdown）。

### 6.6 回滚方案

阶段 1 是 6 个独立 commit / patch，回滚粒度：

- **整体回滚**：`git revert <merge-commit>`，把 `gunicorn.conf.py` / `autovideosrt.service` / 几个 runner 文件全部还原；新增的 `shutdown_coordinator.py` / `cancellation.py` / `admin_runtime.py` 留着无害（无人调）。
- **仅回滚 timeout 改动**：把 `TimeoutStopSec=300` / `graceful_timeout=240` 改回 `900`，重启服务。`shutdown_coordinator` 等保留。
- **仅关闭 signal hook**：把 `post_worker_init` / `worker_exit` 函数体改成 `return`；scheduler 仍然能跑，cancellation 仍然有效，只是收不到 SIGTERM 信号——退化到现状。

每个 commit 单独可逆。

---

## 7. 阶段 2：结构性 worker 化

阶段 1 跑稳后启动；阶段 2 设计在此处只到目标架构 + 关键路径，详细 spec 留待阶段 1 验收完成后再写一份单独的 design doc。

### 7.1 目标架构

```
┌─────────────────────────────────────────┐
│  systemd: autovideosrt.service          │
│  Gunicorn (gthread x32, workers=1)      │
│  TimeoutStopSec=60                      │  ← Web 重启秒级
│  · HTTP / WebSocket                     │
│  · DB 写入 + 入队                       │
│  · 不跑长任务、不跑 APScheduler        │
└──────────┬──────────────────────────────┘
           │ INSERT INTO project_jobs
           ▼
┌─────────────────────────────────────────┐
│  DB 表 project_jobs (新增)              │
│  · id, project_id, project_type        │
│  · status (queued/running/done/failed) │
│  · claimed_by, claimed_at, payload     │
└──────────┬──────────────────────────────┘
           │ SELECT … FOR UPDATE SKIP LOCKED
           ▼
┌─────────────────────────────────────────┐
│  systemd: autovideosrt-worker.service   │
│  Python entrypoint: appcore.worker     │
│  TimeoutStopSec=300（沿用阶段 1）       │
│  · 单进程 + N 线程消费队列              │
│  · 跑 PipelineRunner 等所有长任务       │
│  · 跑 APScheduler                      │
│  · 通过 socketio Redis adapter 推事件   │
└─────────────────────────────────────────┘
           │ 事件 publish
           ▼
       Redis (socketio message_queue)
           │
       Web 进程 socketio 转发回浏览器
```

### 7.2 涉及文件（新增 / 修改）

**新增**
- `deploy/autovideosrt-worker.service`：systemd unit，`ExecStart=python -m appcore.worker`。
- `deploy/autovideosrt-worker-test.service`：测试环境同构。
- `appcore/worker.py`：worker 主循环 entrypoint。
- `appcore/job_queue.py`：DB 队列 claim / complete / fail / retry。
- `migrations/NN_create_project_jobs.sql`：建 `project_jobs` 表。

**修改**
- `web/services/*_runner.py`：从直接调 `start_tracked_thread` → 调 `job_queue.enqueue(...)`，立即返回；保留 `start_tracked_thread` fallback（feature flag 控制）。
- `appcore/scheduler.py`：`get_scheduler` 加环境变量门 `AUTOVIDEOSRT_RUN_SCHEDULER`，web 进程默认关，worker 进程默认开。
- `web/extensions.py`：socketio 配 `message_queue=redis://...` 当且仅当 worker 模式启用。
- `main.py`：根据 `AUTOVIDEOSRT_ROLE=web|worker` 不启动对应组件。
- `deploy/setup.sh`、`deploy/publish.sh`：覆盖两个 service 的安装/发布。

### 7.3 子任务清单

1. 建 `project_jobs` 表 + migration（DB 改动，需要审）。
2. `job_queue.py`：claim 用 `SELECT … FOR UPDATE SKIP LOCKED`（MySQL 8.0+）。
3. `worker.py`：主循环 + cancellation（沿用阶段 1）。
4. `web/services/*_runner.py` 改造：feature flag `AUTOVIDEOSRT_USE_JOB_QUEUE` 默认 false；切换 → enqueue。
5. socketio Redis adapter 接入（部署 Redis 服务）。
6. APScheduler 迁移到 worker：web 进程不再起 scheduler，worker 起。
7. systemd unit 上线 + 灰度。
8. 完全切换后，从 web 进程移除 PipelineRunner import 链路（可在阶段 2.5 做）。

### 7.4 风险与回滚

- **DB 锁竞争**：MySQL `FOR UPDATE SKIP LOCKED` 已经验证可用（项目使用 MySQL 8.0）；worker 单例就好。
- **Redis 单点**：起步阶段 Redis 跟 web 同机，作为 socketio queue；任务关键状态仍走 MySQL 持久化，Redis 挂掉不丢任务。
- **socketio 跨进程**：sticky session 问题——本项目 web workers=1，没问题；如果未来 web 加 worker 数，再做 sticky。
- **APScheduler 跑两份**：feature flag 控制；切换期间通过日志加 hostname/role 区分。
- **worker 重启时正在跑的 job**：worker 自己沿用阶段 1 的 cancellation 协议，重启时把 in-flight job 标 `interrupted` 并 release 队列锁（`UPDATE project_jobs SET status='interrupted', claimed_by=NULL`）；下次任意 worker pick up 时根据 project 的 `_active_tasks` / 数据库状态决定续跑还是等用户手动 resume，沿用阶段 1 现有路径。**image_translate 异步任务的自动续跑**仍由 `_auto_resume_after_recovery` 兜底，逻辑不变。
- **回滚**：`AUTOVIDEOSRT_USE_JOB_QUEUE=0` + `systemctl stop autovideosrt-worker` 即退化到阶段 1 行为，原 web 进程仍能跑长任务。

### 7.5 测试方案

- 测试环境双进程跑 ≥ 1 周，验证 multi/image/subtitle/bulk_translate 全覆盖。
- 性能基线：100 张 image_translate、50 段 multi_translate 完成时长对比阶段 1（预期 ≤ 阶段 1 的 105%）。
- 故障注入：`kill -9 <worker-pid>` 模拟 worker 崩溃，web 应能在 60s 内通过 systemd 自动 Restart 或 web 端的 startup recovery 把 in-flight job 标 interrupted。

---

## 8. 验收标准

### 8.1 阶段 1 验收（完整）

**测试环境**（172.30.254.14:8080，admin: testuser.md 凭据）

- [ ] `pytest tests/test_shutdown_coordinator.py tests/test_runner_lifecycle_cancellable.py tests/test_admin_runtime_routes.py -q` 全绿。
- [ ] 测试环境跑 multi_translate 50 段任务，途中 `systemctl restart autovideosrt-test.service`：
  - `journalctl -u autovideosrt-test --since '10m ago' | grep -E 'signal 9|SIGKILL'` 为空。
  - `systemd-analyze` 重启总耗时 ≤ 60s。
  - 任务在 DB `status=interrupted`，`steps.<current>=interrupted`，前端可重新启动。
- [ ] image_translate 100 张图同样验证。
- [ ] bulk_translate 30 项同样验证。
- [ ] APScheduler jobs 在重启前后 `apscheduler.events` 计数一致；下一次 cleanup tick 不延迟。
- [ ] 探针接口 `curl -s http://127.0.0.1:8080/admin/runtime/active-tasks` 在重启前能列出活跃任务，重启后返回空。

**线上**（172.30.254.14）

- [ ] 灰度发布完成后 24 小时无 `SIGKILL` 日志。
- [ ] 24 小时内主动跑 ≥ 1 次重启演练，确认验收点全过。
- [ ] 执行一次"重启前查活跃任务"完整流程，ssh + curl + 决定延后 → 等 5 分钟再 restart → 无任务受影响。
- [ ] 一周内复检 `journalctl --since '7 days ago' | grep -E 'signal 9|SIGKILL'` 为空。

### 8.2 阶段 2 验收（仅在阶段 2 落地后启用）

- [ ] web 进程重启在 ≤ 10s 完成，期间无任务中断。
- [ ] worker 进程重启沿用阶段 1 验收标准。
- [ ] 一周观察期内无任务因为队列竞争丢失。

---

## 9. 已知不在本方案范围

1. **任务级断点恢复**（resume mid-step）。当前所有 runner 都是"从某 step 开头" resume，不支持 step 内部断点。本方案保持这个语义。
2. **跨进程任务迁移**（live migration）。阶段 2 worker 重启时仍然中断当前任务、入队等下次。
3. **前端"等任务跑完"按钮**。运维要做"保留性重启"靠探针 + 命令行；不做前端可视化等待按钮。
4. **多 worker 进程横向扩展**。本项目 web/workers=1 是有意决定（见 [2026-04-22-web-service-tuning-design.md](2026-04-22-web-service-tuning-design.md)），阶段 2 worker 也起步单 worker，未来扩展再说。
5. **观测 / SLO**。SIGKILL 计数报警、graceful 退出耗时 P99 等指标：建议阶段 1 落地后单独跑一次"接入定时任务管理 + 报警"的 spec。

---

## 10. 后续工作（落地实施时再展开）

- **阶段 1 → 实施 plan**：本 spec 通过后，调用 `superpowers:writing-plans` 把 6.2 的 6 个子任务拆成可单 commit 落地的 step-by-step plan，每 step 配 TDD 测试。
- **阶段 2 → 单独 spec**：阶段 1 跑稳 ≥ 1 周后，再写一份 `2026-MM-DD-worker-service-extraction-design.md`，把 7.2 的子任务展开。
- **监控加挂**：阶段 1 落地后给 systemd 配一个简单的 OnFailure 钩子或者用 `appcore/scheduled_tasks.py` 加个 nightly job：扫 `projects.status='running'` 但 `_active_tasks` 没有的孤儿任务，走现有 recovery 路径。
