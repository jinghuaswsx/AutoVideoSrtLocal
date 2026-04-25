# ASR Normalize（en-US 标准化）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `multi_translate` 流水线 ASR 之后插入新 step `asr_normalize`，先用 Gemini Flash 检测原文语言，再按路由用 Claude Sonnet 把任意源语言 ASR 文本统一标准化为 en-US，下游 alignment / translate / tts / subtitle 全部从英文源出发。

**Architecture:** 新 step 封装在独立模块 `pipeline/asr_normalize.py`（detect → 路由 → translate）；通过 `MultiTranslateRunner._step_asr_normalize` 串入状态机，紧跟 `voice_match` 之前；新增 4 条 LLM use_case + 4 条 default prompt 走项目标准 `appcore.llm_client` + `llm_prompt_configs` 通路；下游 alignment 入口加 `utterances_en or utterances` fallback；`utterances` 永远是 ASR 原文，`utterances_en` 是中转过的英文。

**Tech Stack:** Python 3 / Flask / Jinja2 / pytest（mock-only，不连 MySQL）/ JavaScript（vanilla）/ Gemini AIStudio（detect）/ OpenRouter Claude Sonnet 4.6（translate）。

**Spec:** [docs/superpowers/specs/2026-04-25-asr-normalize-en-design.md](../specs/2026-04-25-asr-normalize-en-design.md)

**Worktree:** 实施前请用 `superpowers:using-git-worktrees` skill 在 `.worktrees/asr-normalize-en` 创建独立分支 `feature/asr-normalize-en`（基于 master HEAD）。下面所有 `git -C .worktrees/asr-normalize-en` 命令都假定在该 worktree 内执行。

---

## 文件结构总览

| 文件 | 创建 / 修改 | 责任 |
|------|------------|------|
| `pipeline/asr_normalize.py` | **创建** | detect_language / translate_to_en / run_asr_normalize 三大函数 + 自定义异常 + LANG_LABELS 常量 |
| `appcore/llm_use_cases.py` | 修改 | `USE_CASES` 字典末尾追加 4 条 `asr_normalize.*` |
| `pipeline/languages/prompt_defaults.py` | 修改 | 新增 4 段 prompt 字符串（detect / es→en / generic→en / zh→en）+ DEFAULTS 字典末尾追加 4 条 `("asr_normalize.*", "")` |
| `appcore/runtime_multi.py` | 修改 | 新增 `_step_asr_normalize` 方法；`_get_pipeline_steps` 在 asr 后插入 asr_normalize（voice_match 之前） |
| `web/routes/multi_translate.py` | 修改 | RESUMABLE_STEPS 元组追加 `"asr_normalize"`；alignment 入口（`build_script_segments`）改读 `utterances_en or utterances`；`upload_and_start` docstring 更新；保留 update_source_language 路由 |
| `web/templates/multi_translate_list.html` | 修改 | 上传弹窗"自动识别中文/英文"提示文案改为"自动识别原视频语言并标准化" |
| `web/templates/multi_translate_detail.html` | 修改 | 进度条插入"原文标准化"格；详情区新增 artifact 展示卡片（前后对照 + token + 路由） |
| `tests/test_asr_normalize_use_cases.py` | **创建** | 守 4 条新 use_case 在 USE_CASES 字典中存在且字段正确 |
| `tests/test_asr_normalize_prompts.py` | **创建** | 守 4 条新 prompt 在 DEFAULTS 中存在 + 关键词检查 |
| `tests/test_asr_normalize.py` | **创建** | `pipeline.asr_normalize` 单元测试（mock LLM） |
| `tests/test_runtime_multi_asr_normalize.py` | **创建** | `MultiTranslateRunner._step_asr_normalize` 集成测试 |
| `tests/test_multi_translate_routes.py` | 修改 | 增 RESUMABLE_STEPS 含 `asr_normalize` 用例；增 alignment 入口 utterances_en fallback 用例 |

---

## Task 1: 注册 4 条 LLM use_case（TDD）

**Files:**
- Create: `tests/test_asr_normalize_use_cases.py`
- Modify: `appcore/llm_use_cases.py`（在 `USE_CASES` 字典末尾、`get_use_case` 函数之前追加 4 条）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_asr_normalize_use_cases.py`：

```python
"""appcore.llm_use_cases 中 4 条 asr_normalize.* use_case 守护测试。"""
from appcore.llm_use_cases import USE_CASES, get_use_case


def test_four_asr_normalize_use_cases_registered():
    assert "asr_normalize.detect_language" in USE_CASES
    assert "asr_normalize.translate_zh_to_en" in USE_CASES
    assert "asr_normalize.translate_es_to_en" in USE_CASES
    assert "asr_normalize.translate_generic_to_en" in USE_CASES


def test_detect_use_case_uses_gemini_flash_lite():
    uc = get_use_case("asr_normalize.detect_language")
    assert uc["default_provider"] == "gemini_aistudio"
    assert uc["default_model"] == "gemini-3.1-flash-lite-preview"
    assert uc["module"] == "video_translate"
    assert uc["units_type"] == "tokens"
    assert uc["usage_log_service"] == "gemini"


def test_translate_use_cases_use_openrouter_claude_sonnet():
    for code in (
        "asr_normalize.translate_zh_to_en",
        "asr_normalize.translate_es_to_en",
        "asr_normalize.translate_generic_to_en",
    ):
        uc = get_use_case(code)
        assert uc["default_provider"] == "openrouter"
        assert uc["default_model"] == "anthropic/claude-sonnet-4.6"
        assert uc["module"] == "video_translate"
        assert uc["units_type"] == "tokens"
        assert uc["usage_log_service"] == "openrouter"
```

- [ ] **Step 2: 跑测试，验证全部 FAIL（KeyError 或 AssertionError）**

```bash
cd g:/Code/AutoVideoSrtLocal && python -m pytest .worktrees/asr-normalize-en/tests/test_asr_normalize_use_cases.py -v 2>&1 | tail -10
```

预期：3 FAILED — `'asr_normalize.detect_language' not in USE_CASES` 等。

- [ ] **Step 3: 在 `appcore/llm_use_cases.py` 的 `USE_CASES` 字典末尾（视频创作 `video_creation.generate` 之后、`}` 闭合之前）追加：**

```python
    # 原文标准化（ASR 后插入步骤）
    "asr_normalize.detect_language": _uc(
        "asr_normalize.detect_language",
        "video_translate",
        "原文语言识别",
        "ASR 完成后识别原视频语言以决定标准化路由",
        "gemini_aistudio",
        "gemini-3.1-flash-lite-preview",
        "gemini",
        "tokens",
    ),
    "asr_normalize.translate_zh_to_en": _uc(
        "asr_normalize.translate_zh_to_en",
        "video_translate",
        "中文 ASR → en-US 标准化",
        "中文素材 ASR 文本翻译为 en-US（注册保留，runner 当前路由跳过）",
        "openrouter",
        "anthropic/claude-sonnet-4.6",
        "openrouter",
        "tokens",
    ),
    "asr_normalize.translate_es_to_en": _uc(
        "asr_normalize.translate_es_to_en",
        "video_translate",
        "西语 ASR → en-US 标准化",
        "西班牙语素材 ASR 文本精修翻译为 en-US",
        "openrouter",
        "anthropic/claude-sonnet-4.6",
        "openrouter",
        "tokens",
    ),
    "asr_normalize.translate_generic_to_en": _uc(
        "asr_normalize.translate_generic_to_en",
        "video_translate",
        "任意源 → en-US 标准化（兜底）",
        "白名单内非中英文素材 ASR 文本通用翻译为 en-US",
        "openrouter",
        "anthropic/claude-sonnet-4.6",
        "openrouter",
        "tokens",
    ),
```

- [ ] **Step 4: 跑测试，验证全部 PASS**

```bash
cd g:/Code/AutoVideoSrtLocal && python -m pytest .worktrees/asr-normalize-en/tests/test_asr_normalize_use_cases.py -v 2>&1 | tail -10
```

预期：3 passed。

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/asr-normalize-en add appcore/llm_use_cases.py tests/test_asr_normalize_use_cases.py
git -C .worktrees/asr-normalize-en commit -m "$(cat <<'EOF'
feat(asr-normalize): register 4 LLM use_cases

asr_normalize.detect_language uses gemini_aistudio + flash-lite-preview;
the three translate use_cases (zh/es/generic → en) all use openrouter +
anthropic/claude-sonnet-4.6. translate_zh_to_en is registered for completeness
but the runner does not currently route to it (zh keeps its direct path).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 注册 4 条 default prompt（TDD）

**Files:**
- Create: `tests/test_asr_normalize_prompts.py`
- Modify: `pipeline/languages/prompt_defaults.py`（在 `_EN_REWRITE` 字符串之后、`DEFAULTS` 字典之前插入 4 段 prompt 常量；DEFAULTS 字典末尾追加 4 条）

- [ ] **Step 1: 写失败测试**

新建 `tests/test_asr_normalize_prompts.py`：

```python
"""pipeline.languages.prompt_defaults 中 4 条 asr_normalize.* prompt 守护测试。"""
from pipeline.languages.prompt_defaults import DEFAULTS


def test_four_new_prompts_registered_with_empty_lang_key():
    for slot in (
        "asr_normalize.detect",
        "asr_normalize.translate_zh_en",
        "asr_normalize.translate_es_en",
        "asr_normalize.translate_generic_en",
    ):
        assert (slot, "") in DEFAULTS, f"missing default prompt: ({slot!r}, '')"


def test_detect_prompt_includes_supported_lang_enum():
    content = DEFAULTS[("asr_normalize.detect", "")]["content"]
    # 必须告诉模型可选 enum 包含全部 10 种白名单 + other
    for code in ("en", "zh", "es", "pt", "fr", "it", "ja", "nl", "sv", "fi", "other"):
        assert f'"{code}"' in content, f"detect prompt missing language code {code!r}"
    # 必须明确 JSON 输出 schema 字段
    for field in ("language", "confidence", "is_mixed"):
        assert field in content


def test_es_translate_prompt_includes_en_us_vocab_anchors():
    content = DEFAULTS[("asr_normalize.translate_es_en", "")]["content"]
    # 必须含 en-US 反翻译陷阱锚点
    for token in ("sneakers", "apartment", "elevator"):
        assert token in content
    # 必须明确 1:1 映射要求
    assert "1:1 mapping by index" in content
    # 必须明确 ASCII 标点要求
    assert "ASCII punctuation only" in content


def test_generic_translate_prompt_handles_is_mixed_and_low_confidence_flags():
    content = DEFAULTS[("asr_normalize.translate_generic_en", "")]["content"]
    assert "is_mixed" in content
    assert "low_confidence" in content
    assert "1:1 mapping by index" in content
    assert "ASCII punctuation only" in content


def test_zh_translate_prompt_keeps_us_voice():
    """zh→en prompt 注册保留，确保至少结构与其他三条一致。"""
    content = DEFAULTS[("asr_normalize.translate_zh_en", "")]["content"]
    assert "1:1 mapping by index" in content
    assert "en-US" in content
