# 最近 15 天新品明空配对本地同步报告设计

日期：2026-06-10

## 事实来源

- `AGENTS.md`：文档驱动代码、worktree 隔离、本地 MySQL 禁止和 focused pytest 规则。
- `docs/superpowers/specs/2026-06-09-mingkong-product-library-foundation-design.md`：明空产品库、Shopify variant 基底、已配置 SKU 保护、批量同步和报告基础。
- `docs/superpowers/specs/2026-06-09-mingkong-pairing-full-procurement-closure-design.md`：明空候选加载、本地 SKU 导入、AI 置信度门槛和 DXM03 写入边界。
- `docs/superpowers/specs/2026-06-05-dianxiaomi-sku-purchase-sync-design.md`：`media_product_skus` 与 `dianxiaomi_yuncang_skus` 的既有数据语义。

## 背景

素材管理 SKU 模块已经有明空产品库和明空配对工作台。现有批量编排偏向“未处理 SKU 后续闭环”，并可能继续触发 DXM03 商品复刻、1688 采购配对和小秘云仓阶段。运营这次要的是另一条更窄的执行：扫描最近 15 天新添加到素材管理的产品，把明空那边已经同步过来且高确定性的 SKU 配对结果写入我们本地系统；本地已经配置过的 SKU 不动，明空缺数据的 SKU 保留空值，并在执行后输出详细报告。

这个需求不是从 DXM03 同步 SKU 到我们系统，也不是把店小秘当前 SKU 重新抓一遍。DXM03 只可能是后续采购闭环的消费者，本次 local-only 同步不能调用 DXM03 写入、复刻、1688 确认或云仓添加。

## 目标

1. 新增最近 15 天新品明空配对本地同步模式，候选范围为 `media_products.created_at >= cutoff`，其中 `cutoff` 由执行程序按当前服务器时间减 15 天计算，且 `deleted_at IS NULL`，默认排除归档产品。
2. SKU 行基准固定来自我们商品的 Shopify 链接 / Shopify variant 基底，不能以明空返回的 SKU 行数作为基准。
3. 本地已配置过的 SKU 行保持不动，包括人工配置、已有真实 `dianxiaomi_sku` / `dianxiaomi_product_sku` / `dianxiaomi_sku_code` / 人工商品名或人工价格的行。
4. 本地未配置的 SKU 行，如果明空产品库能高确定性命中，就同步明空 SKU 配对字段到 `media_product_skus`。
5. 明空没有数据、只有模糊候选、低置信、SKU 数量/规格冲突或关键字段缺失时，不自动猜测，不写入明空 SKU，只保留空 SKU 基底并标记挂起原因。
6. 执行后输出详细报告，回答：
   - 一共同步了多少个 SKU 到我们系统。
   - 最近 15 天新入库产品中，有多少个完成同步。
   - 有多少产品部分完成、全部挂起、跳过、失败，以及逐产品逐 SKU 原因。

## 非目标

- 不从 DXM03 反向同步 SKU 到我们系统。
- 不调用 DXM03 商品管理复刻、1688 商品配对确认、小秘云仓添加、采购价刷新或采购建议下单。
- 不覆盖本地已配置 SKU，不删除本地 SKU，不启用无订单激进重置模式。
- 不把低置信 AI 判断或 keyword-only 候选自动写入本地 SKU。
- 不把明空 DXM02 的账号主键、`pairProductId` 或云仓主键写成我们系统自己的 DXM03 主键。

## 候选范围

候选产品查询固定为：

1. `media_products.deleted_at IS NULL`。
2. `media_products.created_at >= cutoff`，其中 `cutoff = 当前服务器时间 - 15 天`。
3. 默认 `COALESCE(archived, 0)=0`。
4. 默认只处理上架产品：`listing_status IS NULL OR listing_status='上架'`。
5. 产品必须有可解析的 `product_code` 或 Shopify 商品链接；缺基准数据的产品计入 `blocked_no_shopify_base`。

