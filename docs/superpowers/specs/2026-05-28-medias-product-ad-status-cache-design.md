# 素材管理产品投放汇总缓存设计

Date: 2026-05-28

## Context

`/medias/` 产品列表当前在 `/medias/api/products` 中批量返回素材数、语种覆盖和产品基础信息。语种覆盖只展示语种 chip，不展示对应语种的推送视频数、广告 ROAS 或产品投放状态。

列表页不能在每次请求时实时扫描订单、推送日志和广告明细，否则分页加载会变慢。项目已有 `media_push_status_cache` 的缓存化模式，本需求沿用“后台定时维护，列表只读缓存”的设计。

## Goals

1. 在“语种覆盖”列中只显示有视频素材的语种，一行一个。
2. 每个语种行展示该语种成功推送过的不同视频数，以及该语种视频对应广告的总 ROAS。
3. 如果某语种有视频素材但成功推送视频数为 0，`0` 必须使用醒目的红色大号样式。
4. “语种覆盖”列顶部新增“总体ROAS”，口径与数据分析统一：`(商品销售额 + 运费) / 产品相关广告消耗`。
5. 在“语种覆盖”列后新增“投放情况”列。
6. 产品列表筛选区新增“投放情况：全部 / 投放中 / 终止投放 / 未投”。
7. 列表接口保持快速：请求路径只批量读取缓存，缓存由 APScheduler 每小时刷新。

## Metrics

### Today Realtime Supplement

- 实时“今天”按每个 `ad_account_id` 最新 `realtime_partial` Meta 业务日取数，不直接等同于数据库 `CURDATE()`；同一 `(business_date, ad_account_id)` 已用实时快照时，daily 行要跳过，避免 open-day 数据重复计入。

缓存刷新口径需要把每个广告账号最新 `realtime_partial` Meta 业务日的实时快照并入广告消耗；该业务日可能因 Meta 账号时区不同而不等于数据库 `CURDATE()`：

- 产品级 `ad_spend_usd` 继续以 campaign 粒度汇总，历史读取 `meta_ad_daily_campaign_metrics`，今天读取 `meta_ad_realtime_daily_campaign_metrics`。
- 语种级 `ad_roas` 继续以 ad 粒度汇总，历史读取 `meta_ad_daily_ad_metrics`，今天读取 `meta_ad_realtime_daily_ad_metrics`。
- 实时表只取 `data_completeness='realtime_partial'` 的最新快照，并且必须按 `(business_date, ad_account_id)` 分组取 `MAX(snapshot_at)`，不能用单个全局最新时间。
- 如果某个环境尚未创建对应 realtime 表，缓存刷新必须自动降级为 daily-only SQL，避免定时任务失败；已建表环境继续并入今天实时数据。
- 列表请求仍只读 `media_product_ad_summary_cache` 和 `media_product_lang_ad_summary_cache`，不在请求内实时拉取 Meta。

### Per-Language Video Ad ROAS

语种广告 ROAS 按 `product_id + lang` 聚合：

```text
language_ad_roas = SUM(meta_ad_daily_ad_metrics.purchase_value_usd)
                 / SUM(meta_ad_daily_ad_metrics.spend_usd)
```

广告与视频素材匹配沿用现有素材广告详情口径：

- `media_items.product_id = meta_ad_daily_ad_metrics.product_id`
- 且广告名称或归一化广告名包含 `media_items.filename` 或 `media_items.display_name`
- 当素材文件名曾经调整导致名称不再精确包含时，允许用 `meta_ad_daily_ad_metrics.market_country` 映射到 `media_items.lang` 做语种兜底
- 只统计 `spend_usd > 0` 的广告行
- 同一个广告明细行如果匹配到同一语种多个素材，只计一次，避免重复加总

### Pushed Video Count

推送视频数为该 `product_id + lang` 下 `media_push_logs.status='success'` 的不同 `item_id` 数量。

### Overall ROAS

产品顶部“总体ROAS”使用数据分析统一口径：

