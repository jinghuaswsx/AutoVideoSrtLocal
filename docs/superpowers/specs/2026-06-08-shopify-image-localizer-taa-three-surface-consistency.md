# 2026-06-08 Shopify Image Localizer TAA Three-Surface Consistency Fix

## 背景

`instant-snap-iodine-swabs-rjc` 意大利语替换后出现严重不一致：

- 链接检查页与买家前台运行态显示详情图已替换完成。
- Shopify Translate & Adapt 官方后台 `shopLocale=it` 的意大利语详情 HTML 仍显示英文图。
- Shopify 产品 `.js` 持久化数据中的意大利语 `description/body_html` 仍保留旧英文外链图。

这会让运营误判替换状态，并且让自动化在 TAA 未真正保存目标语种详情图时仍显示完成。

## 目标

1. 素材管理库的英语详情图、TAA 目标语种 `body_html`、产品前台链接实际详情图必须统一。
2. TAA 替换成功必须以目标语种 `editable_body_html` 中的新图 URL 保存并可读回为准。
3. 前台校验不能只看运行态插件替换效果；必须校验 Shopify 产品 `.js` 的持久化 `description/body_html`。
4. 如果 TAA 里存在参与替换的详情图，但替换结果 `expected_total=0` 或 `replacement_count=0`，任务必须失败，不能以 `expected=0/0` 通过。

## 验收标准

打开以下 Shopify 官方 TAA 页面：

`https://admin.shopify.com/store/0ixug9-pv/apps/translate-and-adapt/localize/product?highlight=handle&id=8603069350061&shopLocale=it`

右侧意大利语详情页中的详情图必须是正确的意大利语图片，不得继续显示英文图片。

## 设计约束

- 不依赖 link-check 的前台运行态结论来覆盖 TAA 保存失败。
- 不因为 EZ Product Image Translate 插件在买家页运行时换图成功，就判定 TAA 持久化成功。
- 保存后当前 TAA 会话读回、reload 诊断读回、Shopify `.js` 持久化层校验都必须围绕同一批新图 URL。
- 对于 TAA `body_html` 中的旧外链图，应通过 token、source index、位置兜底或视觉兜底找到素材管理库对应本地化图；找不到时必须报告失败。

## 涉及文件

- `tools/shopify_image_localizer/rpa/taa_cdp.py`
- `tools/shopify_image_localizer/rpa/run_product_cdp.py`
- `tests/test_shopify_image_localizer_batch_cdp.py`

## 验证

- `pytest tests/test_shopify_image_localizer_batch_cdp.py -q`
- 发布新 EXE 前按 `tools/shopify_image_localizer/CLAUDE.md` 与 `docs/superpowers/specs/2026-05-24-shopify-image-localizer-release-standard-fix.md` 执行完整发布验证。

## 7.2 Exact Product Link Gate

The 7.2 release must verify the exact material-library product link returned by
the bootstrap API, not only Shopify `.js` and not only a successful variant URL.

For the incident product the bootstrap `link_url` is:

`https://newjoyloo.com/it/products/instant-snap-iodine-swabs-rjc`

The run is successful only when that exact page contains the same newly uploaded
detail-image filenames as TAA and contains no old external `wxalbum` detail
image URLs. If `?variant=...` passes while the bootstrap link fails, the run
must fail and report the product-link verification gap.
