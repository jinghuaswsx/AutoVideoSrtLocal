# 明空产品库数据底座设计

日期：2026-06-09

## 背景

新上产品如果来自明空，最可靠的配对路径不应该从单品页面临时反查，而应该先沉淀一套本地“明空产品库”。该库每周同步明空店小秘全量 Shopify 在线商品，并补齐商品链接、产品 code、Shopify variants、店小秘 SKU、1688 采购链接、供应商等采购侧信息。后续新品 SKU 工作台先读本地库做匹配；本地库没有命中时，再实时访问 DXM02-MK 明空店小秘后台按当前产品补采并回写本地库，随后重新读取本地结果，人工确认后再写入本地产品和 DXM03 店小秘。

## 事实来源

- `AGENTS.md`：文档驱动代码、定时任务登记、验证规则。
- `docs/server_browser_runtime.md`：DXM02-MK CDP `127.0.0.1:9223`，DXM03-RJC CDP `127.0.0.1:9225`。
- `docs/superpowers/specs/2026-05-18-dianxiaomi-full-listing-archive-design.md`：明空商品/素材归档已有 `dianxiaomi_product_assets`。
- `docs/superpowers/specs/2026-05-07-dianxiaomi-sku-variant-source-and-cdp-recovery-design.md`：店小秘 Shopify 在线商品与 ERP 商品管理的 SKU 解析方式。
- `docs/superpowers/specs/2026-06-05-dianxiaomi-sku-purchase-sync-design.md`：现有 `media_product_skus` 与采购价同步模型。
- `docs/superpowers/plans/2026-05-04-1688-url-backfill-exploration.md`：1688 商品配对接口与 `alibabaProductId` 构造采购链接逻辑。

## 目标

1. 新增本地“明空产品库”数据模型，保存明空全量 Shopify 在线商品、产品链接、产品 code、Shopify 商品/variant、SKU、1688 采购链接、供应商、采购配对状态。
2. 新增每周北京时间周一 04:00 同步任务，从 DXM02-MK 店小秘拉取明空数据。
3. 同步范围首版按明空 Shopify 在线商品全量分页同步，后续可在全量基线稳定后增加增量优化。
4. 新品 SKU 工作台改为优先查本地明空产品库；本地库缺失时按产品 code 实时访问 DXM02 补采并回写，再用本地库结果渲染。
5. 针对 `adjustable-claw-clippers-rjc`，先用本地明空产品库完成可解释的候选匹配；如果明空侧缺精确采购关系，工作台展示候选与缺口，不自动猜测。

## 非目标

- 不在同步任务中写 DXM03 店小秘。
- 不在同步任务中自动确认模糊采购关系。
- 不替换现有 `dianxiaomi_product_assets`，首版可复用并补充新的明细表。
- 不在用户未确认前改动素材产品的 `shopifyid` / SKU / 采购链接。

## 数据来源

### 明空商品基础库

候选来源：

- `dianxiaomi_product_assets`：已有明空商品归档，包含 `product_code`、`product_id`、`product_url`、中英文标题、主图与详情图。
- DXM02 店小秘 Shopify 在线商品接口：`/api/shopifyProduct/pageList.json`，可按创建/更新时间或全量分页拉取商品、handle、shopifyProductId、variants。
- DXM02 店小秘统计/榜单接口：已有 `dianxiaomi_rankings` 可作为近 30 天活跃商品窗口辅助。

首版策略：

- 以 DXM02 店小秘在线商品接口为主，全量分页拉取商品。
- 用 `product_url` 或 handle 解析 `product_code`。
- 用 `dianxiaomi_product_assets.product_code` 做补充，补齐明空选品页已归档但在线商品接口缺失的字段。

### SKU 与 ERP 商品

来源：

- DXM02 店小秘商品管理接口：`/api/dxmCommodityProduct/pageList.json`。
- 解析方式沿用 `tools/dianxiaomi_sku_sync.py`：
  - Shopify variant `pair_key = sku or shopifyVariantId`。
  - ERP 商品以 `sku` 建索引，字段包括 `skuCode`、`productSku/goodsSku`、`name/nameEn`、`relationFlag`。

### 采购配对与供应商

来源：

- DXM02 1688 商品配对列表：`/api/dxmAlibabaProductPair/alibabaProductPairPageList.json`。
- 采购链接优先级沿用 `appcore.supply_pairing.extract_1688_url()`：
  1. `sourceUrl` 且为 1688。
  2. `alibabaProductList[*].sourceUrl` 且为 1688。
  3. `alibabaProductId` 组装 `https://detail.1688.com/offer/{id}.html`。
  4. 原始 `sourceUrl` 作为非 1688 来源参考。

关键字段：

