# ROI hourly sync 浏览器锁恢复 + Feishu 失败告警去重（2026-05-09）

## 背景与触发事件

- 2026-05-08 12:37–15:17 期间，`scheduled_task_runs` 里 `task_code='roi_hourly_sync'` 连续 4 次 failed，错误信息全部是：
  ```
  browser automation lock timeout after 901s: /data/autovideosrt/browser/runtime/automation.lock
  ```
- 2026-05-09 00:42:51 有人 `sudo systemctl stop autovideosrt-roi-realtime-sync.timer autovideosrt-meta-daily-final-sync.timer autovideosrt-meta-daily-final-check.timer` 并 `pkill` 掉当时还在 retry 的 `tools/roi_hourly_sync.py` / `scripts/run_meta_ads_backfill_range.py`，之后忘了 `start` 回来。systemd timer 的 `Loaded` 仍是 `enabled`，但 `Active=inactive (dead)`、`Trigger=n/a`，相当于 schedule 死透。
- 直接业务影响：5/9 实时大盘看板里 BJ 业务日 `spend / order_count / ROAS` 全 0；广告分析「购买金额」按订单口径兜底（[2026-05-09 ads-purchase-value-order-fallback-design](2026-05-09-ads-purchase-value-order-fallback-design.md)）也无源可兜，因为 `meta_ad_realtime_daily_campaign_metrics` 当天没有任何 snapshot。
- 事件被 Feishu 告警链路漏报：`appcore/feishu_alerts.send_scheduled_task_failure` 每条 failed 都直接发一次，5/8 当天浏览器导出反复 timeout 时本应有连发 4 条告警，没有人响应；timer 被人手 stop 之后 5/9 全天 0 条 run 也就 0 条告警，事故连续 24+ 小时无人发现。

## 目标

1. 让 `roi_hourly_sync` 在浏览器共享锁被长时间占用时**早 fail**，把决策面交给下一 tick，而不是吞掉一整个小时的 schedule。
2. 让 `cdp_environment_watchdog`（每分钟跑一次）在浏览器共享锁出现「持有 PID 已死 / 持有时长超阈值」时，写一条 failed run 触发 Feishu 告警，避免再次「24 小时没人发现」。
3. 让 Feishu 告警**节流**：同一 task_code 短时间内连续 failed 时只发首条 + 节奏化的提醒，但保证「连续 N 次失败」与「转回 success」两类拐点都有提醒，避免告警刷屏与彻底沉默两种极端。
4. 文档先于代码：本 spec 是上述行为的事实来源，后续修改 `with_browser_lock.sh` / `cdp_environment_watchdog.py` / `feishu_alerts.py` / `scheduled_tasks.py` 必须先回到本 spec 修订。

## 非目标

- 不改 `roi_hourly_sync` 业务口径、SQL、字段、CSV 解析逻辑。
- 不重写 `appcore/browser_automation_lock.py` 的核心 `fcntl.flock` 语义；fcntl 已经保证 holder 进程退出时 kernel 自动释放，无需在用户态重新发明 stale lock detection。
- 不动 `runtime-meta-ads/automation.lock` 内层锁（meta-ads-cdp）的超时；它是子进程持有，受外层 `with_browser_lock.sh` 时长上限约束。
- 不引入新的告警通道（短信 / 邮件 / 电话），延用 Feishu 一条腿。
- 不改 systemd unit 文件本身的拓扑（仍然是「timer → service → with_browser_lock.sh → python tools/roi_hourly_sync.py」），只动 drop-in 环境变量与 wrapper 脚本本身。

## 现状诊断

- 外层「全 server-browser 共享锁」：`/data/autovideosrt/browser/runtime/automation.lock`，由 [`deploy/server_browser/with_browser_lock.sh`](../../../deploy/server_browser/with_browser_lock.sh) 用 `flock -n 9` 抢占，超时由环境变量 `BROWSER_AUTOMATION_LOCK_TIMEOUT_SECONDS` 控制（drop-in 当前值 900s）。被以下 service 共享：
  - `autovideosrt-roi-realtime-sync.service`（每 1 小时跑一次，OnCalendar=*:02:00）
  - `autovideosrt-roas-backfill.service`（长跑）
  - `autovideosrt-sku-aggregates.service`（长跑）
  - 临时跑的 `appcore/supply_pairing.py`、`tools/probe_supply_pairing_v4.py` 也用同把锁的默认路径（[`appcore/browser_automation_lock.default_lock_path`](../../../appcore/browser_automation_lock.py)）。
