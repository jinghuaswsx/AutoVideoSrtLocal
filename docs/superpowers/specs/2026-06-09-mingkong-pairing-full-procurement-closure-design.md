# 明空配对全量采购闭环设计

日期：2026-06-09

## 背景

运营从明空搬一个新品到我们自己的店铺后，DXM03 店小秘里可能已经有在线商品链接，但我们系统和 DXM03 的商品 SKU、采购链接、1688 规格配对、小秘云仓采购价和采购建议链路还没有建好。现有“明空配对”工作台已经能展示候选、导入 SKU、复刻普通 SKU、确认 1688 配对，但它仍是多按钮半自动流程，且采购价、组合 SKU、小秘云仓采购建议/缺货建议没有闭环。

本设计把“明空配对”升级为一个可执行的同步计划：先以产品 code 精确关联明空产品库；规则不足时用 OpenRouter Gemini 3.1 Flash-Lite 做图片和标题辅助判断；人工确认或高置信校验后，把 SKU、采购链接、供应商、采购价校验和 DXM03 配对全部同步到我们系统，并给采购建议/缺货建议生成采购订单打通可验证入口。

## 事实来源

- `AGENTS.md`：文档驱动代码、定时任务登记、测试与发布门禁。
- `docs/superpowers/specs/2026-06-09-mingkong-product-library-foundation-design.md`：明空产品库、SKU、采购链接、组合组件与工作台现状。
- `docs/superpowers/specs/2026-06-05-dianxiaomi-sku-purchase-sync-design.md`：DXM03 SKU 与小秘云仓采购价同步模型。
- `docs/superpowers/specs/2026-06-05-xmyc-retirement-dianxiaomi-yuncang-design.md`：采购价唯一活跃来源为店小秘小秘云仓。
- `docs/superpowers/specs/2026-05-08-cdp-environment-split-design.md`：DXM02-MK 使用 `127.0.0.1:9223`，DXM03-RJC 使用 `127.0.0.1:9225`。
- `docs/superpowers/plans/2026-04-19-llm-call-unification.md` 与 `appcore/llm_client.py`：新 LLM 业务必须走统一 use case/billing 入口。

## 目标

1. 新增“执行完整同步”后端状态机，把明空候选加载、本地 SKU 导入、DXM03 SKU 复刻、1688 采购配对、DXM03 小秘云仓采购价校验串成一个可运行流程。
2. 明空产品库补充明空小秘云仓采购价缓存，单独保存 DXM02-MK 采购价，不能混入 DXM03 的 `dianxiaomi_yuncang_skus`。
3. 工作台展示明空采购价、DXM03 小秘云仓采购价、供应商、库存、采购链接、1688 商品 ID、1688 SKU ID 和校验状态。
4. 本地 SKU 不存在时自动用明空库写入；本地 SKU 已存在但来源不是人工维护时，允许管理员选择“按明空覆盖/修正”，人工维护行仍保留。
5. 普通 SKU 自动创建或复用 DXM03 商品，确认 1688 规格配对，并用 DXM03 小秘云仓结果确认“可采购”。
6. 组合 SKU 必须组件优先：先复刻普通组件并配对采购，再创建或校验外层组合 SKU；外层组合 SKU 不要求直接 1688 配对。
7. AI 只做辅助判断，输出候选是否同品、置信度、风险和需要人工确认的字段；AI 结果不得绕过规则校验直接写 DXM03。
8. 为采购建议/缺货建议生成采购订单提供探测、校验和接口封装；未完成接口实测时，工作台必须显示“采购建议接口待确认”，不得伪造已闭环。

## 2026-06-09 追加：按钮精简与三栏确认弹窗

现有工作台右上角存在“同步明空 SKU 到我们系统”“复刻明空 SKU”“同步明空店小秘SKU”三个执行按钮，实际对应同一条采购 SKU 闭环的不同阶段。后续页面只保留一个主执行入口“同步明空店小秘SKU”，刷新状态仍为只读动作。

点击“同步明空店小秘SKU”时不得立即写入 DXM03，而是打开确认弹窗：

1. 弹窗左栏展示明空 SKU 数据，包括明空 SKU 图片、变体、店小秘 SKU、ERP 编码、商品名、供应商、1688 商品 ID、1688 SKU ID 和采购链接。
2. 弹窗中栏展示当前我们系统 / DXM03 的 SKU 情况，包括图片、变体、我们系统 SKU、ERP 编码、商品名、采购链接、DXM03 商品状态和已配对信息。
3. 弹窗右栏展示“执行后的目标效果”，初始值从明空 SKU 数据推导，所有写入字段可由用户手动编辑，包括变体、SKU、ERP 编码、商品名、采购链接、1688 商品 ID、1688 SKU ID 和图片 URL。
4. SKU 对应关系必须带图片；无图片时明确显示“无图”，不得让用户只能靠文字判断。
5. 用户点击底部确认按钮后，后端才按目标列执行：先写入/覆盖我们系统 SKU，再复刻 DXM03 商品管理 SKU，最后确认 DXM03 1688 采购规格配对。
6. 确认执行后，弹窗顶部展示执行阶段、进度、耗时、逐 SKU 日志和报错；失败或阻断必须保持弹窗可见，方便用户继续查看并调整目标字段。

## 非目标

- 不把 DXM02 的账号主键、仓库主键、供应商主键直接写到 DXM03。
- 不跨账号复用 DXM02 的 `pairProductId`。
- 不在模型判断低置信或字段缺失时自动写 DXM03。
- 不把 DXM02 明空小秘云仓采购价写入 DXM03 云仓表；DXM03 采购价以 DXM03 自己的小秘云仓同步结果为准。

## 数据扩展

### `mingkong_yuncang_skus`

