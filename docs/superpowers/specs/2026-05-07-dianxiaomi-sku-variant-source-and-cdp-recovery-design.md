# 店小秘 SKU 变体来源与 CDP 恢复设计

日期：2026-05-07

## 背景

`3d-curved-screen-magnifier-for-smartphones-rjc` 在 Shopify 公开商品数据中有 9 个变体，但店小秘 Shopify 在线商品库接口 `shopifyProduct/pageList.json` 当前只返回 5 个 `variants`。现有 `tools/dianxiaomi_sku_sync.py` 直接信任该列表接口内嵌的 `variants`，导致 `media_product_skus` 只写入 5 行。

同一链路依赖服务端共享 Chromium CDP。最近 `shopifyid` / `dianxiaomi_sku` 同步出现过 Playwright `connect_over_cdp` 超时。CDP 连接不能长期卡住；如果共享浏览器需要重启，应自动重启一次并重试。重启失败或重启后仍不可访问时，必须把失败写入 `scheduled_task_runs`，让后台 admin 通过现有定时任务失败告警看到原因。

## 事实来源

- 共享浏览器运行层见 `docs/server_browser_runtime.md`。
- 店小秘 Shopify 在线商品库用于发现商品和店铺上下文，但其 `variants` 不保证完整。
- Shopify 公开商品 JSON 是变体完整性事实来源，优先用于 SKU 同步的 `variants`。
- 店小秘商品管理接口仍是 ERP SKU / 商品编码配对来源。

## 行为要求

1. `dianxiaomi_sku` 同步构建 Shopify product 时，不得只依赖 `shopifyProduct/pageList.json` 内嵌 `variants`。
2. 如果能通过公开 Shopify 商品 JSON 获取同一 `shopify_product_id` 的更多变体，应使用公开 Shopify 变体覆盖或补齐店小秘列表内的变体。
3. 未匹配到 ERP SKU 编码的 Shopify 变体也必须保留到配对结果中，前端显示为空 ERP 配对，而不是隐藏该变体。
4. CDP 连接必须有有界超时和恢复流程：
   - 先探测 `/json/version`。
   - Playwright 连接超时或 CDP 不可用时，针对 `127.0.0.1:9222` 的共享浏览器重启 `autovideosrt-browser.service` 一次。
   - 等待 CDP 恢复后重试连接一次。
   - 仍失败时抛出明确错误。
5. 如果 CDP 恢复失败、没有权限重启、重启命令失败或重启后仍不可访问，必须记录 `scheduled_task_runs` 失败记录。现有 `layout.html` 的定时任务失败 banner 会把该错误通知给 admin。
6. Web 后台手动刷新 SKU/英文名时，如果数据拉取失败，也应把失败写入 `dianxiaomi_sku` 的定时任务失败记录，避免只返回 502 而后台没有可追踪告警。
7. Web 后台手动刷新 SKU/英文名不得依赖常驻可视化页面的 JS execution context 完成接口请求。该刷新链路应优先使用浏览器上下文级请求复用店小秘登录 Cookie，避免用户手工操作、订单页自动刷新或店小秘页面跳转导致 `Page.evaluate: Execution context was destroyed`。

## 验收

- 构造一个店小秘列表只有 5 个变体、公开 Shopify JSON 有 9 个变体的样例，`plan_sync` 结果应有 9 个 variant pair。
- 公开 Shopify 变体价格如果是 Shopify cents 口径，应归一为数据库使用的美元小数。
- CDP 初次连接超时时，应调用一次 browser service restart，然后重试连接。
- browser service restart 不可用或重试仍失败时，应记录失败，并给出包含 CDP URL / service 名称 / 失败阶段的错误。
- 后台手动刷新捕获拉取失败时，应调用注入的失败记录函数，返回原有 502 响应。
- 后台手动刷新通过 CDP 拉取店小秘接口时，即使页面处于导航中，也不应因 `Page.evaluate: Execution context was destroyed` 直接失败。
