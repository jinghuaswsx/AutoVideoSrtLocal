# Meta 广告实时同步 多账户改造（2026-05-07）

## 背景

`tools/roi_hourly_sync.py` 调度（systemd timer `autovideosrt-roi-realtime-sync.timer`，每 20 分钟一次）目前**只同步一个 Meta 广告账户** `2110407576446225`（newjoyloo），账户 ID / business ID / CSV 文件名前缀 `newjoyloo` 全部硬编码或仅由环境变量控制。

事件：
- 2026-05-07 newjoyloo 账户被 Meta 封禁，最近的 CSV `已花费金额` 全为 0。
- 实际公司同时运营 newjoyloo + Omurio 两个账户；订单侧 `STORE_SCOPE = "newjoy,omurio"` 已经在并行同步两个店铺，但 Meta 广告侧从未对接 Omurio。
- newjoyloo 解封时间未定，期间 Omurio 数据必须可见。

## 目标

1. Meta 广告同步原生支持多账户：每次定时跑都遍历**所有 enabled 账户**。
2. 单账户失败不影响其他账户继续跑（CDP 浏览器登录态在某账户失效，不会拖垮整个 sync）。
3. CSV 文件名前缀按账户走，导出目录按账户分子目录，避免相互覆盖。
4. 配置存在数据库 `system_settings.meta_ad_accounts`（JSON），并在数据分析模块新增「广告账户」Tab 管理。
5. newjoyloo 配置保留但 `enabled=false`，Omurio 配置 `enabled=true`，解封后只需把 enabled 翻成 true。
6. `meta_daily_final_sync` 收盘日同步与实时同步共用同一份账户配置，避免只同步一个店铺 / 一个广告户。
7. 账户必须声明对应店铺 `store_codes`，让同步、看板、产品盈亏广告费分摊共用同一份「店铺 ↔ 广告户」映射。

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
    "account_id": "2110407576446225",
    "business_id": "476723373113063",
    "csv_prefix": "newjoyloo",
    "store_codes": ["newjoy"],
    "enabled": false,
    "note": "2026-05-07 被 Meta 封禁，等待恢复"
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
- `code`：账户唯一 code，用作 export 子目录名 / 日志标签；不可重复。
- `account_id` / `business_id`：Meta Ads Manager URL 里的 `act=` / `business_id=`。
- `csv_prefix`：CSV 文件名前缀。**保持原始大小写**（沿用线上 `newjoyloo`、Omurio 后台显示 `Omurio`）。
- `store_codes`：该广告账户覆盖的店铺编码数组，例如 `newjoy`、`omurio`。一个账户可对应多个店铺；同一个店铺同时绑定多个 enabled 账户时，利润分摊按该店铺所有账户 spend 合计。
- `enabled`：是否参与每轮同步。被封 / 未授权账户置 false。
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

1. 应用 SQL `db/migrations/2026_05_07_meta_ad_accounts_setting.sql`：
   - `INSERT INTO system_settings (key, value) VALUES ('meta_ad_accounts', '<seed JSON>') ON DUPLICATE KEY UPDATE value=VALUES(value);`
   - 包含 newjoyloo (enabled=false, store_codes=["newjoy"]) + Omurio (enabled=true, store_codes=["omurio"]) 两条种子。
2. 部署代码（按本机部署 SOP）。
3. 服务重启后 systemd 启动器 apply 该 SQL。
4. 等下一个 timer tick（最多 20 分钟），观察 `meta_ad_realtime_import_runs`：
   - `ad_account_ids` 含两个 ID。
   - `summary_json.account_results[*]` 各账户独立结果。
   - 如果 newjoyloo enabled=false：`account_results` 应只有 Omurio 一条；`ad_account_ids` 只含 Omurio ID。

## 回滚

把 `meta_ad_accounts` setting 删除即可：

```sql
DELETE FROM system_settings WHERE `key`='meta_ad_accounts';
```

代码会回退到环境变量默认值（newjoyloo 单账户旧行为）。

## 测试覆盖

`tests/test_roi_hourly_sync_meta_multi_account.py`：

1. 双账户都成功 → run.status=success、rows/spend 累加、ad_account_ids 含两个 ID。
2. 一个账户 export 抛异常 → run.status=success、另一个账户的数据仍写入、failed 账户在 account_results 里有 error。
3. 全部失败 → run.status=failed、error_message 含两个账户错误。
4. 没有 enabled 账户 → status=skipped、不调 export 子进程。
5. settings 没设值 → fallback 到 env 默认（newjoyloo 单账户），保持向后兼容。
6. 收盘日同步双账户成功 → 两个账户各自导出 / 删除 / 写入，快照广告费汇总两户。
7. 收盘日同步部分失败 → 成功账户入库，整体 run failed，summary.account_results 标明失败账户。
8. 数据分析「广告账户」Tab API 能读取、校验、保存 `store_codes`。
9. 产品盈亏广告费分摊从 `meta_ad_accounts.store_codes` 生成映射，不再依赖硬编码常量。

## Docs-anchor

- 本文件
- 修订条目：[CLAUDE.md](../../../CLAUDE.md) 新增"Meta 广告多账户同步"小节
