# 数据分析看板数据质量护栏实施指令

最后更新：2026-05-10（新增 Meta 广告自然日唯一性校验）

实施状态：v1 已落地。后端模块 `appcore/order_analytics/data_quality.py`、
共享前端组件 `web/templates/_data_quality_bar.html`、定时巡检任务
`analytics_data_quality_inspection`（登记于 `appcore/scheduled_tasks.py`）已就位。
后续阶段（巡检结果落库、xlsx 导出说明页、单元 tab 级别广告费分摊重算）见末尾「后续 TODO」。

## 给执行 agent 的入口

先读本文件，再读：

- `AGENTS.md`
- `docs/superpowers/specs/2026-05-08-analytics-business-date-alignment-fix.md`
- `docs/superpowers/specs/2026-05-02-realtime-dashboard-redesign.md`
- `docs/superpowers/specs/2026-05-07-order-profit-detail-tab-design.md`
- `docs/superpowers/specs/2026-05-07-product-profit-dashboard-tabs-redesign-design.md`

本任务目标不是再修一个单点 0 值问题，而是建立一套后端可证明、前端可见的数据质量机制，避免页面展示“看起来有数、其实没更新或没对齐”的错误数据。

## 背景问题

2026-05-08 发生过两类同源问题：

1. 数据分析实时大盘在 Meta 业务日切换后，日终广告表尚未生成时显示广告费 0。
2. 订单利润核算页读取 `order_profit_lines.ad_cost_usd`，该派生字段滞后时显示广告费 0；后来日终广告表到位后，如果页面仍信任旧派生字段，也会继续展示旧值。

肉眼只能发现“明显为 0”的错误；如果旧值不是 0，只是停留在上一批数据，业务方很难看出来。因此所有数据分析类页面都必须带数据水位、源表对账和派生数据新鲜度判断。

## 适用范围

必须覆盖以下页面和接口：

- `/order-analytics` 实时大盘及其 `realtime-overview` 相关接口。
- `/order-profit` 订单利润核算页及 `/order-profit/api/*`。
- `/product-profit` 产品盈亏看板。
- `/order-analytics/product-profit/*` 产品盈亏报表、产品列表、国家看板、广告明细等接口。
- 任何复用上述聚合结果的导出接口。

如实现时发现其它页面也读同一批聚合函数，应一起接入数据质量字段，不要只改前端可见页面。

## 核心原则

1. **页面不能静默展示未校验数据。**
   每个聚合 API 必须返回 `data_quality` 对象。前端根据 `data_quality.status` 显示状态条。

2. **源表优先，派生表不能长期被无条件信任。**
   `order_profit_lines`、`roi_realtime_daily_snapshots` 等派生表可以作为性能缓存，但页面返回前必须能说明它们是否覆盖了最新源表数据。

3. **金额必须跨表对账。**
   对广告费这类核心指标，必须校验：

   ```text
   源广告费总额 = 已分摊广告费 + 未分摊广告费
   ```

   差额超过容忍阈值时，API 不能返回 `ok`。

4. **Meta 广告自然日不能重复入账。**
   `meta_ad_daily_campaign_metrics` / `meta_ad_daily_ad_metrics` 必须满足：
   同一个 `ad_account_id + report_start_date + campaign/ad` 不能跨多个
   `meta_business_date` 出现，也不能把 `date_start != target_date` 的 XHR
   行合并进目标业务日。若检测到 `raw_json.merged_rows > 1`、跨业务日重复
   或 `report_start_date` 错挂，`data_quality.status` 必须降级为 `mismatch`。
   具体修复锚点见
   `docs/superpowers/specs/2026-05-10-meta-ads-one-row-per-ad-day.md`。

5. **时间范围必须显式。**
   所有页面显示的日期都按 Meta 业务日口径，即北京时间 16:00 切日。前端显示自然时间时，必须同时展示业务日范围。

6. **水位必须可见。**
   前端至少展示订单数据水位、广告数据水位、利润/产品盈亏计算水位，以及本页使用的数据源模式。

7. **异常比错误数字更可接受。**
   如果无法证明数据正确，页面应显示 warning/error，而不是给用户一个看起来正常的数字。

## API 契约

所有相关 JSON API 顶层都应包含：

