# 广告与订单同步调度优化设计（2026-06-04）

## 文档锚点

- 全局调度规则：[AGENTS.md](../../../AGENTS.md) 的“定时任务一律登记”与“文档驱动代码”。
- Meta 多账户日终同步：[2026-05-07-meta-ads-multi-account-design.md](2026-05-07-meta-ads-multi-account-design.md)。
- Meta 收盘日 guard 与实时兜底：[2026-05-09-realtime-dashboard-ad-spend-source-of-truth.md](2026-05-09-realtime-dashboard-ad-spend-source-of-truth.md)。
- Meta Ad Set 日终常态同步：[2026-05-28-meta-daily-final-adset-steady-sync-design.md](2026-05-28-meta-daily-final-adset-steady-sync-design.md)。
- 店小秘订单新鲜度看护：[2026-05-09-dianxiaomi-order-freshness-watchdog.md](2026-05-09-dianxiaomi-order-freshness-watchdog.md)。
- 数据质量护栏：[docs/analytics-data-quality-guardrails.md](../../analytics-data-quality-guardrails.md)。

## 背景

当前线上有三类相关同步：

1. `autovideosrt-roi-realtime-sync.timer` 每 20 分钟运行一次，执行 `tools/roi_hourly_sync.py`，同时做店小秘近期订单导入、Meta 日内广告快照、实时 ROAS 快照。
2. `autovideosrt-meta-daily-final-sync.timer` 每天 16:30 运行 `tools/meta_daily_final_sync.py --mode run --include-adsets`，抓刚收盘 Meta 业务日的 Campaign / Ad Set / Ad 日终数据。
3. `autovideosrt-meta-daily-final-check.timer` 每天 17:00 运行 `--mode check --include-adsets`，只在前一轮未成功时补跑。

用户确认的新要求：

- 20 分钟一次的日常 ROI / 订单 / 日内广告同步保持不变。
- 原 16:30 日终同步提前到 16:10。
- 每天 19:00 对 16:10 同一个刚收盘 Meta 业务日做第二轮日终同步确认。
- 每天 12:00 按 Meta 业务日口径补跑上一完整业务日。
- 每周开始时补跑上一周 7 个 Meta 业务日。
- 订单数据处理逻辑与广告数据一致，补跑时同步覆盖店小秘订单导入、Meta 日终广告同步、订单利润重算。

## 口径定义

Meta 业务日沿用既有口径：北京时间 16:00 切日。业务日 `D` 的窗口是北京时间 `D 16:00` 到 `D+1 16:00`。

“上一完整 Meta 业务日”必须通过 `tools.meta_daily_final_sync.completed_meta_business_date(now)` 推导，不能用北京时间自然日昨天代替。

示例：

- 北京时间 2026-06-04 12:00，当前 Meta 业务日尚未在 16:00 收盘，上一完整 Meta 业务日是 `2026-06-02`。
- 北京时间 2026-06-04 16:10，刚收盘的上一完整 Meta 业务日是 `2026-06-03`。

一个 Meta 业务日跨两个北京时间自然日。补拉订单时必须导入业务窗口覆盖到的自然日集合，再由现有报表/利润逻辑按 `meta_business_date` 或业务窗口过滤，不能只导入一个自然日。

## 目标

1. 保留现有 20 分钟实时同步频率和职责。
2. 将 Meta 日终同步从 16:30 提前到 16:10。
3. 将 17:00 check 调整为 19:00 二次同步确认；19:00 必须重新拉取并替换同一目标日数据，不能只依赖 `mode=check` 的“已成功则跳过”逻辑。
4. 新增日常补拉：每天 12:00 对上一完整 Meta 业务日执行订单 + 广告 + 利润重算。
5. 新增周补拉：每周一 20:30 对上一 ISO 周的 7 个 Meta 业务日执行订单 + 广告 + 利润重算。选择 20:30 是为了等上周日 Meta 业务日完全收盘，并避开当天 19:00 二次确认的主要执行窗口。
6. 所有新增 systemd timer / service 必须登记到 `appcore/scheduled_tasks.py`，在 Web 后台“定时任务”模块可见。
7. 任务日志必须能区分实时 20 分钟同步、16:10 日终、19:00 确认、12:00 日补拉、周补拉。

## 非目标

- 不改变 Meta 业务日 16:00 切日规则。
- 不改变现有 20 分钟 ROI timer 的周期。
- 不改 Meta 广告、店小秘订单、订单利润表 schema。
- 不引入新的广告账户配置方式。
- 不在本次改动中做历史大范围回填；本设计只定义后续自动补拉节奏。

## 调度设计

### 20 分钟实时同步

保持现状：

- `autovideosrt-roi-realtime-sync.timer`
- `OnCalendar=*-*-* *:00,20,40:00`
- `tools/roi_hourly_sync.py --lookback-hours 3 --max-scan-pages 40 --meta-channel browser`

`appcore/scheduled_tasks.py` 中 `roi_hourly_sync`、`dianxiaomi_order_import`、`meta_realtime_import` 的描述要同步确认是“每 20 分钟随 ROI 触发”，清理旧的“每 1 小时 / :02”文案。

