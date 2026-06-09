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
3. 首版如果缺少可验证的组合保存 payload，只在工作台展示组件复刻缺口，不自动提交外层组合保存。

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