- 内层「Meta Ads 专用锁」：`/data/autovideosrt/browser/runtime-meta-ads/automation.lock`，由 [`appcore/meta_ads_cdp.meta_ads_cdp_lock`](../../../appcore/meta_ads_cdp.py) 抢占，超时由 `META_ADS_CDP_LOCK_TIMEOUT_SECONDS` / `BROWSER_AUTOMATION_LOCK_TIMEOUT_SECONDS` 控制（默认 600s）。在 [`scripts/run_meta_ads_backfill_range.py`](../../../scripts/run_meta_ads_backfill_range.py) 与 [`appcore/meta_ads_in_page_fetch.open_meta_ads_session`](../../../appcore/meta_ads_in_page_fetch.py) 里用，是子进程层。
- `cdp_environment_watchdog` 当前职责：每分钟检查 DXM01-Meta / DXM02-MK / DXM03-RJC 的 systemd / CDP / noVNC，**不**碰 `runtime/automation.lock` / `runtime-meta-ads/automation.lock`。
- Feishu 告警当前路径：[`appcore.scheduled_tasks.finish_run`](../../../appcore/scheduled_tasks.py) 的 `status=='failed'` 分支调 `_dispatch_failure_alert(run_id)` → `feishu_alerts.send_scheduled_task_failure(row)`。**没有任何节流 / 去重**。

## 行为约束（事实来源）

### 1. `with_browser_lock.sh` fail-fast 默认值

- `BROWSER_AUTOMATION_LOCK_TIMEOUT_SECONDS` 默认值改为 `300`（5 分钟），`BROWSER_AUTOMATION_LOCK_RETRY_SECONDS` 默认值保持 `10`。drop-in `/etc/systemd/system/autovideosrt-roi-realtime-sync.service.d/10-browser-lock.conf` 也同步成 `300`。
- 选 5 分钟的理由：`roi_hourly_sync` 的 timer 周期是 `*:02:00`（每小时一次）。当外层锁被长跑任务持有时，让 ROI tick 5 分钟后早 fail，failed run 立即触发 Feishu，下一小时 tick 自然再来；同时给短跑（如 `meta_daily_final` ~2 分钟）足够的等待窗口避免误 fail。
- timeout 触发时，`with_browser_lock.sh` 仍走 `record_timeout_failure` 写一条 `scheduled_task_runs` failed 行（已实现），并在 `summary_json` 里追加新字段：`lock_holder_pid`、`lock_holder_command`、`lock_age_seconds`，由 wrapper 在 timeout 那刻调 `lsof -t -F pcLst -- "$LOCK_PATH"` 拿到。这些字段帮助人工判断该 kill 谁。

### 2. `cdp_environment_watchdog` 增加锁健康检查

- 新增数据结构 `BrowserLockTarget`（与 `CdpEnvironment` 并列），描述待检查的锁文件，至少包含：
  ```python
  BrowserLockTarget(code="runtime", path="/data/autovideosrt/browser/runtime/automation.lock", max_age_seconds=600)
  BrowserLockTarget(code="runtime-meta-ads", path="/data/autovideosrt/browser/runtime-meta-ads/automation.lock", max_age_seconds=900)
  ```
- 每次 `run_watchdog` 多跑一段 `check_browser_locks(targets)`：
  1. `lsof -t -F p -- <path>` 拿持有 PID 列表（无持有者 → 健康，跳过）。
  2. 对每个 PID 跑 `ps -p <pid> -o pid=,etimes=,cmd=`：
     - 进程不存在 → 异常 kind="lock_orphan_pid"（fcntl 应自动释放，但锁文件 mtime 仍可能滞后；记录但不直接 kill）。
     - 进程存在但 `etimes` > `max_age_seconds` → 异常 kind="lock_held_too_long"。
- 异常落进 watchdog 的 `summary["browser_locks"]`；任一异常都让本次 watchdog run 走 `status="failed"` 路径（与现有 `had_outage` 同款），错误信息形如 `browser lock {code} held too long: pid=X cmd=... age=YYYs`。
- watchdog **不直接 kill** 持有者进程；它只负责报告。kill 决策保留给人工（避免误杀正在跑的 long-running backfill）。
- watchdog 仍然每分钟跑，加上 Feishu 节流（见下），同一异常不会刷屏。

### 3. Feishu 告警节流

- 在 `appcore/feishu_alerts` 新增 `should_dispatch_failure(task_code, *, error_message)`，按 task_code 维度查最近 `scheduled_task_runs` 里同 task_code 的 failed/success 序列，决定本次是否真的发：
  - 当前 run 是该 task_code 自上一次 success 之后的第 1 次 failed → **发**（首条事故告警）。
  - 当前 run 是连续第 N 次 failed，N 为 settings 配置 `feishu_alerts.failure_repeat_every`（默认 5） 的整数倍 → **发**（持续提醒）。
  - 否则不发（吞掉中间的告警）。