- `sku`、`skuCode`、`name`
- `sourceUrl`
- `alibabaProductId`
- `alibabaProductList`
- `supplierName`、`supplierId`
- `productId` / `productIdStr`（店小秘配对行 ID，后续 DXM03 写入时不能跨账号复用，只能作为明空侧参考）

### 仓库/采购价

来源待子 agent 只读确认：

- DXM02 云仓/仓库 SKU 页面或相关接口。
- 目标字段：SKU、商品编码、供应商、采购价、库存、仓库状态。

首版如果仓库接口未确认，明空产品库先落采购链接/供应商/SKU；采购价为空但保留字段。

## 表结构建议

### `mingkong_products`

明空 Shopify 在线商品主表。

- `id`
- `product_code`：明空商品 code，唯一优先键。
- `mk_shopify_product_id`
- `mk_handle`
- `mk_product_url`
- `mk_title`
- `mk_title_cn`
- `mk_main_image_url`
- `first_seen_at`
- `last_seen_at`
- `last_synced_at`
- `raw_json`

唯一键：

- `uk_mk_product_code(product_code)`
- 辅助索引：`idx_mk_shopify_product_id(mk_shopify_product_id)`

### `mingkong_product_variants`

明空 Shopify variant 与店小秘 SKU 关系。

- `id`
- `mingkong_product_id`
- `mk_shopify_product_id`
- `mk_shopify_variant_id`
- `variant_title`
- `shopify_sku`
- `pair_key`
- `dxm_sku`
- `dxm_sku_code`
- `dxm_product_sku`
- `dxm_name`
- `relation_flag`
- `raw_json`
- `last_synced_at`

唯一键：

- `uk_mk_variant(mk_shopify_variant_id)`

### `mingkong_combo_components`

明空组合 SKU 的组成关系。店小秘组合商品在商品管理中以外层 SKU 表示，列表参数 `productGroupLxId=3` 可筛选组合商品；真实组件由 `/api/dxmCommodityProduct/getChildSkuInfo.json?id=<组合商品 productId>` 返回。

- `id`
- `mingkong_product_id`
- `mingkong_variant_id`
- `combo_dxm_product_id`
- `combo_dxm_sku`
- `component_dxm_product_id`
- `component_sku`
- `component_name`
- `component_img_url`
- `component_quantity`
- `raw_json`
- `last_synced_at`

索引：

- `idx_mk_combo_parent(combo_dxm_sku)`
- `idx_mk_combo_component(component_sku)`

### `mingkong_procurement_links`

明空采购配对候选/确认来源。

- `id`
- `mingkong_product_id`
- `mingkong_variant_id`
- `pairing_row_id`
- `sku`
- `sku_code`
- `purchase_1688_url`
- `source_url`
- `alibaba_product_id`
- `supplier_id`
- `supplier_name`
- `pairing_state`
- `confidence`：`exact_sku` / `variant_id` / `keyword_candidate` / `manual_candidate`
- `raw_json`
- `last_synced_at`

索引：

- `idx_mk_proc_sku(sku)`
- `idx_mk_proc_alibaba_product_id(alibaba_product_id)`
- `idx_mk_proc_product_variant(mingkong_product_id, mingkong_variant_id)`

### `mingkong_product_library_sync_runs`

同步任务运行记录，配合 `scheduled_task_runs` 保留业务统计。

- `id`
- `started_at`
- `finished_at`
- `status`
- `window_start`
- `window_end`
- `products_seen`
- `variants_seen`
- `procurement_links_seen`
- `error_message`

## 同步流程

每周一 04:00 运行 `mingkong_product_library_sync`：

1. 连接 DXM02-MK CDP `127.0.0.1:9223`。
2. 全量分页拉取 Shopify 在线商品。
3. 从商品链接/handle 解析 product code，并 upsert `mingkong_products`。
4. 拉取商品管理 ERP SKU 索引，按 variant `pair_key` 合并并 upsert `mingkong_product_variants`。
5. 拉取 1688 商品配对列表：
   - 精确 SKU/variant 命中写 `exact_sku` 或 `variant_id`。
   - 产品中文名/英文名/关键词命中但无法精确绑定 variant 的写 `keyword_candidate`。
6. 对 `groupState=1` 或组合分类 `productGroupLxId=3` 命中的 SKU，调用 `getChildSkuInfo.json` 拉组件 SKU：
   - 外层组合 SKU 保存为 variant/ERP SKU。
   - 组件 SKU、数量、图片写 `mingkong_combo_components`。
   - 采购链接与供应商优先读取组件 SKU 的 1688 配对；外层组合 SKU 没有配对时不得视为缺失。
7. 可用时拉仓库/采购价，补充供应商/采购价/库存字段。
8. 写 `scheduled_task_runs` 与 `mingkong_product_library_sync_runs`。

