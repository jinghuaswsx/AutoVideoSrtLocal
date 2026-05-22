# Shopify ID 多域名自动同步设计

日期：2026-05-22

## 背景

Shopify 图片本地化工具按当前网站进入 Shopify Admin。一个素材产品在不同销售域名下会有不同 Shopify product ID，例如同一个 `product_code` 在 `newjoyloo.com` 和 `omurio.com` 分别对应不同 ID。

旧的 `tools/shopifyid_dianxiaomi_sync.py` 只把店小秘返回的 `handle -> shopifyProductId` 当作全局唯一映射，遇到同一个 handle 多个 ID 时记为 `remote_conflict` 并跳过。这会导致非默认域名缺少 `media_product_shopify_ids` 缓存，桌面工具只能报“未能解析 Shopify ID”。

## 目标

同步任务必须全自动处理 Shopify ID，不依赖用户在桌面 GUI 手动填写：

1. 先在店小秘页面触发“同步全部产品”，让店小秘先从 Shopify 拉最新商品数据。
2. 抓取店小秘在线商品库作为候选 product code 集合。
3. 对每个本地产品的启用域名分别访问公开 Shopify 商品 JSON。
4. 将解析到的 ID 写入 `media_product_shopify_ids(product_id, domain, shopify_product_id)`。
5. `media_products.shopifyid` 仅作为旧版默认域名兼容字段，不再作为多域名唯一事实来源。

## 数据流

1. 店小秘同步：
   - 复用现有 CDP 浏览器和店小秘登录态。
   - 保留 `_sync_all_shopify_products(page)`，并且默认先执行。
2. 店小秘分页：
   - 继续调用 `pageList.json` 获取在线 Shopify 商品。
   - 只使用 `handle` 识别哪些商品存在于店小秘在线商品库。
   - 同 handle 多个 `shopifyProductId` 不再阻断 per-domain 解析。
3. 域名解析：
   - 域名来源优先使用 `media_link_domains` 中全局启用的域名。
   - 对每个产品调用 `https://<domain>/products/<product_code>.js`。
   - 成功解析 `id` 后 upsert 到 `media_product_shopify_ids`。
4. 旧字段兼容：
   - 默认域名解析成功且 `media_products.shopifyid` 为空时，可以继续回填旧字段。
   - 已有且不一致时不覆盖，写入报告冲突。

## 错误处理

- 单个域名商品 JSON 404/403/网络失败：记录该产品该域名失败，不阻断其它域名和其它产品。
- 店小秘同步失败：任务失败，避免基于过期店小秘数据继续运行。
- Shopify 商品 JSON 返回 ID 非数字：跳过并记录失败。
- 所有报告继续写入 `output/shopifyid_dianxiaomi_sync/*.json`，新增 per-domain 统计。

## 验证

- 单元测试覆盖同一 handle 两个 Shopify ID 时仍能按域名写入两个缓存。
- 单元测试覆盖默认域名旧字段兼容更新。
- 单元测试覆盖 Shopify 商品 JSON 解析使用浏览器式请求头，避免公开 `.js` 被 403 拦截。
