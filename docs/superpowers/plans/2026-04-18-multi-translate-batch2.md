# 多语种视频翻译 — 第 2 批实施计划（es / it / pt）

**Goal:** 扩展第 1 批骨架支持西班牙语（es）、意大利语（it）、葡萄牙语（pt）。

**Architecture:** 零骨架改动。每种语言只需：
1. 新增 `pipeline/languages/<lang>.py` 规则文件（字幕 / TTS 语言码 / 前后处理）
2. 在 `pipeline/languages/prompt_defaults.py` 补 3 条 base prompt（translation / tts_script / rewrite）
3. 更新 `pipeline/languages/registry.py` 的 `SUPPORTED_LANGS`
4. 更新 `web/routes/multi_translate.py` 的 `SUPPORTED_LANGS` 白名单

**参考:** [第 1 批设计稿](../specs/2026-04-18-multi-translate-design.md) §6 各语言字幕参数汇总

---

## 每语言字幕规则

| lang | max_chars | CPS | 特殊前处理 | 特殊后处理 |
|---|---|---|---|---|
| es | 42 | 17 | 疑问/感叹句首补 `¿ ¡` | 无 |
| it | 42 | 17 | 保护缩合词 `l'/d'/c'` | 无 |
| pt | 42 | 17 | 保护缩合词 `d'/n'` | 无 |

---

## Task B2-1: 新增 `pipeline/languages/es.py`

西班牙语规则。西班牙本土市场（es-ES）为基准，LLM 输出尽量中性词汇兼容拉美。

**前处理**：自动补倒问号/倒感叹号——LLM 可能忘记加，在分句边界检测疑问/感叹句尾并回补起点符号。实现为启发式：句子以 `?`/`!` 结尾就在句首补 `¿`/`¡`（若句首已存在则跳过）。

## Task B2-2: 新增 `pipeline/languages/it.py`

意大利语规则。无显著标点特殊规则（不像法语 nbsp）。弱边界词覆盖冠词 / 介词 / 缩合。

## Task B2-3: 新增 `pipeline/languages/pt.py`

葡萄牙语规则。默认目标欧洲葡萄牙语（pt-PT）；LLM prompt 里说明可以保留部分 Brasil 常用英语借词。弱边界词同构。

## Task B2-4: 在 `pipeline/languages/prompt_defaults.py` 补 9 条 base prompt

3 语言 × 3 slot（base_translation / base_tts_script / base_rewrite）= 9 条。

## Task B2-5: 更新 `SUPPORTED_LANGS` 白名单

- `pipeline/languages/registry.py`: `("de", "fr")` → `("de", "fr", "es", "it", "pt")`
- `web/routes/multi_translate.py`: 同上
- `web/routes/admin_prompts.py::page`: `langs=["de", "fr"]` → `langs=["de", "fr", "es", "it", "pt"]`
- `web/templates/multi_translate_list.html`: 胶囊按钮 labels 已含 es/it/pt（第 1 批模板已预置），无需改

## Task B2-6: 测试

- 扩展 `tests/test_languages_registry.py` 新增 3 条 test（es/it/pt）
- 扩展 `tests/test_prompt_defaults.py` 新增覆盖率检查
- 跑全套 multi-translate 测试保证无回归

## 不做的事

- 不做 es 倒问号倒感叹号的自动纠正（LLM prompt 要求即可，代码层不做兜底）—— Update: 简单实现
- 不做 pt-PT 与 pt-BR 分支，默认 pt-PT
- 不做拉美西语 dialect 分支
- 不做 ja（第 3 批）
