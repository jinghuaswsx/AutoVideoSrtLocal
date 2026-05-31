# 新品投放分析：按上广告时间拆分新品 / 老品 / 未匹配广告

## 背景

数据分析模块已有「实时大盘」，能按 Meta 业务日窗口展示销售额、广告费、ROAS、订单明细、订单盈亏明细、产品销量、广告计划和 ROAS 走势。运营需要在同样的数据口径下单独观察最近开始投放的产品，判断新品反馈，以及老品运行是否稳定。

本需求在「实时大盘」右侧新增一级 Tab：**新品投放分析**。该 Tab 复用实时大盘的指标和明细结构，但把数据范围限定为新品、老品或未匹配广告。

## Docs Anchor

- `AGENTS.md`：文档驱动代码、改代码前必须有文档锚点。
- `appcore/order_analytics/CLAUDE.md`：实时大盘、多账户实时快照、店铺筛选、数据质量护栏。
- `docs/superpowers/specs/2026-05-02-realtime-dashboard-redesign.md`：实时大盘 UI、KPI、日期范围和明细结构。
- `docs/superpowers/specs/2026-05-08-analytics-business-date-alignment-fix.md`：实时大盘 Meta 业务日、实时广告 fallback、利润与广告费口径。
- `docs/superpowers/specs/2026-05-09-realtime-dashboard-store-filter.md`：店铺筛选、单店时绕过双店预聚合快照。
- `docs/analytics-data-quality-guardrails.md`：`/order-analytics/realtime-overview` 顶层 `data_quality`。
- `docs/superpowers/specs/2026-05-08-ads-analytics-tabs-design.md`：Campaign / Ad Set / Ad 三层广告数据表和查询口径。

## 用户已确认的口径

1. 术语使用 **上广告时间**，不是上架时间。
2. 上广告时间定义为产品第一次在广告同步数据里真实匹配到 Campaign / Ad Set / Ad 的日期。
3. 上广告时间只看日期，不看具体几点几分；统一按北京时间自然日 00:00 粒度处理。
4. 新老品判断也使用北京时间自然日的今天 00:00，不使用实时大盘的 Meta 业务日 16:00 切日。
5. 页面统计数据本身仍沿用实时大盘原有 Meta 业务日口径。
6. 新品：`ad_launch_date >= 今天00:00 - 7天`。
7. 老品：`ad_launch_date < 今天00:00 - 7天`。
8. 新老品归类按当前今天 00:00 固定判断，不跟随页面选择的历史日期范围变化。
9. 历史产品允许一次性回填上广告时间。
10. 找不到真实广告匹配记录时，先用 `media_products.created_at` 的日期作为临时上广告时间。
11. 后续第一次真实匹配到广告数据时，需要把 fallback 上广告时间更新为真实广告匹配日期。
12. 一旦来源变为真实广告匹配，后续不再因更晚广告数据改写，除非未来有明确重算需求。
13. 新品投放分析下设三个子 Tab：
    - 新品分析
    - 老品数据
    - 未匹配产品
14. 未匹配到 `product_id` 的广告数据不算新品，也不算老品，单独进入「未匹配产品」子 Tab；等未来能匹配到产品后，再按该产品上广告时间归入新品或老品。
15. 数据维度与实时大盘保持一致：顶部 KPI、订单明细、订单盈亏明细、产品销量统计、广告计划数据、ROAS 走势都要尽量保留。

## 数据模型

新增表：`product_ad_launch_dates`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `product_id` | INT | `media_products.id`，唯一 |
| `ad_launch_date` | DATE | 上广告日期，北京时间自然日 |
| `source` | VARCHAR | `ad_match` 或 `created_at_fallback` |
| `source_level` | VARCHAR | `campaign` / `adset` / `ad` / `product_created_at` |
| `source_table` | VARCHAR | 真实匹配来源表；fallback 时为 `media_products` |
| `source_row_id` | BIGINT NULL | 真实匹配来源行 id；fallback 可为空 |
| `created_at` | DATETIME | 本表创建时间 |
| `updated_at` | DATETIME | 本表更新时间 |

唯一键：`product_id`。

建议索引：

- `(ad_launch_date, source)`
- `(source, updated_at)`

### 写入规则

1. 产品已有 `source='ad_match'`：不覆盖。
2. 产品无记录：写入最早真实广告匹配日期；若无真实广告匹配，则写入 `DATE(media_products.created_at)`，`source='created_at_fallback'`。
3. 产品已有 `source='created_at_fallback'`，且发现真实广告匹配日期：更新为真实日期，`source='ad_match'`。
4. 产品已有 `source='created_at_fallback'`，仍无真实广告匹配：不更新，除非产品本身缺失创建日期导致之前无法写入。