```text
overall_roas = (SUM(order line amount) + SUM(shipping amount)) / SUM(product ad spend)
```

订单收入优先使用 `order_profit_lines` 的美元字段：

- `COALESCE(order_profit_lines.line_amount_usd, dianxiaomi_order_lines.line_amount, 0)`
- `COALESCE(order_profit_lines.shipping_allocated_usd, dianxiaomi_order_lines.ship_amount, 0)`

广告消耗使用产品维度广告数据：

- `meta_ad_daily_campaign_metrics.product_id`
- `spend_usd > 0`

### Delivery Status

产品投放情况按产品广告消耗判断：

- `投放中`：产品有任意广告消耗，且最新同步的实时广告快照在最近 6 小时内有有效消耗
- `终止投放`：产品有历史广告消耗，但最新同步的实时广告快照最近 6 小时内无有效消耗
- `未投`：产品没有任何广告消耗

“最近 6 小时”只看 `meta_ad_realtime_daily_campaign_metrics` / `meta_ad_realtime_daily_ad_metrics` 的最新同步快照：`snapshot_at >= NOW() - INTERVAL 6 HOUR` 且 `spend_usd > 0`。历史日终表仍参与总消耗和 ROAS 计算，但不再让产品进入 `投放中`。

## Data Model

新增两张缓存表：

### `media_product_ad_summary_cache`

产品级缓存，用于“总体ROAS”和“投放情况”筛选：

- `product_id` primary key
- `order_revenue_usd`
- `shipping_revenue_usd`
- `total_revenue_usd`
- `ad_spend_usd`
- `active_7d_ad_spend_usd`（历史字段名沿用；当前含义为最近 6 小时最新实时快照里的有效消耗）
- `overall_roas`
- `delivery_status`: `active | stopped | never`
- `computed_at`, `created_at`, `updated_at`

### `media_product_lang_ad_summary_cache`

产品语种级缓存，用于“语种覆盖”列：

- `(product_id, lang)` primary key
- `item_count`
- `pushed_video_count`
- `ad_spend_usd`
- `purchase_value_usd`
- `ad_roas`
- `active_7d_ad_spend_usd`（历史字段名沿用；当前含义为最近 6 小时最新实时快照里的有效消耗）
- `computed_at`, `created_at`, `updated_at`

## Request Flow

1. `appcore.medias.list_products()` 接收 `delivery_status` 筛选参数。
2. 当筛选不是 `all` 时，SQL 通过 `media_product_ad_summary_cache` 过滤产品。
3. `web.services.media_products_listing.build_products_list_response()` 对当前页产品 ID 批量读取两张缓存。
4. `_serialize_product()` 把缓存序列化为：
   - `ad_summary`
   - `lang_ad_summary`
5. 前端 `medias.js`：
   - “语种覆盖”列顶部显示总体 ROAS
   - 语种行只渲染 `items > 0` 的语种
   - 推送数为 0 时高亮
   - 新增“投放情况”列和筛选参数

## Scheduler

新增 APScheduler job：

- code: `media_product_ad_status_cache_refresh`
- schedule: 每小时
- runner: `appcore.media_product_ad_status_cache_scheduler.tick_once`
- task registry: `appcore/scheduled_tasks.py`

刷新失败写入 `scheduled_task_runs`，不阻塞列表读取。列表读取到缺失缓存时显示 `-` / `未投`，不在请求内实时回算全量数据。

## Non-Goals

- 不改变推送流程和广告匹配规则。
- 不在列表请求中实时拉取 Meta 或店小秘数据。
- 不加入排序，首版只提供筛选。
- 不按语种拆订单收入；语种行只展示广告 ROAS。

## Verification

- 单元测试覆盖缓存聚合、状态分类、ROAS 计算。
- 列表服务测试覆盖缓存数据传入 serializer。
- `medias.list_products()` SQL 测试覆盖投放情况筛选。
- 静态前端测试覆盖新增筛选框、请求参数、列标题和高亮 class。
- Scheduler 测试覆盖任务登记和每小时注册。
