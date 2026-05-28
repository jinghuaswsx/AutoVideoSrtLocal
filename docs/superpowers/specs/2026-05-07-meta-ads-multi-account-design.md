# Meta 广告实时同步 多账户改造（2026-05-07）

## 背景

`tools/roi_hourly_sync.py` 调度（systemd timer `autovideosrt-roi-realtime-sync.timer`，每 1 小时一次；2026-05-09 前为每 20 分钟）原先**只同步一个 Meta 广告账户** `2110407576446225`（newjoyloo），账户 ID / business ID / CSV 文件名前缀 `newjoyloo` 全部硬编码或仅由环境变量控制。

事件：
- 2026-05-07 newjoyloo 旧账户 `2110407576446225` 被 Meta 封禁，最近的 CSV `已花费金额` 全为 0。
- 2026-05-07 用户提供 newjoyloo 新广告户 Ads Manager URL，解析得到 `business_id=476723373113063`、`act=1861285821213497`；店小秘订单获取环境仍使用 `DXM-01`。
- 实际公司同时运营 newjoyloo + Omurio 两个账户；订单侧 `STORE_SCOPE = "newjoy,omurio"` 已经在并行同步两个店铺，但 Meta 广告侧从未对接 Omurio。
- newjoyloo 旧户解封时间未定，同步必须切到新户；旧户数据仍保留给历史报表分摊。

## 目标

1. Meta 广告同步原生支持多账户：每次定时跑都遍历**所有 enabled 账户**。
2. 单账户失败不影响其他账户继续跑（CDP 浏览器登录态在某账户失效，不会拖垮整个 sync）。
3. CSV 文件名前缀按账户走，导出目录按账户分子目录，避免相互覆盖。
4. 配置存在数据库 `system_settings.meta_ad_accounts`（JSON），并在数据分析模块新增「广告账户」Tab 管理。
5. `newjoyloo` code 指向新广告户 `1861285821213497` 且 `enabled=true`；旧广告户以 `newjoyloo_old` 保留但 `enabled=false`，只参与历史广告费分摊。
6. `meta_daily_final_sync` 收盘日同步与实时同步共用同一份账户配置，避免只同步一个店铺 / 一个广告户。
7. 账户必须声明对应店铺 `store_codes`，让同步、看板、产品盈亏广告费分摊共用同一份「店铺 ↔ 广告户」映射。
8. 旧广告户必须保留历史同步入口：自动定时任务不跑 `enabled=false` 账户，但运维可用 account code 显式指定旧户补抓历史日数据。
9. 数据分析「广告账户」Tab 每个账户提供手动同步入口，可选择日期范围和每一天之间的同步间隔，并在弹窗内展示整体进度。
10. 2026-05-08 临时历史回填要求：使用 `DXM01-Meta` / CDP `9222` 对 `newjoyloo_old` 回填 `2026-01-01` 到 `2026-05-08` 的 `campaign`、`ad_set`、`ad` 三层级数据；每轮最多 5 个成功日期，每 10 分钟触发一轮，完成后自动停止临时 timer。

## 非目标

- 不新增独立配置表；本期继续用 `system_settings.meta_ad_accounts` JSON，避免引入迁移和双写。
- 不改 `_fetch_meta_marketing_api_insights` 的 Marketing API 路径之外的行为；只是让它也走多账户循环（生产用的是 browser 通道，API 通道少用）。
- 不改 `meta_ad_realtime_*` 数据库表 schema —— 字段已经能存多账户。
- 不改 systemd timer / service。

## 数据模型

### `system_settings.meta_ad_accounts`（JSON）