与旧 `find_unprocessed_products()` 不同，本模式不能因为产品已有部分已配置 SKU 而整品排除。已有配置的行只保护对应 variant；其它空白 variant 仍要尝试明空配对。

## 匹配口径

每个产品的处理顺序：

1. 建立我们系统的 Shopify variant 基底：
   - 优先从本地产品链接 / `shopifyid` / 已缓存 Shopify variants 读取。
   - 唯一键优先 `shopify_variant_id`，其次归一化 `shopify_sku`，最后归一化 `variant_title`。
2. 读取本地已有 `media_product_skus`，按 variant 键标记已配置行：
   - `manual_override=1`。
   - 已有真实 `dianxiaomi_sku`、`dianxiaomi_product_sku` 或 `dianxiaomi_sku_code`。
   - 已有人工商品名、人工采购价或其它人工维护字段。
3. 从明空产品库读取候选：
   - product code 去掉 `-rjc` 后精确匹配为第一优先级。
   - 同 product code 多个明空副本时，优先信息更完整的行：有真实明空 SKU、SKUID、采购链接、1688 SKU ID、SKU 图片的候选高于空候选。
   - 只允许 `exact_variant`、`exact_sku`、规格标题唯一命中的确定性候选自动写入。
4. 当候选不唯一、只有 `keyword_candidate`、规格标题冲突、SKU 数量明显不一致、图片/标题明显冲突、采购 SKU 字段缺失时，产品或 SKU 行进入挂起，不自动写入。

AI 只可用于解释低置信候选和报告原因；AI 置信度低于 0.85 或 `requires_manual_review=true` 时，不自动写入。

## 本地写入字段

本模式只写我们系统本地表，目标为 `media_product_skus`。首版不做 schema 迁移，只写当前表已经支持的 SKU 配对字段。每个被同步的 SKU 行可写入：

- `shopify_variant_id`
- `shopify_sku`
- `variant_title`
- `dianxiaomi_sku`
- `dianxiaomi_product_sku`
- `dianxiaomi_sku_code`
- `dianxiaomi_name`
- `source = 'mingkong_local_pairing_15d'`

写入必须是保护性 upsert：已配置字段不覆盖；空白字段可由高确定性明空字段填充。对于明空无数据的 Shopify variant，也要保留空白基底行，以便报告和后续人工维护。

明空候选里的 SKU 图片、采购链接、1688 商品 ID 和 1688 SKU ID 首版进入报告明细，不写入 `media_product_skus`。本需求不更新产品级 `media_products.purchase_1688_url`，避免把单个 SKU 的采购候选提升成产品级采购链接。

## 执行模式

推荐在现有批量工具上新增 local-only 模式，而不是新建一套孤立脚本：

1. `plan`：只扫描最近 15 天产品并分类，不写数据库。
2. `execute-local`：只写本地 `media_product_skus`，不触发 DXM03/1688/云仓。
3. 输出报告写入 `output/mingkong_local_pairing_15d/`，文件名包含 `plan` 或 `execute`。

推荐 CLI 形态：

```bash
python tools/mingkong_local_pairing_15d.py --days 15 --plan
python tools/mingkong_local_pairing_15d.py --days 15 --execute
```

如果复用 `tools/mingkong_weekly_sync_orchestrator.py`，也必须增加明确参数，例如：

```bash
python tools/mingkong_weekly_sync_orchestrator.py --phase local-pairing --created-within-days 15 --execute-local
```

无论采用哪种入口，参数名必须能让执行者看出不会写 DXM03。

## 报告口径

报告 `summary` 至少包含：

