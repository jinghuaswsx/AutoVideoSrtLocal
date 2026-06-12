# 2026-06-12 — 广告预警审查整改

## 背景

广告预警模块已按 `2026-06-11-ad-alert-module-design.md`、`2026-06-12-ad-alert-problem-ads-subtabs-design.md`、`2026-06-12-ad-alert-ad-level-design.md` 和 `2026-06-12-ad-alert-top-losing-ads-design.md` 落地，但审查发现几类会影响运营判断的问题：

1. 商品详情页的国家/语种列表可能展示不满足预警触发条件的缓存行。
2. 商品 × 语种趋势查询直接 join `media_items` 后聚合，未按广告指标行去重，可能在同一广告命中多个素材时放大 spend / purchase。
3. 单条 AD 详情页表头展示“成效（购买次数）”，但前端实际渲染了 `purchase_value_usd`。
4. 广告预警 API 序列化层不得假设服务层 dataclass 一定带有扩展字段，否则旧对象形态或测试桩会触发 500。

## 修复目标

1. 所有“广告预警”路径必须沿用同一触发条件：
   - `ad_roas < threshold`
   - `active_7d_ad_spend_usd > 0`
   - `ad_spend_usd > 0`
2. 商品详情页的国家/语种列表只展示满足上述条件的预警语种；非预警语种不得进入 `countries`。
3. 商品 × 语种趋势和 active window 查询必须按底层广告指标行去重后再聚合。日终表使用 `meta_ad_daily_ad_metrics.id` 作为去重 key，避免同一指标行因匹配多个素材被重复累加。
4. 单条 AD 详情的五个时间窗口必须同时返回：
   - `spend_usd`
   - `purchase_value_usd`
   - `result_count`
   - `roas`
5. 单条 AD 详情页“成效（购买次数）”必须渲染 `result_count`，购买金额只用于 ROAS 和趋势，不伪装成购买次数。
6. API 序列化 `top_losing_ads`、`evaluation_lang` 等扩展字段时必须兼容缺省值，服务层未返回该扩展字段时分别使用空列表或 `null`，不能让列表接口 500。

## 非目标

- 不新增数据库表。
- 不重做广告预警整体 UI。
- 不改变“问题广告”准入条件。
- 不新增自动关停能力。

## 验证

Focused tests：

```bash
/opt/autovideosrt/venv/bin/python -m pytest \
  tests/test_ad_alerts.py \
  tests/test_ad_alert_routes.py \
  tests/test_ad_alert_template.py \
  tests/test_ad_alerts_layered.py \
  tests/test_order_analytics_ads.py -q
```

全量 pytest 仅在发布 / 合并 / 用户明确要求时运行。