```json
[
  {
    "code": "newjoyloo",
    "label": "Newjoyloo",
    "account_id": "1861285821213497",
    "business_id": "476723373113063",
    "csv_prefix": "newjoyloo",
    "store_codes": ["newjoy"],
    "enabled": true,
    "note": "2026-05-07 旧户被封后启用的新广告户"
  },
  {
    "code": "newjoyloo_old",
    "label": "Newjoyloo 旧广告户",
    "account_id": "2110407576446225",
    "business_id": "476723373113063",
    "csv_prefix": "newjoyloo_old",
    "store_codes": ["newjoy"],
    "enabled": false,
    "note": "2026-05-07 被 Meta 封禁，保留历史广告费分摊"
  },
  {
    "code": "Omurio",
    "label": "Omurio",
    "account_id": "1253003326160754",
    "business_id": "909367947900474",
    "csv_prefix": "Omurio",
    "store_codes": ["omurio"],
    "enabled": true
  }
]
```

字段说明：
- `code`：账户唯一 code，用作 export 子目录名 / 日志标签；不可重复。当前 newjoyloo 新户固定使用 `newjoyloo`，旧户使用 `newjoyloo_old`。
- `account_id` / `business_id`：Meta Ads Manager URL 里的 `act=` / `business_id=`。
- `csv_prefix`：CSV 文件名前缀。**保持原始大小写**（沿用线上 `newjoyloo`、Omurio 后台显示 `Omurio`）。
- `store_codes`：该广告账户覆盖的店铺编码数组，例如 `newjoy`、`omurio`。一个账户可对应多个店铺；同一个店铺绑定多个账户时，利润分摊按该店铺所有账户 spend 合计。
- `enabled`：是否参与每轮同步。被封 / 未授权账户置 false；暂停同步不代表历史广告数据失效，产品盈亏分摊仍使用该账户的 `store_codes` 映射。
- `note`：可选备注。
- `label`：UI 展示名（暂未用，预留）。

### 数据分析「广告账户」Tab

在 `/order-analytics` 增加一级 Tab「广告账户」，作为 `system_settings.meta_ad_accounts` 的管理入口：

- 展示字段：启用状态、店铺、账户名称、账户 code、account_id、business_id、CSV 前缀、备注。
- 支持新增、编辑、启停、删除；保存时整份 JSON 覆盖写入 `system_settings.meta_ad_accounts`。
- 后端必须校验：`code` 唯一；`account_id` 去掉 `act_` 前缀；`business_id` / `csv_prefix` 不为空；`store_codes` 至少一个且去重小写。
- API 返回时同时给出 `available_store_codes=["newjoy","omurio"]`，前端用 checkbox 管理，避免自由输入写错。
- UI 不回显任何密钥；本配置只保存账户、业务 ID、CSV 前缀和店铺映射。

### 现有表的多账户兼容性（无需改 schema）

- `meta_ad_realtime_import_runs.ad_account_ids` `VARCHAR(512)`：本来就支持逗号分隔多 ID。改造后写入所有 enabled 账户 ID。
- `meta_ad_realtime_daily_campaign_metrics`：唯一键 `uk_meta_rt_campaign_snapshot(business_date, snapshot_at, ad_account_id, campaign_id)` 已经按账户分行，多账户的 campaign 行天然分离。

## 调用流程

```
_sync_meta_realtime_daily(business_date, snapshot_at, meta_channel)
├─ accounts = settings.get_enabled_meta_ad_accounts()
├─ if not accounts: 整个 run 标 skipped
├─ run_id = _start_meta_run(..., ad_account_ids=",".join(a.account_id))
├─ for account in accounts:                       # 失败隔离
│    try:
│      browser channel:
│        export = _run_meta_ads_manager_export(business_date, snapshot_at, account)
│           → export_dir = .../<business_date>/<snapshot_ts>/<account.code>/
│           → CSV 文件名 <account.csv_prefix>_campaigns_<date>.csv 等
│           → 调 scripts/run_meta_ads_backfill_range.py 时传 --csv-prefix
│        _import_meta_realtime_campaign_rows(...)  # 带 account 上下文
│      api channel:
│        _fetch_meta_marketing_api_insights(business_date, snapshot_at, account)
│        _import_meta_realtime_api_rows(...)
│      account_summary.status = "success"
│    except Exception as exc:
│      account_summary.status = "failed"
│      account_summary.error  = str(exc)
│    summary["account_results"].append(account_summary)
│    summary["rows_imported"] += account_summary["rows_imported"]
│    summary["spend_usd"]     += account_summary["spend_usd"]
├─ status = "success" if 任一 account success else "failed"
└─ _finish_meta_run(run_id, status, summary, error)
```