- `candidate_product_count`：最近 15 天候选产品数。
- `scanned_product_count`：实际扫描产品数。
- `completed_product_count`：本轮存在新增明空 SKU 同步，且所有未配置 variant 都已同步或明确无需写入的产品数。
- `already_configured_product_count`：最近 15 天产品中，本地已经完整配置、无需本轮同步的产品数。
- `partial_product_count`：部分 SKU 同步、部分 SKU 挂起的产品数。
- `suspended_product_count`：没有任何新增 SKU 同步、但存在挂起原因的产品数。
- `skipped_product_count`：归档、下架或缺 Shopify 基底而跳过的产品数。
- `failed_product_count`：代码异常或数据读取异常的产品数。
- `synced_sku_count`：本轮从明空同步到我们系统的 SKU 行数。
- `preserved_sku_count`：本地已有配置并保持不动的 SKU 行数。
- `blank_base_sku_count`：保留为空白基底的 SKU 行数。
- `suspended_sku_count`：因明空缺数据、低置信或冲突挂起的 SKU 行数。

每个产品明细至少包含：

- `product_id`
- `product_code`
- `name`
- `created_at`
- `product_status`
- `sku_total_count`
- `synced_sku_count`
- `preserved_sku_count`
- `blank_base_sku_count`
- `suspended_sku_count`
- `reason_codes`
- `skus`

逐 SKU 明细至少包含：

- `shopify_variant_id`
- `shopify_sku`
- `variant_title`
- `status`
- `reason_code`
- `existing_config_preserved`
- `mingkong_match_method`
- `dianxiaomi_sku`
- `dianxiaomi_product_sku`
- `dianxiaomi_sku_code`
- `image_url`
- `purchase_1688_url`
- `product_id_alibaba`
- `sku_id_alibaba`
- `message`

标准 `reason_code`：

- `synced_from_mingkong`
- `preserved_existing_local_config`
- `blank_base_no_mingkong_data`
- `suspended_low_confidence`
- `suspended_multiple_candidates`
- `suspended_variant_conflict`
- `suspended_missing_required_fields`
- `blocked_no_shopify_base`
- `skipped_archived_or_unlisted`
- `failed_exception`

## 三种实现选项

推荐方案：扩展现有明空批量逻辑，增加 local-only 执行路径。优点是复用明空产品库、Shopify 基底、候选择优和报告结构；风险是必须把 DXM03 后续阶段彻底隔离，防止误调用。

备选方案：新建独立脚本，只依赖 `appcore.mingkong_product_library` 和 `appcore.medias`。优点是边界清晰；缺点是容易复制现有匹配逻辑，后续与工作台规则漂移。

不推荐方案：直接调用现有 `run_product_sync(... execute=True)` 再通过参数跳过部分阶段。该路径默认语义包含 DXM03 复刻、1688 确认和云仓添加，和本需求相反，风险高。

## 验收

1. 计划模式能扫描最近 15 天产品，并包含已有部分 SKU 配置的产品。
2. 执行模式只写本地 `media_product_skus`，不会调用 DXM03 商品复刻、1688 确认、小秘云仓添加或采购价刷新函数。
3. 本地已有配置的 SKU 行不会被覆盖。
4. 明空高确定性命中的空白 SKU 行会写入明空 SKU 配对字段。
5. 明空无数据或低置信候选只保留空白基底并计入挂起。
6. 报告能直接回答同步 SKU 总数、新品同步完成数、挂起/未同步产品数和明细原因。
7. 聚焦测试优先：

```bash
python scripts/pytest_related.py --base origin/master --run
pytest tests/test_mingkong_local_pairing_15d.py tests/test_mingkong_unprocessed_sku_backfill.py tests/test_mingkong_weekly_sync_orchestrator.py -q
```

本需求不默认跑全量 `pytest -q`。不连接 Windows 本机 MySQL；如需真实数据验证，只能在测试服务器或线上服务器环境执行。

## 回滚

- 关闭 local-only 批量入口即可停止后续写入。
- 本地写入的 `source='mingkong_local_pairing_15d'` SKU 行可按报告中的 `product_id` / `shopify_variant_id` 审核；不能默认批量删除人工后续改过的行。
- 报告文件保留，作为后续人工补配和复盘依据。