```

- [ ] **Step 2: 跑测试，验证 5 个用例 FAIL（KeyError）**

```bash
cd g:/Code/AutoVideoSrtLocal && python -m pytest .worktrees/asr-normalize-en/tests/test_asr_normalize_prompts.py -v 2>&1 | tail -15
```

预期：5 FAILED。

- [ ] **Step 3: 在 `pipeline/languages/prompt_defaults.py` 中 `_EN_REWRITE = """..."""` 字符串之后、`DEFAULTS = {` 之前插入 4 段常量**

```python
# ── 原文标准化（asr_normalize 步骤，lang 字段使用空字符串占位）──
_DETECT_PROMPT = """You are a language identification expert for short-form video ASR transcripts (TikTok / Reels / Shorts e-commerce content).

Given a raw ASR transcript (which may be 5–500+ words and may contain transcription noise),
return strictly valid JSON shaped exactly as:
{"language": "...", "confidence": 0.0-1.0, "is_mixed": true/false}

LANGUAGE CODE (must be one of):
- "en" — English (US/UK/AU all collapse to en)
- "zh" — Chinese (Mandarin / Cantonese / mainland / Taiwan all collapse to zh)
- "es" — Spanish (any region)
- "pt" — Portuguese (any region)
- "fr" — French
- "it" — Italian
- "ja" — Japanese
- "nl" — Dutch
- "sv" — Swedish
- "fi" — Finnish
- "other" — anything else (Korean, Russian, Arabic, Vietnamese, Thai, Hindi, German, …)

CONFIDENCE:
- 0.95+ : long clean transcript, dominant single language, no noise
- 0.7–0.95 : clear language but some noise / short transcript
- 0.5–0.7 : short transcript or moderate noise
- <0.5 : very short / mostly noise / hard to determine

IS_MIXED:
- true if 30%+ of meaningful tokens come from a different language (code-switching scenarios common in beauty/tech vlogs)
- false otherwise

Return JSON only. No prose. No markdown fences."""


_TRANSLATE_ZH_TO_EN = """You are a US-based short-form commerce content creator translating a Chinese ASR transcript into natural en-US for downstream localization.

INPUT FORMAT (JSON in user message):
{
  "source_language": "zh",
  "full_text": "全文中文文本",
  "utterances": [{"index": 0, "text": "..."}, ...]
}

OUTPUT FORMAT (JSON only, no prose):
{
  "utterances_en": [{"index": 0, "text_en": "..."}, ...]
}

REQUIREMENTS:
- 1:1 mapping by index. Output utterances_en MUST have the same length as input utterances. Every input index must appear exactly once.
- Use full_text as global context to resolve pronouns and ambiguous references, but emit per-utterance translations.
- Recreate, don't translate literally. Use natural en-US e-commerce vocabulary (sneakers / pants / apartment / fall, NOT trainers / trousers / flat / autumn). US spelling (color / favorite / organize). $ before price.
- Casual conversational tone, default "you", contractions natural ("you'll", "don't", "it's").
- NO hype phrases, NO "link in bio" CTAs, NO em/en-dashes, NO curly quotes — ASCII punctuation only.
- Preserve meaning faithfully; do NOT add facts or product features that aren't in the source.
- Keep each utterance roughly the same word count as its Chinese counterpart (downstream alignment relies on per-utterance pacing)."""


_TRANSLATE_ES_TO_EN = """You are a US-based short-form commerce content creator translating a Spanish ASR transcript into natural en-US for downstream localization.

INPUT FORMAT (JSON in user message):
{
  "source_language": "es",
  "full_text": "texto completo en español",
  "utterances": [{"index": 0, "text": "..."}, ...]
}

OUTPUT FORMAT (JSON only, no prose):
{
  "utterances_en": [{"index": 0, "text_en": "..."}, ...]
}

REQUIREMENTS:
- 1:1 mapping by index. Output utterances_en MUST have the same length as input utterances. Every input index must appear exactly once.
- Use full_text as global context to resolve pronouns, gendered references, and ambiguous antecedents, but emit per-utterance translations.

VOCABULARY (Spanish → en-US e-commerce, common pitfalls):
- "móvil / celular" → smartphone (NOT mobile)
- "ordenador / computadora" → laptop / computer
- "zapatillas / tenis" → sneakers (NOT trainers)
- "pantalón" → pants (NOT trousers)
- "piso / departamento" → apartment (NOT flat)
- "ascensor" → elevator (NOT lift)
- "maquillaje / labial / base / rímel" → makeup / lipstick / foundation / mascara
- "organizador / caja" → organizer / storage box
- US spelling (color / favorite / organize), $ before price ($9.99), imperial units when natural.

TONE:
- Casual conversational, default "you", contractions natural.
- NO hype ("game-changer", "literally amazing", "you NEED this", "obsessed", "last chance").
- NO "link in bio" / "swipe up" / "shop now" CTAs.
- ASCII punctuation only. No em-dashes, no en-dashes, no curly quotes.
- US number convention (2.5 not 2,5; 1,000 not 1.000).

PER-UTTERANCE LENGTH:
- Keep each utterance's English roughly proportional to its Spanish counterpart in word count (Spanish tends to be ~10% longer than English; a 12-word Spanish utterance should land 9–13 words in English). Downstream alignment depends on per-utterance pacing.

Recreate, don't translate literally. Preserve meaning faithfully; do NOT invent product features."""


_TRANSLATE_GENERIC_TO_EN = """You are a US-based short-form commerce content creator translating an ASR transcript into natural en-US for downstream localization.

INPUT FORMAT (JSON in user message):
{
  "source_language": "<ISO code: pt/fr/it/ja/nl/sv/fi/...>",
  "is_mixed": true/false,
  "low_confidence": true/false,
  "full_text": "...",
  "utterances": [{"index": 0, "text": "..."}, ...]
}

OUTPUT FORMAT (JSON only, no prose):
{
  "utterances_en": [{"index": 0, "text_en": "..."}, ...]
}

REQUIREMENTS:
- 1:1 mapping by index. Output utterances_en MUST have the same length as input utterances. Every input index must appear exactly once.
- Use full_text as global context to resolve pronouns and ambiguous references; emit per-utterance translations.
- If is_mixed=true: translate ALL spans into en-US regardless of which sub-language they come from. Do not drop or summarize the minority-language portions.
- If low_confidence=true: rely on textual cues; if a clause is genuinely incomprehensible, transliterate it (do NOT invent content).

VOCABULARY (en-US e-commerce):
- sneakers, pants, apartment, elevator, fall, trash can, smartphone, laptop, headphones, charger
- US spelling (color, favorite, organize); $ before price; imperial units when natural

TONE:
- Casual conversational, default "you", contractions natural ("you'll", "don't", "it's").
- NO hype phrases, NO "link in bio" / "swipe up" CTAs.
- ASCII punctuation only. No em-dashes, no en-dashes, no curly quotes.
- US number convention (2.5 not 2,5; 1,000 not 1.000).

PER-UTTERANCE LENGTH:
- Keep each utterance's English roughly proportional to its source counterpart in word count. Downstream alignment depends on per-utterance pacing.

Recreate, don't translate literally. Preserve meaning faithfully; do NOT invent product features."""
```

- [ ] **Step 4: 在 `DEFAULTS` 字典末尾（最后一条 `("base_rewrite", "en")` 之后、`}` 闭合之前）追加 4 条**

```python
    # 原文标准化（asr_normalize 步骤；lang 字段为空字符串占位）
    ("asr_normalize.detect", ""): {
        "provider": "gemini_aistudio", "model": "gemini-3.1-flash-lite-preview",
        "content": _DETECT_PROMPT,
    },
    ("asr_normalize.translate_zh_en", ""): {
        "provider": _DEFAULT_PROVIDER, "model": _DEFAULT_MODEL,
        "content": _TRANSLATE_ZH_TO_EN,
    },
    ("asr_normalize.translate_es_en", ""): {
        "provider": _DEFAULT_PROVIDER, "model": _DEFAULT_MODEL,
        "content": _TRANSLATE_ES_TO_EN,
    },
    ("asr_normalize.translate_generic_en", ""): {
        "provider": _DEFAULT_PROVIDER, "model": _DEFAULT_MODEL,
        "content": _TRANSLATE_GENERIC_TO_EN,
    },
```

注意：`_DEFAULT_PROVIDER` 和 `_DEFAULT_MODEL` 在文件顶部已定义为 `"openrouter"` 和 `"openai/gpt-4o-mini"`。但本次我们想要 Claude Sonnet。所以**不能直接复用** —— 改用字面量：

```python
    ("asr_normalize.translate_zh_en", ""): {
        "provider": "openrouter", "model": "anthropic/claude-sonnet-4.6",
        "content": _TRANSLATE_ZH_TO_EN,
    },
    ("asr_normalize.translate_es_en", ""): {
        "provider": "openrouter", "model": "anthropic/claude-sonnet-4.6",
        "content": _TRANSLATE_ES_TO_EN,
    },
    ("asr_normalize.translate_generic_en", ""): {
        "provider": "openrouter", "model": "anthropic/claude-sonnet-4.6",
        "content": _TRANSLATE_GENERIC_TO_EN,
    },
```

（这部分覆盖前一段写法：4 条字典里 detect 用 gemini_aistudio + flash-lite-preview，三条 translate 用 openrouter + claude-sonnet-4.6。）

- [ ] **Step 5: 跑测试，验证全部 PASS**

```bash
cd g:/Code/AutoVideoSrtLocal && python -m pytest .worktrees/asr-normalize-en/tests/test_asr_normalize_prompts.py -v 2>&1 | tail -15
```

预期：5 passed。

- [ ] **Step 6: Commit**

```bash
git -C .worktrees/asr-normalize-en add pipeline/languages/prompt_defaults.py tests/test_asr_normalize_prompts.py
git -C .worktrees/asr-normalize-en commit -m "$(cat <<'EOF'
feat(asr-normalize): seed 4 default prompts for detect + translate

Adds detect (Gemini Flash Lite) prompt with 10-language whitelist enum, plus
three Claude Sonnet translate prompts (zh→en, es→en, generic→en). Lang field
in DEFAULTS key uses empty string placeholder since these prompts are not bound
to a target language. Auto-seeded into llm_prompt_configs by resolve_prompt_config.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 创建 `pipeline/asr_normalize.py` 骨架（异常类 + 常量 + LANG_LABELS）

**Files:**
- Create: `pipeline/asr_normalize.py`
- Create: `tests/test_asr_normalize.py`（暂只放骨架级 import 测试）

- [ ] **Step 1: 写失败测试 — 检查模块导出符号**

新建 `tests/test_asr_normalize.py`：

```python
"""pipeline.asr_normalize 单元测试。"""
from __future__ import annotations

import pytest


def test_module_exports_required_symbols():
    from pipeline import asr_normalize
    assert hasattr(asr_normalize, "DETECT_SUPPORTED_LANGS")
    assert hasattr(asr_normalize, "LOW_CONFIDENCE_THRESHOLD")
    assert hasattr(asr_normalize, "LANG_LABELS")
    assert hasattr(asr_normalize, "DetectLanguageFailedError")
    assert hasattr(asr_normalize, "UnsupportedSourceLanguageError")
    assert hasattr(asr_normalize, "TranslateOutputInvalidError")
    assert hasattr(asr_normalize, "detect_language")
    assert hasattr(asr_normalize, "translate_to_en")
    assert hasattr(asr_normalize, "run_asr_normalize")


def test_detect_supported_langs_excludes_other():
    from pipeline.asr_normalize import DETECT_SUPPORTED_LANGS
    assert DETECT_SUPPORTED_LANGS == ("en", "zh", "es", "pt", "fr", "it", "ja", "nl", "sv", "fi")
    assert "other" not in DETECT_SUPPORTED_LANGS


def test_low_confidence_threshold_is_06():
    from pipeline.asr_normalize import LOW_CONFIDENCE_THRESHOLD
    assert LOW_CONFIDENCE_THRESHOLD == 0.6


def test_lang_labels_covers_all_supported():
    from pipeline.asr_normalize import LANG_LABELS, DETECT_SUPPORTED_LANGS
    for code in DETECT_SUPPORTED_LANGS:
        assert code in LANG_LABELS, f"LANG_LABELS missing {code!r}"
    # 中文/西班牙语/英语必须有人话标签
    assert LANG_LABELS["zh"] == "中文"
    assert LANG_LABELS["es"] == "西班牙语"
    assert LANG_LABELS["en"] == "英语"
```

- [ ] **Step 2: 跑测试，验证 4 个用例 FAIL（ModuleNotFoundError 或 AttributeError）**

