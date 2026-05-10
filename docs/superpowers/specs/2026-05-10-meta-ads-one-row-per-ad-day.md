# Meta 广告同步按广告自然日去重（2026-05-10）

## 背景

2026-05-10 订单利润核算对账时，用户从 Meta 后台核对 2026-05-01 至 2026-05-09 三个广告账户消耗：

- Omurio：$2,076.84
- newjoyloo_old / newjoyloo：$20,284.51
- newjoyloo_bak：$5,808.64

系统中 `newjoyloo_old` 与后台一致，但 `Omurio`、`newjoyloo_bak` 偏高。生产只读排查显示，`newjoyloo_bak` 在 `meta_ad_daily_campaign_metrics` / `meta_ad_daily_ad_metrics` 中存在同一 `ad_account_id + report_start_date + campaign/ad` 出现在多个 `meta_business_date` 下的情况。例如 `report_start_date=2026-05-07` 的 campaign 同时挂到 `meta_business_date=2026-05-06` 和 `2026-05-07`，导致同一个 Meta 广告自然日被相邻业务日重复计入。

## 根因

[`docs/superpowers/specs/2026-05-09-meta-ads-account-timezone-and-async-fix.md`](2026-05-09-meta-ads-account-timezone-and-async-fix.md) 为修复 XHR 时区错位，让 `account_xhr_time_range(account, business_date)` 在 PDT 下返回 `{since=D, until=D+1}`，并假设 `/insights?time_increment=1` 多返回的自然日不会污染入库。

实际实现中：

- `tools/meta_daily_final_sync.py` 的 XHR daily-final path 会把 API 返回的每行都归入调用方传入的 `target_date`。
- `aggregate_daily_entity_rows(...)` 按 `ad_account_id + report_date + entity` 聚合，而 `report_date` 固定为 `target_date`，没有把 `date_start` 纳入去重键。
- realtime XHR path 也把所有返回行写到同一个 `business_date + snapshot_at` 下，唯一键没有包含 `date_start`。

因此只要 Meta 返回 `date_start=D` 和 `date_start=D+1` 两组行，系统就会把不属于目标日的行混入目标业务日，后续订单利润核算、实时大盘、产品盈亏看板都会读到偏高广告费。

## 修复目标

1. 每个广告账户、每个 Meta 广告自然日、每个 campaign/ad/adset 只保留一份数据。
2. XHR daily-final 只接受 `date_start == target_date` 的 API 行；`date_stop` 缺失时不阻断，但有值且不等于 `target_date` 时丢弃。
3. XHR realtime 只接受 `date_start == business_date` 的 API 行；缺少 `date_start` 的旧 CSV/browser 行不受影响。
4. 保留 `account_xhr_time_range(...)` 作为请求覆盖范围，避免刚过切日时取不到行；但入库前必须按 API 行自己的 `date_start/date_stop` 做目标日过滤。
5. 订单利润核算、实时大盘、产品盈亏看板继续共享同一广告来源表；修复发生在同步入口，避免三个消费侧各自补丁。

## 非目标

- 不在本次改动中修改 DB schema。
- 不把广告费改成半日级别切分；Meta `/insights` 当前按账户时区自然日返回，系统先保证自然日不重复。
- 不直接清理生产 DB。生产修复需在代码上线并重新同步对应日期后执行，或另行准备审计 SQL。

## 验收

- daily-final XHR 对同一个 target date 收到 `date_start=target_date` 和 `date_start=target_date+1` 两组 campaign/ad/adset 行时，只写入 target date 那组。
- realtime XHR 对同一个 business date 收到多天 rows 时，只把 target date 那组写入 `meta_ad_realtime_daily_campaign_metrics`。
- 同一 `ad_account_id + report_start_date + campaign/ad` 不再跨多个 `meta_business_date` 出现正数 spend。
- 三个前端入口读取到的广告费总额来自同一套去重后数据：
  - 订单利润核算
  - 数据分析实时大盘
  - 产品盈亏看板

## Docs-anchor

- 本文件
- [Meta 广告 XHR 通道账户时区 + Playwright 线程隔离](2026-05-09-meta-ads-account-timezone-and-async-fix.md)
- [实时大盘广告费选源修复 + 收盘日 guard](2026-05-09-realtime-dashboard-ad-spend-source-of-truth.md)
- [数据分析时间对齐修复](2026-05-08-analytics-business-date-alignment-fix.md)