### 16:10 日终同步

调整现有 `autovideosrt-meta-daily-final-sync.timer`：

- `OnCalendar=*-*-* 16:10:00`
- 目标日期不显式传 `--date`，继续由 `completed_meta_business_date()` 推导。
- 命令保持 `tools/meta_daily_final_sync.py --mode run --include-adsets`。

`run_final_sync` 已在成功后重算目标业务日 `order_profit_lines`，这一路径继续复用。

### 19:00 二次同步确认

调整现有 `autovideosrt-meta-daily-final-check.timer`：

- `OnCalendar=*-*-* 19:00:00`
- service 描述从“success check and retry”改为“confirmation rerun”。
- 命令改为 `tools/meta_daily_final_sync.py --mode run --include-adsets`，使其无论 16:10 是否成功都重新拉取同一目标日。

不能继续只跑现有 `--mode check`，因为 `check` 在 `already_successful(target_date)` 时会跳过，无法满足“19:00 对 16:10 数据做一轮二次同步确认”的要求。

### 12:00 日补拉

新增编排脚本 `tools/ad_order_sync_orchestrator.py`，支持：

```bash
python tools/ad_order_sync_orchestrator.py --mode previous-business-day
```

每天 12:00 的目标业务日为 `completed_meta_business_date(now)`。在 12:00 这个时点，它通常是北京时间自然日的前两天业务日，这是预期行为。

每个目标业务日按顺序执行：

1. 店小秘订单导入：导入该 Meta 业务窗口覆盖到的北京自然日集合。例如业务日 `D` 覆盖自然日 `D` 和 `D+1`。
2. Meta 日终广告同步：调用 `meta_daily_final_sync.run_final_sync(target_date, mode="run", include_adsets=True)`。
3. 利润重算：若 Meta 日终同步成功，复用 `run_final_sync` 内置的 `_recompute_order_profit_after_final_sync`，不重复调用；若订单导入成功但 Meta 同步失败，整体任务标 failed，不把该业务日声明为完整。

对应 systemd：

- `autovideosrt-ad-order-previous-business-day-sync.service`
- `autovideosrt-ad-order-previous-business-day-sync.timer`
- `OnCalendar=*-*-* 12:00:00`

### 每周 7 日补拉

新增同一个编排脚本模式：

```bash
python tools/ad_order_sync_orchestrator.py --mode previous-week
```

每周一 20:30 运行，目标为上一 ISO 周周一到周日的 7 个 Meta 业务日。例如 2026-06-08 周一 20:30 运行时，目标日期为 `2026-06-01` 到 `2026-06-07`。

周补拉按日期从旧到新串行执行。某一天失败后继续尝试后续日期，但最终任务状态为 failed，并在 summary 中列出每个业务日的 `order_status`、`ad_status`、`profit_status` 和错误信息。

对应 systemd：

- `autovideosrt-ad-order-previous-week-sync.service`
- `autovideosrt-ad-order-previous-week-sync.timer`
- `OnCalendar=Mon *-*-* 20:30:00`

## 店小秘订单导入细节

补拉订单必须复用 `tools.dianxiaomi_order_import.run_import_from_server_browser`，默认：

- `site_codes=["newjoy", "omurio"]`
- `dxm_env="DXM03-RJC"`
- `skip_login_prompt=True`
- `date_filter_mode="recent-scan"`

日补拉的 `max_scan_pages` 默认 220。周补拉如果导入上一周完整窗口，必须允许配置更大的 `--max-scan-pages`，默认 500。实现时若扫描到最大页仍未看到早于导入起始日期的订单，任务必须标 failed，不得静默 success。

为避免 12:00 补拉与 `roi_hourly_sync` 同时操作 DXM03 店小秘浏览器，订单导入需要新增一把轻量进程锁，建议路径：

```text
/data/autovideosrt/browser/runtime-dxm-order-import/automation.lock
```

这把锁只串行店小秘订单导入，不改变 20 分钟 ROI timer 的触发频率。若补拉任务持锁，ROI 的订单导入子任务按下面的固定超时策略处理；Meta 日内同步与快照仍按现有逻辑执行并记录状态。

锁超时策略固定为：

- ROI 20 分钟子任务等待最多 60 秒，超时后 `dxm_report.status="skipped_lock_timeout"`，继续执行 Meta 日内同步和快照落库。
- 12:00 / weekly 补拉任务等待最多 600 秒，超时后该业务日 `order_import.status="failed"`，继续尝试 Meta 日终同步，但整体任务最终 failed。
- summary 必须记录 `lock_path`、`timeout_seconds`、`holder_pid`（能拿到时）和 `holder_command`（能拿到时）。

## 任务登记

`appcore/scheduled_tasks.py` 需要同步维护：

- `roi_hourly_sync`：描述保持 20 分钟实时同步。
- `dianxiaomi_order_import`：改为“每 20 分钟随 ROI 触发；补拉编排也会调用同一导入入口”。
- `meta_realtime_import`：改为“每 20 分钟随 ROI 触发”。
- `meta_daily_final`：描述更新为“每天 16:10 日终同步；19:00 二次同步确认”。
- 新增 `ad_order_previous_business_day_sync`：每天 12:00 补拉上一完整 Meta 业务日。
- 新增 `ad_order_previous_week_sync`：每周一 20:30 补拉上一 ISO 周 7 个 Meta 业务日。