## `meta_daily_final_sync` 收盘日同步

收盘日同步必须与实时同步一致，遍历所有 `enabled=true` 的账户。旧逻辑中 `META_AD_EXPORT_ACCOUNT_ID`、`META_AD_EXPORT_BUSINESS_ID`、`newjoyloo_*.csv` 只能作为未配置 setting 时的兼容 fallback，不能再作为主流程。

```
run_final_sync(target_date, mode)
├─ accounts = meta_ad_accounts.get_enabled_accounts()
├─ check 模式：仅当 target_date 最近成功 run 的 summary.account_results 覆盖全部 enabled accounts 时跳过
├─ run_id = scheduled_tasks.start_run("meta_daily_final")
├─ for account in accounts:
│    export_dir = .../<target_date>/<run_ts>/<account.code>/
│    _run_meta_ads_export(..., account)
│    _replace_campaign_daily_rows(..., account)
│    _replace_ad_daily_rows(..., account)
│    account_summary.status = success / failed
├─ _refresh_final_roas_snapshot(target_date, run_id)
└─ status:
     - all success → success
     - partial success → failed（17:00 check 可补跑失败账户；成功账户数据已落库）
     - all failed → failed
     - no enabled accounts → failed
```

删除和重写日表时必须按 `target_date + account.account_id` 限定，不得删除其他账户数据。刷新 `roi_realtime_daily_snapshots` 时广告费按 `meta_business_date` 汇总所有账户，不再按单一 `ad_account_id` 过滤。

### 旧户历史同步

旧 `newjoyloo_old` 账户保持 `enabled=false`，避免每小时实时同步和每日收盘同步反复请求已封账户。但该账户仍完整保留 `account_id`、`business_id`、`csv_prefix`、`store_codes`，可用于人工补抓历史数据：

```bash
python tools/meta_daily_final_sync.py --date 2026-05-06 --mode run --account-code newjoyloo_old
```

`--account-code` 可重复传入，也可传逗号分隔值。只要显式指定 account code，就从 `get_all_accounts()` 里匹配账户，包括 `enabled=false` 的历史账户；未显式指定时仍只跑 `enabled=true` 账户。这样旧户历史数据可继续同步，但不会影响新户与 Omurio 的自动同步稳定性。

### 数据分析「广告账户」Tab 手动同步

每个账户行在「操作」列提供「同步」按钮。点击后打开 modal 弹窗，弹窗包含两个 Tab：

- `同步设置`：展示当前账户 code / account_id，选择开始日期、结束日期、同步间隔秒数。默认开始日期和结束日期为昨天，默认间隔为 20 秒。
- `同步进度`：点击「开始同步」后自动切换到该 Tab，弹窗不关闭；前端轮询后台 job 状态，展示整体进度、当前同步日期、成功 / 失败天数和逐日结果。

手动同步必须复用 `tools/meta_daily_final_sync.run_final_sync(target_date, mode="run", account_codes=[account.code])`。即使用户选择 30 天，也必须拆成 30 次调用，每次只同步一天；第 N 天结束后等待配置的间隔秒数，再进入第 N+1 天。间隔只发生在两天之间，最后一天完成后不再等待。

手动同步选择账户时从 `meta_ad_accounts.get_all_accounts()` 读取，允许选择 `enabled=false` 的 `newjoyloo_old` 旧户补抓历史数据。未点击行内同步按钮的自动定时任务行为不变，仍只跑 `enabled=true` 账户。