## 工作台读取顺序

明空配对工作台的读取顺序固定为：

1. 先读取本地 `media_product_skus`；如果已有我们 DXM03 自营 SKU，则沿用现有数据。
2. 如果本地 SKU 为空，读取本地 `mingkong_products` / `mingkong_product_variants` / `mingkong_procurement_links` / `mingkong_combo_components`。
3. 如果明空本地库仍然没有命中，按当前产品 code、明空历史素材表、商品链接、Shopify product id 生成搜索词，实时访问 DXM02-MK 店小秘后台补采该产品，写回本地明空产品库。
4. 补采完成后再次只读本地明空产品库渲染工作台；如果仍无结果，则展示“无明空匹配数据”，不自动猜测。

## Shopify variants 完整性补充

2026-06-09 对产品 `hygienic-silicone-back-scrub-rjc` / AutoVideoSrtLocal 产品 ID `772` 复核发现：店小秘 Shopify 在线商品接口 `shopifyProduct/pageList.json` 返回的商品行包含 `variantSize=24`，但内嵌 `variants` 只有前 5 条。公开 Shopify 商品 JSON：

- `https://0ixug9-pv.myshopify.com/products/hygienic-silicone-back-scrub-rjc.js`
- `https://7t1gn3-sv.myshopify.com/products/hygienic-silicone-back-scrub-rjc.js`
- `https://newshopllox.com/products/hygienic-silicone-back-scrub.js`

均返回 24 个 variants/SKU。明空产品库同步不得只信店小秘列表接口内嵌 variants；当 `variantSize` 大于内嵌 variants 数量时，必须用 `sellerLoginId + handle` 打开公开 Shopify `.js` / `.json` 补齐全量 variants，并且只有公开 JSON 的 `product.id` 与店小秘 `shopifyProductId` 一致时才允许替换。补齐失败时保留店小秘接口结果并在同步摘要中体现缺口。

## 全量 SKU 基底与明空填充口径

明空配对工作台的 SKU 行基底必须来自“我们自己的商品全量 Shopify variants”，不能来自“明空已配采购信息的行数”。流程固定为：

1. 先从当前产品的 `product_link` 或 DXM03 Shopify 在线商品公开 JSON 建全量 variant/SKU 基底，唯一键是 `shopify_variant_id`。
2. 再按 `shopify_variant_id`、`shopify_sku` / 明空 `dxm_sku`、规格标题 `variant_title` 从明空产品库填充采购相关字段，包括店小秘 SKU、SKUID、商品名、图片、供应商、1688 商品 ID、1688 SKU ID、采购链接。搬品后 Shopify variant id 不一致但规格标题一致时，可以用规格标题做保守填充。
3. 明空库命中的行填充完整后可参与 DXM03 商品复刻和 1688 配对。
4. 明空库没有命中的行也必须保留在工作台和本地 `media_product_skus`，但店小秘 SKU 和采购字段置空，后续运营在真实发货/采购前手动维护；不得把 Shopify 前台 SKU / `pair_key` 兜底写成店小秘 SKU。
5. 后端不得按 `dianxiaomi_sku` 对目标行去重；同一个采购 SKU 被多个前台 variant 复用时也必须保留所有 `shopify_variant_id` 行。

## 已配置 SKU 保护

同步明空 SKU 到 DXM03 时，必须优先保护我们自己店小秘中已经配置过的 SKU：

1. 单品 SKU：确认 1688 采购配对前，先查询 DXM03 当前 SKU 的配对状态；如果已经 `is_paired=1`，本轮只标记 `already_configured_preserved`，不得改 `sourceUrl`，不得重新勾选/确认 1688 SKU，即使明空候选与现有配置不同。
2. 组合 SKU：如果 DXM03 已存在组合商品，并且所有组件 SKU 的采购配对都完整，只标记已存在/已配对，不重新复刻组合结构。
3. 本地 `media_product_skus.manual_override=1` 的人工行继续由 `replace_product_skus()` 原有逻辑保护，不被自动同步覆盖或删除。
4. 批量同步不得把“空 SKU 基底”误判为已处理。`media_product_skus` 只有 Shopify variant/title/price/weight 等前台字段，或者只有商品标题类 `dianxiaomi_name`，但 `dianxiaomi_sku` / `dianxiaomi_product_sku` / `dianxiaomi_sku_code` / 人工字段为空时，只表示基底已建立，仍应继续尝试同步明空 SKU。
5. 批量同步跳过的“已处理数据”只包括：存在 `manual_override=1`、人工价格/商品名，或已经有真实店小秘 SKU/Product SKU/SKUID 的本地 SKU 行。DXM03 后台已经配置过的 SKU 则在执行阶段实时查询并以 `already_configured_preserved` 保护。

