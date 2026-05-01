# 优雅停机与后台任务生命周期治理设计方案

> 本文档只设计、不落地代码。落地实现需要再写 implementation plan，并按小步提交执行。
>
> **基线 commit：** `2a796edc`（第 7 项修复已合入 master，测试环境已验证）。
> **目标环境：** 先测试环境 `http://172.30.254.14:8080/`，确认后再发布线上。
> **数据库规则：** 不连接 Windows 本机 MySQL，不访问 `127.0.0.1:3306`；数据库验证只在服务器环境执行。

---

## 1. 背景与问题

线上最近一次发布时，`autovideosrt.service` 停止阶段出现：

- `State 'stop-sigterm' timed out. Killing.`
- `Main process exited, code=killed, status=9/KILL`
- 新 Gunicorn 已启动，页面冒烟通过，但旧进程不是自然退出。

这说明当前发布链路仍有一个稳定性缺口：Web 进程里承载了长任务线程，systemd/Gunicorn 停机窗口与后台任务生命周期没有统一治理。现有代码已经有一层防护：

- `appcore.runner_lifecycle.start_tracked_thread`：统一注册/注销活动任务。
- `appcore.task_recovery`：启动时修复部分被中断任务。
- 多个 runner 已改为通过 active registry 避免重复启动。

但仍缺少三件关键能力：

1. **重启前不知道是否有活跃长任务。** 发布脚本/人工重启无法安全判断是否应该延后。
2. **停机时没有持久化“正在执行”快照。** 只靠内存 `_active_tasks`，进程退出后证据消失。
3. **Web 进程和长任务执行仍强耦合。** 只调 `TimeoutStopSec` 和 `graceful_timeout` 是止血，不是根治。

---

## 2. 目标

### 本期目标

- 发布/重启前能查询活跃长任务，并在存在高风险任务时阻止或提示。
- 停机信号到来时，把活动任务快照写入可审计位置。
- 统一 Gunicorn `graceful_timeout` 与 systemd `TimeoutStopSec` 的策略，避免代码注释、配置、线上 drop-in 互相冲突。
- 为独立 worker service 设计清晰演进路径，但第一阶段不大改任务执行架构。

### 非目标

- 第一阶段不把所有任务一次性迁到 worker。
- 第一阶段不引入 Celery / Redis / RabbitMQ 等新依赖。
- 第一阶段不改变任务创建接口、页面路由和用户操作路径。
- 不在 Windows 开发机本地启动或依赖 MySQL。

---

## 3. 总体方案

分两阶段推进。

### Phase 1：低风险止血

目标是让发布可控、可观测、可回滚，不大改业务执行模型。

核心动作：

1. 新增运行时任务快照表或文件级快照。
2. 扩展 active task registry，记录 `project_type`、`task_id`、线程名、启动时间、入口、用户、最后心跳时间。
3. 新增只读 CLI：`python -m appcore.ops.active_tasks`。
4. 新增发布前检查命令：若存在不可中断任务，默认退出非 0。
5. 停机信号时写入 shutdown snapshot，并在日志中明确列出未完成任务。
6. 统一配置口径：
   - 应用默认 `AUTOVIDEOSRT_GUNICORN_GRACEFUL_TIMEOUT=30~60` 秒；
   - systemd `TimeoutStopSec` 与 Gunicorn 保持一致；
   - 长任务不能再依赖 Web 进程停机等待 15 分钟。

### Phase 2：结构性 worker 化

目标是把长任务从 Web 进程迁出，让 Web 重启不再影响任务执行。

核心动作：

1. 新增 `autovideosrt-worker.service` 和 `autovideosrt-test-worker.service`。
2. 引入 DB-backed worker lease，不新增外部队列依赖。
3. Web 进程只负责创建任务、查询状态、发起取消。
4. Worker 循环 claim 任务、执行任务、心跳、完成/失败/释放 lease。
5. 先迁一个低风险任务链路，再迁高风险长任务：
   - 先迁 `link_check` 或 `subtitle_removal` 这类边界清楚的任务；
   - 再迁 `image_translate`；
   - 最后迁 `multi_translate` / `omni_translate` / `translate_lab`。

