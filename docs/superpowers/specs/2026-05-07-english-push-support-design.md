# 推送管理英语推送放开设计

**日期**：2026-05-07
**状态**：已确认，实施中
**相关文档**：
- `docs/superpowers/specs/2026-04-18-push-management-design.md`
- `docs/superpowers/specs/2026-04-21-autopush-multilang-text-push-design.md`
- `docs/明空素材推送接口.md`

## 背景

推送管理首版只把非英语素材纳入列表、统计、投放链接补推和产品文案补推。现在业务要求是：英语素材具备条件以后也可以推送，并且产品维度的“推送文案”“推送链接”也要包含英语数据。

## 目标

1. `/pushes` 推送管理列表不再排除 `media_items.lang='en'`，英语素材满足就绪条件后进入 `pending`。
2. 任务统计不再排除英语素材，提交数、已推送数、未推送数和推送率都包含英语。
3. 英语素材的 `lang_supported` 自动视为满足，不要求写入 `media_products.ad_supported_langs`；非英语仍按 `ad_supported_langs` 判定。
4. 产品维度“推送文案”的 `texts` 包含英语首条文案和其他启用语种首条文案。
5. 产品维度“推送链接”和素材推送 payload 的 `product_links` 包含英语产品链接和其他启用语种产品链接。

## 设计

### 推送列表与状态

`appcore.pushes.list_items_for_push()` 的基础查询移除 `i.lang <> 'en'` 条件。列表语言筛选下拉展示所有启用语种，包括 `en`。

英语素材的就绪条件：

- `is_listed`：产品上架。
- `has_object`：视频文件存在。
- `has_cover`：封面存在。
- `has_copywriting`：产品有英语文案记录。
- `lang_supported`：英语固定为 `True`。
- `has_push_texts`：英语 `idx=1` 文案能解析出标题、文案、描述。
- `shopify_image_confirmed`：英语沿用现有逻辑，自动通过。

### 推送文案

产品维度 `build_product_localized_texts_push_preview()` 继续复用 `resolve_localized_texts_payload()`，但后者不再过滤 `en`。输出顺序按 `media_languages` 的启用语种顺序；每个语种只取首条文案，字段不完整的语种跳过。

### 推送链接

`build_product_links_push_preview()` 和 `build_item_payload()` 使用所有启用语种生成链接，包含英语。英语 URL 使用 `product_link_domains.build_product_page_url()` 的既有规则，即：

```text
https://<domain>/products/<product_code>
```

非英语继续使用：

```text
https://<domain>/<lang>/products/<product_code>
```

旧的 `build_material_push_payload()` 也需要在请求某个语种 payload 时至少包含英语链接；非英语请求包含英语链接和当前语种链接，英语请求只包含英语链接。

## 非目标

- 不改下游接口地址、鉴权方式或响应判定。
- 不要求把英语写入 `ad_supported_langs`。
- 不改变英语详情图替换逻辑；英语仍不需要 Shopify 图片替换确认。

## 验收

1. 推送管理列表可出现 `lang=en` 的素材，且满足条件时状态为 `pending`。
2. 产品“推送文案”预览和 JSON 中能看到英语文案。
3. 产品“推送链接”预览和 JSON 中能看到英语产品链接。
4. 素材推送 payload 的 `product_links` 包含英语产品链接。
5. 聚焦测试通过：
   `pytest tests/test_appcore_pushes.py tests/test_product_link_push.py tests/test_openapi_push_items_service.py tests/test_pushes_routes.py tests/test_pushes_stats.py -q`
