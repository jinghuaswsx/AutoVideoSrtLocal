# Block 1 — Prompt 正确性 + 首句 Hook / 尾句 CTA 职责（P0）

- **日期**: 2026-06-12
- **状态**: Approved（待实施）
- **总览**: [2026-06-12-omni-quality-overview.md](2026-06-12-omni-quality-overview.md)（红线必读）
- **实施计划**: [plans/2026-06-12-omni-quality-block1-prompt-correctness.md](../plans/2026-06-12-omni-quality-block1-prompt-correctness.md)
- **改动层**: 纯 prompt / 消息构建 / 工具脚本，不动任何收敛与时长逻辑 → 音画对齐零影响

## 背景与问题

1. **user message 硬编码 "Source Chinese"**：`pipeline/localization.py` 的 `build_localized_translation_messages()` 无论源语言是什么，user 消息都写 `Source Chinese full text:` / `Source Chinese segments:`。omni 支持 11 种源语言；es 源时 system prompt 的 INPUT NOTICE 说"源是 ES"、user 消息却说"中文原文"，自相矛盾的指令直接喂给模型。
2. **es/it/pt/de 初译 prompt 残留 "ASCII punctuation only"**：`pipeline/languages/prompt_defaults.py` 中 it / pt 的 base_translation 写 `ASCII punctuation only`，es 写 `ASCII punctuation plus ¿ ¡ only`（¿¡ 本身不是 ASCII，自相矛盾），de 同时要求 umlaut/ß 又写 `ASCII punctuation only`。`docs/superpowers/specs/2026-04-30-translate-quality-eval-report.md` §4.2 已实证该措辞会让严格遵守的模型剥掉重音（意大利语整段破读）。当前 Gemini Flash 不严守所以侥幸，模型一换就复发。
3. **nl/sv/fi generic 模板源语言错位**：`_build_generic_translation` 写死 `Recreate the English script`，而 omni 默认 asr_clean 路径源语言保持原语言（zh→sv 时输入根本不是英文）。
4. **首句 Hook / 尾句收尾职责缺失**（产品新需求）：base_translation 没有要求第一句承担前 3 秒钩子功能、最后一句承担收尾/CTA 功能；base_rewrite 没有禁止删改首尾句——shrink 时模型可能把钩子削平、把结尾砍掉。
5. **DB seed 不同步**：prompt 运行时 DB（`llm_prompt_configs`）优先，改代码默认值不会生效。2026-04-30 的 ASCII 修复就只改了一处导致问题残留至今。缺一个对比/重 seed 工具。
6. **遗留 fallback prompt 未修复**：`pipeline/localization.py` 的 `LOCALIZED_REWRITE_SYSTEM_PROMPT` 仍写 `Plain ASCII punctuation only`；中文展示版 `LOCALIZED_TRANSLATION_SYSTEM_PROMPT_ZH` / `HOOK_CTA_TRANSLATION_SYSTEM_PROMPT_ZH` 也写"仅使用纯 ASCII 标点"（管理员可能参照它写自定义 prompt）。

## 目标

1. 翻译消息中的源语言标签按任务实际 `source_language` 动态生成。
2. 清除所有语言 prompt 中"会被理解为禁用重音字母"的措辞，明确"标点限制不涉及字母，重音/特殊字母为强制"。
3. nl/sv/fi generic 模板改为源语言中立表述。
4. 11 个目标语言的 base_translation 与 base_rewrite 全部加入"首句 Hook + 尾句收尾/CTA"职责与保护条款。
5. 交付 `scripts/reseed_prompt_defaults.py` DB 同步工具，并在验收清单中包含"上线后 dry-run + 人工确认 apply"。

## 非目标

- 不动 `ecommerce_plugin`（其"不发明 CTA + 源 CTA 保留意图"条款保持现状）。
- 不动 multi_translate 模块的任何文件（`runtime_multi.py` 等零触碰；`localization.py` 是共享 pipeline 文件，允许改，但所有改动必须保持 multi 调用方的默认行为不变——即新增参数必须带向后兼容默认值）。
- 不为 nl/sv/fi 编写完整定制 prompt（词汇表/hook 模式等留到后续独立任务），本块只修"明显错误"。
- 不改任何模型绑定。

## 需求细则

### R1 源语言动态标签

- `build_localized_translation_messages()` 新增参数 `source_language: str = "zh"`（默认值保证 multi 等旧调用方行为完全不变）。
- user content 模板改为 `Source {label} full text:` / `Source {label} segments:`，label 用 ISO 码 → 英文语言名映射（Chinese / English / Spanish / Portuguese / French / Italian / Japanese / German / Dutch / Swedish / Finnish；未知码原样用大写 ISO 码）。映射表放 `localization.py` 模块级常量 `SOURCE_LANG_PROMPT_LABEL`，并让 `appcore/runtime_omni.py` 的 `OmniLocalizationAdapter._SOURCE_LANG_LABEL` 改为引用它（消除双份维护）。
- 透传链：`generate_localized_translation(..., source_language=...)` → `_generate_localized_translation_single` / `_generate_localized_translation_batched` → `build_localized_translation_messages`。
- 调用方更新：仅 `appcore/runtime_omni_steps.py::step_translate_standard`（该函数作用域内已有 `source_language` 变量）。**不改 `runtime_multi.py`**（它不传参，默认 "zh"，行为不变）。

### R2 标点措辞修复（es/it/pt/de + generic + 遗留）

