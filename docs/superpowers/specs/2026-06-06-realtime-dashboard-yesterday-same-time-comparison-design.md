# 实时大盘全局卡片：昨天同进度变化百分比

## 背景

`数据分析 -> 实时大盘` 顶部已经有全局 / 新品 / 老品 / 未匹配四组汇总卡。用户希望只在「全局数据」里给三个核心指标增加“相比昨天同一时间”的变化百分比：

- 总销售额
- 订单数
- 利润

该变化只服务当天实时经营判断，不扩展到昨天、本周、本月、自定义日期范围，也不扩展到新品 / 老品 / 未匹配卡片。

## 文档锚点

- `docs/superpowers/specs/2026-05-02-realtime-dashboard-redesign.md`
  - 实时大盘顶部卡片走 `/order-analytics/realtime-overview?start_date=X&end_date=Y`。
  - `period.data_until_at` 表示当前数据截止水位。
  - 卡片 sub 行使用 `.oa-stat-sub` 小字号。
- `docs/superpowers/specs/2026-05-08-analytics-business-date-alignment-fix.md`
  - Meta 业务日按北京时间 16:00 切日。
  - 当前业务日订单必须按 `meta_business_date = target` 且 `order_time <= snapshot_at/data_until` 截断。
  - 利润卡必须和广告消耗费用在同一实时口径下扣减。
  - 实时广告快照必须按 `(business_date, ad_account_id)` 各自取最新 snapshot 后汇总。
- `docs/superpowers/specs/2026-05-10-realtime-dashboard-profit-margin.md`
  - 实时大盘利润 KPI 使用 `order_profit_summary.profit_with_estimate_usd`。
  - 现有利润率 sub 行保留，不改变 `profit_with_estimate_margin_pct` 语义。

## 用户已确认的范围

1. 只在「当天」显示对比昨天同一业务日进度的变化。
2. 只影响「全局数据」卡片，不影响新品 / 老品 / 未匹配。
3. 只加三个指标：总销售额、订单数、利润。
4. 百分比显示为主数字约一半大小，保留 0 位小数，带正负号，表达相对昨天同进度的增减。

## 数据源支持结论

现有数据源支持该需求，但需要补后端比较字段：

- 订单和销售额：`dianxiaomi_order_lines` 有 `meta_business_date` 和订单时间字段，现有 `_get_realtime_order_summary(target, data_until, ...)` 已能按同一水位截断订单。
- 广告实时快照：`meta_ad_realtime_daily_campaign_metrics` 有 `business_date`、`snapshot_at`、`ad_account_id`，现有 `_get_realtime_campaign_details()` 已按每账户 `MAX(snapshot_at) <= snapshot_until` 汇总。
- 预聚合快照：`roi_realtime_daily_snapshots` 有 `business_date`、`snapshot_at`、累计订单数、销售额和广告费，可作为全局双店默认路径的快速来源。
- 利润：现有 `_get_realtime_order_profit_details()` 能按 `data_until` 截断订单利润明细，但 `_apply_realtime_ad_cost_adjustments()` 目前调用的 `_load_realtime_ad_cost_adjustments()` 只按日期取最新 / 日终广告分摊，不能显式传 `snapshot_until`。为避免“昨天半天营收 + 昨天整天广告费”的错配，本期必须补一个水位受限的实时广告分摊路径。

## 显示规则

### 何时显示

仅当全局卡片请求满足以下条件时显示：

- `start_date == end_date`
- 该日期等于 `current_meta_business_date(now)`
- 没有 `product_id`
- 没有 `site_code`
- 没有 `product_launch_scope`

其它范围显示为空，不渲染变化百分比。这样保持“当天 vs 昨天同进度”的语义明确，避免多日范围或局部过滤下出现误导。

### 文案和样式

三个全局卡片把昨天同进度变化百分比放在主数字后面，不新增独立 sub 行，不展示中文提示文案：

- 总销售额：`$1,200.00  (+12%)`
- 订单数：`60  (-8%)`
- 利润：`$240.00  (0%)`

显示规范：

