# 店小秘订单新鲜度看护设计

最后更新：2026-05-09

## 背景

- 店小秘订单同步入口是 [tools/dianxiaomi_order_import.py](../../../tools/dianxiaomi_order_import.py)，但不是独立 systemd timer，而是作为
  [tools/roi_hourly_sync.py](../../../tools/roi_hourly_sync.py) 的子任务在每小时 `:02` 触发，命中 `_run_dxm_recent_import`
  后把订单写进 `dianxiaomi_order_lines`。任务定义已登记在
  [appcore/scheduled_tasks.py::dianxiaomi_order_import](../../../appcore/scheduled_tasks.py)，
  `source_type=subtask`，`source_ref=autovideosrt-roi-realtime-sync.timer`。
- 这个绑定意味着：只要父任务 `roi_hourly_sync` 因任何原因不能跑出
  `_run_dxm_recent_import`（CDP 锁超时、CSV 导出 timeout、整轮 abort），订单就**静默不入库**。
  - 既有 [_dispatch_failure_alert](../../../appcore/scheduled_tasks.py) 只在 `roi_hourly_sync_runs.status='failed'`
    或 `dianxiaomi_order_import_batches.status='failed'` 落地后才触发飞书告警，
    无法覆盖「父任务彻底没跑」「锁等了 901s 才标 failed」「子任务被父循环 skip 掉」这几种延迟暴露的场景。
- 已知事故：2026-05-08 12:37 起 `roi_hourly_sync` 因 `automation.lock` 901s timeout 连续失败，2026-05-09 00:42
  之后 `dianxiaomi_order_lines` 14+ 小时没有新增订单（`newjoy` + `omurio` 两站合并），
  实时大盘 5/9 BJ 业务日 `order_count`、`order_revenue` 显示为 0。AUT-21 在跟进父任务的锁 / orchestrator 修复，
  但需要一个独立、轻量、不依赖父任务运行结果的「新鲜度看护」来兜底。

## 目标

新增一个独立的 systemd timer 任务 `dianxiaomi_order_freshness_watchdog`：

- 每分钟检查 `dianxiaomi_order_lines` 表的最新水位（`MAX(updated_at)` 与 `MAX(paid_at)`）。
- 当 `now() - max(updated_at) > 2 小时` 时触发飞书告警，并把 `scheduled_task_runs` 那条记录标 `failed`，
  让现有 `_dispatch_failure_alert` 自动调起 `feishu_alerts.send_scheduled_task_failure` 推送。
- 既要在「订单同步停摆」时 2 小时内告警一次，又不能在每分钟反复告警造成噪声——
  采用 `cooldown` 思路：一次告警之后默认 60 分钟内不再重复同样的失败告警。
- 任务定义同步登记到 `appcore/scheduled_tasks.py`，并在 Web 后台「定时任务」模块出现，
  和 `cdp_environment_watchdog` 同级，符合 [`AGENTS.md` 「定时任务归集规则」](../../../AGENTS.md)。

## 不在范围

- 不改 `roi_hourly_sync` 的锁、不改 `dianxiaomi_order_import` 的实际同步流程（那是 AUT-21 的范围）。
- 不替换 `_dispatch_failure_alert` 已有的失败告警链路；只是在另一条独立路径上补充「新鲜度」维度。
- 不改 `dianxiaomi_order_lines` schema、订单分摊计算、数据看板查询。

## 设计

### 阈值与行为

| 阈值 | 默认值 | 含义 |
|------|--------|------|
| `--max-stale-minutes` | `120` | `now - max(updated_at) > N 分钟` 即视为停摆 |
| `--cooldown-minutes`  | `60`  | 上一次同任务标 `failed` 后，N 分钟内即使依旧停摆也只标 `success` 不再告警 |
| `--ignore-empty-table` | True | `dianxiaomi_order_lines` 一行都没有时不视为告警，避免新环境 / 测试库刷红 |

`max(updated_at)` 取的是订单写入或 upsert 的服务器时间戳；新订单刚导入或现有订单在
`recent-scan` 中被刷新，都会推进这个水位。`max(paid_at)` 仅作为摘要展示，不参与告警判断（`paid_at`
本身受店小秘端时区和支付节奏影响，会在自然时段降到 0）。