```json
{
  "data_quality": {
    "status": "ok",
    "source_mode": "daily_final",
    "business_date_from": "2026-05-07",
    "business_date_to": "2026-05-07",
    "generated_at": "2026-05-08T18:30:00+08:00",
    "watermarks": {
      "orders": {
        "latest_business_date": "2026-05-08",
        "latest_updated_at": "2026-05-08T18:20:00+08:00"
      },
      "meta_daily_ads": {
        "latest_business_date": "2026-05-07",
        "latest_import_finished_at": "2026-05-08T17:10:00+08:00"
      },
      "meta_realtime_ads": {
        "latest_business_date": "2026-05-08",
        "latest_snapshot_at": "2026-05-08T18:20:00+08:00"
      },
      "derived_profit": {
        "latest_business_date": "2026-05-08",
        "latest_run_finished_at": "2026-05-08T18:25:00+08:00"
      }
    },
    "checks": [
      {
        "code": "ad_spend_reconciled",
        "status": "ok",
        "expected": 1443.75,
        "actual": 1443.75,
        "diff": 0.0,
        "message": "广告源表总额与已分摊+未分摊金额一致"
      },
      {
        "code": "meta_ad_day_uniqueness",
        "status": "ok",
        "duplicate_groups": 0,
        "affected_spend_usd": 0.0,
        "message": "Meta 广告自然日未发现跨业务日重复"
      }
    ],
    "warnings": [],
    "errors": []
  }
}
```

### status 枚举

- `ok`：关键对账全部通过，数据源水位满足当前日期范围。
- `warning`：数据可展示，但存在未分摊、实时兜底、部分源表未最终完成等业务可接受情况。
- `stale`：派生数据落后于源表，页面数字可能不是最新。
- `mismatch`：跨表对账失败，页面数字不能当作准确值。
- `error`：缺少必要源数据或校验过程异常。

### source_mode 枚举

- `daily_final`：使用 Meta 日终广告表。
- `realtime_snapshot`：使用 Meta 实时快照兜底。
- `mixed`：日期范围内部分日期用日终表、部分日期用实时快照。
- `derived_cache`：使用派生缓存，且已通过新鲜度校验。
- `unknown`：无法判断时只允许配合 `warning` / `stale` / `error`。

## 后端实施要求

建议新增一个独立模块，例如：

- `appcore/order_analytics/data_quality.py`

职责：

- 统一构造 `data_quality`。
- 提供水位查询函数。
- 提供金额对账函数。
- 提供派生数据新鲜度判断。

不要把大量校验逻辑散落在 Flask route 或前端模板里。

### 订单利润页必须实现的校验

对 `/order-profit/api/summary` 和 `/order-profit/api/orders`：

1. 按业务日读取订单利润行。
2. 优先按 `meta_ad_daily_campaign_metrics` 现场重算广告费。
3. 对缺少日终表的业务日，用 `meta_ad_realtime_daily_campaign_metrics` 最新快照兜底。
4. 对已匹配 product 但没有可分摊订单 units 的 spend，计入 `unallocated_ad_spend_usd`。
5. 校验：

   ```text
   meta_source_ad_spend = allocated_order_ad_spend + unallocated_ad_spend
   ```

6. 如果 `order_profit_lines.ad_cost_usd` 与现场重算结果不一致，`data_quality` 必须说明派生字段 stale，但页面仍应展示现场重算结果。

### 实时大盘必须实现的校验

对 `/order-analytics/realtime-overview`：

1. 明确当前请求使用的是 `roi_realtime_daily_snapshots`、`meta_ad_realtime_daily_campaign_metrics` 还是 `meta_ad_daily_campaign_metrics`。
2. 单日当前业务日、或刚过 16:00 后选择上一业务日但日终表缺失时，必须使用最新实时快照。
3. 带 `product_id` 时，广告费必须从 campaign 级实时行按 product 匹配过滤，不能回退成日终 0。
4. 返回订单水位与广告快照水位。如果订单截止时间晚于广告快照或广告快照明显过旧，返回 `warning`。

### 产品盈亏必须实现的校验

对 `/product-profit` 和 `/order-analytics/product-profit/*`：

1. 产品广告费以 `meta_ad_daily_campaign_metrics.product_id` 为主；日终缺失日期应明确 `source_mode`，不要静默使用 0。
   当产品盈亏接口带具体 `country` 时，广告费不能再使用该产品全量 spend，而应使用广告
   名称解析出的 `market_country` 过滤后的 ad 层 spend；该口径是运营命名估算，不是
   Meta API country breakdown。
2. 总账中必须暴露 `unallocated_ad_spend_usd`，并纳入利润口径或明确说明未纳入口径。
3. 产品列表、订单明细、国家看板、广告明细的广告费汇总口径必须能互相对账。
4. 导出的 xlsx 应包含数据质量摘要页或顶部说明。

## 前端实施要求

每个页面顶部加数据质量条：

