# 最近 15 天新品明空到 DXM03 完整同步设计

日期：2026-06-11

## 文档锚点

- `AGENTS.md`：文档驱动代码、worktree 隔离、本地 MySQL 禁止和 focused pytest 规则。
- `docs/superpowers/specs/2026-06-09-mingkong-product-library-foundation-design.md`：明空产品库、Shopify variant 基底和已配置 SKU 保护规则。
- `docs/superpowers/specs/2026-06-09-mingkong-pairing-full-procurement-closure-design.md`：明空候选加载、本地 SKU 导入、DXM03 写入和采购闭环边界。
- `docs/superpowers/specs/2026-06-05-dianxiaomi-sku-purchase-sync-design.md`：`media_product_skus` 与 `dianxiaomi_yuncang_skus` 的采购价语义。
- 已废弃旧文档：`docs/superpowers/specs/2026-06-10-mingkong-15d-local-pairing-report-design.md`。

## 背景

素材管理 SKU 模块已有明空产品库、明空配对工作台和一条旧的批量闭环链路：先建立本地 Shopify SKU 基底，再把明空/DXM02 的 SKU 商品信息同步到我们自己的 DXM03 店小秘系统，随后确认 1688/采购配对，加入 DXM03 小秘云仓，并把云仓单价回写为本地采购价。

此前 2026-06-10 的 local-only 版本错误地把需求理解成“只同步到我们本地系统，禁止 DXM03/云仓”。用户已在 2026-06-11 明确修正：这次要完整落地到我们自己的 DXM03 店小秘系统，操作范围是最近 15 天新增产品。

## 目标

1. 候选范围固定为素材管理里最近 15 天新增产品：`media_products.created_at >= 当前服务器时间 - 15 天`。
2. 以我们商品的 Shopify 链接 / Shopify variant 作为 SKU 基准，不能以明空返回的 SKU 行数作为唯一基准。
3. 本地已经配置过的 SKU 行保持不动；同一产品里未配置的 SKU / variant 仍继续执行完整同步。
4. 对本地未配置且明空/DXM02 数据确定的 SKU，执行完整闭环：
   - 写入/补齐本地 `media_product_skus`。
   - 把明空店小秘 SKU 和产品信息同步到我们 DXM03 店小秘系统。
   - 确认 DXM03 采购/1688 配对。
   - 加入 DXM03 小秘云仓。
   - 刷新并回写采购价。
   - 同步或补齐物流与包装信息。
5. 明空无数据、低置信、多候选冲突、关键字段缺失或无法确认的 SKU 不猜测写入，进入挂起报告。
6. 执行结束输出详细报告，说明：
   - 一共同步了多少个 SKU 到 DXM03 / 我们系统。
   - 最近 15 天新增产品里多少个完成完整同步。
   - 多少个产品部分完成、挂起、失败或跳过。
   - 每个阶段的原因：本地保护、DXM03 复制、DXM03 配对确认、云仓入库、采购价、物流包装。

## 非目标

- 不从 DXM03 反向覆盖已人工维护的本地 SKU 配置。
- 不覆盖本地已配置 SKU 行的人工字段、真实 `dianxiaomi_sku`、`dianxiaomi_product_sku`、`dianxiaomi_sku_code`、人工商品名或人工采购价。
- 不对低置信或多候选明空数据做自动猜测。
- 不连接 Windows 本机 MySQL；真实数据验证只能在测试服务器或线上服务器环境执行。
- 不在没有明确“发测试 / 上线”的情况下重启服务。

## 候选范围

候选商品查询：

1. `media_products.deleted_at IS NULL`。
2. `media_products.created_at >= cutoff`，其中 `cutoff = 当前服务器时间 - 15 天`。
3. 默认排除归档产品：`COALESCE(archived, 0)=0`。
4. 默认只处理上架产品：`listing_status IS NULL OR listing_status='上架'`。
5. 产品必须有可解析的 `product_code`、`product_link` 或 `shopifyid`。缺少 Shopify 基准的产品进入 `blocked_no_shopify_base` 报告。