### 任务出口

- `scheduled_task_runs` 写入一行 `task_code='dianxiaomi_order_freshness_watchdog'`：
  - `status='success'` + `summary_json` 携带最新水位，表示新鲜（或冷却中）；
  - `status='failed'` + `error_message` 描述「最新订单滞后 X 分钟，超过阈值 Y 分钟」，触发飞书告警。
- 飞书告警走现有 `feishu_alerts.send_scheduled_task_failure(row)`，不新增告警渠道。
- `summary_json` 字段：

  ```json
  {
    "max_updated_at": "2026-05-09 00:42:38",
    "max_paid_at": "2026-05-09 00:26:19",
    "stale_minutes": 871,
    "threshold_minutes": 120,
    "cooldown_minutes": 60,
    "alert_action": "alerted",          // alerted / cooldown_skip / fresh / empty_table
    "row_count_total": 12345
  }
  ```

### 调度

- systemd timer：`autovideosrt-dianxiaomi-order-freshness-watchdog.timer`，`OnUnitActiveSec=60`，
  与 `cdp_environment_watchdog.timer` 节奏一致。
- service：`autovideosrt-dianxiaomi-order-freshness-watchdog.service`，oneshot，
  `ExecStart=python tools/dianxiaomi_order_freshness_watchdog.py`。
- 任务在 `appcore/scheduled_tasks.py` 登记 `source_type='systemd'`、`log_table='scheduled_task_runs'`，
  支持后台「停用」。停用时跑 service 也会被 `is_task_enabled` 拦住，符合既有 systemd guard 行为。

### 与父任务的关系

- watchdog 不读 `roi_hourly_sync_runs`，只读 `dianxiaomi_order_lines` 水位。这是有意为之：
  即使 `roi_hourly_sync` 因为 webserver 重启 / cron 失活根本没跑出过 run 记录，watchdog 仍能感知。
- 父任务恢复后只要新订单进库，下一分钟 watchdog 自动回到 `success` 状态，cooldown 到期后下次故障可再次告警。

## 验收

- `tools/dianxiaomi_order_freshness_watchdog.py` 在水位 < 阈值时返回 `0` 且 `scheduled_task_runs.status='success'`，
  飞书无新告警。
- 水位 > 阈值且不在 cooldown 时返回 `2`、`scheduled_task_runs.status='failed'`、
  `summary_json.alert_action='alerted'`、`error_message` 中包含 `stale_minutes` 与 `threshold_minutes`。
- 水位仍 > 阈值但 cooldown 未结束时返回 `0`、`status='success'`、`summary_json.alert_action='cooldown_skip'`。
- 表为空（`row_count_total=0`）时返回 `0`、`status='success'`、`alert_action='empty_table'`，避免误报。
- 任务在 `appcore/scheduled_tasks.py` 登记，能在 `/scheduled-tasks` 页面看到「店小秘订单新鲜度看护」。
- 单测覆盖：`stale_minutes >= threshold && cooldown 不命中 → failed`、`cooldown 命中 → success`、
  `empty_table → success`、`fresh → success`。

## 文档锚点

- 认知文档：`AGENTS.md` 与 `CLAUDE.md` 新增「店小秘订单新鲜度看护（2026-05-09 起）」段落，引用本文档。
- 规范文档：本文件。
- 代码：
  - [tools/dianxiaomi_order_freshness_watchdog.py](../../../tools/dianxiaomi_order_freshness_watchdog.py)
  - [appcore/scheduled_tasks.py](../../../appcore/scheduled_tasks.py) 中新增 `dianxiaomi_order_freshness_watchdog` 任务定义
- 部署：
  - `deploy/server_browser/autovideosrt-dianxiaomi-order-freshness-watchdog.timer`
  - `deploy/server_browser/autovideosrt-dianxiaomi-order-freshness-watchdog.service`
- 测试：`tests/test_dianxiaomi_order_freshness_watchdog.py`