## 未处理产品批量同步

针对当前系统中还没有 SKU 数据的产品，提供独立 CLI 批量执行完整流程：

1. 候选过滤：
   - `media_products.deleted_at IS NULL`
   - 默认排除已归档产品。
   - 默认只处理上架产品：`listing_status IS NULL OR listing_status='上架'`。
   - 不存在已处理 SKU 行；空 SKU 基底产品仍然要进入批量同步。
2. 单产品流程：
   - 构建 Shopify 全量 variant 基底。
   - 从本地明空产品库读取 SKU、采购链接、采购价/供应商等可用字段；本地缺失时按现有逻辑实时补采 DXM02。
   - 当同一个明空 product code 下存在多个 Shopify 副本时，合并 SKU 基底不能让“同 variant 但无 DXM SKU”的空候选压过“同规格标题且有 DXM SKU/采购信息”的候选；同规格候选必须按真实 DXM SKU、SKUID、采购链接、1688 SKU ID 的信息量择优。
   - Shopify 公开 JSON 或 DXM02 返回的重量字段如果超过本地数据库可保存范围，写入本地明空库和 `media_product_skus` 前必须置空，不得中断整品同步。
   - 写入本地 `media_product_skus`。
   - 在 DXM03 小秘云仓/商品管理中补缺 SKU；已存在且已配置的 SKU 保持不动。
   - DXM03 复刻顺序必须先普通组件 SKU、后组合 SKU，避免组合商品先执行时因为组件还没创建而被误判为缺组件；如果组合组件不在当前 Shopify variant 基底里，但 DXM02 组合关系返回了组件 SKU，也允许先把该组件从 DXM02 复刻到 DXM03。
   - 对未配置的单品 SKU 执行 1688 采购配对；触发 1688 来源同步后必须轮询等待配对行生成，不能只做一次即时查询；`confirm` 接口报错后要复查实际配对状态，避免接口返回异常但已成功写入的误判。
   - 组合 SKU 只在组件关系完整时视为完成；组件未配对时，可用本地明空采购库中的组件采购链接与 1688 SKU ID 自动补配组件，再重新判断组合是否完整。
3. 输出报告：
   - `product_id`
   - `product_code`
   - 产品中文名
   - 执行结果
   - 本地写入 SKU 数
   - DXM03 新建/已存在/已保护/阻断/失败数量
   - 逐 SKU 状态、采购链接、SKU ID、错误信息

## 无订单产品激进重置模式

当产品在本地已同步订单系统中查不到任何订单时，可以进入激进模式，允许重置本地 SKU 状态并强制同步明空 SKU：

1. 订单安全判断必须只读执行，至少检查：
   - `dianxiaomi_order_lines.product_id`
   - `dianxiaomi_order_lines.product_code`
   - `dianxiaomi_order_lines.shopify_product_id`
   - `dianxiaomi_order_lines.product_sku` / `product_sub_sku` / `product_display_sku`
   - `dianxiaomi_order_lines.raw_line_json` / `raw_order_json` 中的 product code
   - `shopify_orders.product_id`
   - `shopify_orders.lineitem_sku`
2. 如果任一订单计数大于 0，激进模式也必须跳过该产品，不执行本地删除或 DXM03 写入。
3. 如果订单计数全部为 0，允许：
   - 删除本地 `media_product_skus` 该产品全部行，包括历史自动基底和人工行。
   - 强制实时刷新 DXM02 明空产品库数据。
   - 重新按全量 Shopify variant 基底 + 明空 SKU 填充写入本地。
   - DXM03 确认采购配对时允许覆盖已有 1688 配对，不再走 `already_configured_preserved`。
4. 激进模式仍不得伪造明空 SKU。强制刷新后如果仍没有真实 `dianxiaomi_sku`，只能保留空 SKU 基底并输出 `blocked_no_mingkong_skus`，等待后续人工或更强匹配逻辑处理。

## 组合 SKU 接口实测补充

2026-06-09 在 DXM02-MK 明空账号实测：

- 商品管理组合筛选使用 `productGroupLxId=3`；但按精确 SKU 搜索时，`productGroupLxId=1` / `3` / 空值均可能返回外层组合 SKU。
- 外层组合 SKU 通过 `groupState=1` 判定，不能依赖 `productType`；组合样本仍返回 `productType=100`。
- 组合详情读取接口：
  - `POST /api/dxmCommodityProduct/getChildSkuInfo.json`
  - form: `id=<组合商品 productId>`
  - 返回组件 `productId`、`sku`、`name`、`imgUrl`、`num`。