保存 DXM02-MK 明空小秘云仓货品数据。

- `sku`
- `sku_code`
- `goods_name`
- `stock_available`
- `unit_price`
- `raw_json`
- `synced_at`

该表只代表明空账号的采购价参考，不参与我们产品成本最终计算。

### `mingkong_pairing_execution_runs`

保存单品闭环执行记录。

- `id`
- `product_id`
- `product_code`
- `status`：`running` / `success` / `blocked` / `failed`
- `mode`：`plan` / `execute`
- `started_at`
- `finished_at`
- `created_by`
- `plan_json`
- `result_json`
- `ai_review_json`
- `logs_json`
- `error_message`

### 采购价字段口径

- 明空参考采购价：`mingkong_yuncang_skus.unit_price`。
- 我们采购价：`dianxiaomi_yuncang_skus.unit_price`。
- 本地产品成本：继续按 `media_product_skus.manual_unit_price_rmb`、`dianxiaomi_yuncang_skus.unit_price`、`media_products.purchase_price` 的既有优先级。

## 状态机

### 1. 计划阶段

输入：`media_products.id`。

步骤：

1. 读取本地产品 code、产品链接、Shopify ID、图片、已有 SKU。
2. 去掉 `-rjc` 后从明空产品库精确匹配。
3. 本地库无结果时实时访问 DXM02-MK 补采当前 product code，并回写明空库。
4. 生成候选 SKU 行、组合组件、采购链接和明空云仓采购价。
5. 如果候选超过一个、标题/图片明显冲突、SKU 数量不一致或采购链接缺规格，则调用 `mingkong_pairing.match_candidate`。
6. 输出可执行计划：`ready`、`needs_review`、`blocked`。

### 2. 执行阶段

仅当计划为 `ready`，或管理员确认 `needs_review` 后执行。

步骤：

1. 本地 SKU 导入：
   - 空本地 SKU：直接写入明空 SKU。
   - 非人工来源 SKU：允许按明空覆盖。
   - 人工维护 SKU：保留并标记需人工确认。
2. DXM03 SKU 复刻：
   - 普通 SKU：复用或创建 DXM03 商品管理 SKU。
   - 组合组件：先处理组件普通 SKU。
   - 外层组合：组件全部存在后再创建或校验外层组合关系。
3. DXM03 1688 配对：
   - 普通 SKU 必须有 1688 商品 ID 与 1688 SKU ID。
   - 组合 SKU 校验组件采购配对，外层不直接配 1688。
4. DXM03 云仓采购价校验：
   - 触发或读取 `dianxiaomi_yuncang_skus`。
   - 每个可采购 SKU 至少要能在 DXM03 小秘云仓命中采购价或明确显示缺口。
5. 写执行记录，刷新工作台。

### 3. 采购建议/缺货建议阶段

目标页面来自 DXM03 店小秘小秘云仓的采购建议/缺货建议。首版先做接口探测与校验：

1. 用 DXM03 CDP 打开采购建议/缺货建议页面。
2. 监听 XHR，识别建议列表、选中货品、生成采购单接口。
3. 对目标 SKU 做只读定位，确认它出现在建议列表且供应商/采购价/采购链接完整。
4. 接口确认后才开放“一键生成采购订单”动作。

在接口未确认前，工作台展示“采购建议下单接口待确认”，但不阻断 SKU 与采购配对闭环。

## AI 辅助判断

新增 use case：

- `mingkong_pairing.match_candidate`
- 默认 provider：`openrouter`
- 默认 model：`google/gemini-3-flash-preview`
- 输入：我们产品标题、产品 code、产品链接、主图 URL、明空候选标题、SKU 图片、采购商品标题、1688 规格文本。
- 输出 JSON：
  - `is_same_product`
  - `confidence`
  - `reason`
  - `risks`
  - `requires_manual_review`
  - `variant_mapping_notes`

门禁：

- product code 精确命中且 SKU 数量、变体文本、采购链接都完整时，不调用 AI。
- AI 置信度低于 0.85 或 `requires_manual_review=true` 时，只展示建议，不自动执行。
- AI 不参与 DXM03 写入参数生成，只给计划阶段打分。
- 工作台首版提供“AI辅助判断”按钮，由人工主动触发；模型输出候选排序、置信度、风险和变体映射说明，只展示在确认弹窗中，最终仍以右侧目标字段和人工确认按钮为准。

## 验收

1. 数据迁移可重复执行，新增明空云仓表和执行记录表。
2. 明空库 SKU rows 能合并明空云仓采购价并在工作台展示。
3. 新增“一键同步采购 SKU”接口：
   - 本地无 SKU 时能从明空库导入。
   - 能调用现有复刻和确认流程。
   - 返回逐步 logs、blocked 原因和最终 ready 状态。
4. AI 匹配 helper 有单元测试，覆盖高置信、低置信、JSON 解析失败 fallback。
5. 普通 SKU 闭环测试覆盖：导入、复刻、确认、DXM03 云仓价校验。
6. 组合 SKU 闭环测试覆盖：组件先行，外层组合缺实现时明确 blocked。
7. 采购建议/缺货建议接口未实测时，接口返回 `procurement_order_probe_required`，不伪造成功。
8. 聚焦测试优先：

```bash
python3 scripts/pytest_related.py --base origin/master --run
pytest tests/test_mingkong_pairing_workbench.py tests/test_mingkong_product_library.py tests/test_dianxiaomi_yuncang_storage.py tests/test_llm_use_cases_registry.py -q
```

## 回滚

- 关闭工作台“一键同步采购 SKU”入口即可回到原三按钮流程。
- 新增表只读保留，不影响既有 SKU、云仓、订单同步。
- AI use case 可在后台 binding 中禁用或改回只规则匹配。