---

## 4. Phase 1 详细设计

### 4.1 Active Task Registry 扩展

现有：

- `appcore/task_recovery.py`
  - `_active_tasks: set[tuple[str, str]]`
  - `register_active_task`
  - `try_register_active_task`
  - `unregister_active_task`
  - `is_task_active`

建议新增：

- `appcore/active_tasks.py`
  - `ActiveTask` dataclass。
  - 内存 registry：`dict[(project_type, task_id), ActiveTask]`。
  - `list_active_tasks()`。
  - `heartbeat_active_task(project_type, task_id, stage=None)`。
  - `snapshot_active_tasks(reason)`。

保留旧函数兼容：

- `task_recovery.register_active_task` 内部转调 `active_tasks.register(...)`。
- `task_recovery.try_register_active_task` 内部转调 `active_tasks.try_register(...)`。
- `task_recovery.unregister_active_task` 内部转调 `active_tasks.unregister(...)`。

这样可以不一次性改所有调用方。

### 4.2 持久化快照

第一阶段建议先使用数据库表，原因是服务器验证和后台展示都更直接。

新增 migration：

```sql
CREATE TABLE IF NOT EXISTS runtime_active_task_snapshots (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  snapshot_reason VARCHAR(64) NOT NULL,
  project_type VARCHAR(64) NOT NULL,
  task_id VARCHAR(128) NOT NULL,
  user_id BIGINT NULL,
  runner VARCHAR(255) NOT NULL DEFAULT '',
  stage VARCHAR(255) NOT NULL DEFAULT '',
  started_at DATETIME NULL,
  last_heartbeat_at DATETIME NULL,
  captured_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  details_json JSON NULL,
  KEY idx_captured_at (captured_at),
  KEY idx_task (project_type, task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

写入场景：

- 发布前人工执行 preflight 时，写 `snapshot_reason='pre_restart_check'`。
- Gunicorn worker 收到 SIGTERM 时，写 `snapshot_reason='shutdown_signal'`。
- 启动恢复发现中断任务时，写 `snapshot_reason='startup_recovery'`。

如果数据库不可用，降级写 JSON Lines 到：

- 测试：`/opt/autovideosrt-test/logs/active-task-snapshots.jsonl`
- 线上：`/opt/autovideosrt/logs/active-task-snapshots.jsonl`

### 4.3 发布前检查 CLI

新增：

- `appcore/ops/active_tasks.py`

命令：

```bash
/opt/autovideosrt/venv/bin/python -m appcore.ops.active_tasks list
/opt/autovideosrt/venv/bin/python -m appcore.ops.active_tasks pre-restart --max-age-seconds 30
```

行为：

- `list` 输出当前进程内 active tasks。若未来有 worker，则合并 DB lease。
- `pre-restart`：
  - 没有活跃任务：退出 `0`。
  - 只有可安全中断任务：退出 `0`，但打印 warning。
  - 有不可中断长任务：退出 `2`，发布脚本默认停止。

第一阶段分类建议：

| 类型 | 默认重启策略 | 说明 |
|------|-------------|------|
| `link_check` | 可中断 | 已有启动恢复，可重跑 |
| `subtitle_removal` | 谨慎中断 | 有 provider task 时可恢复轮询 |
| `image_translate` | 谨慎中断 | APIMART/上游任务需避免重复提交 |
| `multi_translate` / `omni_translate` / `translate_lab` | 不建议中断 | 业务链路长，恢复成本高 |
| `video_creation` / `video_review` | 不建议中断 | 生成/评估过程重跑成本高 |

### 4.4 Gunicorn/systemd 统一策略

当前代码默认：

- `deploy/gunicorn.conf.py`：`graceful_timeout=900`
- 线上 systemd drop-in：`TimeoutStopSec=30`

这两个值冲突。建议 Phase 1 改为：

- 代码默认 `AUTOVIDEOSRT_GUNICORN_GRACEFUL_TIMEOUT=45`。
- systemd `TimeoutStopSec=60`。
- 文档明确：Web 停机不等待长任务完成；长任务必须通过 active snapshot + startup recovery/worker lease 保护。

为什么不是继续 900 秒：

- 900 秒会让发布卡住，且如果线程不响应 SIGTERM，最终仍会被 kill。
- 900 秒掩盖了“长任务不应该在 Web 进程里等待完成”的架构问题。

为什么不是直接 30 秒：

- 30 秒对普通 HTTP 请求足够，但对迁移/启动恢复/清理 shutdown hooks 偏紧。
- 60 秒能给快照写入和普通请求收尾留余量。

### 4.5 Web 后台登记

根据项目规则，新增后台常驻 worker 或发布前检查脚本，都必须在“定时任务”模块登记。

Phase 1 需要补：

- `appcore/scheduled_tasks.py`
  - `active_task_pre_restart_check`
  - 类型：`manual_ops`
  - 入口：`python -m appcore.ops.active_tasks pre-restart`
  - 日志：systemd journal / deploy log / `runtime_active_task_snapshots`

Phase 2 新增 worker 后再补：

- `background_worker_loop`
  - 类型：`systemd_service`
  - 入口：`python -m appcore.worker`
  - 部署：`autovideosrt-worker.service`
  - 日志：`journalctl -u autovideosrt-worker`

---

## 5. Phase 2 Worker 化设计

### 5.1 Worker Lease 表

新增表：

```sql
CREATE TABLE IF NOT EXISTS background_task_leases (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  task_key VARCHAR(191) NOT NULL UNIQUE,
  project_type VARCHAR(64) NOT NULL,
  task_id VARCHAR(128) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'queued',
  priority INT NOT NULL DEFAULT 100,
  worker_id VARCHAR(128) NOT NULL DEFAULT '',
  lease_until DATETIME NULL,
  heartbeat_at DATETIME NULL,
  attempts INT NOT NULL DEFAULT 0,
  max_attempts INT NOT NULL DEFAULT 3,
  payload_json JSON NULL,
  last_error TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_claim (status, priority, created_at),
  KEY idx_lease_until (lease_until)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 5.2 Worker 模块边界

新增：

- `appcore/worker/__init__.py`
- `appcore/worker/leases.py`
- `appcore/worker/registry.py`
- `appcore/worker/main.py`

职责：

- `leases.py`：DB claim / heartbeat / complete / fail。
- `registry.py`：`project_type -> handler` 映射。
- `main.py`：循环 claim、执行 handler、处理 SIGTERM。

Web 侧改造：

- 原先 `_start_runner(task_id, user_id)` 改成 `enqueue_background_task(project_type, task_id, payload)`。
- 页面仍然轮询同一套 task state，不感知 worker 变化。

### 5.3 迁移顺序

推荐迁移顺序：

1. `link_check`
   - 状态机清晰，失败可重跑。
   - 验证 worker claim/heartbeat/retry 成本低。
2. `subtitle_removal`
   - 已有 provider task 恢复逻辑。
   - 能验证“外部异步任务已提交后不重复提交”。
3. `image_translate`
   - APIMART/Seedream/OpenRouter 多 channel，风险中等。
4. `multi_translate` / `omni_translate`
   - 涉及子任务和批量链路，最后迁。
5. `translate_lab`
   - 实验链路复杂，最后迁或单独设计。

---

## 6. 测试方案

### Phase 1 单元测试

新增或扩展：

- `tests/test_active_tasks.py`
  - register / try_register / unregister。
  - duplicate start 被拒绝。
  - heartbeat 更新。
  - snapshot 写入成功。
- `tests/test_active_tasks_cli.py`
  - 无任务退出 0。
  - 可中断任务退出 0 + warning。
  - 不可中断任务退出 2。
- `tests/test_runner_lifecycle.py`
  - `start_tracked_thread` 自动注册和注销。
  - target 抛异常也会注销。
  - metadata 被保留。
- `tests/test_appcore_scheduled_tasks.py`
  - 新 ops 任务被登记。

### Phase 1 集成验收

测试环境执行：

1. 部署代码到 `/opt/autovideosrt-test`。
2. 登录后台，确认“定时任务”页面能看到 active task preflight。
3. 执行：

```bash
cd /opt/autovideosrt-test
/opt/autovideosrt/venv/bin/python -m appcore.ops.active_tasks pre-restart
```

4. 无活跃任务时应退出 0。
5. 人工制造一个测试用 active task，preflight 应退出 2。
6. 重启测试服务，确认：
   - 服务 `active`。
   - `journalctl -u autovideosrt-test -p warning` 无新增异常。
   - snapshot 表或 jsonl 有停机记录。

### Phase 2 单元测试

新增：

- `tests/test_worker_leases.py`
  - claim 同一 task 只有一个 worker 成功。
  - lease 过期后可重新 claim。
  - heartbeat 延长 lease。
  - complete / fail 状态正确。
- `tests/test_worker_registry.py`
  - 未注册 handler 拒绝执行。
  - handler 异常会记录失败。
- 对首个迁移任务补端到端测试。

### Phase 2 测试环境验收

1. 启动 `autovideosrt-test-worker.service`。
2. Web 创建任务，但不在 Web 进程内启动线程。
3. Worker claim 并执行。
4. 重启 Web：任务继续运行。
5. 重启 Worker：任务被标记 interrupted 或 lease 到期后恢复。
6. 页面状态与任务日志一致。

---

## 7. 回滚方案

### Phase 1 回滚

- 代码回滚 active task CLI 与快照逻辑。
- migration 表保留不影响旧代码。
- systemd timeout 可恢复为当前线上 drop-in 30 秒。
- 如果 snapshot 写入异常，允许通过环境变量关闭：

```bash
AUTOVIDEOSRT_ACTIVE_TASK_SNAPSHOT_ENABLED=0
```

### Phase 2 回滚

- Web 侧保留旧 `_start_runner` 入口一个 release。
- 若 worker 出问题，关闭 worker service，并把对应 project_type 切回 in-process runner。
- lease 表保留不影响旧代码。

---

## 8. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| preflight 阻止发布，影响紧急修复 | 线上 hotfix 延误 | 支持 `--force`，但必须写 snapshot 和日志 |
| snapshot 写 DB 失败 | 停机日志缺失 | 降级写 jsonl |
| 线程不调用 heartbeat | active task 看起来过期 | Phase 1 仅作提示，不自动 kill；Phase 2 再强制 lease |
| systemd timeout 改短导致任务中断更多 | 长任务失败 | 通过 preflight 阻止有任务时重启；中断由 recovery/worker 兜底 |
| worker claim 逻辑 bug 导致重复执行 | 外部 API 重复计费 | DB 唯一键 + row lock + provider task id 幂等校验 |
| APIMART/外部异步任务恢复不完整 | 结果丢失或重复提交 | 先迁 subtitle_removal/image_translate 时补 provider task id 单测 |

---

## 9. 验收标准

Phase 1 完成标准：

- 发布前可以用 CLI 清楚看到是否存在活跃长任务。
- 测试环境重启时，若有活跃长任务，preflight 默认阻止。
- 无活跃任务时，测试环境重启不会出现 systemd stop timeout。
- “定时任务”页面能看到发布前检查入口登记。
- 聚焦测试、`py_compile`、`git diff --check` 通过。

Phase 2 完成标准：

- 至少一个长任务类型由 worker 执行，Web 重启不影响执行。
- Worker 有 systemd service、日志、状态检查和后台登记。
- lease 过期恢复、重复 claim 防护、失败重试都有测试覆盖。
- 测试环境完成 Web 重启、Worker 重启、任务继续/恢复三类验收。

---

## 10. 建议执行顺序

1. 先写 Phase 1 implementation plan。
2. 实现 active task metadata + CLI，不改 systemd。
3. 接入 `start_tracked_thread`，补测试。
4. 加 preflight 到部署流程，测试环境验证。
5. 再统一 Gunicorn/systemd timeout。
6. Phase 1 线上稳定后，再单独开 Phase 2 worker 化。

第一阶段完成后，再决定是否启动第二阶段。不要把 worker 化和 timeout 调整混在同一个发布里。