后台同一时间只允许一个 Meta 广告账户手动同步 job 运行，避免多个 Web 请求同时驱动同一个 Meta Ads Manager 浏览器。若已有 job 处于 `queued` / `running`，新的启动请求返回 409。

手动同步 job 的进度状态保存在 Web 进程内存，单日同步结果仍由 `meta_daily_final_sync` 写入 `scheduled_task_runs(task_code="meta_daily_final")`，可在「定时任务的运行日志」追踪每一天的实际执行摘要。Web 进程重启会丢失弹窗进度，但不会影响已完成单日 run 的日志。

### Meta Ads Manager CDP 锁

所有连接 `DXM01-Meta` / `META_AD_EXPORT_CDP_URL` 的 Meta Ads Manager 自动化入口必须共用一把 OS 级文件锁，默认路径为 `/data/autovideosrt/browser/runtime-meta-ads/automation.lock`。覆盖环境变量为 `META_ADS_CDP_LOCK_PATH`，等待超时配置为 `META_ADS_CDP_LOCK_TIMEOUT_SECONDS`，默认 600 秒；重试间隔为 `META_ADS_CDP_LOCK_RETRY_SECONDS`，默认 5 秒。

锁必须使用 `fcntl.flock`（Linux）或等价的 Windows 文件锁，锁状态绑定打开的文件描述符。锁文件留在磁盘上不代表仍被占用；如果 Web worker、systemd service 或手工脚本进程崩溃 / 被 kill / 服务重启，内核会关闭文件描述符并自动释放锁。实现可以在锁文件中写入 `pid`、`task_code`、`started_at` 等 holder 元信息用于排查，但不得依赖删除锁文件来释放锁。

锁等待不得无限挂起。拿不到锁时必须在超时后失败，并把 lock path、等待秒数、task code 和命令摘要写入错误信息或 summary。这样网页端手动同步拿锁后即使 Web 服务重启，也不会形成永久锁；真正仍活着但卡住的进程会被超时机制暴露出来。

直接运行 `scripts/run_meta_ads_backfill_range.py` 也必须自动获取这把锁。上层调用方（ROI 实时同步、收盘日同步、手动同步、临时旧户回填）不应各自实现不同锁；如果临时 runner 已经在外层持有同一把锁，传给子进程的导出脚本必须显式跳过二次加锁，避免同进程任务在父子进程之间自锁。

Meta Ads Manager 默认 CDP URL 固定为 `http://127.0.0.1:9222`。当 Web 进程没有显式 `META_AD_EXPORT_CDP_URL` 环境变量时，也必须使用 `DXM01-Meta` 的 9222，不能回落到历史旧端口 `9845`。

### 临时旧户三层级历史回填（2026-05-08）

临时任务用于 `newjoyloo_old` 旧广告户，日期范围固定为 `2026-01-01` 到 `2026-05-08`。它不进入 Web 后台「定时任务管理」，但必须保留文件状态和 systemd journal，方便确认抓到哪一天。

- 运行环境：`DXM01-Meta`，CDP URL 固定为 `http://127.0.0.1:9222`。
- 账户：`account_code=newjoyloo_old`，即使该账户 `enabled=false` 也可显式选择。
- 层级：`campaigns`、`adsets`、`ads`。2026-05-28 起常规收盘同步也显式启用 `include_adsets=true`，写入三层级；临时旧户 runner 继续显式启用同一开关。
- 入库：`campaigns` 写 `meta_ad_daily_campaign_metrics`，`ads` 写 `meta_ad_daily_ad_metrics`，`adsets` 写 `meta_ad_daily_adset_metrics`。
- 调度：临时 systemd timer 每 10 分钟触发一次，每次最多推进 5 个成功日期；如果浏览器自动化锁超时或 ROI / Meta 收盘同步正在运行，本轮跳过并等待下一轮。
- 进度：`output/meta_legacy_newjoyloo_old_backfill/state.json` 保存 `next_date`、成功 / 失败日期、最近一次错误、最近运行批次。失败日期不推进游标，下轮从失败日期重试，避免漏抓。
- 完成：当 `next_date > 2026-05-08` 时 runner 将状态标为 `complete`，并自动 disable 指定的临时 timer。

