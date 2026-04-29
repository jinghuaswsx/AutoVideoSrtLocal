# 店小秘订单明细导入设计

最后更新：2026-04-28

## 目标

在订单数据分析模块中新增一条店小秘订单明细导入链路，从店小秘后台抓取 `2026-01-01` 到 `2026-04-28` 的订单数据，先可靠落库，后续再逐步接入现有分析视图。

本次导入范围：

- 只保留 NewJoy 和 omurio 两个站点对应 Shopify ID 的订单数据。
- 排除 SmartGearX 站点数据。
- 优先抓订单明细，包含地区、运费、商品定价金额、含运费销售额等字段。
- Listing 销量报表只作为补充和校验，不作为主数据源。

## 已确认的店小秘页面和接口

`数据 -> Listing 销量` 页面：

- 页面地址：`https://www.dianxiaomi.com/web/stat/salesStatistics`
- 列表接口：`/api/stat/product/statSalesPageListNew.json`
- 导出接口：`/api/stat/exportStatSalesDailyExcelNew.json`
- 异步进度接口：`/api/checkProcess.json`

Listing 销量页导出是产品维度汇总，字段包括 `productId`、店铺、付款订单数、付款销售量、付款销售额、均摊金额、退款订单数、退款金额等。它能解释金额口径：

- 付款销售额 = 产品售价 * 付款数量
- 均摊金额 = (产品售价 + 运费) * 产品数量

但 Listing 导出没有足够细的订单行信息，因此不作为主导入路径。

`订单 -> 订单处理` 页面：

- 页面地址：`https://www.dianxiaomi.com/web/order/paid`
- 明细接口：`/api/package/list.json`
- 利润和物流费用补充接口：`/api/orderProfit/getOrderProfit.json`

`/api/package/list.json` 返回订单级和商品行级 JSON，已经包含本次需要的主要字段：

- 店铺与平台：`shopId`、`platform`、`shopPlatform`
- 订单标识：`id`、`orderId`、`extendedOrderId`、`packageNumber`
- 时间：`orderCreateTime`、`orderPayTime`、`paidTime`、`shippedTime`
- 地区：`buyerCountry`、`countryCN`、`dxmPackageAddr.country`
- 订单金额：`orderAmount`、`orderUnit`、`orderAmountStr`
- 运费：`shipAmount`
- 退款：`refundAmountUsd`、`refundAmount`
- 商品行：`productList`、`cancelProductList`，其中包含 `productId`、`productSku`、`productSubSku`、`productDisplaySku`、`productName`、`productUrl`、`quantity`、`productCount`、`price`、`attrList`
- 原始完整 JSON：用于后续字段补充和口径修正

`/api/orderProfit/getOrderProfit.json` 可按页面返回的 package/order id 批量补充：

- `amount`
- `amountCNY`
- `logisticFee`
- `cost`
- `profit`
- `saleProfitMarginCny`
- `orderCommission`

## 推荐方案

采用“订单接口 JSON 直抓 + 原始 JSON 落库 + Listing 汇总校验”的方案。

抓取流程：

1. 连接服务器店小秘专用浏览器 CDP：`http://127.0.0.1:9223`。
2. 使用已登录会话访问店小秘订单页。
3. 从本地数据库读取 NewJoy 和 omurio 站点对应的 Shopify ID 集合。
4. 按天遍历 `2026-01-01` 到 `2026-04-28`。
5. 对每一天调用 `/api/package/list.json` 分页抓取订单。
6. 在本地过滤订单商品行：
   - 商品行 Shopify ID 或商品 URL 命中 NewJoy/omurio 对应集合则保留。
   - SmartGearX 相关商品 URL、shopify_id 或店铺标识直接跳过。
7. 对每页订单调用 `/api/orderProfit/getOrderProfit.json` 补充利润和物流费用字段。
8. 写入导入批次表和订单行明细表。
9. 可选抓取同日 Listing 汇总，用于后续校验订单行汇总和 Listing 页面汇总是否大体一致。

## 数据模型

新增 `dianxiaomi_order_import_batches`，记录每个导入批次和每天抓取状态。

建议字段：

- `id`
- `source`
- `date_from`
- `date_to`
- `status`
- `started_at`
- `finished_at`
- `duration_seconds`
- `requested_site_codes`
- `included_shopify_ids_count`
- `total_pages`
- `fetched_orders`
- `fetched_lines`
- `inserted_lines`
- `updated_lines`
- `skipped_lines`
- `error_message`
- `summary_json`
- `created_at`
- `updated_at`

新增 `dianxiaomi_order_lines`，保存订单商品行明细。

建议字段：

- `id`
- `batch_id`
- `source`
- `dxm_order_id`
- `dxm_package_id`
- `order_id`
- `extended_order_id`
- `package_number`
- `platform`
- `shop_id`
- `shop_name`
- `shop_platform`
- `site_code`
- `shopify_product_id`
- `product_url`
- `product_name`
- `product_sku`
- `product_sub_sku`
- `product_display_sku`
- `variant_text`
- `quantity`
- `unit_price`
- `line_amount`
- `order_amount`
- `order_currency`
- `ship_amount`
- `amount_with_shipping`
- `amount_cny`
- `logistic_fee`
- `profit`
- `refund_amount_usd`
- `refund_amount`
- `buyer_country`
- `buyer_country_name`
- `province`
- `city`
- `order_created_at`
- `order_paid_at`
- `paid_at`
- `shipped_at`
- `raw_order_json`
- `raw_line_json`
- `profit_json`
- `imported_at`
- `created_at`
- `updated_at`