- 在 `feishu_alerts` 中再加一个 `send_scheduled_task_recovery(task_code, recovered_run)`：当 `finish_run(status="success")` 检测到上一条同 task_code run 是 failed → 触发恢复告警「{task_name} 已恢复，连续失败 N 次后转 success」。
- `_dispatch_failure_alert` 改为先调 `should_dispatch_failure`，决定真的发再走原有 `send_scheduled_task_failure`。`finish_run` 在 status="success" 分支新增 `_dispatch_recovery_alert(run_id)`。
- 两类拐点都覆盖：「第 1 次失败」「连续 N 次失败」「转回 success」均有 Feishu 推送，中间相同模式的连发告警被抑制。

### 4. settings 字段（DB `system_settings` 表）

- 新增 `feishu_alerts.failure_repeat_every`：默认 `5`，类型 int，含义「连续 failed 序列里每 N 次发一次告警」。
- 不新增 schema migration（`system_settings` 是 KV 表）。`/settings?tab=alerts` 页面后续可加 input；本期不做 UI，admin 直接 SQL `INSERT INTO system_settings VALUES ('feishu_alerts.failure_repeat_every', '5', NOW())` 即可。
- `feishu_alerts.enabled` / `app_id` / `app_secret` / `chat_id` 沿用现有 key，**不改 schema、不改默认行为**。

### 5. 文档锚点联动

- 本 spec：`docs/superpowers/specs/2026-05-09-roi-hourly-sync-lock-recovery.md`（即本文件）。
- `CLAUDE.md` 「Meta 广告多账户同步」段补一段「故障 SOP」子节，指向本 spec：发现 `runtime/automation.lock` 长持有 → 看 watchdog 报告 → 决定 kill 哪个进程 → 必要时 `sudo systemctl start` 三个 timer。
- `appcore/scheduled_tasks.py` 的 `cdp_environment_watchdog` 注册描述里增加「兼盯 `runtime/*automation.lock` 持有时长」一句，便于 `/scheduled-tasks` 页面理解 watchdog 的范围。
- 不写 CHANGELOG.md（仓库无此文件）。

## 验收

- `scheduled_task_runs.task_code='roi_hourly_sync'` 在恢复 timer 后能连续出 success 行；Feishu 在首次 failed / 连续 N 次 failed / 恢复 success 三个拐点都收到一条提醒。
- 人为 `flock /data/autovideosrt/browser/runtime/automation.lock -c 'sleep 1200'` 模拟锁占用：下一次 `cdp_environment_watchdog` tick（≤ 1 分钟）就把 `lock_held_too_long` 写进 `scheduled_task_runs` failed，触发 Feishu。下一次 ROI tick 5 分钟后早 fail，summary 里带上持有 PID。
- 持有者进程退出后下一次 watchdog tick 报告「lock target healthy」，同时 ROI tick 在 5 分钟内成功跑完。
- 单元测试覆盖：
  - `with_browser_lock.sh` 默认值 300。
  - `cdp_environment_watchdog.check_browser_locks` 在 lsof 模拟无持有者 / PID 不存在 / etimes 超阈值三种场景下分别返回正确状态。
  - `feishu_alerts.should_dispatch_failure` 在「首次 failed / 连续第 2/3/4 次 failed / 第 5 次 failed」上的开关行为，以及 `failure_repeat_every` 配置覆盖。
  - `scheduled_tasks.finish_run` 在 status='success' 时调用 recovery dispatch 一次。

## 实施顺序

1. 文档：本 spec + `CLAUDE.md` SOP 段 + `scheduled_tasks.TASK_DEFINITIONS['cdp_environment_watchdog']` 描述。
2. 代码：
   - `deploy/server_browser/with_browser_lock.sh`：默认 300s + lsof 持有者捕获。
   - `etc/systemd/system/autovideosrt-roi-realtime-sync.service.d/10-browser-lock.conf` 同步（仓库内对应文件 `deploy/server_browser/autovideosrt-roi-realtime-sync.service.d/10-browser-lock.conf`，如果不存在就在仓库内 `deploy/` 下补一份并加 README 注明部署点）。
   - `tools/cdp_environment_watchdog.py`：加 `BrowserLockTarget` + `check_browser_locks`。
   - `appcore/feishu_alerts.py`：加 `should_dispatch_failure` + `send_scheduled_task_recovery`。
   - `appcore/scheduled_tasks.py`：`finish_run` success 分支调 recovery；failed 分支先过 `should_dispatch_failure`。
3. 测试：
   - `tests/test_cdp_environment_watchdog.py`：新增锁检查 case。
   - `tests/test_feishu_alerts.py`：新增节流 / recovery case。
   - `tests/test_appcore_scheduled_tasks.py`：新增 success → recovery dispatch 与 failed dedup 路径。
4. 部署：worktree 跑 pytest 通过 → push → `sudo` 同步 prod → restart service & timer → 观察一轮 ROI tick。