```bash
cd g:/Code/AutoVideoSrtLocal && python -m pytest .worktrees/asr-normalize-en/tests/test_asr_normalize.py -v 2>&1 | tail -15
```

预期：4 FAILED。

- [ ] **Step 3: 创建 `pipeline/asr_normalize.py`（暂只含异常类、常量、空函数 stub）**

```python
"""ASR 后置 en-US 标准化步骤。

接 ASR 输出的 `utterances`（带时间戳的逐句文本，可能是任意语言），先用 Gemini Flash
检测原文语言，再按路由：
- en → 跳过（直接用原 utterances）
- zh → 跳过（保留中文路径）
- es → 走西语精修 prompt 翻译为 en-US 句级 utterances_en
- pt/fr/it/ja/nl/sv/fi → 走通用兜底 prompt
- other（白名单外） → 抛 UnsupportedSourceLanguageError

句级输出 1:1 映射回原 utterances 的 start/end 时间戳；调用方将结果写到
task["utterances_en"]，下游 alignment 入口走 utterances_en or utterances fallback。
"""
from __future__ import annotations

import json
import time
from typing import Any

from appcore import llm_client
from appcore.llm_prompt_configs import resolve_prompt_config


DETECT_SUPPORTED_LANGS: tuple[str, ...] = (
    "en", "zh", "es", "pt", "fr", "it", "ja", "nl", "sv", "fi",
)

LOW_CONFIDENCE_THRESHOLD: float = 0.6

LANG_LABELS: dict[str, str] = {
    "en": "英语",
    "zh": "中文",
    "es": "西班牙语",
    "pt": "葡萄牙语",
    "fr": "法语",
    "it": "意大利语",
    "ja": "日语",
    "nl": "荷兰语",
    "sv": "瑞典语",
    "fi": "芬兰语",
}


class DetectLanguageFailedError(RuntimeError):
    """detect API 重试耗尽仍失败。"""


class UnsupportedSourceLanguageError(RuntimeError):
    """detect 出 language='other'，超出当前流水线支持范围。"""


class TranslateOutputInvalidError(RuntimeError):
    """Claude 翻译输出 schema 不合法（长度对不上 / index 缺漏 / text_en 为空）。"""


def detect_language(full_text: str, *, task_id: str, user_id: int | None) -> tuple[dict, dict]:
    """detect_language 占位 — Task 4 实现。"""
    raise NotImplementedError


def translate_to_en(
    utterances: list[dict],
    detected_language: str,
    *,
    route: str,
    task_id: str,
    user_id: int | None,
) -> tuple[list[dict], dict]:
    """translate_to_en 占位 — Task 5 实现。"""
    raise NotImplementedError


def run_asr_normalize(
    *,
    task_id: str,
    user_id: int | None,
    utterances: list[dict],
) -> dict:
    """run_asr_normalize 占位 — Task 6 实现。"""
    raise NotImplementedError
```

- [ ] **Step 4: 跑测试，验证全部 PASS**

```bash
cd g:/Code/AutoVideoSrtLocal && python -m pytest .worktrees/asr-normalize-en/tests/test_asr_normalize.py -v 2>&1 | tail -15
```

预期：4 passed（symbols / DETECT_SUPPORTED_LANGS / threshold / LANG_LABELS）。

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/asr-normalize-en add pipeline/asr_normalize.py tests/test_asr_normalize.py
git -C .worktrees/asr-normalize-en commit -m "$(cat <<'EOF'
feat(asr-normalize): scaffold module with constants and exception classes

New module pipeline/asr_normalize.py exposes DETECT_SUPPORTED_LANGS (10 lang
codes, no "other"), LOW_CONFIDENCE_THRESHOLD=0.6, LANG_LABELS for UI rendering,
and three sentinel exceptions (DetectLanguageFailedError /
UnsupportedSourceLanguageError / TranslateOutputInvalidError). Function bodies
are NotImplementedError stubs filled in by subsequent tasks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 实现 `detect_language`（TDD）

**Files:**
- Modify: `tests/test_asr_normalize.py`（追加 detect 相关用例）
- Modify: `pipeline/asr_normalize.py`（实现 `detect_language` 函数 + `_parse_detect_result`）

- [ ] **Step 1: 在 `tests/test_asr_normalize.py` 末尾追加测试用例**

```python
from unittest.mock import MagicMock, patch


@patch("pipeline.asr_normalize.resolve_prompt_config")
@patch("pipeline.asr_normalize.llm_client.invoke_chat")
def test_detect_language_normal_returns_parsed_dict_and_usage(
    mock_invoke, mock_resolve,
):
    mock_resolve.return_value = {"content": "DETECT_PROMPT_FAKE"}
    mock_invoke.return_value = {
        "text": '{"language":"es","confidence":0.97,"is_mixed":false}',
        "usage": {"input_tokens": 320, "output_tokens": 40},
    }
    from pipeline.asr_normalize import detect_language
    parsed, usage = detect_language("Hola, este es un producto", task_id="t1", user_id=1)
    assert parsed == {"language": "es", "confidence": 0.97, "is_mixed": False}
    assert usage == {"input_tokens": 320, "output_tokens": 40}
    mock_invoke.assert_called_once()


@patch("pipeline.asr_normalize.time.sleep")  # 跳过真实 sleep
@patch("pipeline.asr_normalize.resolve_prompt_config")
@patch("pipeline.asr_normalize.llm_client.invoke_chat")
def test_detect_language_retries_once_on_api_error(
    mock_invoke, mock_resolve, mock_sleep,
):
    mock_resolve.return_value = {"content": "DETECT_PROMPT_FAKE"}
    mock_invoke.side_effect = [
        Exception("network burp"),
        {"text": '{"language":"en","confidence":0.99,"is_mixed":false}',
         "usage": {"input_tokens": 100, "output_tokens": 30}},
    ]
    from pipeline.asr_normalize import detect_language
    parsed, _ = detect_language("Hello there", task_id="t2", user_id=1)
    assert parsed["language"] == "en"
    assert mock_invoke.call_count == 2
    mock_sleep.assert_called_once_with(2)


@patch("pipeline.asr_normalize.time.sleep")
@patch("pipeline.asr_normalize.resolve_prompt_config")
@patch("pipeline.asr_normalize.llm_client.invoke_chat")
def test_detect_language_fails_after_two_attempts(
    mock_invoke, mock_resolve, mock_sleep,
):
    mock_resolve.return_value = {"content": "X"}
    mock_invoke.side_effect = [Exception("fail1"), Exception("fail2")]
    from pipeline.asr_normalize import detect_language, DetectLanguageFailedError
    with pytest.raises(DetectLanguageFailedError) as exc_info:
        detect_language("foo", task_id="t3", user_id=1)
    assert "2 attempts" in str(exc_info.value)
    assert mock_invoke.call_count == 2


@patch("pipeline.asr_normalize.time.sleep")
@patch("pipeline.asr_normalize.resolve_prompt_config")
@patch("pipeline.asr_normalize.llm_client.invoke_chat")
def test_detect_language_handles_invalid_json_in_response(
    mock_invoke, mock_resolve, mock_sleep,
):
    mock_resolve.return_value = {"content": "X"}
    mock_invoke.return_value = {"text": "not json at all", "usage": {}}
    from pipeline.asr_normalize import detect_language, DetectLanguageFailedError
    # 第一次 JSON 解析失败被当作"模型输出异常" -> 重试一次仍是同样输出 -> fail
    with pytest.raises(DetectLanguageFailedError):
        detect_language("foo", task_id="t4", user_id=1)
```

- [ ] **Step 2: 跑这 4 个用例验证 FAIL（NotImplementedError）**

```bash
cd g:/Code/AutoVideoSrtLocal && python -m pytest .worktrees/asr-normalize-en/tests/test_asr_normalize.py -v -k detect_language 2>&1 | tail -15
```

预期：4 FAILED（NotImplementedError）。

- [ ] **Step 3: 在 `pipeline/asr_normalize.py` 中替换 `detect_language` stub 为完整实现，并新增 `_parse_detect_result`**

```python
def _parse_detect_result(raw_text: str) -> dict:
    """把 LLM 的 JSON 响应解析成 dict，做基本结构校验。"""
    payload = json.loads(raw_text)
    if not isinstance(payload, dict):
        raise ValueError("detect response is not a JSON object")
    for key in ("language", "confidence", "is_mixed"):
        if key not in payload:
            raise ValueError(f"detect response missing {key!r}")
    if not isinstance(payload["language"], str):
        raise ValueError("language must be string")
    if not isinstance(payload["confidence"], (int, float)):
        raise ValueError("confidence must be number")
    if not isinstance(payload["is_mixed"], bool):
        raise ValueError("is_mixed must be boolean")
    return {
        "language": payload["language"],
        "confidence": float(payload["confidence"]),
        "is_mixed": bool(payload["is_mixed"]),
    }


def detect_language(
    full_text: str, *, task_id: str, user_id: int | None,
) -> tuple[dict, dict]:
    """检测原文语言。返回 (parsed_dict, usage_tokens)。

    parsed_dict: {"language", "confidence", "is_mixed"}
    usage_tokens: {"input_tokens", "output_tokens"} or {} on failure
    """
    system_prompt = resolve_prompt_config("asr_normalize.detect", "")["content"]
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "detect_language_result",
            "schema": {
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "enum": list(DETECT_SUPPORTED_LANGS) + ["other"],
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "is_mixed": {"type": "boolean"},
                },
                "required": ["language", "confidence", "is_mixed"],
                "additionalProperties": False,
            },
        },
    }
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            result = llm_client.invoke_chat(
                "asr_normalize.detect_language",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": full_text[:4000]},
                ],
                user_id=user_id, project_id=task_id,
                temperature=0.0,
                response_format=response_format,
            )
            parsed = _parse_detect_result(result["text"])
            usage = result.get("usage") or {"input_tokens": None, "output_tokens": None}
            return parsed, usage
        except Exception as exc:
            last_exc = exc
            if attempt == 0:
                time.sleep(2)
                continue
    raise DetectLanguageFailedError(
        f"detect_language failed after 2 attempts: {last_exc}"
    )
```

- [ ] **Step 4: 跑全部 detect 用例 + 既有用例验证 PASS**

```bash
cd g:/Code/AutoVideoSrtLocal && python -m pytest .worktrees/asr-normalize-en/tests/test_asr_normalize.py -v 2>&1 | tail -20
```

预期：8 passed（4 既有 + 4 新增）。

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/asr-normalize-en add pipeline/asr_normalize.py tests/test_asr_normalize.py
git -C .worktrees/asr-normalize-en commit -m "$(cat <<'EOF'
feat(asr-normalize): implement detect_language with one retry