## 回填与同步更新

### 一次性回填

新增可重复执行的回填函数或脚本，扫描所有 `media_products.deleted_at IS NULL` 产品：

1. 从以下表中查找该 `product_id` 最早真实匹配日期：
   - `meta_ad_daily_campaign_metrics`
   - `meta_ad_daily_adset_metrics`
   - `meta_ad_daily_ad_metrics`
2. 日期优先使用 `meta_business_date`，缺失时回退 `report_date`。
3. 三层都有记录时，取最早日期；若同日存在多个层级，`source_level` 仅作为排查字段，不影响分类。
4. 无真实广告匹配记录时，取 `DATE(media_products.created_at)` 作为 fallback。
5. 如果 `media_products.created_at` 异常为空，使用当前北京时间日期作为 fallback，并在回填摘要里计数；正常生产 schema 中该字段不应为空。

### 后续同步更新

在 daily final 广告同步写入 Campaign / Ad Set / Ad 后，调用同一个 helper，对本批次匹配到 `product_id` 的产品补写或覆盖 fallback。

当前 `meta_ad_realtime_daily_campaign_metrics` 没有 `product_id` 字段。本期不强行给 realtime 表加产品列，避免扩大风险。当天刚推的产品如果 daily final 尚未生成，先通过 `created_at_fallback` 进入新品；等日终三层广告匹配完成后再更新为真实 `ad_match` 日期。

## API 设计

优先扩展现有 `GET /order-analytics/realtime-overview`，新增参数：

| 参数 | 取值 | 含义 |
| --- | --- | --- |
| `product_launch_scope` | `new` | 新品分析 |
| `product_launch_scope` | `old` | 老品数据 |
| `product_launch_scope` | `unmatched` | 未匹配产品 |

不传该参数时，实时大盘现有行为不变。

### scope 语义

`new` / `old`：

- 订单、订单盈亏、产品销量：限定 `dianxiaomi_order_lines.product_id IN (...)`。
- 日终广告：限定 `meta_ad_daily_*_metrics.product_id IN (...)`。
- 实时 Campaign 广告：如果只能按 campaign 名解析产品，则沿用现有实时大盘匹配逻辑过滤；如果未能稳定得到产品集合内的 `product_id`，不得回退展示全量广告。
- 顶部 KPI 的销售额、运费、订单数、利润等全部只统计 scope 内产品。
- `scope.product_launch_scope` 返回当前 scope，`scope.product_ids` 可返回产品数量或压缩摘要，避免大列表撑爆响应。

`unmatched`：

- 广告侧限定 `product_id IS NULL` 或无法解析到产品的广告行。
- 订单、订单盈亏限定 `dianxiaomi_order_lines.product_id IS NULL` 的订单行；这类订单属于“未匹配产品”，必须进入未匹配子 Tab，确保 `新品 + 老品 + 未匹配产品` 与不传 `product_launch_scope` 的实时大盘全量订单侧合计一致。
- 产品销量统计可用一个“未匹配产品”汇总行承载 `product_id IS NULL` 的订单销量；如果 UI 无法稳定展示该行，至少不能影响顶部 KPI、订单明细、订单盈亏明细的守恒。
- Campaign / Ad Set / Ad 明细展示未匹配广告，并沿用现有未分摊广告费提示。
- 顶部 KPI 中广告费、Meta 购买金额、Meta ROAS、`product_id IS NULL` 订单销售额和真实 ROAS 都按同一未匹配 scope 展示；数据质量提示应说明该 scope 是订单未匹配产品与广告未匹配产品的合并排查口径。

## UI 设计

一级 Tab：

- 在「实时大盘」右侧新增 `新品投放分析`。

新品投放分析内部：

- 顶部沿用实时大盘的日期范围、店铺筛选、数据质量条和 KPI 卡。
- 实时大盘顶部新品 / 老品卡片说明必须直接展示业务口径：新品为“上广告时间近 7 天内”，老品为“上广告时间 7 天前”，并保留范围产品数量，避免只显示 `product_launch_scope` 技术参数。
- 增加三段式子 Tab：
  - `新品分析`
  - `老品数据`
  - `未匹配产品`
- 切换子 Tab 时，保留当前日期范围和店铺筛选，只改变 `product_launch_scope`。
- 默认进入 `新品分析`。
- 产品搜索框在新品投放分析中暂不作为主要入口；若复用现有产品搜索，则它与 `product_launch_scope` 取交集。首版可以不显示产品搜索，避免用户误解为全量大盘搜索。

