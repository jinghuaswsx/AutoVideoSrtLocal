# Block 4 — 产品上下文注入 + 长视频批间上下文（P1）

- **日期**: 2026-06-12
- **状态**: Approved（待实施）
- **总览**: [2026-06-12-omni-quality-overview.md](2026-06-12-omni-quality-overview.md)（红线必读）
- **实施计划**: [plans/2026-06-12-omni-quality-block4-context-enrichment.md](../plans/2026-06-12-omni-quality-block4-context-enrichment.md)
- **改动层**: prompt 拼装与任务元数据，不动收敛与时长逻辑 → 音画对齐零影响

## 背景与问题

1. **翻译对产品一无所知**：初译 prompt 是通用电商文案写手角色，没有产品名、类目、卖点。视频里 ASR 出的品牌词/型号/规格，模型只能瞎猜译法。而批量翻译链路（任务中心 → `appcore/bulk_translate_runtime.py`）本身就带 `product_id`，产品库有名称（含多语言）/类目等数据——数据在链路上却没喂给翻译。品牌词/规格的译法准确性是电商翻译质量的最大单点杠杆。
2. **长视频分批翻译批间零上下文**：`pipeline/translate.py::_generate_localized_translation_batched`（段数 >18 触发，12 段/批）每批 user 消息只含本批原文——看不到全局原文、看不到前批译文。批边界处术语不一致（同一产品词前后两种译法）、代词指代断裂、语气跳变。

## 目标

1. omni 任务可携带 `product_context`（产品名/类目/卖点/品牌词），批量链路自动注入，初译与 rewrite/压缩重译的 prompt 都带上。
2. 分批翻译时每批附带全局原文 + 已译前文尾部 + 术语一致性指令。

## 非目标

- 不做产品术语库/翻译记忆系统（YAGNI；先把已有数据用起来）。
- 不改单任务创建 UI（手动输入产品信息的表单留作后续可选任务，本块只做批量链路自动注入 + task_state 字段，单任务无 product_context 时行为与现状完全一致）。
- 不动 multi 模块（`runtime_multi.py` 不读该字段，零影响）。

## 需求细则

### R1 task_state 字段 `product_context`

结构（全部可空，存在才注入）：

```json
{
  "name": "产品名（运营主名称）",
  "name_target_lang": "目标语言官方名（如产品库有对应语言名）",
  "category": "类目",
  "selling_points": ["卖点1", "卖点2"],
  "brand_terms": ["必须保留原样的品牌词/型号"]
}
```

### R2 批量链路自动注入

- `appcore/bulk_translate_runtime.py` 创建 omni 子任务处：用在手的 `product_id` 查产品库（**实施时调研**：grep 产品 DAO / `products` 表结构 / 任务中心按语言的产品名称表；最低交付 `name` + `category`，多语言名与卖点字段存在则一并带上），把 `product_context` 写进子任务 task_state / 创建参数。
- 查询失败或字段为空 → 不写字段，静默跳过（不得阻塞任务创建）。

### R3 Prompt 注入

- `appcore/runtime_omni_steps.py::step_translate_standard`：task 含非空 `product_context` 时，system prompt（`runner._build_system_prompt(lang)` 产物 + INPUT NOTICE 之后）追加：

```
PRODUCT CONTEXT (authoritative product facts — use them to translate product
references correctly):
- Product name: {name}
- Official name in target language: {name_target_lang}（有才输出此行）
- Category: {category}
- Key selling points: {selling_points 分号连接}（有才输出）
- Brand terms to keep verbatim: {brand_terms 逗号连接}（有才输出）
When the script mentions the product, use the official target-language name
verbatim. Never translate brand terms.
```

- rewrite 链路：`appcore/runtime_omni.py::OmniLocalizationAdapter.build_localized_rewrite_messages` 的 user content 在 ORIGINAL VIDEO TRANSCRIPT 段之前追加同样的 PRODUCT CONTEXT 段（adapter 构造时从 task 取 `product_context` 传入，仿 `original_asr_text` 的传递方式）。压缩重译终轮（Block 3）走同一 builder 自动受益。语言定制 rewrite 入口（例如日语字符预算 rewrite）也必须等价携带 PRODUCT CONTEXT，不能因绕过通用 builder 而丢失产品名 / 品牌词约束。
- 注入文本统一由新函数 `pipeline/localization.py::build_product_context_block(product_context: dict) -> str` 生成（空 dict / 全空字段 → 返回 ""），两处调用，禁止复制粘贴模板。

### R4 批间上下文

`_generate_localized_translation_batched` 对第 2 批起的每批，messages 构建时额外注入（实现方式：`build_localized_translation_messages` 增加可选参数 `batch_context: str | None = None`，非空时拼在 user content 末尾）：

```
GLOBAL CONTEXT (for consistency; translate ONLY the segments above):
Full source script:
{全局 source_full_text}

Previous batch translation (continue seamlessly from here, keep terminology
and tone consistent):
{前一批译文最后 3 句}
```

- 全局原文超过 4000 字符时截断为前 2000 + 后 1000 字符（防 token 失控），中间以 `...` 标注。
- 第 1 批不注入（无前文）；单批路径（短视频）完全不变。

## 验收标准

1. 单测：`build_product_context_block` 空/部分/全字段三态；`step_translate_standard` 注入后 system prompt 含 PRODUCT CONTEXT（mock task）；通用 rewrite 与日语定制 duration rewrite 均携带 PRODUCT CONTEXT；批 2+ 的 user content 含 GLOBAL CONTEXT 且第 1 批不含；无 `product_context` 任务的 messages 与现状逐字节一致（回归保护）。
2. `python3 scripts/pytest_related.py --base origin/master --run` 通过。
3. 人工验收：从任务中心发起一条批量翻译，任务详情的"初始翻译 prompt"artifact（`localized_translate_messages.json`）可见 PRODUCT CONTEXT 段且产品名正确。