Calls Gemini via appcore.llm_client with json_schema response_format pinning
the 10-language whitelist + "other". On API/parse error, sleeps 2s then retries
once; second failure raises DetectLanguageFailedError. full_text capped at 4000
chars (detect doesn't need the entire transcript).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: 实现 `translate_to_en`（TDD）

**Files:**
- Modify: `tests/test_asr_normalize.py`（追加 translate 相关用例）
- Modify: `pipeline/asr_normalize.py`（实现 `translate_to_en` 函数 + `_USE_CASE_BY_ROUTE` / `_PROMPT_SLOT_BY_ROUTE` 映射）

- [ ] **Step 1: 在 `tests/test_asr_normalize.py` 末尾追加测试用例**

```python
@patch("pipeline.asr_normalize.resolve_prompt_config")
@patch("pipeline.asr_normalize.llm_client.invoke_chat")
def test_translate_to_en_preserves_timestamps_and_returns_usage(
    mock_invoke, mock_resolve,
):
    mock_resolve.return_value = {"content": "ES_PROMPT_FAKE"}
    mock_invoke.return_value = {
        "text": json.dumps({
            "utterances_en": [
                {"index": 0, "text_en": "Hi there"},
                {"index": 1, "text_en": "Check this out"},
            ],
        }),
        "usage": {"input_tokens": 1850, "output_tokens": 1620},
    }
    from pipeline.asr_normalize import translate_to_en
    utterances = [
        {"index": 0, "start": 0.5, "end": 2.3, "text": "Hola, este..."},
        {"index": 1, "start": 2.3, "end": 4.8, "text": "Mira esto"},
    ]
    out, usage = translate_to_en(
        utterances, detected_language="es", route="es_specialized",
        task_id="t10", user_id=1,
    )
    assert len(out) == 2
    assert out[0] == {"index": 0, "start": 0.5, "end": 2.3, "text": "Hi there"}
    assert out[1] == {"index": 1, "start": 2.3, "end": 4.8, "text": "Check this out"}
    assert usage == {"input_tokens": 1850, "output_tokens": 1620}


@patch("pipeline.asr_normalize.resolve_prompt_config")
@patch("pipeline.asr_normalize.llm_client.invoke_chat")
def test_translate_to_en_raises_on_length_mismatch(mock_invoke, mock_resolve):
    mock_resolve.return_value = {"content": "X"}
    mock_invoke.return_value = {
        "text": json.dumps({"utterances_en": [{"index": 0, "text_en": "Only one"}]}),
        "usage": {},
    }
    from pipeline.asr_normalize import translate_to_en, TranslateOutputInvalidError
    utterances = [
        {"index": 0, "start": 0, "end": 1, "text": "a"},
        {"index": 1, "start": 1, "end": 2, "text": "b"},
    ]
    with pytest.raises(TranslateOutputInvalidError) as exc:
        translate_to_en(utterances, detected_language="fr",
                         route="generic_fallback", task_id="t11", user_id=1)
    assert "length mismatch" in str(exc.value).lower()


@patch("pipeline.asr_normalize.resolve_prompt_config")
@patch("pipeline.asr_normalize.llm_client.invoke_chat")
def test_translate_to_en_raises_on_index_gap(mock_invoke, mock_resolve):
    mock_resolve.return_value = {"content": "X"}
    mock_invoke.return_value = {
        "text": json.dumps({"utterances_en": [
            {"index": 0, "text_en": "a"},
            {"index": 2, "text_en": "c"},  # missing index 1
        ]}),
        "usage": {},
    }
    from pipeline.asr_normalize import translate_to_en, TranslateOutputInvalidError
    utterances = [
        {"index": 0, "start": 0, "end": 1, "text": "x"},
        {"index": 1, "start": 1, "end": 2, "text": "y"},
    ]
    with pytest.raises(TranslateOutputInvalidError) as exc:
        translate_to_en(utterances, detected_language="fr",
                         route="generic_fallback", task_id="t12", user_id=1)
    assert "index" in str(exc.value).lower()


@patch("pipeline.asr_normalize.resolve_prompt_config")
@patch("pipeline.asr_normalize.llm_client.invoke_chat")
def test_translate_to_en_uses_es_use_case_for_es_specialized_route(
    mock_invoke, mock_resolve,
):
    mock_resolve.return_value = {"content": "X"}
    mock_invoke.return_value = {
        "text": json.dumps({"utterances_en": [{"index": 0, "text_en": "foo"}]}),
        "usage": {},
    }
    from pipeline.asr_normalize import translate_to_en
    translate_to_en(
        [{"index": 0, "start": 0, "end": 1, "text": "x"}],
        detected_language="es", route="es_specialized",
        task_id="t13", user_id=1,
    )
    # use_case 第一个位置参数
    assert mock_invoke.call_args.args[0] == "asr_normalize.translate_es_to_en"
    mock_resolve.assert_called_with("asr_normalize.translate_es_en", "")


@patch("pipeline.asr_normalize.resolve_prompt_config")
@patch("pipeline.asr_normalize.llm_client.invoke_chat")
def test_translate_to_en_uses_generic_use_case_for_fallback_routes(
    mock_invoke, mock_resolve,
):
    mock_resolve.return_value = {"content": "X"}
    mock_invoke.return_value = {
        "text": json.dumps({"utterances_en": [{"index": 0, "text_en": "foo"}]}),
        "usage": {},
    }
    from pipeline.asr_normalize import translate_to_en
    for route in ("generic_fallback", "generic_fallback_low_confidence",
                  "generic_fallback_mixed"):
        translate_to_en(
            [{"index": 0, "start": 0, "end": 1, "text": "x"}],
            detected_language="pt", route=route, task_id="t", user_id=1,
        )
    for call in mock_invoke.call_args_list:
        assert call.args[0] == "asr_normalize.translate_generic_to_en"


@patch("pipeline.asr_normalize.resolve_prompt_config")
@patch("pipeline.asr_normalize.llm_client.invoke_chat")
def test_translate_to_en_passes_is_mixed_low_confidence_in_user_payload(
    mock_invoke, mock_resolve,
):
    mock_resolve.return_value = {"content": "X"}
    mock_invoke.return_value = {
        "text": json.dumps({"utterances_en": [{"index": 0, "text_en": "foo"}]}),
        "usage": {},
    }
    from pipeline.asr_normalize import translate_to_en
    translate_to_en(
        [{"index": 0, "start": 0, "end": 1, "text": "x"}],
        detected_language="pt", route="generic_fallback_mixed",
        task_id="t", user_id=1,
    )
    user_msg = mock_invoke.call_args.kwargs["messages"][1]["content"]
    payload = json.loads(user_msg)
    assert payload["is_mixed"] is True
    assert payload["low_confidence"] is False
```

- [ ] **Step 2: 跑这 6 个用例验证 FAIL（NotImplementedError）**

```bash
cd g:/Code/AutoVideoSrtLocal && python -m pytest .worktrees/asr-normalize-en/tests/test_asr_normalize.py -v -k translate_to_en 2>&1 | tail -20
```

预期：6 FAILED（NotImplementedError）。

- [ ] **Step 3: 在 `pipeline/asr_normalize.py` 中替换 `translate_to_en` stub 为完整实现，并加路由映射常量**

```python
_USE_CASE_BY_ROUTE: dict[str, str] = {
    "es_specialized": "asr_normalize.translate_es_to_en",
    "generic_fallback": "asr_normalize.translate_generic_to_en",
    "generic_fallback_low_confidence": "asr_normalize.translate_generic_to_en",
    "generic_fallback_mixed": "asr_normalize.translate_generic_to_en",
}

_PROMPT_SLOT_BY_ROUTE: dict[str, str] = {
    "es_specialized": "asr_normalize.translate_es_en",
    "generic_fallback": "asr_normalize.translate_generic_en",
    "generic_fallback_low_confidence": "asr_normalize.translate_generic_en",
    "generic_fallback_mixed": "asr_normalize.translate_generic_en",
}


def translate_to_en(
    utterances: list[dict],
    detected_language: str,
    *,
    route: str,
    task_id: str,
    user_id: int | None,
) -> tuple[list[dict], dict]:
    """把 utterances 整体翻译为 en-US 句级。返回 (utterances_en, usage_tokens)。

    utterances_en 结构同 utterances（含 index/start/end/text），text 字段为英文。
    """
    if route not in _USE_CASE_BY_ROUTE:
        raise ValueError(f"translate_to_en got unsupported route: {route!r}")

    use_case_code = _USE_CASE_BY_ROUTE[route]
    prompt_slot = _PROMPT_SLOT_BY_ROUTE[route]
    system_prompt = resolve_prompt_config(prompt_slot, "")["content"]

    full_text = " ".join(u["text"] for u in utterances)
    user_payload = {
        "source_language": detected_language,
        "is_mixed": route == "generic_fallback_mixed",
        "low_confidence": route == "generic_fallback_low_confidence",
        "full_text": full_text,
        "utterances": [{"index": i, "text": u["text"]} for i, u in enumerate(utterances)],
    }

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "asr_normalize_translate_result",
            "schema": {
                "type": "object",
                "properties": {
                    "utterances_en": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "index": {"type": "integer", "minimum": 0},
                                "text_en": {"type": "string", "minLength": 1},
                            },
                            "required": ["index", "text_en"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["utterances_en"],
                "additionalProperties": False,
            },
        },
    }

    result = llm_client.invoke_chat(
        use_case_code,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        user_id=user_id, project_id=task_id,
        temperature=0.2,
        response_format=response_format,
    )

    payload = json.loads(result["text"])
    items = payload["utterances_en"]

    if len(items) != len(utterances):
        raise TranslateOutputInvalidError(
            f"length mismatch: input={len(utterances)} output={len(items)}",
        )
    by_index = {item["index"]: item["text_en"] for item in items}
    if set(by_index.keys()) != set(range(len(utterances))):
        missing = set(range(len(utterances))) - set(by_index.keys())
        raise TranslateOutputInvalidError(
            f"index coverage mismatch: missing {missing}",
        )

    utterances_en = [
        {
            "index": i,
            "start": utterances[i]["start"],
            "end": utterances[i]["end"],
            "text": by_index[i],
        }
        for i in range(len(utterances))
    ]
    usage = result.get("usage") or {"input_tokens": None, "output_tokens": None}
    return utterances_en, usage
```

- [ ] **Step 4: 跑全部用例验证 PASS**

```bash
cd g:/Code/AutoVideoSrtLocal && python -m pytest .worktrees/asr-normalize-en/tests/test_asr_normalize.py -v 2>&1 | tail -25
```

预期：14 passed（8 既有 + 6 新增）。

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/asr-normalize-en add pipeline/asr_normalize.py tests/test_asr_normalize.py
git -C .worktrees/asr-normalize-en commit -m "$(cat <<'EOF'
feat(asr-normalize): implement translate_to_en with index-aligned schema

Routes to one of three use_cases via _USE_CASE_BY_ROUTE / _PROMPT_SLOT_BY_ROUTE
maps. User payload carries source_language, is_mixed, low_confidence flags plus
full_text and utterances list. Strict response_format json_schema requires
utterances_en array with non-empty text_en. After response, validates 1:1
length and full index coverage; mismatch raises TranslateOutputInvalidError.
Output preserves original start/end timestamps unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: 实现 `run_asr_normalize` 路由编排（TDD）

**Files:**
- Modify: `tests/test_asr_normalize.py`（追加 run_asr_normalize 相关用例）
- Modify: `pipeline/asr_normalize.py`（实现 `run_asr_normalize`）

- [ ] **Step 1: 在 `tests/test_asr_normalize.py` 末尾追加测试用例**

```python
def _make_utterances():
    return [
        {"index": 0, "start": 0.5, "end": 2.3, "text": "Hola, este es un producto"},
        {"index": 1, "start": 2.3, "end": 4.8, "text": "Mira esto"},
    ]


@patch("pipeline.asr_normalize.translate_to_en")
@patch("pipeline.asr_normalize.detect_language")
def test_run_asr_normalize_routes_en_to_en_skip(mock_detect, mock_translate):
    mock_detect.return_value = ({"language": "en", "confidence": 0.99, "is_mixed": False},
                                 {"input_tokens": 100, "output_tokens": 30})
    from pipeline.asr_normalize import run_asr_normalize
    artifact = run_asr_normalize(task_id="t", user_id=1, utterances=_make_utterances())
    assert artifact["route"] == "en_skip"
    assert artifact["detected_source_language"] == "en"
    assert "_utterances_en" not in artifact
    mock_translate.assert_not_called()


@patch("pipeline.asr_normalize.translate_to_en")
@patch("pipeline.asr_normalize.detect_language")
def test_run_asr_normalize_routes_zh_to_zh_skip(mock_detect, mock_translate):
    mock_detect.return_value = ({"language": "zh", "confidence": 0.98, "is_mixed": False},
                                 {"input_tokens": 90, "output_tokens": 30})
    from pipeline.asr_normalize import run_asr_normalize
    artifact = run_asr_normalize(task_id="t", user_id=1, utterances=_make_utterances())
    assert artifact["route"] == "zh_skip"
    assert artifact["detected_source_language"] == "zh"
    assert "_utterances_en" not in artifact
    mock_translate.assert_not_called()


@patch("pipeline.asr_normalize.translate_to_en")
@patch("pipeline.asr_normalize.detect_language")
def test_run_asr_normalize_routes_es_to_specialized(mock_detect, mock_translate):
    mock_detect.return_value = ({"language": "es", "confidence": 0.97, "is_mixed": False},
                                 {"input_tokens": 320, "output_tokens": 40})
    fake_en = [{"index": 0, "start": 0.5, "end": 2.3, "text": "Hi"},
               {"index": 1, "start": 2.3, "end": 4.8, "text": "Look"}]
    mock_translate.return_value = (fake_en, {"input_tokens": 1850, "output_tokens": 1620})
    from pipeline.asr_normalize import run_asr_normalize
    artifact = run_asr_normalize(task_id="t", user_id=1, utterances=_make_utterances())
    assert artifact["route"] == "es_specialized"
    assert artifact["_utterances_en"] == fake_en
    assert artifact["detected_source_language"] == "es"
    assert artifact["confidence"] == 0.97
    mock_translate.assert_called_once_with(
        _make_utterances(), detected_language="es",
        route="es_specialized", task_id="t", user_id=1,
    )


@patch("pipeline.asr_normalize.translate_to_en")
@patch("pipeline.asr_normalize.detect_language")
def test_run_asr_normalize_routes_pt_to_generic_fallback(mock_detect, mock_translate):
    mock_detect.return_value = ({"language": "pt", "confidence": 0.92, "is_mixed": False},
                                 {})
    mock_translate.return_value = ([{"index": 0, "start": 0, "end": 1, "text": "Hi"}], {})
    from pipeline.asr_normalize import run_asr_normalize
    artifact = run_asr_normalize(
        task_id="t", user_id=1,
        utterances=[{"index": 0, "start": 0, "end": 1, "text": "Olá"}],
    )
    assert artifact["route"] == "generic_fallback"
    assert artifact["detected_source_language"] == "pt"
    mock_translate.assert_called_once()
    assert mock_translate.call_args.kwargs["route"] == "generic_fallback"


@patch("pipeline.asr_normalize.translate_to_en")
@patch("pipeline.asr_normalize.detect_language")
def test_run_asr_normalize_routes_low_confidence_to_fallback(mock_detect, mock_translate):
    mock_detect.return_value = ({"language": "fr", "confidence": 0.45, "is_mixed": False},
                                 {})
    mock_translate.return_value = ([{"index": 0, "start": 0, "end": 1, "text": "Hi"}], {})
    from pipeline.asr_normalize import run_asr_normalize
    artifact = run_asr_normalize(
        task_id="t", user_id=1,
        utterances=[{"index": 0, "start": 0, "end": 1, "text": "Bonjour"}],
    )
    assert artifact["route"] == "generic_fallback_low_confidence"
    assert mock_translate.call_args.kwargs["route"] == "generic_fallback_low_confidence"


@patch("pipeline.asr_normalize.translate_to_en")
@patch("pipeline.asr_normalize.detect_language")
def test_run_asr_normalize_routes_mixed_to_fallback(mock_detect, mock_translate):
    mock_detect.return_value = ({"language": "es", "confidence": 0.85, "is_mixed": True},
                                 {})
    mock_translate.return_value = ([{"index": 0, "start": 0, "end": 1, "text": "Hi"}], {})
    from pipeline.asr_normalize import run_asr_normalize
    artifact = run_asr_normalize(
        task_id="t", user_id=1,
        utterances=[{"index": 0, "start": 0, "end": 1, "text": "Hola hello"}],
    )
    assert artifact["route"] == "generic_fallback_mixed"
    assert mock_translate.call_args.kwargs["route"] == "generic_fallback_mixed"


@patch("pipeline.asr_normalize.translate_to_en")
@patch("pipeline.asr_normalize.detect_language")
def test_run_asr_normalize_raises_unsupported_on_other(mock_detect, mock_translate):
    mock_detect.return_value = ({"language": "other", "confidence": 0.88, "is_mixed": False},
                                 {})
    from pipeline.asr_normalize import run_asr_normalize, UnsupportedSourceLanguageError
    with pytest.raises(UnsupportedSourceLanguageError) as exc:
        run_asr_normalize(task_id="t", user_id=1, utterances=_make_utterances())
    assert "other" in str(exc.value)
    mock_translate.assert_not_called()


@patch("pipeline.asr_normalize.translate_to_en")
@patch("pipeline.asr_normalize.detect_language")
def test_run_asr_normalize_artifact_includes_token_metadata(mock_detect, mock_translate):
    mock_detect.return_value = ({"language": "es", "confidence": 0.97, "is_mixed": False},
                                 {"input_tokens": 320, "output_tokens": 40})
    mock_translate.return_value = (
        [{"index": 0, "start": 0.5, "end": 2.3, "text": "Hi"},
         {"index": 1, "start": 2.3, "end": 4.8, "text": "Look"}],
        {"input_tokens": 1850, "output_tokens": 1620},
    )
    from pipeline.asr_normalize import run_asr_normalize
    artifact = run_asr_normalize(task_id="t", user_id=1, utterances=_make_utterances())
    assert artifact["tokens"]["detect"] == {"input_tokens": 320, "output_tokens": 40}
    assert artifact["tokens"]["translate"] == {"input_tokens": 1850, "output_tokens": 1620}
    assert "elapsed_ms" in artifact and artifact["elapsed_ms"] >= 0
    assert artifact["model"]["detect"] == "gemini-3.1-flash-lite-preview"
    assert artifact["model"]["translate"] == "anthropic/claude-sonnet-4.6"
    assert artifact["input"]["language_label"] == "西班牙语"
    assert artifact["input"]["utterance_count"] == 2
    assert artifact["output"]["utterance_count"] == 2
```

- [ ] **Step 2: 跑这 8 个用例验证 FAIL**

```bash
cd g:/Code/AutoVideoSrtLocal && python -m pytest .worktrees/asr-normalize-en/tests/test_asr_normalize.py -v -k run_asr_normalize 2>&1 | tail -25
```

预期：8 FAILED。

- [ ] **Step 3: 在 `pipeline/asr_normalize.py` 中替换 `run_asr_normalize` stub 为完整实现**

```python
def run_asr_normalize(
    *,
    task_id: str,
    user_id: int | None,
    utterances: list[dict],
) -> dict:
    """主入口。封装 detect → 路由 → translate → artifact 构建。

    成功路径返回 artifact dict（含内部字段 _utterances_en，由 runner 拿走后写到
    task["utterances_en"]，然后从 artifact 删掉再 set_artifact）。
    失败路径直接抛异常（DetectLanguageFailedError / UnsupportedSourceLanguageError /
    TranslateOutputInvalidError 或 translate_to_en 内的 LLM 异常）。
    """
    t0 = time.monotonic()
    full_text = " ".join(u["text"] for u in utterances)

    detect_result, detect_tokens = detect_language(
        full_text, task_id=task_id, user_id=user_id,
    )
    lang = detect_result["language"]
    conf = detect_result["confidence"]
    is_mixed = detect_result["is_mixed"]

    if lang == "other":
        raise UnsupportedSourceLanguageError(
            f"原视频语言检测为「other」（confidence={conf:.2f}），"
            f"当前流水线仅支持中文/英文/西班牙语/葡萄牙语/法语/意大利语/日语/荷兰语/瑞典语/芬兰语。"
            f"请使用支持的语言素材重建项目。"
        )

    if lang == "en":
        route = "en_skip"
    elif lang == "zh":
        route = "zh_skip"
    elif is_mixed:
        route = "generic_fallback_mixed"
    elif conf < LOW_CONFIDENCE_THRESHOLD:
        route = "generic_fallback_low_confidence"
    elif lang == "es":
        route = "es_specialized"
    else:
        route = "generic_fallback"

    utterances_en: list[dict] | None = None
    translate_tokens: dict = {}
    if route not in ("en_skip", "zh_skip"):
        utterances_en, translate_tokens = translate_to_en(
            utterances, detected_language=lang, route=route,
            task_id=task_id, user_id=user_id,
        )

    artifact: dict[str, Any] = {
        "detected_source_language": lang,
        "confidence": conf,
        "is_mixed": is_mixed,
        "route": route,
        "input": {
            "language_label": LANG_LABELS.get(lang, lang),
            "full_text_preview": full_text[:200],
            "utterance_count": len(utterances),
        },
        "output": {
            "full_text_preview": (
                " ".join(u["text"] for u in utterances_en)[:200]
                if utterances_en else full_text[:200]
            ),
            "utterance_count": len(utterances_en) if utterances_en else len(utterances),
        },
        "tokens": {"detect": detect_tokens, "translate": translate_tokens},
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
        "model": {
            "detect": "gemini-3.1-flash-lite-preview",
            "translate": "anthropic/claude-sonnet-4.6" if utterances_en else None,
        },
    }
    if utterances_en:
        artifact["_utterances_en"] = utterances_en
    return artifact
```

- [ ] **Step 4: 跑全部用例验证 PASS**

```bash
cd g:/Code/AutoVideoSrtLocal && python -m pytest .worktrees/asr-normalize-en/tests/test_asr_normalize.py -v 2>&1 | tail -30
```

预期：22 passed（14 既有 + 8 新增）。

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/asr-normalize-en add pipeline/asr_normalize.py tests/test_asr_normalize.py
git -C .worktrees/asr-normalize-en commit -m "$(cat <<'EOF'
feat(asr-normalize): implement run_asr_normalize with full routing matrix

en/zh skip translate; es+high-confidence+not-mixed → es_specialized; is_mixed →
generic_fallback_mixed (priority over confidence); confidence<0.6 →
generic_fallback_low_confidence; everything else white-listed →
generic_fallback. detect=='other' raises UnsupportedSourceLanguageError. The
artifact carries detect/translate token usage, elapsed_ms, model identifiers,
and a 200-char preview of input/output. _utterances_en lives inside the
artifact temporarily; the runner pops it before persisting.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: `MultiTranslateRunner._step_asr_normalize` + 串入状态机（TDD）

**Files:**
- Create: `tests/test_runtime_multi_asr_normalize.py`
- Modify: `appcore/runtime_multi.py`（新增 `_step_asr_normalize` 方法；修改 `_get_pipeline_steps` 在 `asr` 后追加 `asr_normalize` 然后再追加 `voice_match`）

- [ ] **Step 1: 写失败测试 — 新建 `tests/test_runtime_multi_asr_normalize.py`**

```python
"""MultiTranslateRunner._step_asr_normalize 集成测试。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_runner():
    """复用 test_runtime_multi_translate.py 同款 runner 构造方式。"""
    from appcore.runtime_multi import MultiTranslateRunner
    runner = MultiTranslateRunner.__new__(MultiTranslateRunner)
    runner.user_id = 1
    runner._emit = MagicMock()
    return runner


def _utterances():
    return [
        {"index": 0, "start": 0.5, "end": 2.3, "text": "Hola, este es un producto"},
        {"index": 1, "start": 2.3, "end": 4.8, "text": "Mira esto"},
    ]


@patch("appcore.runtime_multi.task_state")
@patch("appcore.runtime_multi.pipeline_asr_normalize")
def test_step_asr_normalize_writes_source_language_en_for_es_route(
    mock_asr_norm, mock_state,
):
    mock_state.get.return_value = {"utterances": _utterances(), "_user_id": 1}
    fake_en = [{"index": 0, "start": 0.5, "end": 2.3, "text": "Hi"},
               {"index": 1, "start": 2.3, "end": 4.8, "text": "Look"}]
    mock_asr_norm.run_asr_normalize.return_value = {
        "detected_source_language": "es",
        "confidence": 0.97,
        "is_mixed": False,
        "route": "es_specialized",
        "input": {"language_label": "西班牙语", "full_text_preview": "Hola, este...",
                   "utterance_count": 2},
        "output": {"full_text_preview": "Hi Look", "utterance_count": 2},
        "tokens": {"detect": {}, "translate": {}},
        "elapsed_ms": 100,
        "model": {"detect": "g", "translate": "c"},
        "_utterances_en": fake_en,
    }
    runner = _make_runner()
    runner._step_asr_normalize("t1")
    update_kwargs = mock_state.update.call_args.kwargs
    assert update_kwargs["source_language"] == "en"
    assert update_kwargs["detected_source_language"] == "es"
    assert update_kwargs["utterances_en"] == fake_en
    # artifact 写入时不应该再含 _utterances_en
    set_artifact_kwargs = mock_state.set_artifact.call_args
    artifact_arg = set_artifact_kwargs.args[2]
    assert "_utterances_en" not in artifact_arg


@patch("appcore.runtime_multi.task_state")
@patch("appcore.runtime_multi.pipeline_asr_normalize")
def test_step_asr_normalize_routes_zh_keeps_source_language_zh(
    mock_asr_norm, mock_state,
):
    mock_state.get.return_value = {"utterances": [{"index": 0, "start": 0, "end": 1,
                                                      "text": "你好"}], "_user_id": 1}
    mock_asr_norm.run_asr_normalize.return_value = {
        "detected_source_language": "zh",
        "confidence": 0.98, "is_mixed": False, "route": "zh_skip",
        "input": {"language_label": "中文", "full_text_preview": "你好",
                   "utterance_count": 1},
        "output": {"full_text_preview": "你好", "utterance_count": 1},
        "tokens": {"detect": {}, "translate": {}}, "elapsed_ms": 50,
        "model": {"detect": "g", "translate": None},
    }
    runner = _make_runner()
    runner._step_asr_normalize("t-zh")
    update_kwargs = mock_state.update.call_args.kwargs
    assert update_kwargs["source_language"] == "zh"
    assert update_kwargs["detected_source_language"] == "zh"
    assert "utterances_en" not in update_kwargs


@patch("appcore.runtime_multi.task_state")
@patch("appcore.runtime_multi.pipeline_asr_normalize")
def test_step_asr_normalize_short_circuits_on_empty_utterances(
    mock_asr_norm, mock_state,
):
    mock_state.get.return_value = {"utterances": [], "_user_id": 1}
    runner = _make_runner()
    runner._step_asr_normalize("t-empty")
    mock_asr_norm.run_asr_normalize.assert_not_called()
    # 标记为 done，message 含"无音频文本"
    set_step_call = mock_state.set_step.call_args
    assert set_step_call.args[1] == "asr_normalize"
    assert set_step_call.args[2] == "done"
    assert "无音频文本" in set_step_call.args[3]


@patch("appcore.runtime_multi.task_state")
@patch("appcore.runtime_multi.pipeline_asr_normalize")
def test_step_asr_normalize_marks_failed_on_unsupported_language(
    mock_asr_norm, mock_state,
):
    mock_state.get.return_value = {"utterances": _utterances(), "_user_id": 1}
    from pipeline.asr_normalize import UnsupportedSourceLanguageError
    mock_asr_norm.UnsupportedSourceLanguageError = UnsupportedSourceLanguageError
    mock_asr_norm.run_asr_normalize.side_effect = UnsupportedSourceLanguageError(
        "原视频语言检测为「other」(confidence=0.88)，..."
    )
    runner = _make_runner()
    runner._step_asr_normalize("t-other")
    set_step_call = mock_state.set_step.call_args
    assert set_step_call.args[1] == "asr_normalize"
    assert set_step_call.args[2] == "failed"
    assert "other" in set_step_call.args[3]
    update_kwargs = mock_state.update.call_args.kwargs
    assert "error" in update_kwargs


@patch("appcore.runtime_multi.task_state")
@patch("appcore.runtime_multi.pipeline_asr_normalize")
def test_step_asr_normalize_marks_failed_on_detect_exhaustion(
    mock_asr_norm, mock_state,
):
    mock_state.get.return_value = {"utterances": _utterances(), "_user_id": 1}
    from pipeline.asr_normalize import DetectLanguageFailedError
    mock_asr_norm.run_asr_normalize.side_effect = DetectLanguageFailedError(
        "detect_language failed after 2 attempts: network"
    )
    runner = _make_runner()
    runner._step_asr_normalize("t-net")
    set_step_call = mock_state.set_step.call_args
    assert set_step_call.args[2] == "failed"
    update_kwargs = mock_state.update.call_args.kwargs
    assert "原文标准化失败" in update_kwargs["error"]


@patch("appcore.runtime_multi.task_state")
@patch("appcore.runtime_multi.pipeline_asr_normalize")
def test_step_asr_normalize_resume_idempotent_when_utterances_en_present(
    mock_asr_norm, mock_state,
):
    """再次调用时（utterances_en 已存在）应短路 done，不重新调 LLM。"""
    mock_state.get.return_value = {
        "utterances": _utterances(),
        "utterances_en": [{"index": 0, "start": 0, "end": 1, "text": "Hi"}],
        "source_language": "en",
        "_user_id": 1,
    }
    runner = _make_runner()
    runner._step_asr_normalize("t-resume")
    mock_asr_norm.run_asr_normalize.assert_not_called()


def test_get_pipeline_steps_inserts_asr_normalize_after_asr_before_voice_match():
    runner = _make_runner()
    base = [("extract", lambda: None), ("asr", lambda: None),
            ("alignment", lambda: None)]
    with patch.object(type(runner).__bases__[0], "_get_pipeline_steps",
                       return_value=base):
        steps = runner._get_pipeline_steps("t1", "/tmp/v.mp4", "/tmp")
    names = [name for name, _ in steps]
    asr_idx = names.index("asr")
    norm_idx = names.index("asr_normalize")
    voice_idx = names.index("voice_match")
    assert asr_idx < norm_idx < voice_idx
```

- [ ] **Step 2: 跑这 7 个用例验证 FAIL（AttributeError: no _step_asr_normalize）**

```bash
cd g:/Code/AutoVideoSrtLocal && python -m pytest .worktrees/asr-normalize-en/tests/test_runtime_multi_asr_normalize.py -v 2>&1 | tail -20
```

预期：7 FAILED。

- [ ] **Step 3: 在 `appcore/runtime_multi.py` 顶部 import 区添加：**

```python
from pipeline import asr_normalize as pipeline_asr_normalize
```

- [ ] **Step 4: 在 `appcore/runtime_multi.py` 中找到 `_get_pipeline_steps` 方法（约 413 行附近），将其改为：**

```python
    def _get_pipeline_steps(self, task_id: str, video_path: str, task_dir: str) -> list:
        """覆盖基类：在 asr 后插入 asr_normalize → voice_match。"""
        base_steps = super()._get_pipeline_steps(task_id, video_path, task_dir)
        out = []
        for name, fn in base_steps:
            out.append((name, fn))
            if name == "asr":
                out.append(("asr_normalize", lambda: self._step_asr_normalize(task_id)))
                out.append(("voice_match", lambda: self._step_voice_match(task_id)))
        return out
```

- [ ] **Step 5: 在 `appcore/runtime_multi.py` 中 `_step_voice_match` 方法之前（约 337 行附近）插入新方法 `_step_asr_normalize`：**

```python
    def _step_asr_normalize(self, task_id: str) -> None:
        """ASR 后的原文 → en-US 标准化。

        - 空 utterances → 短路 done
        - utterances_en 已存在或 source_language ∈ {en, zh} → 短路 done（resume 幂等）
        - 否则调 pipeline_asr_normalize.run_asr_normalize：
          - 成功路径写 source_language / detected_source_language / utterances_en（若有）+
            asr_normalize artifact
          - UnsupportedSourceLanguageError / DetectLanguageFailedError /
            TranslateOutputInvalidError 等任何异常 → step failed + task["error"]，artifact 不写入
        """
        task = task_state.get(task_id)
        utterances = task.get("utterances") or []

        if not utterances:
            task_state.set_step(
                task_id, "asr_normalize", "done", "无音频文本，跳过标准化",
            )
            return

        if task.get("utterances_en") or task.get("source_language") in ("en", "zh"):
            # resume 幂等：已经跑过或已是 zh/en skip 路径
            task_state.set_step(
                task_id, "asr_normalize", "done", "已标准化（resume 跳过）",
            )
            return

        task_state.set_step(
            task_id, "asr_normalize", "running", "正在识别原文语言…",
        )
        try:
            artifact = pipeline_asr_normalize.run_asr_normalize(
                task_id=task_id, user_id=self.user_id, utterances=utterances,
            )
        except pipeline_asr_normalize.UnsupportedSourceLanguageError as exc:
            task_state.set_step(task_id, "asr_normalize", "failed", str(exc))
            task_state.update(task_id, error=str(exc))
            return
        except Exception as exc:
            err = f"原文标准化失败：{exc}"
            task_state.set_step(task_id, "asr_normalize", "failed", err)
            task_state.update(task_id, error=err)
            return

        # 拆 artifact：_utterances_en 单独写到 task["utterances_en"]，不进 artifact 落盘
        utterances_en = artifact.pop("_utterances_en", None)
        updates = {
            "detected_source_language": artifact["detected_source_language"],
        }
        if artifact["route"] == "en_skip":
            updates["source_language"] = "en"
        elif artifact["route"] == "zh_skip":
            updates["source_language"] = "zh"
        else:
            updates["source_language"] = "en"
            updates["utterances_en"] = utterances_en
        task_state.update(task_id, **updates)

        msg_map = {
            "en_skip": "原文为英文，跳过标准化",
            "zh_skip": "原文为中文，走中文路径",
            "es_specialized": "西班牙语 → 英文标准化完成",
            "generic_fallback":
                f"{artifact['detected_source_language']} → 英文标准化完成（通用）",
            "generic_fallback_low_confidence":
                f"{artifact['detected_source_language']} → 英文标准化完成（低置信兜底）",
            "generic_fallback_mixed": "混合语言 → 英文标准化完成（兜底）",
        }
        task_state.set_step(
            task_id, "asr_normalize", "done",
            msg_map.get(artifact["route"], "原文标准化完成"),
        )
        task_state.set_artifact(task_id, "asr_normalize", artifact)
```

- [ ] **Step 6: 跑测试，验证全部 PASS**

```bash
cd g:/Code/AutoVideoSrtLocal && python -m pytest .worktrees/asr-normalize-en/tests/test_runtime_multi_asr_normalize.py -v 2>&1 | tail -25
```

预期：7 passed。

也要验证 `test_runtime_multi_translate.py` 不被破坏：

```bash
cd g:/Code/AutoVideoSrtLocal && python -m pytest .worktrees/asr-normalize-en/tests/test_runtime_multi_translate.py -v 2>&1 | tail -15
```

预期：全部既有用例继续 PASS（不应该破坏 translate / voice_match 等步骤）。

- [ ] **Step 7: Commit**

```bash
git -C .worktrees/asr-normalize-en add appcore/runtime_multi.py tests/test_runtime_multi_asr_normalize.py
git -C .worktrees/asr-normalize-en commit -m "$(cat <<'EOF'
feat(asr-normalize): wire _step_asr_normalize into MultiTranslateRunner

New step inserted into the pipeline between asr and voice_match. Empty
utterances and resume-on-completed task short-circuit to done idempotently.
Successful runs write source_language (en or zh) + detected_source_language +
optionally utterances_en, then persist the artifact (without _utterances_en).
UnsupportedSourceLanguageError, DetectLanguageFailedError, and any other
exception from run_asr_normalize mark the step failed and write task["error"]
without persisting an artifact.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: alignment 入口 utterances_en fallback（TDD）

**Files:**
- Modify: `tests/test_multi_translate_routes.py`（追加 alignment 入口 fallback 用例）
- Modify: `web/routes/multi_translate.py:424`（`update_alignment` 函数内 `build_script_segments` 调用处）

- [ ] **Step 1: 在 `tests/test_multi_translate_routes.py` 末尾追加 2 个用例**

```python
def test_alignment_reads_utterances_en_when_present(
    tmp_path, authed_client_no_db, monkeypatch,
):
    """alignment 入口应优先用 task['utterances_en']（若存在）。"""
    from web import store
    from web.routes import multi_translate as mt_module

    captured: dict = {}

    def fake_build(utterances, break_after):
        captured["utterances"] = utterances
        return [{"index": 0, "text": "x"}]

    monkeypatch.setattr(mt_module, "build_script_segments", fake_build)
    monkeypatch.setattr(mt_module, "build_alignment_artifact",
                        lambda *a, **k: {})
    monkeypatch.setattr(mt_module.multi_pipeline_runner, "resume",
                        lambda *a, **k: None)

    task_id = "t-utt-en"
    store.create(task_id, "/tmp/v.mp4", str(tmp_path))
    store.update(
        task_id,
        _user_id=1, type="multi_translate", target_lang="de",
        utterances=[{"index": 0, "start": 0, "end": 1, "text": "Hola"}],
        utterances_en=[{"index": 0, "start": 0, "end": 1, "text": "Hi"}],
        scene_cuts=[],
    )

    resp = authed_client_no_db.put(
        f"/api/multi-translate/{task_id}/alignment",
        json={"break_after": [0]},
    )
    assert resp.status_code == 200
    # alignment 应当走 utterances_en
    assert captured["utterances"][0]["text"] == "Hi"


def test_alignment_falls_back_to_utterances_when_en_missing(
    tmp_path, authed_client_no_db, monkeypatch,
):
    """alignment 在 utterances_en 缺失时应使用 task['utterances']。"""
    from web import store
    from web.routes import multi_translate as mt_module

    captured: dict = {}

    def fake_build(utterances, break_after):
        captured["utterances"] = utterances
        return [{"index": 0, "text": "x"}]

    monkeypatch.setattr(mt_module, "build_script_segments", fake_build)
    monkeypatch.setattr(mt_module, "build_alignment_artifact",
                        lambda *a, **k: {})
    monkeypatch.setattr(mt_module.multi_pipeline_runner, "resume",
                        lambda *a, **k: None)

    task_id = "t-utt-only"
    store.create(task_id, "/tmp/v.mp4", str(tmp_path))
    store.update(
        task_id,
        _user_id=1, type="multi_translate", target_lang="de",
        utterances=[{"index": 0, "start": 0, "end": 1, "text": "你好"}],
        # 注意：没有 utterances_en
        scene_cuts=[],
    )

    resp = authed_client_no_db.put(
        f"/api/multi-translate/{task_id}/alignment",
        json={"break_after": [0]},
    )
    assert resp.status_code == 200
    # 应当 fallback 到 utterances
    assert captured["utterances"][0]["text"] == "你好"
```

- [ ] **Step 2: 跑测试验证两个新用例 FAIL（当前 alignment 入口只读 utterances）**

```bash
cd g:/Code/AutoVideoSrtLocal && python -m pytest .worktrees/asr-normalize-en/tests/test_multi_translate_routes.py -v -k alignment 2>&1 | tail -15
```

预期：第 1 个 FAIL（应得 "Hi" 但实际得 "Hola"）；第 2 个 PASS。

- [ ] **Step 3: 修改 `web/routes/multi_translate.py:424` 一行**

把：

```python
    script_segments = build_script_segments(task.get("utterances", []), break_after)
```

改为：

```python
    source_utterances = task.get("utterances_en") or task.get("utterances", [])
    script_segments = build_script_segments(source_utterances, break_after)
```

- [ ] **Step 4: 跑全部 alignment 相关 + 既有用例验证 PASS**

```bash
cd g:/Code/AutoVideoSrtLocal && python -m pytest .worktrees/asr-normalize-en/tests/test_multi_translate_routes.py -v 2>&1 | tail -25
```

预期：全部既有用例 + 2 新增 = PASS。

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/asr-normalize-en add web/routes/multi_translate.py tests/test_multi_translate_routes.py
git -C .worktrees/asr-normalize-en commit -m "$(cat <<'EOF'
feat(asr-normalize): alignment reads utterances_en when present

The /alignment route now consults task['utterances_en'] first and falls back
to task['utterances'] when normalization wasn't run (zh/en source). Downstream
build_script_segments sees the English-normalized text for non-zh/en sources
without any other change.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: RESUMABLE_STEPS + 上传 docstring（TDD）

**Files:**
- Modify: `tests/test_multi_translate_routes.py`（追加 RESUMABLE_STEPS 用例）
- Modify: `web/routes/multi_translate.py:486`（RESUMABLE_STEPS）+ `:261`（upload_and_start docstring）

- [ ] **Step 1: 在 `tests/test_multi_translate_routes.py` 末尾追加用例**

```python
def test_resumable_steps_includes_asr_normalize_between_asr_and_voice_match():
    from web.routes.multi_translate import RESUMABLE_STEPS
    assert "asr_normalize" in RESUMABLE_STEPS
    asr_idx = RESUMABLE_STEPS.index("asr")
    norm_idx = RESUMABLE_STEPS.index("asr_normalize")
    voice_idx = RESUMABLE_STEPS.index("voice_match")
    assert asr_idx < norm_idx < voice_idx


def test_resume_accepts_asr_normalize_as_start_step(
    tmp_path, authed_client_no_db, monkeypatch,
):
    from web import store
    from web.routes import multi_translate as mt_module

    monkeypatch.setattr(mt_module.multi_pipeline_runner, "resume",
                        lambda *a, **k: None)

    task_id = "t-resume-norm"
    store.create(task_id, "/tmp/v.mp4", str(tmp_path))
    store.update(task_id, _user_id=1, type="multi_translate", target_lang="de")

    resp = authed_client_no_db.post(
        f"/api/multi-translate/{task_id}/resume",
        json={"start_step": "asr_normalize"},
    )
    assert resp.status_code == 200
```

- [ ] **Step 2: 跑测试验证 FAIL**

```bash
cd g:/Code/AutoVideoSrtLocal && python -m pytest .worktrees/asr-normalize-en/tests/test_multi_translate_routes.py -v -k "resumable_steps or resume_accepts" 2>&1 | tail -15
```

预期：2 FAILED（asr_normalize 不在 RESUMABLE_STEPS / resume start_step 校验失败）。

- [ ] **Step 3: 修改 `web/routes/multi_translate.py:486` RESUMABLE_STEPS 元组：**

```python
RESUMABLE_STEPS = ["extract", "asr", "asr_normalize", "voice_match", "alignment", "translate", "tts", "subtitle", "compose", "export"]
```

- [ ] **Step 4: 修改 `web/routes/multi_translate.py:261` `upload_and_start` 函数 docstring：**

把：

```python
def upload_and_start():
    """上传视频，创建多语种翻译任务。源语言将在 ASR 后自动检测。"""
```

改为：

```python
def upload_and_start():
    """上传视频，创建多语种翻译任务。

    源语言由 ASR 后置 step `asr_normalize` 自动识别（Gemini Flash），
    任意非中英文素材会进一步走 Claude Sonnet 标准化为 en-US 后再进入下游。
    任务创建时不写入 source_language——由 _step_asr_normalize 唯一负责写入。
    """
```

- [ ] **Step 5: 跑全部用例验证 PASS**

```bash
cd g:/Code/AutoVideoSrtLocal && python -m pytest .worktrees/asr-normalize-en/tests/test_multi_translate_routes.py -v 2>&1 | tail -25
```

预期：全部 PASS。

- [ ] **Step 6: Commit**

```bash
git -C .worktrees/asr-normalize-en add web/routes/multi_translate.py tests/test_multi_translate_routes.py
git -C .worktrees/asr-normalize-en commit -m "$(cat <<'EOF'
feat(asr-normalize): expose asr_normalize in RESUMABLE_STEPS and update docstring

Adds asr_normalize between asr and voice_match in the RESUMABLE_STEPS list so
users can resume from this step after a transient detect/translate failure.
upload_and_start docstring now reflects that source_language is written
exclusively by _step_asr_normalize, not at upload time.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: 模板：multi_translate_list.html 上传弹窗文案

**Files:**
- Modify: `web/templates/multi_translate_list.html`（grep "自动识别视频源语言" 找到提示文案）

- [ ] **Step 1: 在 worktree 中 grep 当前文案位置**

```bash
grep -n "自动识别视频源语言" .worktrees/asr-normalize-en/web/templates/multi_translate_list.html
```

预期：找到 1 处提示文案（约 ~310 行附近）。

- [ ] **Step 2: 替换该文案**

把：

```html
上传后将自动识别视频源语言（中文/英文）
```

改为：

```html
上传后将自动识别原视频语言并标准化为英文输入
```

- [ ] **Step 3: 加一条简单守护测试，避免文案被回滚**

在 `tests/test_multi_translate_routes.py` 末尾追加：

```python
def test_multi_translate_list_upload_modal_text_mentions_normalization():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "multi_translate_list.html").read_text(encoding="utf-8")
    assert "自动识别原视频语言并标准化" in template
    assert "中文/英文" not in template  # 老文案被移除
```

跑测试：

```bash
cd g:/Code/AutoVideoSrtLocal && python -m pytest .worktrees/asr-normalize-en/tests/test_multi_translate_routes.py::test_multi_translate_list_upload_modal_text_mentions_normalization -v 2>&1 | tail -5
```

预期：PASS。

- [ ] **Step 4: Commit**

```bash
git -C .worktrees/asr-normalize-en add web/templates/multi_translate_list.html tests/test_multi_translate_routes.py
git -C .worktrees/asr-normalize-en commit -m "$(cat <<'EOF'
chore(asr-normalize): update upload modal hint to reflect normalization

The modal previously claimed only zh/en would be auto-detected; with the new
asr_normalize step any whitelist source (zh/en/es/pt/fr/it/ja/nl/sv/fi) is
detected and—if needed—standardized to en before downstream localization.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: 模板：multi_translate_detail.html 进度条 + artifact 卡片

**Files:**
- Modify: `web/templates/multi_translate_detail.html`

本 task **不写新单元测试**（纯模板渲染），落地后通过 Task 13 手测验收。

- [ ] **Step 1: 在 worktree 中查看进度条 step 列表的当前结构**

```bash
grep -n "voice_match\|extract\|alignment" .worktrees/asr-normalize-en/web/templates/multi_translate_detail.html | head -20
```

找到当前 step 列表的 Jinja `{% for step in [...] %}` 或硬编码 step 数组的位置（一般会在文件中段，见 `extract → asr → voice_match → alignment → translate → tts → subtitle → compose` 这段 template）。

- [ ] **Step 2: 在 step 列表中找到 `"asr"` 与 `"voice_match"` 之间，插入新 step**

进度条 step 数组（无论是 Jinja `{% set steps = ['extract','asr','voice_match','alignment','translate','tts','subtitle','compose','export'] %}` 还是 JS 端的同款数组），改为含 `'asr_normalize'`：

```jinja
{% set steps = ['extract','asr','asr_normalize','voice_match','alignment','translate','tts','subtitle','compose','export'] %}
```

step 中文标签字典同步追加：

```jinja
{% set step_labels = {
    'extract':'抽取', 'asr':'识别字幕', 'asr_normalize':'原文标准化',
    'voice_match':'匹配音色', 'alignment':'分段对齐', 'translate':'本土化',
    'tts':'配音', 'subtitle':'字幕', 'compose':'合成', 'export':'导出'
} %}
```

> 实施者注意：detail 模板的具体写法可能与上述 Jinja 结构不完全一致（可能是 JS 端 array、可能是若干 `{% if step == 'xxx' %}` 块）。请按文件实际结构，最小改动地把 `asr_normalize` 插到 `asr` 和 `voice_match` 之间，并把中文标签 "原文标准化" 加进去。

- [ ] **Step 3: 在详情区下方加 artifact 卡片（asr_normalize artifact 展示）**

在 `{% if state.asr_normalize_artifact %}` 块（如果模板已经有一个 generic artifact loop 就直接复用；否则在 ASR 卡片之后追加新卡片），渲染：

```jinja
{% if state.asr_normalize_artifact %}
{% set norm = state.asr_normalize_artifact %}
<section class="card asr-normalize-card">
    <header class="card-header">
        <h3>原文标准化</h3>
        <span class="badge route-{{ norm.route }}">{{ norm.route }}</span>
    </header>
    <div class="kv">
        <div><span class="k">检测语言</span><span class="v">{{ norm.input.language_label }}（{{ norm.detected_source_language }}）</span></div>
        <div><span class="k">置信度</span><span class="v">{{ '%.2f' % norm.confidence }}</span></div>
        <div><span class="k">是否混合</span><span class="v">{{ '是' if norm.is_mixed else '否' }}</span></div>
        <div><span class="k">耗时</span><span class="v">{{ (norm.elapsed_ms / 1000) | round(1) }}s</span></div>
    </div>
    {% if norm.input.full_text_preview %}
    <details>
        <summary>原文预览（前 200 字符）</summary>
        <pre class="preview">{{ norm.input.full_text_preview }}</pre>
    </details>
    {% endif %}
    {% if norm.output.full_text_preview and norm.route not in ['en_skip', 'zh_skip'] %}
    <details>
        <summary>英文标准化预览（前 200 字符）</summary>
        <pre class="preview">{{ norm.output.full_text_preview }}</pre>
    </details>
    {% endif %}
    <div class="tokens">
        detect: {{ norm.tokens.detect.input_tokens or 0 }}↑/{{ norm.tokens.detect.output_tokens or 0 }}↓
        {% if norm.tokens.translate.input_tokens %}
            · translate: {{ norm.tokens.translate.input_tokens }}↑/{{ norm.tokens.translate.output_tokens or 0 }}↓
        {% endif %}
    </div>
</section>
{% endif %}
```

样式按 `CLAUDE.md` Frontend Design System——卡片用 `--radius-lg` + `1px solid --border`，badge 用 `--accent-subtle` 背景，详情区按已有模式。**不要引入紫色或新 hue**。

- [ ] **Step 4: 启动开发服务器手测渲染（无需自动测试）**

```bash
cd g:/Code/AutoVideoSrtLocal && python main.py
```

打开浏览器访问 `http://localhost:5000/multi-translate`，进入任意已存在任务的详情页，确认：
- 进度条出现"原文标准化"格（在"识别字幕"和"匹配音色"之间）
- 老任务（无 asr_normalize_artifact）这一格灰显，无 artifact 卡片
- （Task 13 会用真实西语任务验证有 artifact 时的渲染）

- [ ] **Step 5: Commit**

```bash
git -C .worktrees/asr-normalize-en add web/templates/multi_translate_detail.html
git -C .worktrees/asr-normalize-en commit -m "$(cat <<'EOF'
feat(asr-normalize): expose detail-page progress slot and artifact card

The detail page now shows asr_normalize between asr and voice_match in the
progress bar and renders the asr_normalize artifact (detected language label,
confidence, mixed flag, route badge, elapsed time, before/after previews,
detect+translate token totals) when present. Tasks created before this step
existed have no artifact; the slot grays out gracefully.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: 跑全套相关测试

- [ ] **Step 1: 一次性跑全部直接相关测试**

```bash
cd g:/Code/AutoVideoSrtLocal && python -m pytest \
    .worktrees/asr-normalize-en/tests/test_asr_normalize.py \
    .worktrees/asr-normalize-en/tests/test_runtime_multi_asr_normalize.py \
    .worktrees/asr-normalize-en/tests/test_asr_normalize_use_cases.py \
    .worktrees/asr-normalize-en/tests/test_asr_normalize_prompts.py \
    .worktrees/asr-normalize-en/tests/test_multi_translate_routes.py \
    .worktrees/asr-normalize-en/tests/test_runtime_multi_translate.py \
    -q 2>&1 | tail -15
```

预期：全部 PASS（约 50+ 用例）。

- [ ] **Step 2: 跑相邻测试，确认没破坏其他模块**

```bash
cd g:/Code/AutoVideoSrtLocal && python -m pytest \
    .worktrees/asr-normalize-en/tests/test_appcore_medias_multi_lang.py \
    .worktrees/asr-normalize-en/tests/test_runtime_multi_voice_match.py \
    .worktrees/asr-normalize-en/tests/test_languages_registry.py \
    .worktrees/asr-normalize-en/tests/test_video_translate_defaults.py \
    -q 2>&1 | tail -10
```

预期：全部 PASS。如有 FAIL，先排查是否引用了被改动的文件、是否需要修测试断言（**不要改业务逻辑**——本 task 只验回归）。

---

## Task 13: 手测验收清单

> 此 Task **不可自动化**，需要管理员/QA 在测试环境（连真实 MySQL + Gemini + Claude）跑。完成下面清单后才算 Done。

- [ ] **Step 1: 启动 worktree 服务器**

```bash
cd .worktrees/asr-normalize-en && python main.py
```

- [ ] **Step 2: 西语素材 → 德语本地化（核心场景）**

- [ ] 在 `/multi-translate` 上传一段约 30 秒的西班牙语带货视频，目标语言选 🇩🇪 德语
- [ ] 等待 ASR 完成 → 进度条进入"原文标准化"格（颜色变蓝）
- [ ] 标准化完成 → artifact 卡片显示：检测语言="西班牙语（es）"、置信度 ≥ 0.85、route badge="es_specialized"、elapsed ≤ 30s
- [ ] 点开"英文标准化预览" → 前 200 字符是地道 en-US 文本（无西语残留）
- [ ] 下游 alignment / translate / tts / subtitle / compose 全部跑通
- [ ] 最终德语字幕 / 配音 / 合成视频 全部正确

- [ ] **Step 3: 中文素材 → 德语本地化（zh_skip 路径）**

- [ ] 上传一段中文带货视频，目标 🇩🇪 德语
- [ ] 标准化 artifact 显示 route="zh_skip"、不生成英文预览
- [ ] task state 中 source_language="zh"、`utterances_en` 不存在
- [ ] 下游 translate prompt 标签为 "原文为中文"，跑通

- [ ] **Step 4: 英文素材 → 德语本地化（en_skip 路径）**

- [ ] 上传一段英文带货视频，目标 🇩🇪 德语
- [ ] 标准化 artifact 显示 route="en_skip"
- [ ] source_language="en"、`utterances_en` 不存在
- [ ] 下游跑通

- [ ] **Step 5: 葡语素材（generic_fallback 路径）**

- [ ] 上传一段葡语带货视频，目标 🇩🇪 德语
- [ ] artifact 显示 route="generic_fallback"，detected_source_language="pt"
- [ ] 英文质量可读（不要求精修水平）

- [ ] **Step 6: 短素材（low_confidence 路径）**

- [ ] 上传一段 5-10 秒的短素材（西语或葡语都行）
- [ ] artifact 显示 route="generic_fallback_low_confidence"
- [ ] 任务不 fail，最终下游产出可用

- [ ] **Step 7: 不支持的语言（俄/韩/越南语）→ 任务 fail**

- [ ] 上传一段俄语短视频
- [ ] asr_normalize step 状态变红，task["error"] = "原视频语言检测为「other」..."
- [ ] 任务详情页 banner 显示该错误
- [ ] 后续 step 不启动

- [ ] **Step 8: 管理员 prompt 配置可见**

- [ ] 以管理员身份访问 prompt 配置后台
- [ ] 应当看到 4 条新 prompt：
  - `asr_normalize.detect`（lang="" 或显示为"无目标语言"）
  - `asr_normalize.translate_zh_en`
  - `asr_normalize.translate_es_en`
  - `asr_normalize.translate_generic_en`
- [ ] 改任意一条 → 重跑该 step → 修改后的 prompt 生效
- [ ] 点"恢复默认" → 回到 spec 设计稿值

- [ ] **Step 9: Resume 从 asr_normalize 重跑（管理员功能）**

- [ ] 找一个已经跑过 asr_normalize 的西语任务
- [ ] 用管理员调试接口或 SQL 把 task["utterances_en"] 字段清空
- [ ] 触发 resume from "asr_normalize"
- [ ] asr_normalize step 重新跑一遍，得到新 artifact

- [ ] **Step 10: 老任务（无 asr_normalize_artifact）行为兼容**

- [ ] 找一个新代码上线**之前**就完成的 multi_translate 任务（中文/英文素材）
- [ ] 详情页能正常打开，进度条"原文标准化"格灰显
- [ ] artifact 卡片不渲染
- [ ] 任务可正常 resume 后续 step（因为是 zh/en 素材，跑通无碍）

- [ ] **Step 11: 老任务（西语素材，b3fa903d 类型）保持原状**

- [ ] 访问 b3fa903d 任务详情页
- [ ] 进度条"原文标准化"格灰显，无 artifact 卡片
- [ ] 任务保持卡死状态——按设计**不做迁移**。用户需要重建任务。

---

## Task 14: 准备合并 master

- [ ] **Step 1: rebase master HEAD（拉取本次开发期间 master 上的改动）**

```bash
git -C .worktrees/asr-normalize-en fetch origin master 2>/dev/null || true
git -C .worktrees/asr-normalize-en rebase master
```

如有冲突：解决冲突 + 重跑 Task 12 全套测试。

- [ ] **Step 2: 重跑 Task 12 全套测试做最后一道关**

```bash
cd g:/Code/AutoVideoSrtLocal && python -m pytest \
    .worktrees/asr-normalize-en/tests/test_asr_normalize.py \
    .worktrees/asr-normalize-en/tests/test_runtime_multi_asr_normalize.py \
    .worktrees/asr-normalize-en/tests/test_asr_normalize_use_cases.py \
    .worktrees/asr-normalize-en/tests/test_asr_normalize_prompts.py \
    .worktrees/asr-normalize-en/tests/test_multi_translate_routes.py \
    .worktrees/asr-normalize-en/tests/test_runtime_multi_translate.py \
    -q 2>&1 | tail -10
```

预期：全部 PASS。

- [ ] **Step 3: 调用 superpowers:finishing-a-development-branch skill 让用户决定合并方式**

由用户选择 cherry-pick / merge / PR。**不要自行 push 或 merge。**

---

## 自查清单

- [x] **Spec 覆盖**：spec §2 的 13 个改动文件全部对应到 Task 1–11；spec §5 异常路径 6 条全部由 Task 6 + Task 7 用例覆盖；spec §7 测试计划 4 个测试文件 + 2 处既有测试修改全部对应到 Task 1–9
- [x] **Placeholder 扫描**：所有 Step 含完整可执行内容；没有 TBD/TODO/"以此类推"；prompt 全文、测试代码、实现代码、commit message 一应俱全
- [x] **类型一致性**：`detect_language` 返回 `tuple[dict, dict]`、`translate_to_en` 返回 `tuple[list[dict], dict]`、`run_asr_normalize` 返回 `dict`（含 `_utterances_en` 内部字段）、artifact 字段名（`detected_source_language` / `route` / `tokens` / `model` / `_utterances_en`）从 Task 6 的 artifact 构造、Task 7 的 runner 写回、Task 11 的模板渲染始终一致
- [x] **依赖顺序**：Task 1（use_case）→ Task 2（prompt）→ Task 3（模块骨架）→ Task 4（detect）→ Task 5（translate）→ Task 6（run_asr_normalize）→ Task 7（runner 串入）→ Task 8（alignment fallback）→ Task 9（路由白名单 + docstring）→ Task 10（list 模板）→ Task 11（detail 模板）→ Task 12（汇总测试）→ Task 13（手测）→ Task 14（合并准备）