- 编辑详情读取接口：
  - `POST /api/dxmCommodityProduct/viewDxmCommodityProduct.json`
  - form: `id=<组合商品 productId>`
  - `data` 是 JSON 字符串，里面的 `productDTO.dxmCommodityProductList[*].groupNum` 与 `getChildSkuInfo.json[*].num` 一致。
- 组合商品保存接口由前端 chunk `index-SWSUaQin.js` 反查确认：
  - 普通 SKU：`/api/dxmCommodityProduct/addCommodityProduct.json` / `editCommodityProduct.json`
  - 组合/加工 SKU：`/api/dxmCommodityProduct/addCommodityProductGroup.json` / `editCommodityProductGroup.json`
  - payload 关键字段是 `obj=JSON.stringify(form)`，其中 `form.dxmCommodityProduct` 仍是 JSON 字符串，核心字段包括 `groupState`、`childIds`、`childNums`、`processFee`。
  - 组件关系由 `children.map(idStr).join(",")` 写入 `childIds`，由 `children.map(groupNum).join(",")` 写入 `childNums`。

组合 SKU 搬运规则：

1. 外层组合 SKU 不要求有 1688 采购配对；外层配对列表为空不能算失败。
2. 每个组件 SKU 都必须分别核验采购配对；组件未配齐时，工作台显示为缺口，不自动确认。
3. 如果 DXM03 已经存在外层组合关系，则工作台只需校验组件 SKU 与组件采购配对。
4. 如果 DXM03 缺少外层组合关系，后续写入必须基于 DXM03 自己的商品详情 `viewDxmCommodityProduct.json` 做全量表单合并，再调用 `editCommodityProductGroup.json`；不得只提交 `childIds` / `childNums` 的局部字段。

## 与新品配对工作台的关系

工作台不再每次即时遍历 DXM02。它按以下优先级匹配：

1. 去掉本地 product code 的 `-rjc` 后，精确匹配 `mingkong_products.product_code`。
2. 本地英文标题匹配 `mingkong_products.mk_title`。
3. 本地中文名匹配 `mingkong_products.mk_title_cn` 或采购链接候选 `dxm_name`。
4. 仍无匹配时提供手动搜索 DXM02 并回写明空产品库。

如果明空库里同一个 product code 在多个明空店铺或多个 Shopify 商品 ID 下重复出现，工作台不得把重复商品的 variant 直接相加。候选选择规则：

1. 本地产品有明确 Shopify ID 或素材/资产表能解析出 Shopify ID 时，优先只使用这些 Shopify ID 对应的明空商品。
2. 没有明确 Shopify ID 时，优先使用采购配对完整的明空商品。
3. 如果仍有多个明空候选，按采购配对完整度与组件完整度选择填充来源，但最终目标行仍以我们自己的 `shopify_variant_id` 为唯一基底。
4. 确认弹窗提交的目标计划不得按目标店小秘 SKU 去重；同一个店小秘 SKU 对应多个前台 variant 时，必须保留所有 variant 行。

DXM03 写入仍遵守：

- 必须用户人工确认。
- 必须 DXM03 自己存在待配对行 `pairProductId`。
- 明空侧 `pairing_row_id` 不能直接当 DXM03 `pairProductId` 使用。

## 2026-06-09 实施落地

新增本地数据底座：

- 迁移：`db/migrations/2026_06_09_mingkong_product_library.sql`
- 表：
  - `mingkong_product_library_sync_runs`
  - `mingkong_products`
  - `mingkong_product_variants`
  - `mingkong_combo_components`
  - `mingkong_procurement_links`
- 服务层：`appcore/mingkong_product_library.py`
- 采集脚本：`tools/mingkong_product_library_sync.py`
- 定时任务登记：`appcore/scheduled_tasks.py` 的 `mingkong_product_library_sync`
- systemd：
  - `deploy/server_browser/autovideosrt-mingkong-product-library-sync.service`
  - `deploy/server_browser/autovideosrt-mingkong-product-library-sync.timer`
  - `deploy/server_browser/install_mingkong_product_library_sync_timer.sh`

同步脚本行为：

1. 使用 DXM02-MK CDP `127.0.0.1:9223`。
2. 默认 `--days 0` 同步全量 Shopify 在线商品；`--days > 0` 仅用于临时缩小创建时间窗口。
3. 支持 `--product-code <code>` 单品定向刷新；该模式会结合本地 `media_products`、`mingkong_material_products`、`dianxiaomi_product_assets` 的标题、链接、Shopify 商品 ID 生成搜索词。
4. 写入明空 Shopify 商品和 variants 后，用 variant `pair_key` 反查 DXM02 ERP 商品管理。
5. 1688 采购配对按 SKU 写入 `mingkong_procurement_links`。
6. 组合 SKU 会读取 `getChildSkuInfo.json` 并写入 `mingkong_combo_components`；组件采购配对按组件 SKU 补充。