## 实时表 fallback 读取（多账户聚合）

当 `meta_ad_daily_campaign_metrics` 还没有当日终态数据时，`appcore.order_analytics` 的看板路径会回退到 `meta_ad_realtime_daily_campaign_metrics` 的最近一次 `realtime_partial` snapshot。多账户场景下每个账户的 snapshot tick 时间不一定对齐（任一账户某轮 tick 失败 / 浏览器导出 timeout 都会让该账户最新 snapshot 落后于其他账户）。

硬规则：fallback 必须按 **`(business_date, ad_account_id)`** 取最新 snapshot，再合并各账户结果。**不允许**只 `GROUP BY business_date` 取一个全局 `MAX(snapshot_at)`，因为那会让最新 snapshot 落后的账户被整账户丢弃，看板显示偏小或归零。

```sql
-- 正确：每账户取自己的最新 snapshot
SELECT business_date, ad_account_id, MAX(snapshot_at) AS snapshot_at
FROM meta_ad_realtime_daily_campaign_metrics
WHERE business_date IN (...)
GROUP BY business_date, ad_account_id

-- 拉行时三键齐全
SELECT ...
FROM meta_ad_realtime_daily_campaign_metrics
WHERE business_date=%s AND ad_account_id=%s AND snapshot_at=%s
  AND data_completeness='realtime_partial'
```

已知事故：2026-05-08 17:00 起 newjoyloo_bak 浏览器 export 连续 600s timeout，全局 `MAX(snapshot_at)` 等于 Omurio 17:00 那一行，结果 newjoyloo_bak 16:40 的 $246.36 被静默丢弃，order-profit 看板「成本拆分 → 已分摊广告费」整列读到 $0。

涉及函数：

- `appcore/order_analytics/order_profit_aggregation.py::_load_realtime_ad_snapshot_fallback`（看板「未分摊广告费」与 `_load_realtime_ad_cost_adjustments` 的输入）
- `appcore/order_analytics/realtime.py::_get_today_realtime_meta_totals`（「真实 ROAS」当日卡片汇总）

新增同类查询时遵守同款分组；产品盈亏 / 广告费分摊路径若再加 fallback，必须按 `ad_account_id` 单独取 snapshot 后聚合。

## 失败隔离决策

| 场景 | 整体 status | 备注 |
|---|---|---|
| 所有 enabled 账户成功 | success | 正常路径 |
| 部分成功，部分失败 | success | summary["account_results"] 标各自 status；运维可在 DB 看错误 |
| 全部失败 | failed | error 拼接所有账户错误信息 |
| 没有 enabled 账户 | skipped | 等价 channel="none"，不报错 |
| `_start_meta_run` 之前异常（如读 settings 失败） | failed | 不写 run_id，直接返回 status=failed |

整体 status="success" 包含部分失败，是有意为之——避免单个账户登录态失效连续拉响告警；具体哪个失败看 summary_json。

## 子目录隔离

旧路径：`output/meta_realtime_exports/2026-05-06/20260507_004000/newjoyloo_*.csv`

新路径：`output/meta_realtime_exports/2026-05-06/20260507_004000/<account.code>/<account.csv_prefix>_*.csv`

历史 CSV 文件**不动**（旧 newjoyloo CSV 在原位置保留），新跑的 CSV 进 subfolder。实时同步目录走 `output/meta_realtime_exports/.../<account.code>/`，收盘日同步目录走 `output/meta_daily_final_exports/.../<account.code>/`。

## scripts/run_meta_ads_backfill_range.py 改造

