# 产品盈亏：未匹配广告费跳转明细设计

## 背景

产品盈亏看板 Tab ① 的 summary 已展示 `unallocated_ad_spend_usd`，但只能看到金额，不能直接定位到对应的未匹配广告计划。Tab ④ 已有未匹配 campaign 表，但当前前后端都要求先选具体产品，导致全局未匹配广告无法从 summary 直接排查。

## 目标

在 `/product-profit` 的产品列表 summary 中，把“未匹配广告”金额做成可点击入口。点击后保留当前日期和国家筛选，切到 Tab ④，并展示该筛选范围内所有未匹配广告的 campaign 列表。

## 交互

1. 当 `summary.unallocated_ad_spend_usd > 0` 时，“未匹配广告”显示为按钮样式的文本入口。
2. 点击入口后切到 `tab=ads`，进入全局未匹配模式，不要求选择具体产品。
3. Tab ④ 自动展开“未匹配 campaign”区域，展示 campaign code、campaign name、账户、花费、结果、Meta purchase value、Meta ROAS、最后出现日期和操作列。
4. 若用户当前选择了具体产品，直接点击 Tab ④ 仍保持现有单产品广告明细模式。
5. 全局未匹配模式下，“配对到本产品”按钮不展示；没有具体产品时仍不能执行手动配对。

## 数据口径

1. 全国家口径读取 `meta_ad_daily_campaign_metrics` 中 `product_id IS NULL` 的 campaign 日数据，按 `COALESCE(meta_business_date, report_date)` 过滤日期。
2. 单国家口径读取 `meta_ad_daily_ad_metrics` 中 `product_id IS NULL` 且 `market_country = country` 的 ad 日数据，再按 normalized campaign 聚合。
3. 使用 `resolve_ad_product_match` 对 `product_id IS NULL` 的 code 做一次实时兜底；能解析到产品的广告不进入全局未匹配列表，避免把同步 race condition 误报为未匹配。
4. 返回结构复用 `/order-analytics/product-profit/ads.json`，全局模式下 `accounts`、`campaigns`、`daily` 为空，`unmatched` 承载明细。
5. API 顶层继续带 `data_quality`，沿用产品盈亏数据质量护栏。

## 非目标

- 不改手动匹配写入逻辑。
- 不新建单独页面。
- 不接入本机 MySQL 验证；数据库相关验证仍以测试或服务器环境为准。

## 验收

1. `tests/test_product_profit_ads.py` 覆盖全局未匹配聚合、国家过滤 SQL 和可解析 code 排除。
2. `tests/test_product_profit_routes.py` 覆盖 `ads_scope=unmatched` 不要求 `product_id`，并调用全局未匹配查询。
3. `tests/test_product_profit_dashboard_assets.py` 覆盖 summary 入口、URL 参数、全局未匹配模式和前端不展示错误空态。