工作台读取顺序：

1. 先读本地 `media_product_skus`。
2. 如果本地 SKU 行为空，则用 `appcore.mingkong_product_library.sku_rows_from_library()` 从明空产品库生成候选 SKU 行。
3. 如果明空产品库仍为空，则按当前产品 code / 链接 / 本地明空素材表中沉淀的标题和 Shopify 商品 ID，实时查询 DXM02-MK 店小秘后台，并把查询到的商品、SKU、采购配对和组合组件回写本地明空产品库。
4. 回写完成后重新读取本地明空产品库，页面不直接依赖一次性临时结果。
5. 每个候选行继续实时核验 DXM03 商品管理和 DXM03 采购配对状态。
6. 明空采购候选只作为人工确认默认值；写入 DXM03 时仍必须使用 DXM03 自己的待配对行。

首轮全量同步修正：

- DXM02-MK 全量 Shopify 商品里存在超过 1000 字符的来源 URL，`source_url` 不应使用 `VARCHAR(1000)`。
- `mingkong_products.source_url`、`mingkong_product_variants.dxm_source_url`、`mingkong_procurement_links.purchase_1688_url/source_url` 使用 `TEXT`。
- DXM02-MK 全量 Shopify variants 里存在超过 128 字符的 SKU，`shopify_sku`、`pair_key`、`dxm_sku`、`dxm_product_sku`、组合父/子 SKU、采购配对 SKU 统一使用 `VARCHAR(512)`。
- 迁移文件必须同时包含 `CREATE TABLE` 的 `TEXT` 定义和对既有表的 `ALTER TABLE ... MODIFY ... TEXT NULL`，确保线上已创建表后重新执行同步也能自愈。

## adjustable-claw-clippers-rjc 当前预期

本地产品：

- `product_code = adjustable-claw-clippers-rjc`
- 去后缀后匹配 `adjustable-claw-clippers`

明空产品库应能存到：

- 明空商品 URL：`https://whiskivo.com/products/adjustable-claw-clippers`
- 明空 Shopify 商品 ID：`7693106217011`
- variants：Blue / Pink

已知缺口：

- 明空 variant SKU 为空，pair key 使用明空 variant ID。
- 明空采购配对没有精确命中该 variant ID。
- “指甲剪”能搜到候选，但需要人工确认。

## 单品验收推进：adjustable-claw-clippers-rjc

在明空产品库完整自动同步上线前，允许对 `adjustable-claw-clippers-rjc` 走一次人工确认的单品执行流程，用于打通 DXM03 采购侧验收：

1. 只读复核本地 `media_products`、DXM03 Shopify 在线商品、DXM03 ERP 商品管理、DXM03 1688 商品配对列表。
2. 如 DXM03 已有目标商品的 ERP SKU / `pairProductId`，允许在人工确认采购候选后写入 DXM03 1688 商品配对。
3. 如 DXM03 只有 Shopify 在线商品但没有 ERP SKU / `pairProductId`，不得伪造店小秘配对行；应先完成本地 `shopifyid`、`media_product_skus`、产品级 `purchase_1688_url` 维护，并把 DXM03 缺少 ERP 待配对行作为明确阻断。
4. 采购候选来源必须可追溯到明空 DXM02 或 1688 商品配对候选；`adjustable-claw-clippers-rjc` 当前只允许把“指甲剪”类候选展示给人工确认，不自动选择。
5. DXM03 写入动作必须满足：
   - 目标 SKU 属于 DXM03 账号自己的商品/货品。
   - 写入接口使用 DXM03 自己的 `pairProductId`。
   - 明空 DXM02 的 pairing row id 只能作为参考，不能跨账号复用。

## SKU 工作台最小闭环

在完整明空产品库、智能匹配和定时同步上线前，先提供一个面向单品验收的最小可运行工作台：

1. 素材管理产品列表的 SKU 操作区保留原有 `SKU` 按钮，并在其下方增加同尺寸、同高度、居中对齐的 `明空配对` 按钮。
2. `明空配对` 跳转独立页面，页面读取本地 `media_products`、`media_product_skus` 和产品级 `purchase_1688_url`，展示：
   - 当前产品链接、Shopify 商品 ID、采购链接。
   - 每个 variant 的图片、Shopify variant ID、店小秘 SKU、ERP 编码、店小秘商品名。
   - DXM03 实时核验到的 ERP 商品状态、1688 配对状态、供应商、1688 商品 ID、1688 SKU ID。
   - 如为组合 SKU，展开显示组件 SKU、组件数量、组件图片和组件 1688 配对状态。