新增 CLI 参数 `--csv-prefix`（默认 `newjoyloo` 兼容直接命令行调用），把硬编码的 `newjoyloo_{label}_{day}.csv` 换成 `{prefix}_{label}_{day}.csv`。`--account-id` / `--business-id` 已经支持，不动。

## 迁移 / 上线

1. 首次种子 SQL：`db/migrations/2026_05_07_meta_ad_accounts_setting.sql`：
   - `INSERT IGNORE INTO system_settings (key, value) VALUES ('meta_ad_accounts', '<seed JSON>');`
   - 包含 newjoyloo 新户 (enabled=true, store_codes=["newjoy"]) + newjoyloo_old 旧户 (enabled=false, store_codes=["newjoy"]) + Omurio (enabled=true, store_codes=["omurio"])。
2. 线上/测试已存在旧 JSON 时应用切户 SQL：`db/migrations/2026_05_07_newjoyloo_meta_ad_account_switch.sql`：
   - `INSERT ... ON DUPLICATE KEY UPDATE value=VALUES(value);`
   - 目的：覆盖旧 `newjoyloo=2110407576446225 enabled=false` 配置，确保下一轮 timer 使用新户 `1861285821213497`。
3. 部署代码（按本机部署 SOP）。
4. 服务重启后 systemd 启动器 apply 该 SQL。
5. 等下一个 timer tick（最多 1 小时），观察 `meta_ad_realtime_import_runs`：
   - `ad_account_ids` 含 newjoyloo 新户 `1861285821213497` 和 Omurio `1253003326160754`。
   - `summary_json.account_results[*]` 各账户独立结果。
   - `newjoyloo_old` 为 `enabled=false`，不应出现在同步账户列表里，但产品盈亏历史分摊仍能通过 `enabled_only=false` 映射到旧户。

## 回滚

把 `meta_ad_accounts` setting 删除即可：

```sql
DELETE FROM system_settings WHERE `key`='meta_ad_accounts';
```

代码会回退到环境变量默认值（当前 newjoyloo 新户单账户行为）。若要回退到旧户，需显式把 `META_AD_EXPORT_ACCOUNT_ID=2110407576446225` 注入运行环境或在「广告账户」Tab 改配置。

## 测试覆盖

`tests/test_roi_hourly_sync_meta_multi_account.py`：

1. 双账户都成功 → run.status=success、rows/spend 累加、ad_account_ids 含两个 ID。
2. 一个账户 export 抛异常 → run.status=success、另一个账户的数据仍写入、failed 账户在 account_results 里有 error。
3. 全部失败 → run.status=failed、error_message 含两个账户错误。
4. 没有 enabled 账户 → status=skipped、不调 export 子进程。
5. settings 没设值 → fallback 到 env 默认（newjoyloo 新户单账户），保持向后兼容。
6. 收盘日同步双账户成功 → 两个账户各自导出 / 删除 / 写入，快照广告费汇总两户。
7. 收盘日同步部分失败 → 成功账户入库，整体 run failed，summary.account_results 标明失败账户。
8. 数据分析「广告账户」Tab API 能读取、校验、保存 `store_codes`。
9. 产品盈亏广告费分摊从 `meta_ad_accounts.store_codes` 生成映射，不再依赖硬编码常量。
10. 收盘日同步支持 `--account-code newjoyloo_old` 显式选择 disabled 旧户做历史补抓。
11. 数据分析「广告账户」Tab 渲染行内「同步」按钮、同步 modal、设置 / 进度 Tab，并通过 Web API 启动和查询手动同步 job。
12. 临时旧户历史 runner 能按状态文件每轮推进 5 个成功日期、启用 `include_adsets`、遇到失败保留游标、完成后停止 timer。

## Docs-anchor

- 本文件
- 修订条目：[CLAUDE.md](../../../CLAUDE.md) 新增"Meta 广告多账户同步"小节

## 相关文档

- [Meta 广告费人工录入兜底（2026-05-09）](2026-05-09-manual-daily-ad-spend-design.md) — sync 失败时的兜底入口