明细区域：

- 订单明细、订单盈亏明细、产品销量统计、广告计划数据结构复用实时大盘。
- ROAS 走势也要实现；如果现有 `roi_daily_roas_nodes` 无法按新品/老品/未匹配拆分，后端应走明细聚合生成同口径节点，不能展示全量实时大盘走势冒充 scope 走势。
- 未匹配产品子 Tab 的订单类明细为空时，显示现有空态；广告计划表必须能看到未匹配广告。

## ROAS 走势策略

现有 `roi_daily_roas_nodes` 是双店全量预聚合节点，不包含新品/老品/未匹配 scope。

本期策略：

1. 全量实时大盘继续使用 `roi_daily_roas_nodes`。
2. `product_launch_scope` 非空时，不复用全量节点。
3. 对 `new` / `old`：按当前日期范围和产品集合，从订单明细与广告明细实时聚合 24 个节点。
4. 对 `unmatched`：只有广告费节点，订单收入为 0，真实 ROAS 为 `null`；Meta ROAS 可按未匹配广告的 Meta purchase value / spend 计算，若前端图只支持 true ROAS，则显示空或 `null` 点。
5. 单店筛选继续遵守 `docs/superpowers/specs/2026-05-09-realtime-dashboard-store-filter.md`：不得读取双店 scope 的预聚合节点。

## 数据质量

`/order-analytics/realtime-overview` 仍必须返回顶层 `data_quality`。

新增 scope 后，`data_quality` 至少要能体现：

- `product_launch_scope`。
- 新品/老品产品集合生成时间。
- 上广告时间表是否存在 fallback 数据。
- 未匹配产品子 Tab 中未匹配广告费总额。
- 当 scope 下广告费无法完全归因到订单时，状态应为 `warning`，不能标成无条件 `ok`。

## 非目标

- 不重做实时大盘整体 UI。
- 不改变 Meta 业务日 16:00 切日统计口径。
- 不改变利润公式、采购价、物流成本、Shopify fee 计算方式。
- 不给 `meta_ad_realtime_daily_campaign_metrics` 强行新增 `product_id` 字段；如后续需要实时广告级产品归因，再单独设计。
- 不在本期解决所有历史未匹配广告的产品归因；未匹配先单独展示。

## 验收标准

1. 进入 `数据分析 -> 新品投放分析` 默认展示 `新品分析`。
2. 新品/老品按北京时间今天 00:00 与 `ad_launch_date` 差值判断，不随查询历史日期范围变化。
3. 有真实广告匹配记录的产品，回填为最早 `meta_business_date/report_date`。
4. 无真实广告匹配记录的产品，回填为 `DATE(media_products.created_at)`。
5. fallback 产品后续真实匹配到广告后，更新为真实匹配日期。
6. `ad_match` 产品不会被更晚广告记录覆盖。
7. 新品、老品、未匹配产品三个子 Tab 的广告费互不污染。
8. 未匹配广告不进入新品或老品 KPI，但能在「未匹配产品」里看到广告费和广告计划。
9. ROAS 走势在 scope 模式下不显示全量实时大盘节点。
10. 现有实时大盘不传 `product_launch_scope` 时行为不变。

## 测试建议

后端：

- 新增 `tests/test_order_analytics_product_ad_launch_dates.py`
  - 最早真实匹配日期优先于产品创建日期。
  - 无广告匹配时使用产品创建日期。
  - fallback 可被真实广告匹配覆盖。
  - `ad_match` 不被更晚记录覆盖。
- 扩展实时大盘测试：
  - `product_launch_scope=new` 只统计新品产品。
  - `product_launch_scope=old` 只统计老品产品。
  - `product_launch_scope=unmatched` 只统计未匹配广告，订单侧为空。
  - 店铺筛选与 launch scope 同时存在时，按交集过滤。
  - `data_quality` 顶层存在且包含 scope 信息。

前端：

- 模板静态测试覆盖一级 Tab `新品投放分析` 与三个子 Tab。
- JS 请求参数包含 `product_launch_scope`。
- 切换子 Tab 不重置日期范围和店铺筛选。

建议回归命令：

```bash
pytest tests/test_order_analytics_realtime_site_filter.py \
       tests/test_order_analytics_true_roas.py \
       tests/test_order_analytics_data_quality.py \
       tests/test_order_analytics_template_layout.py \
       tests/characterization/test_order_analytics_baseline.py -q
```