- 百分比与主数字在同一行，间隔约两个空格。
- 百分比字号约为主数字 2/3，加粗显示，并用括号包裹。
- 正数显示 `+N%`，负数显示 `-N%`，0 显示 `0%`。
- 百分比保留 0 位小数。
- 正数使用现有正向色，负数使用现有亏损 / danger 色，0 使用正文黑色。
- 昨天为 0 且今天大于 0 时百分比不可定义，前端不渲染该行内百分比；昨天和今天都为 0 时显示 `0%`。

## API 设计

在 `/order-analytics/realtime-overview` 响应顶层新增：

```json
{
  "comparison": {
    "yesterday_same_time": {
      "enabled": true,
      "label": "较昨天同刻",
      "basis": {
        "current_business_date": "2026-06-05",
        "previous_business_date": "2026-06-04",
        "current_until_at": "2026-06-06T10:20:00",
        "previous_until_at": "2026-06-05T10:20:00"
      },
      "summary": {
        "revenue_with_shipping": {
          "current": 1200.0,
          "previous": 1000.0,
          "pct": 20.0
        },
        "order_count": {
          "current": 60,
          "previous": 50,
          "pct": 20.0
        },
        "profit_with_estimate_usd": {
          "current": 240.0,
          "previous": 200.0,
          "pct": 20.0
        }
      }
    }
  }
}
```

当不满足显示条件时仍返回稳定结构：

```json
{
  "comparison": {
    "yesterday_same_time": {
      "enabled": false,
      "label": "较昨天同刻",
      "basis": null,
      "summary": {}
    }
  }
}
```

百分比字段按指标类型计算：

- `revenue_with_shipping` / `order_count`：使用现有 `_compute_pct_change(current, previous)` 语义；`previous=0 && current>0` 时为 `null`。
- `profit_with_estimate_usd`：利润允许为负，不能直接用负数 `previous` 作分母，否则会出现“昨天亏损、今天盈利却显示负增长”。利润变化百分比按改善率计算：`(current - previous) / abs(previous) * 100`；`previous=0 && current!=0` 时为 `null`，`previous=0 && current=0` 时为 `0%`。

## 后端设计

### 水位计算

在单日当前业务日、全局范围下：

1. 当前水位：使用响应已经确定的 `period.data_until_at`。
2. 当前业务日开始：`compute_meta_business_window_bj(current_business_date)[0]`。
3. 业务日进度：`elapsed = current_until_at - current_day_start`，下限 0，上限 24 小时。
4. 昨天水位：`previous_until_at = previous_day_start + elapsed`，并 clamp 到昨天业务日 `[previous_day_start, previous_day_end]`。

### 昨天同进度 summary

优先复用现有 helper：

- 订单 / 销售额：调用 `_get_realtime_order_summary(previous_date, previous_until_at, site_codes=default)`。
- 广告：调用 `_get_realtime_ad_summary_for_business_date(previous_date, previous_until_at, site_codes=default)`，保持 per-account latest snapshot 规则。
- 利润：调用新的水位受限利润汇总 helper。

昨天同进度比较不能用 `meta_ad_daily_campaign_metrics` 的日终整日广告费代替 partial 水位。若昨天没有 `snapshot_at <= previous_until_at` 的实时广告快照，广告和利润比较按不可计算处理，避免半天订单搭配整天广告费。

### 利润水位受限 helper

新增局部 helper，避免改动订单利润核算页全局口径：

- `_get_realtime_order_profit_details(..., data_until=previous_until_at)` 已经能只取昨天同进度订单。
- 新增 `snapshot_until` 参数的实时广告分摊路径，用于本场景：
  - 按 `(business_date, ad_account_id)` 取 `MAX(snapshot_at) <= snapshot_until`。
  - campaign -> product 匹配逻辑沿用现有 `resolve_ad_product_match`。
  - units 只统计 `order_time <= snapshot_until` 的订单行，保证广告分摊分母和利润订单集合同进度。
  - 未分摊广告费继续进入 `total_ad_spend_usd - ad_cost_usd`，维持利润卡口径。

该 helper 只供实时大盘昨天同进度比较使用，不改变 `/order-profit` 或历史整日利润逻辑。

