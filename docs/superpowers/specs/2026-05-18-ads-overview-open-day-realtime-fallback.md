# 2026-05-18 — 广告分析概览未收盘日实时兜底

## 背景

`/order-analytics` 的「广告分析 -> 概览」在用户选择当天业务日时，只通过
`/order-analytics/ad-summary` 读取 `meta_ad_daily_campaign_metrics`。
未收盘业务日的日终广告表尚未生成，但 `meta_ad_realtime_daily_campaign_metrics`
已经有 hourly snapshot，因此页面会误显示「暂无广告数据，请先上传 Meta 广告报表」。

## 决策

- 显式日期范围包含当前 Meta 业务日时，`get_meta_ad_summary` 对历史已收盘日期继续读取
  `meta_ad_daily_campaign_metrics`，对当前未收盘业务日追加
  `meta_ad_realtime_daily_campaign_metrics` 的每个广告户最新 snapshot。
- 当前业务日即使存在误写入的 daily-final 行，也以 realtime snapshot 为准，避免未收盘 partial
  日终数据压过真实实时数据。
- Realtime campaign 表没有 `product_id` / `matched_product_code`，进入概览前用现有
  `resolve_ad_product_match` 规则补齐产品匹配；匹配失败的 campaign 进入「未匹配广告系列」。
- Ad Set / Ad 级没有 realtime 表，本次不改。

## 回归

新增测试覆盖：当天没有日终数据但有 realtime snapshot 时，「概览」返回 campaign 行和未匹配行，
不再空列表。