新增编排任务可以继续写 `scheduled_task_runs`，`summary_json` 至少包含：

```json
{
  "mode": "previous-business-day",
  "target_dates": ["2026-06-02"],
  "timezone": "Asia/Shanghai",
  "meta_cutover_hour_bj": 16,
  "days": [
    {
      "target_date": "2026-06-02",
      "order_import": {"status": "success", "batch_id": 123},
      "meta_daily_final": {"status": "success", "run_id": 456},
      "profit_backfill": {"status": "success", "profit_run_id": 789}
    }
  ]
}
```

## 错误处理

- 单日补拉：任一阶段失败则本次 `scheduled_task_runs.status='failed'`，错误信息指向具体阶段。
- 周补拉：逐日隔离，一天失败不阻断后续日期；最终只要有任一日期失败，整体 failed。
- Meta 日终同步沿用现有账户级失败隔离；成功账户可落库，但整体状态按 `run_final_sync` 的返回结果决定。
- 店小秘订单导入失败时，不继续把该业务日标为完整；编排仍继续尝试 Meta 同步以尽量补齐广告数据，但 summary 必须标出订单失败，整体任务最终 failed。
- 所有失败继续走 `scheduled_tasks.finish_run` 的飞书告警/恢复链路。

## 测试计划

新增/更新测试：

1. `tests/test_server_browser_runtime.py`
   - 断言 Meta 日终 sync timer 是 `16:10`。
   - 断言 Meta 二次确认 timer 是 `19:00`。
   - 断言 19:00 service 不再只调用 `--mode check`。
   - 断言新增 12:00 / 周一 20:30 systemd timer。
2. `tests/test_appcore_scheduled_tasks.py`
   - 断言任务定义时间、runner、description 与新增任务登记。
   - 断言子任务描述不再写旧的每小时 `:02`。
3. 新增 `tests/test_ad_order_sync_orchestrator.py`
   - 12:00 `previous-business-day` 使用 `completed_meta_business_date(now)`，不是自然日昨天。
   - 周一 20:30 `previous-week` 生成上一 ISO 周 7 个目标业务日。
   - 单个业务日订单导入覆盖两个自然日。
   - 编排顺序为订单导入 → Meta 日终 → 利润结果读取。
   - 周补拉单日失败后继续后续日期，最终 status 为 failed。
4. 店小秘导入锁测试
   - ROI 子任务和补拉编排使用同一锁路径。
   - 拿不到锁时 summary 记录 lock path 与等待秒数。

验证命令：

```bash
pytest tests/test_server_browser_runtime.py tests/test_appcore_scheduled_tasks.py tests/test_ad_order_sync_orchestrator.py -q
pytest tests/test_roi_hourly_sync_controls.py tests/test_dianxiaomi_order_import.py tests/test_meta_server_sync_tools.py -q
```

本次验证不得连接 Windows 本机 MySQL `127.0.0.1:3306`。

## 实施顺序

1. 文档：本 spec。
2. 编排脚本：新增 `tools/ad_order_sync_orchestrator.py`，只组合现有同步入口，不复制 Meta/店小秘解析逻辑。
3. 锁：新增店小秘订单导入锁 helper，并让 ROI 订单导入子任务与补拉编排共用。
4. systemd：调整 Meta 16:10/19:00 timer，新增 12:00/weekly service + timer。
5. 任务登记：更新 `appcore/scheduled_tasks.py`。
6. 测试：补齐上述单元测试与 systemd 静态测试。
7. 使用文档：如需要，在部署说明或运维手册补充 timer reload/start 命令。

## 验收

- `systemctl list-timers` 能看到：
  - ROI 实时同步仍为每 20 分钟。
  - Meta 日终同步为每天 16:10。
  - Meta 二次确认同步为每天 19:00。
  - 广告订单上一业务日补拉为每天 12:00。
  - 广告订单上一周补拉为周一 20:30。
- Web 后台“定时任务”能看到新增/调整后的任务描述。
- 16:10 和 19:00 的 `meta_daily_final` 日志目标日期相同，19:00 不因 16:10 success 而跳过。
- 12:00 日补拉 summary 中的 `target_dates` 符合 Meta 业务日口径。
- 周补拉 summary 中有上一 ISO 周 7 个业务日。
- 对任一成功的目标业务日，Meta 日终表、店小秘订单水位、订单利润重算水位在数据质量栏可对账。

## 回滚

- 保留 20 分钟 ROI timer 不变，无需回滚。
- 若新补拉编排异常，停止新增的 12:00/weekly timer 即可：

```bash
systemctl disable --now autovideosrt-ad-order-previous-business-day-sync.timer
systemctl disable --now autovideosrt-ad-order-previous-week-sync.timer
```

- 若 16:10/19:00 调整异常，可把两个 Meta daily-final timer 恢复为 16:30/17:00；代码层 `run_final_sync` 与数据表无需回滚。