唯一键建议：

- `uk_dxm_order_line (dxm_package_id, shopify_product_id, product_sku, product_sub_sku, variant_hash)`

原因：同一个订单可能有多个商品行，商品行也可能同 SKU 多变体；需要避免重复跑导入时重复插入。

## 金额口径

落库时保留原始字段，不提前做过度推导。

第一版标准化字段：

- `unit_price`：商品行 `price`
- `quantity`：商品行 `quantity`，缺失时回退 `productCount`
- `line_amount`：`unit_price * quantity`
- `ship_amount`：订单级 `shipAmount`
- `order_amount`：订单级 `orderAmount`
- `amount_with_shipping`：优先用订单级 `orderAmount`，后续分析可按订单行数量分摊
- `logistic_fee`：来自 `/api/orderProfit/getOrderProfit.json` 的 `logisticFee`

需要注意：Listing 页的“均摊金额”是 `(产品售价 + 运费) * 产品数量`，不是订单级总金额的简单别名。第一版不把它强行写入订单行主字段，只在后续校验/分析中单独处理。

## 脚本与运行方式

新增脚本：

- `tools/dianxiaomi_order_import.py`

命令形态：

```powershell
python tools/dianxiaomi_order_import.py --start-date 2026-01-01 --end-date 2026-04-28 --sites newjoy,omurio --browser-mode server-cdp --browser-cdp-url http://127.0.0.1:9223 --db-mode local
```

默认行为：

- 默认连接服务器测试/生产同一台店小秘浏览器环境，实际数据库由 `--env` 或部署命令决定。
- 默认分天抓取。
- 支持 `--resume`，跳过已成功完成的日期。
- 支持 `--dry-run`，只抓取并输出统计，不写入订单明细。
- 支持 `--include-listing-summary`，额外抓 Listing 汇总用于校验。

## Web 集成

第一版 Web 只做轻量入口，不重做订单分析页：

- 在 `/order-analytics` 的“订单导入”区域增加“从店小秘抓取订单明细”按钮。
- 默认日期显示 `2026-01-01` 到 `2026-04-28`。
- 站点固定显示 NewJoy、omurio。
- 后端接口触发脚本或调用同一导入服务函数，返回批次状态。

如果后台任务设施不够稳定，第一版允许先只提供脚本和只读批次状态接口，把数据抓取作为服务器命令执行。

## 错误处理

- 店小秘登录失效：返回明确错误，不写入半成品批次为成功。
- 某日分页失败：该日期批次标记 `failed`，保留错误和已抓页数，允许 `--resume` 重跑。
- 单条订单字段异常：保留 `raw_order_json`，标准化失败的行写入 `skipped_lines` 并记录原因。
- 重复导入：使用唯一键 upsert，不重复累加。
- SmartGearX 数据：过滤阶段跳过，并计入 `skipped_lines`/summary。

## 测试策略

单元测试：

- 店小秘订单 JSON 标准化：订单字段、商品行字段、金额字段、国家字段。
- NewJoy/omurio/SmartGearX 过滤规则。
- 批次状态更新和失败重跑。
- upsert SQL 不重复写入。
- Listing 汇总解析作为可选校验。

集成/回归测试：

- 复用 `tests/test_shopifyid_dianxiaomi_sync.py` 中的 CDP 和店小秘成功响应模式。
- 新增 `tests/test_dianxiaomi_order_import.py`。
- 路由测试覆盖 `/order-analytics` 页面入口和触发接口。

手工验证：

1. 在测试服务器确认 `autovideosrt-mk-browser.service` 正常。
2. 使用小日期范围 dry-run，例如 `2026-04-27` 到 `2026-04-28`。
3. 检查落库行包含 NewJoy/omurio 商品，且不包含 SmartGearX。
4. 抽样核对一条订单的地区、运费、商品售价、订单金额。
5. 扩大到 `2026-01-01` 到 `2026-04-28` 全量抓取。

## 非目标

- 不在第一版重做订单分析视图。
- 不改变现有 Shopify CSV/Excel 上传逻辑。
- 不直接依赖 Listing 汇总作为订单明细来源。
- 不在 Windows 本机安装或依赖 MySQL。
- 不操作线上目录或重启线上服务，除非用户明确要求发布线上。

## 未决风险和约束

- 店小秘店铺显示名没有直接出现 `NewJoy` 或 `omurio`，第一版必须基于本地 Shopify ID / 商品 URL / 站点映射过滤，而不是仅按店铺名过滤。
- 店小秘订单状态页默认是 `paid`，历史已发货/已退款/已搁置订单可能需要按多个 state 补抓。实现计划需要确认 state 覆盖：至少覆盖 `paid`、`approved`、`processed`、`allocated`、`shipped`、`refound`、`ignore`，再按接口实际可用状态收敛。
- 订单行字段在不同订单状态下可能出现在 `productList` 或 `cancelProductList`，标准化必须同时兼容。
- 运费有两个口径：买家支付运费 `shipAmount` 与实际物流费用 `logisticFee`，两者都要保留。