### 当前值来源

当前三个指标直接取当前响应已经计算好的值：

- `summary.revenue_with_shipping`
- `summary.order_count`
- `order_profit_summary.profit_with_estimate_usd`

避免重复计算当前日，也减少当前页面既有口径漂移。

## 前端设计

只改 `web/templates/order_analytics.html` 的全局卡片：

- 不新增 `#realtimeRevenueWithShippingCompare`、`#realtimeOrderCountCompare`、`#realtimeProfitCompare` 独立 DOM 节点。
- `#realtimeRevenueWithShipping`、`#realtimeOrderCount`、`#realtimeProfit` 主值节点内追加行内 `<span class="oar-same-time-compare">(+N%)</span>`。
- 行内百分比 class 按数值添加：增长 `oar-same-time-up`，下降 `oar-same-time-down`，无变化 `oar-same-time-flat`。
- `renderRealtimeScopeSummary('global', data)` 中读取 `data.comparison.yesterday_same_time.summary`，只在 `enabled=true` 时追加；其它 scope 不渲染。

## 错误和空值

- 后端比较计算失败时，不影响主卡片数据；返回 `enabled=false` 并可在日志记录异常；前端只显示主数字。
- 昨天没有对应快照时：
  - 订单 / 销售额仍可从订单明细按时间截断计算。
  - 广告和利润若缺实时广告水位，则利润比较返回 `pct=null`，前端不追加百分比。
  - 不退回日终整日广告表。
- 昨天同刻订单数为 0、今天订单数为 0：显示 `0%`。
- 昨天同刻订单数为 0、今天订单数大于 0：不追加百分比。
- 昨天同刻利润为负、今天利润更高（含转正）：显示正百分比并用增长色；昨天同刻利润为负、今天利润更低：显示负百分比并用下降色。

## 不做

- 不在新品 / 老品 / 未匹配卡片展示对比。
- 不在昨天、本周、本月、自定义范围展示对比。
- 不新增数据库表或迁移。
- 不改变 `profit_with_estimate_margin_pct`、成本占比、ROAS 或现有利润公式。
- 不改变子 tab、订单明细、广告计划、ROAS 走势。

## 修改范围

1. `docs/superpowers/specs/2026-06-06-realtime-dashboard-yesterday-same-time-comparison-design.md`
2. `appcore/order_analytics/realtime.py`
   - 新增 comparison builder。
   - 新增 / 调整水位受限利润汇总 helper。
   - 单日当前业务日全局响应附加 `comparison.yesterday_same_time`。
3. `web/templates/order_analytics.html`
   - 在三个全局主值节点内追加行内百分比。
   - 增加格式化、样式和渲染逻辑。
4. `tests/`
   - 增加后端单测覆盖水位、百分比、利润水位受限和非当天禁用。
   - 增加模板/JS 字符串级测试覆盖三个 DOM id 与格式化逻辑。

## 验证

必须跑：

```bash
pytest tests/test_order_analytics_true_roas.py \
       tests/test_order_analytics_realtime_profit_details.py \
       tests/test_order_analytics_realtime_profit_margin.py \
       tests/test_order_analytics_realtime_site_filter.py \
       tests/test_order_analytics_template_layout.py \
       -q
```

建议手动验收：

1. 启动 dev server。
2. 登录后访问 `/order-analytics`，进入实时大盘。
3. 选择“今天”：全局卡片的总销售额、订单数、利润主数字后显示 `+/-N%`，没有中文提示。
4. 选择“昨天”或自定义非今天范围：只显示主数字，不追加对比百分比。
5. 切换店铺筛选 / 产品筛选：只显示主数字，避免局部筛选误读为全局对比。

## related

- `docs/superpowers/specs/2026-05-02-realtime-dashboard-redesign.md`
- `docs/superpowers/specs/2026-05-08-analytics-business-date-alignment-fix.md`
- `docs/superpowers/specs/2026-05-09-realtime-dashboard-store-filter.md`
- `docs/superpowers/specs/2026-05-10-realtime-dashboard-profit-margin.md`