3. 工作台的确认入口必须由管理员触发，POST 前端带 `X-CSRFToken`，后端使用 DXM03 浏览器环境写入 DXM03 店小秘。
4. 后端写入规则：
   - 本地没有 `purchase_1688_url` 或没有可配对 SKU 时，不写 DXM03，返回明确缺口。
   - DXM03 找不到自己的 ERP SKU 时，不伪造配对行，返回阻断。
   - 已经 `state=1` 的配对行只做核验并返回 `already_paired`。
   - 组合 SKU 不用外层 SKU 直接确认 1688；应先核验/搬运组件 SKU 及数量，组件 SKU 采购配对完整后再视为可采购。
   - 未完成配对时，先用 DXM03 商品管理接口确认/更新采购链接，再触发 DXM03 1688 商品同步，最后用 DXM03 自己的 `pairProductId` 调用确认配对接口。
5. `adjustable-claw-clippers-rjc` 的首版工作台数据源允许使用已人工确认并写入本地的 `source=mingkong_pair` SKU 行；后续明空产品库上线后，工作台候选区改为读取明空产品库，而不是每次实时访问 DXM02。

## 2026-06-09 追加：明空 SKU 同步与页面标注

针对本地产品没有任何 `media_product_skus` 行、但明空产品库已有同款 SKU 的场景，工作台提供管理员手动触发的“同步明空 SKU 到我们系统”动作：

1. 后端先读本地 `media_product_skus`；如果已经存在 SKU 行，默认不覆盖，返回“本地已有 SKU”。
2. 本地 SKU 为空时，按当前产品 code / 链接 / Shopify 商品 ID 读取 `mingkong_product_library.sku_rows_from_library()`。
3. 明空本地库仍未命中时，允许定向调用 `refresh_product_from_dxm02()` 补采 DXM02-MK，并重新读取明空本地库。
4. 写入 `media_product_skus` 时只落 Shopify variant、明空店小秘 SKU、明空 ERP 编码、明空商品名等候选字段，`source='mingkong_library'`，用于我们系统后续可见和人工核对。
5. 该同步动作不代表 DXM03 已有 ERP SKU / 采购配对；确认写入 DXM03 时仍必须实时核验 DXM03 自己的商品管理 SKU 和待配对行。DXM03 找不到 SKU 时继续阻断，不用明空侧 `pairing_row_id` 跨账号写入。

页面展示必须明确区分数据归属：

1. 左侧区域展示“我们系统 / DXM03”维度：我们系统 Shopify variant、我们系统 SKU、DXM03 ERP 状态、DXM03 供应商、DXM03 1688 商品 ID、DXM03 1688 SKU ID、写入状态。
2. 右侧区域展示“明空店小秘”维度：明空 SKU 图片、明空店小秘 SKU、明空店小秘 ERP 编码、明空店小秘商品名、明空供应商、明空 1688 商品 ID、明空 1688 SKU ID、明空组合组件。
3. 页面中所有明空维度字段必须加“明空”前缀；所有 DXM03 维度字段必须加“DXM03”前缀；本地字段用“我们系统”前缀，避免混淆来源。
4. 明空 SKU 图片首版只读取并展示明空产品库已同步的 `dxm_img_url` / `image_url`，不新增 `media_product_skus` 图片字段；如后续需要把图片持久写入我们系统 SKU 表，必须另行补充 schema 迁移设计。

操作过程必须可视化：

1. 工作台上会触发远程读取或写入的按钮必须打开 modal 弹窗，至少包括“刷新状态”“同步明空 SKU 到我们系统”“复刻明空 SKU”“同步明空店小秘SKU”。
2. modal 必须显示当前状态、已耗时、步骤日志、后端返回的逐 SKU 结果和报错信息；失败时不自动关闭，方便管理员复制/排查。
3. 后端写入类接口返回体必须包含可读 `message` 和 `logs`；`logs` 用于前端 modal 展示，`items` 继续保留逐 SKU 结构化结果。
4. 原“确认写入 DXM03”按钮文案调整为“同步明空店小秘SKU”，但接口语义仍是把已确认的明空店小秘 SKU / 1688 SKU 选择写入 DXM03 自己的采购配对，不跨账号复用明空 pairing row id。

## DXM02 到 DXM03 SKU 复刻规则

当明空产品库或人工确认候选已经能定位到明空 DXM02 的 ERP 商品行，但 DXM03 商品管理中找不到同一个 SKU 时，工作台不能直接停在“缺 ERP SKU”。它应提供管理员确认的“创建/补齐 DXM03 SKU”动作，先把明空 SKU 设置复刻到 DXM03，再继续 1688 采购配对确认。