与旧 `find_unprocessed_products()` 不同，本模式不能因为产品已有部分已配置 SKU 就整品排除。已配置行只保护对应 variant；同一产品里的空白 variant 仍要尝试明空配对和 DXM03 完整同步。

## 本地保护规则

已配置 SKU 行的判断沿用现有 `tools/mingkong_unprocessed_sku_backfill.py::is_configured_local_sku_row` 语义：

- `manual_override=1`。
- `manual_unit_price_rmb` 不为空。
- `manual_goods_name` 不为空。
- 已有真实 `dianxiaomi_sku`，且不是 Shopify variant id 自动占位。
- `dianxiaomi_product_sku` 或 `dianxiaomi_sku_code` 不为空。

处理规则：

- 已配置行不覆盖、不删除、不参与 DXM03 重写。
- 未配置行可以写入明空确定数据，并进入 DXM03 复制、确认、云仓和采购价链路。
- 如果一个产品所有 SKU 行都已配置，本轮状态为 `already_configured`，不做 DXM03 写入。

## 完整同步流程

每个候选产品按以下顺序处理：

1. 读取本地 `media_product_skus`，标记 protected variant。
2. 构建明空配对工作台 payload，包含 Shopify 基底和明空/DXM02 reference。
3. 使用现有 `build_default_targets()` 生成目标 SKU selections。
4. 使用 `pairing.build_target_sku_import_pairs()` 生成本地 SKU 写入 pairs。
5. 对 protected variant 执行字段保护，只让未配置 variant 进入后续 action 集合。
6. 写入本地 SKU：
   - 保护模式下只 upsert 未保护 variant，不删除已配置行。
   - source 使用完整同步语义，例如 `mingkong_15d_dxm03_full_sync`。
7. 如果没有任何可写入且可同步的明空 SKU：
   - 有 protected 行但无新增 action：`already_configured`。
   - 无明空 SKU：`blocked_no_mingkong_skus`。
8. 调用 `pairing.replicate_mingkong_skus_to_dxm03()`：
   - 把明空/DXM02 SKU 和产品信息复制到我们 DXM03。
   - 如果 DXM03 已有 SKU，复用现有商品，并尝试补齐物流与包装缺失字段。
   - 对组合 SKU 按现有逻辑处理组件；已有组合 SKU 的物流包装不能用普通商品接口盲目覆盖。
9. 调用 `pairing.confirm_dxm03_pairing()`：
   - 用 selections 中的 1688 商品 / SKU 信息确认 DXM03 采购配对。
   - 默认 `preserve_existing_pairing=True`，不覆盖已存在且确定的配对；需要覆盖时必须显式参数。
10. 调用 `dianxiaomi_yuncang.add_product_skus_to_yuncang()`：
    - 把确认后的 DXM03 SKU 加入我们的小秘云仓。
    - 云仓已有 SKU 视为 `already_exists`，并参与后续采购价刷新。
11. 云仓入库后刷新本地 `dianxiaomi_yuncang_skus`，并回写 `media_products.purchase_price`。
12. 输出阶段报告。

## 采购价和物流包装

采购价：

- 采购价来源为 DXM03 小秘云仓 SKU 单价。
- 云仓添加/已存在后，调用现有云仓刷新逻辑，把 `dianxiaomi_yuncang_skus.unit_price` 回写到 `media_products.purchase_price`。
- 如果云仓成功但没有可用单价，产品状态不能算完全成功，应在报告中标记 `purchase_price_missing`。

物流包装：

- 新复制到 DXM03 的 SKU 应继承明空/DXM02 商品详情里的物流与包装字段。
- DXM03 已存在 SKU 时，允许补齐缺失字段，但不覆盖已有非空字段。
- 组合 SKU 若现有逻辑标记为不能用普通商品接口自动补齐，则报告为 `logistics_packaging_skipped_combo_existing`。

## CLI 和执行模式

废弃旧入口：

```bash
python tools/mingkong_local_pairing_15d.py --execute
```

推荐新入口：

```bash
python tools/mingkong_recent_15d_full_sync.py --days 15 --plan
python tools/mingkong_recent_15d_full_sync.py --days 15 --execute
```

参数：

