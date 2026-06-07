# 实时大盘未匹配明细移动端与商品信息增强

## 背景

`/order-analytics/realtime-unmatched-orders` 和 `/order-analytics/realtime-unmatched-ads` 已经能从实时大盘「未匹配广告和订单」卡片打开单独明细页。移动端当前仍按宽表展示，Safari/手机视口下表头和数据列容易视觉错位，且明细只显示订单标题或 campaign 信息，缺少商品主图和中文名，排查数据来源不够直接。

## 锚点

- [2026-06-07-realtime-unmatched-detail-pages-design.md](2026-06-07-realtime-unmatched-detail-pages-design.md)：两个未匹配明细页和数据接口的基础口径。
- [2026-05-09-realtime-dashboard-store-filter.md](2026-05-09-realtime-dashboard-store-filter.md)：店铺筛选必须透传并由统一白名单校验。
- [2026-06-01-ad-allocation-label-clarity-design.md](2026-06-01-ad-allocation-label-clarity-design.md)：未匹配广告仅包含 `allocation_reason=unmatched_product`，不包含 `matched_no_units`。
- [2026-05-19-mingkong-product-library-assets-design.md](2026-05-19-mingkong-product-library-assets-design.md)：`dianxiaomi_product_assets` 提供商品主图、中文名和英文标题。
- [2026-05-19-meta-hot-posts-product-title-translation-design.md](2026-05-19-meta-hot-posts-product-title-translation-design.md)：商品标题翻译使用统一 LLM 调用和 OpenRouter Gemini Flash-Lite 口径。
- [appcore/order_analytics/CLAUDE.md](../../../appcore/order_analytics/CLAUDE.md)：实时大盘业务日、店铺筛选与数据质量硬规则。

## 范围

做：

- 两个未匹配明细页移动端改为卡片式列表；桌面端保留表格。
- 移动端卡片按字段名展示，避免表头和数据列错位。
- 订单和广告明细行增加商品主图、商品中文名、原始标题、翻译来源。
- 商品信息优先从已有系统数据补齐：
  1. `dianxiaomi_product_assets`
  2. `product_name_dictionary`
  3. `media_products`
  4. 当前明细行自带标题 / SKU / campaign code
- 如果系统里没有中文名，但有英文商品标题，则只对当前页缺中文名的标题做批量翻译。
- 翻译统一走 `appcore.llm_client`，use case 注册为 `order_analytics.unmatched_title_translate`。
- 翻译 provider 固定 `openrouter`，model 固定 `google/gemini-3.1-flash-lite`。

不做：

- 不新增数据库表或 migration。
- 不改变广告匹配、广告分摊或实时大盘汇总公式。
- 不把 `matched_no_units` 纳入未匹配广告页。
- 不在页面加载时扫描全库或批量翻译非当前页数据。
- 不把翻译结果写回生产数据表；当前页接口返回翻译结果即可。

## 数据增强口径

接口返回的每行增加：

```text
product_image_url
product_image_object_key
product_image_local_url
product_cn_name
product_title
product_title_zh_source
product_code_hint
```

### 未匹配订单

订单仍以 `dianxiaomi_order_lines.product_id IS NULL` 为准。增强信息从当前行的 `skus`、`product_names` 提取候选 key：

- `skus` 可作为 SKU / product code hint。
- `product_names` 作为英文标题 fallback。
- 优先用 `dianxiaomi_product_assets.product_code/product_name/product_english_title/product_url` 或 `product_name_dictionary.product_code` 命中。
- 命中失败时，如果 `product_names` 非空且不是中文，则调用 OpenRouter `google/gemini-3.1-flash-lite` 翻译为简体中文。

### 未匹配广告

广告仍只展示 `allocation_reason=unmatched_product`。增强信息从 `normalized_campaign_code`、`campaign_name` 提取候选 key：

- `normalized_campaign_code` 作为 product code hint。
- campaign name 作为英文标题 fallback。
- 命中失败时，如果 campaign name 非空且不是中文，则调用 OpenRouter `google/gemini-3.1-flash-lite` 翻译为简体中文。

## 前端

桌面端：

- 保留表格。
- 首列展示商品主图 + 中文名 / 英文标题 / code hint。

移动端：

- 隐藏 `<table>` 宽表，改用 `.rud-mobile-list` 渲染卡片。
- 每张卡片顶部显示商品主图、中文名、英文标题。
- 关键字段用 label/value 成对展示，确保字段名和值一一对应。
- 分页、刷新和 KPI 保持可用。

## 验证

必跑：

```bash
pytest tests/test_order_analytics_true_roas.py \
       tests/test_order_analytics_template_layout.py \
       tests/test_llm_use_cases_registry.py -q
```

手动：

1. 手机宽度访问两个页面，不能出现表头和数据错位。
2. 订单页每行能看到商品主图占位或图片、中文名或翻译中文名。
3. 广告页仍不展示 `matched_no_units`。
4. 缺中文名时翻译调用参数为 `provider_override=openrouter`、`model_override=google/gemini-3.1-flash-lite`。