统一替换原则：删除一切 `ASCII punctuation only` 类表述，改为如下语义（措辞可由实施者润色，要素不可少）：

> Do not use em-dashes or en-dashes. Use standard punctuation. Accented and special letters required by {语言} orthography are MANDATORY — never strip accents or transliterate them.

各语言要素：
- **es**：保留 ¿/¡ 强制条款；明确 `á é í ó ú ñ ü` 必须保留。
- **it**：明确 `à è é ì ò ù` 必须保留。
- **pt**：明确 `ã õ á é ê ç` 必须保留。
- **de**：删除 `ASCII punctuation only` 字样（与既有 "native German umlaut letters and Eszett" 条款矛盾），保留 umlaut/ß 强制条款。
- **en**：保持 ASCII 限制不变（英语无重音需求，不要顺手改）。
- **generic（nl/sv/fi）**：`_build_generic_translation` 中 `Recreate the English script` → `Recreate the source script (it may be in any language)`；`use plain punctuation only` 行改为上面的统一语义（nl `ë ï ĳ` / sv `å ä ö` / fi `ä ö` 必须保留）。
- **遗留同步**：`localization.py` 的 `LOCALIZED_REWRITE_SYSTEM_PROMPT`、`LOCALIZED_TRANSLATION_SYSTEM_PROMPT_ZH`、`HOOK_CTA_TRANSLATION_SYSTEM_PROMPT_ZH` 同样修正措辞；并 grep 确认 `build_localized_rewrite_messages`（localization.py 版本）是否仍有运行时调用方——若没有，函数与常量上方加 `# DEPRECATED: omni/multi 运行时走 per-language base_rewrite（DB），本常量仅作 fallback` 注释，避免下次再被误当唯一修改点。

### R3 首句 Hook / 尾句收尾职责（11 语种）

**base_translation**（11 个语种 + generic builder）统一追加一段（英文指令，放在各 prompt 的 FORMATTING/STYLE 段之前或之后均可，但必须独立成段、标题固定为 `OPENING & ENDING (mandatory):` 以便测试断言）：

```
OPENING & ENDING (mandatory):
- Sentence 1 is the 3-second hook (roughly the first 7-10 words). Rewrite the
  source opening into a strong hook — clear outcome, obvious benefit, curiosity,
  or surprise contrast — WITHOUT inventing facts not implied by the source.
- The final sentence must properly close the script. If the source ends with a
  CTA or wrap-up, preserve that intent in natural local phrasing. Never invent
  a new CTA (a universal CTA clip is appended later). Never end mid-thought.
```

（ja 版词数说法可改为「最初の文＝冒頭3秒のフック」语义等价表述，或直接用英文指令——实施者二选一，测试只断言标题行存在。）

**base_rewrite**（11 语种 + generic builder）统一追加：

```
OPENING & ENDING PROTECTION (mandatory):
- Sentence 1 must keep functioning as the 3-second hook.
- The final sentence must keep its closing / CTA intent.
- When shrinking, remove modifiers, repetition, and secondary details from the
  MIDDLE of the script. Never delete or flatten the hook sentence or the
  closing sentence.
```

### R4 DB 重 seed 工具

`scripts/reseed_prompt_defaults.py`：
- 无参运行 = dry-run：遍历 `pipeline.languages.prompt_defaults.DEFAULTS`，对每个 (slot, lang) 比较 DB `llm_prompt_configs.content` 与出厂默认，输出 `SAME` / `DIFF`（DIFF 时打印 unified diff）/ `MISSING`（DB 无行）。
- `--apply --yes [--slot S] [--lang L]`：用出厂默认 upsert 覆盖 DB（走 `appcore.llm_prompt_configs.upsert`），打印覆盖了哪些行。无 `--yes` 时拒绝执行。
- 退出码：dry-run 有 DIFF 时返回 1（便于 CI/人工感知），其余 0。

### R5 测试要求

- `tests/test_prompt_defaults_quality.py`（新）：
  - es/it/pt/de 的 base_translation content 不含子串 `ASCII punctuation only`；
  - es 含 `¿`、it 含 `à è é ì ò ù` 列举、pt 含 `ã`、de 含 `Eszett`（或 `ß`）；
  - 11 个 (base_translation, lang) 的 content 均含 `OPENING & ENDING`；11 个 (base_rewrite, lang) 均含 `OPENING & ENDING PROTECTION`；
  - generic 模板产物不含 `English script`。
- `tests/test_localization_messages.py`（新或并入现有）：`build_localized_translation_messages(..., source_language="es")` 的 user content 含 `Source Spanish full text` 且不含 `Source Chinese`；不传参时仍为 `Source Chinese full text`（multi 兼容回归）。
- reseed 脚本：mock DB 的单测覆盖 SAME/DIFF/MISSING 与 `--apply` 路径。

## 验收标准

1. 上述全部测试通过；`python3 scripts/pytest_related.py --base origin/master --run` 通过。
2. 人工抽查：创建一个 es 源 → en 目标的 omni V2 任务（或单测断言 messages），确认 user 消息标签正确、INPUT NOTICE 与 user 消息不再矛盾。
3. 交付物包含 reseed 脚本使用说明（写在脚本 docstring）；验收时在测试环境跑一次 dry-run 并贴输出。
4. 改动文件清单不含 `appcore/runtime_multi.py`、`appcore/runtime/_pipeline_runner.py`、任何 tts/时长相关文件。
