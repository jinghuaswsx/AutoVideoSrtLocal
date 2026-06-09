# 产品链接 GET 参数污染修复

- 日期：2026-06-09
- 锚点：`docs/superpowers/specs/2026-06-09-product-link-query-param-repair-design.md`
- 范围：`appcore/product_link_domains.py`、`web/services/openapi_shopify_localizer.py`、`tools/shopify_image_localizer/rpa/run_product_cdp.py`、生产库 `media_products.localized_links_json`、产品链接补推
- 上位文档：
  - `docs/superpowers/specs/2026-05-09-product-link-management-modal.md`
  - `docs/superpowers/specs/2026-06-03-pushes-material-text-link-chain-design.md`
  - `docs/明空素材推送接口.md`

## 背景

素材管理中近期新增产品的多语种商品链接出现 `?variant=<id>` GET 参数，例如：

`https://newjoyloo.com/fr/products/balance-spaceman-stacking-game-rjc?variant=45910969090221`

产品链接用于投放、明空素材推送、Shopify Image Localizer 链接校验和产品链接管理弹窗。该链路要求产品页 URL 是稳定的产品 handle 页面，不应携带 variant、utm 或其它 GET 参数。

## 只读核查结果

2026-06-09 对生产库所有未删除产品按「产品 × 当前启用语种 × 产品启用域名」解析实际推送链接后：

- 扫描产品数：275
- 启用语种：`en,de,fr,es,it,ja,nl,pt,sv`
- 命中 GET 参数链接：15 条
- 受影响产品：8 个
- 命中的 query key：全部为 `variant`

受影响产品：

| product_id | product_code | 问题语种 |
| --- | --- | --- |
| 325 | `balance-spaceman-stacking-game-rjc` | `fr, es, it` |
| 704 | `instant-snap-iodine-swabs-rjc` | `it` |
| 716 | `rechargeable-sensor-control-headlamp-rjc` | `de` |
| 730 | `beginner-friendly-floral-tips-rjc` | `de, fr` |
| 738 | `edgepro-compact-tungsten-blade-sharpener-rjc` | `de, fr, es` |
| 742 | `usb-rechargeable-led-motion-sensor-cabinet-light-rjc` | `de` |
| 750 | `3-in-1-ultimate-caulking-tool-rjc` | `de, it` |
| 754 | `2-in-1-adjustable-seam-guide-with-built-in-seam-ripper-rjc` | `fr, it` |

历史推送记录中，除 product_id `742` 尚无素材推送日志外，其余 7 个产品均存在成功素材推送，且对应 `media_push_logs.request_payload.product_links` 中已经包含上述 `?variant=` 链接。

## 根因

Shopify Image Localizer 在详情图替换后会校验素材库商品链接详情页。当前实现中：

1. 先校验素材库返回的无 query 产品链接。
2. 若校验不通过，会尝试把 Shopify 默认 variant id 拼到链接上。
3. 如果 variant 链接校验通过，就调用 `/openapi/medias/shopify-image-localizer/product-link` 保存该链接。
4. 服务端保存接口将 `link_url` 原样写入 `media_products.localized_links_json`。
5. 推送链路 `build_product_links_push_preview()` / `build_item_payload()` 再从 `localized_links_json` 原样取出 URL，导致污染进入明空/下游。

## 目标

1. 产品页链接在入库、解析、推送前都必须规范化为无 query、无 fragment 的产品页 URL。
2. Shopify Image Localizer 允许临时用 `?variant=` 做前台校验，但不得把 variant URL 保存回素材库。
3. 修复生产库既有污染数据，只剥离 query/fragment，不改变 scheme、host、path、语种和产品 handle。
4. 对受影响产品重新执行产品链接补推，把修复后的链接同步到下游。

## 实施

### 源头修复

- 在 `appcore.product_link_domains` 增加产品页 URL 规范化 helper：
  - 输入必须保留 `scheme/netloc/path/params`。
  - 输出剥离 `query` 和 `fragment`。
  - 空值或非法值保持为空，避免引入新链接。
- `resolve_product_page_url_rows()` 对来自 `localized_links_json` 的 URL 做规范化后再返回，保证所有下游统一拿到无 query 链接。
- `web.services.openapi_shopify_localizer.build_shopify_localizer_product_link_save_response()` 保存前规范化 `link_url`，返回值也使用规范化后的 URL。
- `tools.shopify_image_localizer.rpa.run_product_cdp` 在 variant 链接校验通过时，保存原始 canonical 链接，而不是保存 variant 链接；结果中保留 `verified_variant_url` 供诊断。

### 生产数据修复

- 扫描 `media_products.localized_links_json` 的所有字符串或按域名嵌套 URL。
- 对有 query/fragment 的产品页 URL 执行同一规范化，仅更新实际变化的产品。
- 修复后再次全量扫描实际推送链接，要求 GET 参数命中数为 0。

### 补推

- 对受影响产品逐个调用 `appcore.pushes.push_product_links(product)`，与素材管理产品行「推送链接」按钮后端逻辑一致。
- 记录每个 product_id 的 payload、上游 HTTP 状态、下游响应和 ok 状态。
- 任一补推失败必须单独列出，不隐式吞掉。

## 验证

- focused tests：
  - `tests/test_shopify_image_localizer_batch_cdp.py`
  - `tests/test_openapi_shopify_localizer.py`
  - `tests/test_product_link_domains.py`
  - push 相关测试由 `scripts/pytest_related.py --base origin/master --run` 选择
- 生产只读复扫：
  - 所有未删除产品实际推送链接中 `urlparse(url).query == ""`
  - 受影响产品的 `build_product_links_push_preview(product).payload.product_links` 不含 `?`
- 补推验收：
  - 8 个产品逐个 `ok=true`
  - payload 中不含 GET 参数

## 非目标

- 不改变产品启用域名配置。
- 不改变 `media_products.localized_links_json` schema。
- 不重新推送视频素材本体；本次只重新推送产品链接。
- 不重启线上服务，除非用户明确要求发布/上线。