- `ok`：低调展示“数据已校验”，可折叠查看水位。
- `warning`：黄色条，展示未分摊广告费、实时兜底、源表未完成等。
- `stale` / `mismatch` / `error`：红色或强提示，明确“当前数据不能作为准确值”。

页面上不能只显示金额，还必须显示：

- 数据源模式：日终 / 实时 / 混合 / 派生缓存。
- 订单数据最新时间。
- 广告数据最新时间。
- 利润或产品盈亏计算时间。
- 对账结果摘要。

如果某接口尚未接入 `data_quality`，前端应把它当作 `unknown`，不要默认当作 `ok`。

## 定时巡检要求

新增定时巡检任务时，必须遵守 `AGENTS.md` 的定时任务归集规则，同步维护 `appcore/scheduled_tasks.py`。

建议新增最近 7 天巡检：

- 实时大盘广告费 vs Meta 实时快照 / 日终表。
- 订单利润广告费 vs Meta 日终/实时源表。
- 产品盈亏广告费 vs 产品广告明细。
- 订单利润和产品盈亏的同业务日广告总额差异。

巡检结果应写入可查询日志或状态表，并在页面 `data_quality` 中复用最近巡检结果。

## 测试要求

必须 TDD。至少新增以下测试：

1. 日终广告表已有、`order_profit_lines.ad_cost_usd` 仍为 0 时，订单利润 API 返回现场重算广告费，并标记派生字段 stale。
2. 日终广告表缺失、实时快照存在时，订单利润 API 使用实时快照兜底。
3. 已匹配 product 但无订单 units 的广告费进入未分摊广告费，并扣入总利润。
4. `allocated_ad_spend + unallocated_ad_spend != source_ad_spend` 时，`data_quality.status = mismatch`。
5. `/order-analytics/realtime-overview` 带 `product_id` 时不允许回退到日终表 0。
6. 产品盈亏各 tab 的广告费口径可以和源表对账。
7. 前端模板或静态测试覆盖 `data_quality` 状态条存在，并覆盖 warning/error 文案。

建议测试命令：

```bash
/opt/autovideosrt/venv/bin/python -m pytest \
  tests/test_order_profit_aggregation.py \
  tests/test_order_profit_routes.py \
  tests/test_order_analytics_dashboard.py \
  tests/test_order_analytics_true_roas.py \
  tests/test_product_profit_report.py \
  tests/test_product_profit_list.py \
  tests/test_product_profit_routes.py \
  tests/test_product_profit_dashboard_assets.py \
  -q
```

## 生产验收样例

以 2026-05-07 为回归样例：

- `/order-profit` 选择“昨天”时，广告费不能显示 0。
- 订单利润汇总应满足：

  ```text
  已分摊广告费 1038.12 + 未分摊广告费 405.63 = Meta 日终广告费 1443.75
  ```

- 页面顶部应显示该日期使用 `daily_final` 数据源。
- 如果后续源表金额变化，页面要么重新计算并更新金额，要么显示 stale/mismatch，不能静默保持旧值。

## 非目标

- 不在本任务里重做利润公式。
- 不改变 Meta 业务日 16:00 切日规则。
- 不引入新的广告账户配置方式。
- 不把所有历史派生表一次性重算；优先保证页面返回值和质量状态准确。

## 后续 TODO

首版仅在 API 顶层挂上 `data_quality` 并接入前端状态条，下面这些点还要后续 PR 跟进：

- 巡检结果落库：`run_recent_inspection` 当前只返回内存对象，待加 `data_quality_inspection_runs`
  表或写入 `scheduled_task_runs.summary_json`，便于历史检索。
- 产品盈亏 tab 级别（订单/国家/广告明细）的广告费现场重算：当前 `data_quality` 校验依赖
  接口已聚合好的总额；个别 tab 没有"已分摊广告费"字段时只能落 `unknown`，需要后续把现场
  重算下沉到聚合层。
- xlsx 导出顶层说明页：`/order-analytics/product-profit/list.xlsx` / `report.xlsx` 还没有
  把 `data_quality` 摘要页打入文件。
- `/order-profit/api/orders/<id>` 单订单详情、`/order-profit/api/cost_completeness` 等
  辅助接口暂未挂 `data_quality`，前端读到时按 `unknown` 处理。

## 提交流程要求

- 开发必须走 worktree 隔离，不要在主工作目录直接改源码。
- commit 必须包含本文件或其它相关 Markdown 文档更新。
- commit message 必须包含：

  ```text
  Docs-anchor: docs/analytics-data-quality-guardrails.md
  ```

- 发布前按 `AGENTS.md` 的测试环境/线上发布规则执行。