- `--days`，默认 15。
- `--limit`，默认 0 表示不限。
- `--execute`，执行真实写入；未传时只 plan。
- `--include-archived`，默认不包含归档。
- `--include-unlisted`，默认只处理上架。
- `--force-refresh-mingkong`，强制刷新明空/DXM02 reference。
- `--overwrite-existing-pairing`，默认关闭；打开才允许覆盖 DXM03 已有采购配对。
- `--product-delay-seconds`，产品间隔。

## 报告口径

报告写入：`output/mingkong_recent_15d_full_sync/`。

`summary` 至少包含：

- `candidate_product_count`
- `scanned_product_count`
- `completed_product_count`
- `already_configured_product_count`
- `partial_product_count`
- `suspended_product_count`
- `failed_product_count`
- `synced_sku_count`
- `protected_sku_count`
- `dxm03_replicated_sku_count`
- `dxm03_existing_sku_count`
- `dxm03_confirmed_sku_count`
- `yuncang_added_sku_count`
- `yuncang_existing_sku_count`
- `purchase_price_updated_product_count`
- `purchase_price_missing_product_count`
- `logistics_packaging_updated_sku_count`
- `logistics_packaging_skipped_sku_count`

每个产品明细至少包含：

- `product_id`
- `product_code`
- `product_name`
- `created_at`
- `status`
- `local_import`
- `replicate`
- `confirm`
- `yuncang`
- `purchase_price_status`
- `logistics_packaging_summary`
- `sku_details`
- `message`

标准产品状态：

- `completed`：本轮需要同步的 SKU 全部完成 DXM03 复制/复用、确认、云仓和采购价闭环。
- `already_configured`：最近 15 天产品已全部配置，无新增 action。
- `partial`：部分 SKU 完成，部分 SKU 挂起或失败。
- `suspended`：没有 SKU 完成，且存在可解释挂起原因。
- `failed`：执行异常。

## 实现方案

推荐方案：复用并收紧现有 `run_product_sync()` 完整链路。

理由：

- 现有链路已经包含 DXM03 复制、DXM03 配对确认、云仓添加、采购价刷新和物流包装补齐。
- 需要新增的是“最近 15 天候选选择”和“保护已配置 SKU 但继续处理未配置 variant”的批量入口。
- 避免重新实现 Playwright/RPA 细节，降低线上执行风险。

具体方向：

- 保留旧 `run_product_sync()` 的完整阶段，但调整 15 天模式默认 `protect_configured_local_skus=True`。
- 新增候选 selector，不复用会整品排除已配置 SKU 的 `find_unprocessed_products()`。
- 新增 full-sync batch/CLI/report，替换或废弃 local-only CLI。
- 修改旧 local-only 测试，改为断言 full-sync 会调用 DXM03/云仓链路，同时保护已配置本地行。

## 验收

1. plan 模式能扫描最近 15 天新增产品，且不会因产品已有部分已配置 SKU 而整品排除。
2. execute 模式对未配置 SKU 执行完整链路：本地导入、DXM03 复制、DXM03 确认、小秘云仓、采购价刷新、物流包装同步。
3. 本地已配置 SKU 行不被覆盖、不被删除、不参与 DXM03 重写。
4. DXM03 已存在 SKU 时复用并补齐物流包装缺失字段。
5. 云仓已有 SKU 或新加入 SKU 都能参与采购价刷新。
6. 报告能回答 SKU 同步总数、新品完成数、挂起/失败/部分完成原因。
7. focused tests：

```bash
python scripts/pytest_related.py --base origin/master --run
python -m pytest tests/test_mingkong_unprocessed_sku_backfill.py tests/test_mingkong_pairing_workbench.py tests/test_mingkong_weekly_sync_orchestrator.py -q
```

本需求不默认运行全量 `pytest -q`。不得连接 Windows 本机 MySQL。

## 回滚

- 未执行生产写入前：删除或停用新 CLI 即可。
- 已执行 DXM03/云仓写入后：不能用批量删除回滚，必须按报告里的 product/SKU 明细人工审查 DXM03、云仓和本地 SKU 状态。
- 本地保护规则避免覆盖人工配置；如仍需恢复，按报告中的 `product_id` / `shopify_variant_id` 审核单项恢复。