线上验收要求：`复刻明空 SKU` 按钮必须完成真实 DXM02 -> DXM03 复刻或返回逐 SKU 业务缺口，不能只把后端异常包装成可读错误。由于 Web 路由运行环境可能已有 asyncio loop，复刻动作中的 Playwright Sync API 必须脱离 Flask / gunicorn worker 运行环境执行，避免 `Playwright Sync API inside the asyncio loop` 阻断真实写入。

复刻动作需要同时访问 DXM02-MK 与 DXM03-RJC CDP。实现上只能启动一个 `sync_playwright()` 实例，再用同一个 Playwright 分别 `connect_over_cdp` 两个账号浏览器；不能在同一线程里连续启动两个 Playwright Sync context。

DXM03 普通商品新增接口 `POST /api/dxmCommodityProduct/addCommodityProduct.json` 除 `obj` 外还必须携带前端保存动作同款外层字段，至少包括 `shopId=-1`、`pt=-1`、`pid`、`vid`、`orderStatus`、`orderId`、`orderWarehoseId=-1`、`orderCount=0`；缺少 `shopId/pt` 时接口会直接返回 404。

复刻字段分三类处理：

1. 可复刻字段：
   - 商品 SKU：优先保持明空 `dxmCommodityProduct.sku` 不变。
   - 店小秘商品编码 / SKUID：优先保持明空 `skuCode` 不变。
   - 商品名、英文名、SPU、价格、重量、体积、图片、属性、报关信息等非账号身份字段。
   - 采购来源 URL：优先使用工作台确认的 1688 采购链接；缺失时才沿用明空商品详情里的 `sourceUrl`。
2. 必须重置字段：
   - DXM02 账号身份与主键：`id`、`idStr`、`puid`、`parentId`、`productId`、`developmentId`。
   - 明空仓库、货架、供应商主键：`warehouseId` / `warehoseId`、`goodsShelfId`、`supplierId`。
   - 创建时间、更新时间、审核/同步状态等由 DXM03 重新生成的字段。
3. 暂不跨账号复刻字段：
   - 明空仓库库存、仓位、供货商 ID、采购关系 ID。
   - 这些关系要在 DXM03 商品行存在后，通过 DXM03 1688 商品配对和 DXM03 自己的仓库/供应商数据生成。

冲突避让规则：

1. 复刻前先在 DXM03 商品管理用 SKU 精确搜索。
   - 如同 SKU 已存在，视为 DXM03 已有商品，不重复创建；后续直接用该 DXM03 商品继续采购配对。
2. SKU 不存在时，再用明空 `skuCode` / SKUID 精确搜索 DXM03。
   - 如 `skuCode` 未冲突，原样复刻。
   - 如 `skuCode` 已被其它 DXM03 商品占用，保持商品 SKU 不变，将 `skuCode` 改为 `{原 skuCode}-MK`；仍冲突时依次使用 `{原 skuCode}-MK2`、`{原 skuCode}-MK3`，最多尝试 20 次。
3. 避让后的最终 `skuCode` 必须写回本地 `media_product_skus.dianxiaomi_sku_code`，并在工作台结果中展示“原明空 SKUID”和“DXM03 实际 SKUID”。
4. 复刻完成后重新搜索 DXM03 商品管理，以 DXM03 返回的 `id`、`sku`、`skuCode` 为准，不使用 DXM02 的商品 ID。

组合 SKU 的复刻顺序：

1. 先复刻所有组件普通 SKU；组件缺失或冲突无法解决时，不创建外层组合 SKU。
2. 组件在 DXM03 均存在后，再按 DXM02 外层组合 SKU 的 `childIds` / `childNums` 关系，在 DXM03 使用组件自己的商品 ID 组装组合 SKU。
3. 组合保存使用 `POST /api/dxmCommodityProduct/addCommodityProductGroup.json`，`dxmCommodityProduct.groupState=1`，`childIds` 必须是 DXM03 组件商品 ID，`childNums` 使用 DXM02 组件数量；创建后仍要重新搜索 DXM03，以 DXM03 返回的外层组合商品 ID 为准。

## 验收

1. 数据表迁移存在并可重复执行。
2. 同步服务 pure 解析函数有单元测试，覆盖 Shopify 商品、ERP SKU、1688 配对候选。
3. 定时任务登记到 `appcore/scheduled_tasks.py`，计划为每周一 04:00。
4. 同步脚本支持手动运行，只读拉 DXM02 并输出 products/variants/procurement counts。
5. 工作台读取本地明空产品库，不依赖每次实时访问 DXM02。
6. 聚焦测试通过；不跑全量 pytest，除非涉及迁移/定时任务广影响时按规则扩大。

## 回滚

- 禁用 `mingkong_product_library_sync` 定时任务。
- 保留新增数据表，不影响现有素材管理、SKU 同步和 DXM03 店小秘流程。
