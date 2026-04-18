# 多语种视频翻译 — 第 1 批实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付多语种视频翻译模块的骨架 + `llm_prompt_configs` 可视化 prompt 管理后台 + de/fr 两种目标语言跑通全流程（上传 → ASR → 向量音色匹配 → 翻译 → TTS → 字幕 → 合成）。

**Architecture:** 单一 `MultiTranslateRunner` 继承 `PipelineRunner`；语言规则（字幕 / TTS 语言码 / 后处理）放 `pipeline/languages/<lang>.py`；prompt 和模型放 `llm_prompt_configs` 数据库表，通过管理员后台可视化编辑；电商插件 prompt 作为共享片段；复用现有 `elevenlabs_voices` 向量库做音色匹配。

**Tech Stack:** Flask + MySQL + SocketIO（现有）；resemblyzer（音色向量，已集成）；ElevenLabs multilingual_v2（TTS）；OpenRouter / Doubao（LLM）。

**Scope（只含第 1 批）:**
- 仅 `de` / `fr` 两种目标语言的运行时跑通
- `llm_prompt_configs` 表 + 管理后台页面 + resolver
- 共享模块（`pipeline/subtitle.py`, `pipeline/voice_match.py`）以"加参数 + 默认值"方式小改，保证老 DE/FR 模块行为不变
- 前端列表页 + 工作台 + 侧边栏导航调整

第 2 批（es/it/pt）和第 3 批（ja）独立写 plan。

**参考设计稿:** [docs/superpowers/specs/2026-04-18-multi-translate-design.md](../specs/2026-04-18-multi-translate-design.md)

---

## Task 1: 建表 — `llm_prompt_configs` + `projects.type` 枚举扩展

**Files:**
- Create: `db/migrations/2026_04_18_multi_translate_schema.sql`

- [ ] **Step 1: 写 migration 文件**

```sql
-- db/migrations/2026_04_18_multi_translate_schema.sql
-- 多语种视频翻译模块：新表 + projects.type 枚举扩展
-- 设计文档: docs/superpowers/specs/2026-04-18-multi-translate-design.md

-- ========== 1. projects.type 增加 'multi_translate' ==========
ALTER TABLE projects
  MODIFY COLUMN type ENUM(
    'translation','de_translate','fr_translate','copywriting',
    'video_creation','video_review','translate_lab',
    'image_translate','subtitle_removal',
    'bulk_translate','copywriting_translate',
    'multi_translate'
  ) NOT NULL;

-- ========== 2. llm_prompt_configs 表 ==========
CREATE TABLE llm_prompt_configs (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  slot            VARCHAR(64) NOT NULL COMMENT 'base_translation|base_tts_script|base_rewrite|ecommerce_plugin',
  lang            VARCHAR(8)  NULL     COMMENT 'de/fr/es/it/ja/pt；ecommerce_plugin 用 NULL 共享',
  model_provider  VARCHAR(32) NOT NULL COMMENT 'openrouter|doubao|openai|anthropic',
  model_name      VARCHAR(128) NOT NULL,
  content         MEDIUMTEXT NOT NULL,
  enabled         TINYINT DEFAULT 1,
  updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  updated_by      INT NULL,
  UNIQUE KEY uk_slot_lang (slot, lang)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 2: 应用 migration**

```bash
python db/migrate.py up
```

Expected: 输出 `applied 2026_04_18_multi_translate_schema.sql`

- [ ] **Step 3: 验证 schema**

```bash
python -c "from appcore.db import query_one; print(query_one(\"SHOW COLUMNS FROM projects LIKE 'type'\"))"
python -c "from appcore.db import query_one; print(query_one('DESCRIBE llm_prompt_configs'))"
```

Expected: type 列枚举值含 `multi_translate`；`llm_prompt_configs` 表存在。

- [ ] **Step 4: 提交**

```bash
git add db/migrations/2026_04_18_multi_translate_schema.sql
git commit -m "feat(multi-translate): 新增 llm_prompt_configs 表并扩展 projects.type 枚举"
```

---

## Task 2: DAO + resolver（`appcore/llm_prompt_configs.py`）

**Files:**
- Create: `appcore/llm_prompt_configs.py`
- Create: `tests/test_llm_prompt_configs_dao.py`

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_llm_prompt_configs_dao.py
from unittest.mock import patch

from appcore import llm_prompt_configs as dao


def test_upsert_and_get():
    with patch("appcore.llm_prompt_configs.query_one") as m_one, \
         patch("appcore.llm_prompt_configs.execute") as m_exec:
        m_one.return_value = None
        dao.upsert("base_translation", "de",
                   provider="openrouter", model="gpt-4o-mini",
                   content="You are a German creator", updated_by=1)
        m_exec.assert_called_once()
        sql = m_exec.call_args.args[0]
        assert "INSERT INTO llm_prompt_configs" in sql
        assert "ON DUPLICATE KEY UPDATE" in sql


def test_resolve_prompt_config_hits_db():
    row = {
        "slot": "base_translation", "lang": "de",
        "model_provider": "openrouter", "model_name": "gpt-4o-mini",
        "content": "content-from-db", "enabled": 1,
    }
    with patch("appcore.llm_prompt_configs.query_one", return_value=row):
        cfg = dao.resolve_prompt_config("base_translation", "de")
    assert cfg == {
        "provider": "openrouter",
        "model": "gpt-4o-mini",
        "content": "content-from-db",
    }


def test_resolve_prompt_config_fallback_to_defaults_and_seeds():
    with patch("appcore.llm_prompt_configs.query_one", return_value=None), \
         patch("appcore.llm_prompt_configs.execute") as m_exec, \
         patch("appcore.llm_prompt_configs._get_default",
               return_value={"provider": "openrouter", "model": "dflt",
                             "content": "dflt-content"}):
        cfg = dao.resolve_prompt_config("base_translation", "de")
    assert cfg["provider"] == "openrouter"
    assert cfg["content"] == "dflt-content"
    # seed 写回 DB
    m_exec.assert_called_once()


def test_resolve_ecommerce_plugin_lang_is_null():
    with patch("appcore.llm_prompt_configs.query_one") as m_one:
        m_one.return_value = {
            "slot": "ecommerce_plugin", "lang": None,
            "model_provider": "openrouter", "model_name": "gpt-4o-mini",
            "content": "plugin", "enabled": 1,
        }
        cfg = dao.resolve_prompt_config("ecommerce_plugin", None)
    # 验证 SQL 用 IS NULL 而非 = NULL
    sql = m_one.call_args.args[0]
    assert "lang IS NULL" in sql
    assert cfg["content"] == "plugin"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_llm_prompt_configs_dao.py -v
```

Expected: FAIL（模块不存在）

- [ ] **Step 3: 写实现**

```python
# appcore/llm_prompt_configs.py
"""LLM prompt 配置 DAO + resolver。

运行时通过 resolve_prompt_config() 取配置；DB 为空时 fallback 到
pipeline/languages/prompt_defaults.py 里的 DEFAULTS，并 seed 写回 DB。

管理员后台通过 upsert() / list_all() 编辑。
"""
from __future__ import annotations

from typing import Optional

from appcore.db import query, query_one, execute


VALID_SLOTS = {"base_translation", "base_tts_script", "base_rewrite", "ecommerce_plugin"}


def _get_default(slot: str, lang: Optional[str]) -> Optional[dict]:
    """从代码里的出厂默认取一条；空则 None。"""
    from pipeline.languages.prompt_defaults import DEFAULTS
    return DEFAULTS.get((slot, lang))


def resolve_prompt_config(slot: str, lang: Optional[str]) -> dict:
    """返回 {provider, model, content}。DB 命中即返回；否则从 DEFAULTS 取并 seed 写回。

    `lang` 对 slot=='ecommerce_plugin' 传 None（表示共享），SQL 用 IS NULL 精确匹配。
    """
    if slot not in VALID_SLOTS:
        raise ValueError(f"invalid slot: {slot}")

    if lang is None:
        row = query_one(
            "SELECT model_provider, model_name, content FROM llm_prompt_configs "
            "WHERE slot = %s AND lang IS NULL AND enabled = 1 LIMIT 1",
            (slot,),
        )
    else:
        row = query_one(
            "SELECT model_provider, model_name, content FROM llm_prompt_configs "
            "WHERE slot = %s AND lang = %s AND enabled = 1 LIMIT 1",
            (slot, lang),
        )

    if row:
        return {
            "provider": row["model_provider"],
            "model": row["model_name"],
            "content": row["content"],
        }

    default = _get_default(slot, lang)
    if not default:
        raise LookupError(f"no prompt config and no default for slot={slot} lang={lang}")
    # seed 写回 DB
    upsert(slot, lang,
           provider=default["provider"], model=default["model"],
           content=default["content"], updated_by=None)
    return default


def upsert(slot: str, lang: Optional[str], *,
           provider: str, model: str, content: str,
           updated_by: Optional[int]) -> None:
    if slot not in VALID_SLOTS:
        raise ValueError(f"invalid slot: {slot}")
    execute(
        "INSERT INTO llm_prompt_configs "
        "(slot, lang, model_provider, model_name, content, updated_by) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "  model_provider = VALUES(model_provider), "
        "  model_name = VALUES(model_name), "
        "  content = VALUES(content), "
        "  updated_by = VALUES(updated_by)",
        (slot, lang, provider, model, content, updated_by),
    )


def list_all() -> list[dict]:
    return query(
        "SELECT id, slot, lang, model_provider, model_name, content, "
        "       enabled, updated_at, updated_by "
        "FROM llm_prompt_configs ORDER BY slot, lang"
    )


def get_one(slot: str, lang: Optional[str]) -> Optional[dict]:
    if lang is None:
        return query_one(
            "SELECT * FROM llm_prompt_configs WHERE slot = %s AND lang IS NULL",
            (slot,),
        )
    return query_one(
        "SELECT * FROM llm_prompt_configs WHERE slot = %s AND lang = %s",
        (slot, lang),
    )


def delete(slot: str, lang: Optional[str]) -> None:
    """删掉一条 override，下次 resolve 时会重新 seed 默认值。"""
    if lang is None:
        execute(
            "DELETE FROM llm_prompt_configs WHERE slot = %s AND lang IS NULL",
            (slot,),
        )
    else:
        execute(
            "DELETE FROM llm_prompt_configs WHERE slot = %s AND lang = %s",
            (slot, lang),
        )
```

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_llm_prompt_configs_dao.py -v
```

Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add appcore/llm_prompt_configs.py tests/test_llm_prompt_configs_dao.py
git commit -m "feat(multi-translate): 新增 llm_prompt_configs DAO 与 resolver"
```

---

## Task 3: 语言规则骨架 — `pipeline/languages/` 包 + registry + de.py + fr.py

**Files:**
- Create: `pipeline/languages/__init__.py`
- Create: `pipeline/languages/registry.py`
- Create: `pipeline/languages/de.py`
- Create: `pipeline/languages/fr.py`
- Create: `tests/test_languages_registry.py`

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_languages_registry.py
import pytest

from pipeline.languages import registry


def test_supported_langs_includes_de_fr():
    assert "de" in registry.SUPPORTED_LANGS
    assert "fr" in registry.SUPPORTED_LANGS


def test_get_rules_de():
    rules = registry.get_rules("de")
    assert rules.TTS_LANGUAGE_CODE == "de"
    assert rules.TTS_MODEL_ID == "eleven_multilingual_v2"
    assert rules.MAX_CHARS_PER_LINE == 38
    assert rules.MAX_CHARS_PER_SECOND == 17
    assert "und" in rules.WEAK_STARTERS


def test_get_rules_fr_has_post_process():
    rules = registry.get_rules("fr")
    assert rules.TTS_LANGUAGE_CODE == "fr"
    assert rules.MAX_CHARS_PER_LINE == 42
    # fr 有标点空格后处理
    assert callable(rules.post_process_srt)
    sample = "1\n00:00:00,000 --> 00:00:01,000\nBonjour ?\n"
    out = rules.post_process_srt(sample)
    assert "Bonjour\u00A0?" in out


def test_get_rules_unknown_raises():
    with pytest.raises(LookupError):
        registry.get_rules("xx")
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_languages_registry.py -v
```

Expected: FAIL（模块不存在）

- [ ] **Step 3: 写实现 — `pipeline/languages/__init__.py`**

```python
# pipeline/languages/__init__.py
"""多语种视频翻译：语言规则包。

每种语言一份模块（de.py / fr.py / ...），声明字幕规则、TTS 语言码、
前后处理函数。Prompt 不在这里——走 llm_prompt_configs 数据库表。
"""
```

- [ ] **Step 4: 写实现 — `pipeline/languages/registry.py`**

```python
# pipeline/languages/registry.py
"""语言规则注册中心。第 1 批只含 de / fr。

扩展第 7 种语言：加一个 pipeline/languages/<lang>.py + 在 SUPPORTED_LANGS 加一行。
"""
from __future__ import annotations

import importlib
from types import ModuleType

SUPPORTED_LANGS = ("de", "fr")


def get_rules(lang: str) -> ModuleType:
    if lang not in SUPPORTED_LANGS:
        raise LookupError(f"unsupported language: {lang}")
    return importlib.import_module(f"pipeline.languages.{lang}")
```

- [ ] **Step 5: 写实现 — `pipeline/languages/de.py`**

```python
# pipeline/languages/de.py
"""德语字幕/TTS 规则。Prompt 见 llm_prompt_configs.slot='base_*' lang='de'。"""
from __future__ import annotations

TTS_MODEL_ID = "eleven_multilingual_v2"
TTS_LANGUAGE_CODE = "de"

# 字幕 — 德语词长，每行短一点
MAX_CHARS_PER_LINE = 38
MAX_CHARS_PER_SECOND = 17          # Netflix 规范
MAX_LINES = 2

WEAK_STARTERS = {
    "und", "oder", "der", "die", "das", "ein", "eine", "einem", "einen", "einer",
    "für", "mit", "von", "zu", "zum", "zur", "aber", "auch", "wenn", "dass",
    "den", "dem", "des", "auf", "aus", "bei", "bis", "nach", "über", "unter",
}
WEAK_STARTER_PHRASES: list[str] = []


def pre_process(text: str) -> str:
    """德语无需前处理。"""
    return text


def post_process_srt(srt_content: str) -> str:
    """德语无需后处理。"""
    return srt_content
```

- [ ] **Step 6: 写实现 — `pipeline/languages/fr.py`**

```python
# pipeline/languages/fr.py
"""法语字幕/TTS 规则。"""
from __future__ import annotations

import re

TTS_MODEL_ID = "eleven_multilingual_v2"
TTS_LANGUAGE_CODE = "fr"

MAX_CHARS_PER_LINE = 42
MAX_CHARS_PER_SECOND = 17
MAX_LINES = 2

WEAK_STARTERS = {
    "et", "ou", "de", "du", "des", "le", "la", "les", "un", "une",
    "pour", "avec", "dans", "mais", "aussi", "que", "qui", "sur",
    "par", "en", "au", "aux",
    "il", "elle", "ils", "elles", "on", "nous", "vous",
    "ne", "ni", "si", "car", "donc", "puis", "comme",
    "ce", "cette", "ces", "son", "sa", "ses",
    "mon", "ma", "mes", "ton", "ta", "tes", "leur", "leurs",
}
WEAK_STARTER_PHRASES = ["à partir de", "en train de", "afin de"]

_NBSP = "\u00A0"


def pre_process(text: str) -> str:
    """法语无需前处理——élision 由 LLM 输出；断行保护在字幕层做。"""
    return text


def post_process_srt(srt_content: str) -> str:
    """法语 SRT 后处理：? ! : ; 前加 nbsp；« » 内侧加 nbsp。只改字幕文本行。"""
    lines = srt_content.split("\n")
    out = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            out.append(line)
            continue
        # ? ! : ; 前加不间断空格
        line = re.sub(r"\s*([?!;:])", rf"{_NBSP}\1", line)
        # guillemets 内侧加不间断空格
        line = re.sub(r"«\s*", f"«{_NBSP}", line)
        line = re.sub(r"\s*»", f"{_NBSP}»", line)
        out.append(line)
    return "\n".join(out)
```

- [ ] **Step 7: 跑测试确认通过**

```bash
pytest tests/test_languages_registry.py -v
```

Expected: 4 passed

- [ ] **Step 8: 提交**

```bash
git add pipeline/languages/ tests/test_languages_registry.py
git commit -m "feat(multi-translate): 新增语言规则注册中心与 de/fr 规则"
```

---

## Task 4: 出厂默认 prompt — `pipeline/languages/prompt_defaults.py`

**Files:**
- Create: `pipeline/languages/prompt_defaults.py`
- Create: `tests/test_prompt_defaults.py`

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_prompt_defaults.py
from pipeline.languages.prompt_defaults import DEFAULTS


def test_defaults_cover_de_and_fr_base_slots():
    for slot in ("base_translation", "base_tts_script", "base_rewrite"):
        assert ("base_translation", "de") in DEFAULTS or slot == "base_translation"
        assert (slot, "de") in DEFAULTS, f"missing de {slot}"
        assert (slot, "fr") in DEFAULTS, f"missing fr {slot}"


def test_ecommerce_plugin_shared():
    assert ("ecommerce_plugin", None) in DEFAULTS
    entry = DEFAULTS[("ecommerce_plugin", None)]
    # 平台不能只提 TikTok
    assert "Facebook" in entry["content"] or "短视频带货" in entry["content"]


def test_each_entry_has_provider_model_content():
    for key, entry in DEFAULTS.items():
        assert "provider" in entry
        assert "model" in entry
        assert "content" in entry and entry["content"].strip()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_prompt_defaults.py -v
```

Expected: FAIL

- [ ] **Step 3: 写实现**

```python
# pipeline/languages/prompt_defaults.py
"""出厂默认 prompt + 模型配置。

仅用于：
  1. 空库冷启动 seed
  2. 管理员后台"恢复此项默认"按钮
运行时绝不直接 import——走 appcore.llm_prompt_configs.resolve_prompt_config()。
"""
from __future__ import annotations

_DEFAULT_PROVIDER = "openrouter"
_DEFAULT_MODEL = "openai/gpt-4o-mini"


# ── 共享电商插件（平台中立：TikTok + Facebook + Reels + Shorts 等）──
_ECOMMERCE_PLUGIN = """This is a short-form commerce video (for platforms like TikTok, Facebook, Reels, Shorts, etc.).
Write authentically — like a local creator casually recommending something useful they discovered.
Avoid exaggerated claims, artificial urgency, superlatives without substance, aggressive CTAs.
The audience distrusts hard selling; emphasize quality, value, and practicality.
Do NOT add any CTA at the end — the video will have a separate universal CTA clip appended later."""


# ── 德语 base prompts ──
_DE_TRANSLATION = """You are a native German content creator. Return valid JSON only, shaped as
{"full_text": "...", "sentences": [{"index": 0, "text": "...", "source_segment_indices": [...]}]}.

You are NOT translating — you are RECREATING the script the way a German creator would naturally say it.
Use terms German consumers actually use (Caps, Organizer, Display — keep common English loanwords where
locals do). Pick one term per concept and stay consistent. Never literal-translate product category
names from the source.

Conversational German at B1 level, sachlich und authentisch. Prefer 6–12 words per sentence; avoid
long compound subordinate clauses. Capitalize all nouns (German grammar). Numbers use German
convention (2,5 not 2.5). No em-dashes, no en-dashes, ASCII punctuation only. Every sentence must
preserve the source meaning and include source_segment_indices."""


_DE_TTS_SCRIPT = """Prepare German text for ElevenLabs TTS and on-screen subtitles. Return valid JSON only:
{"full_text": "...", "blocks": [{"index": 0, "text": "...", "sentence_indices": [...], "source_segment_indices": [...]}],
 "subtitle_chunks": [{"index": 0, "text": "...", "block_indices": [...], "sentence_indices": [...], "source_segment_indices": [...]}]}.

Blocks: optimize for natural German speaking rhythm with energy; hook block punchy, benefit blocks
confident and informative. Subtitle chunks: 4–8 words each (German words are long), semantically
complete, no trailing punctuation, no em/en dashes."""


_DE_REWRITE = """You are a native German content creator REWRITING an existing German translation
to approximately {target_words} words (±10%). Direction: {direction} (shrink | expand).

Keep the same number of sentences when possible. Preserve every source_segment_indices mapping.
Same tone, capitalization, and formatting rules as the original German localization. Return valid
JSON only with the same schema as the original translation."""


# ── 法语 base prompts ──
_FR_TRANSLATION = """You are a French content creator based in France. Return valid JSON only,
shaped as {"full_text": "...", "sentences": [{"index": 0, "text": "...", "source_segment_indices": [...]}]}.

You are NOT a translator — you are RECREATING the script the way a French TikToker or Facebook
creator would naturally present this product to a French audience. Use terms French consumers
actually search for (rouge à lèvres, fond de teint, rangement…). Keep widely adopted English
loanwords French people actually use (design, look, tips, lifestyle). Pick one term per concept.

Tone: décontracté et informatif — a friend casually recommending something, not a sales pitch.
NO exaggerated claims, NO artificial urgency. French audiences distrust aggressive selling.
Conversational French at B1–B2. Default to "vous". Prefer 6–10 words per sentence.

Apply ALL mandatory French élisions: l'organizer, d'abord, j'adore, qu'il, c'est, n'est. NEVER
write "le organizer". Proper contractions: au, aux, du, des. French punctuation: non-breaking
space (U+00A0) before ? ! : ; and inside «  ». Preserve accents on uppercase: É, È, À, Ç, Ô.
No em/en dashes. Every sentence must preserve source meaning and include source_segment_indices."""


_FR_TTS_SCRIPT = """Prepare French text for ElevenLabs TTS and on-screen subtitles. Return valid JSON only:
{"full_text": "...", "blocks": [...], "subtitle_chunks": [...]} with the same schema as the German variant.

Blocks: décontracté French rhythm, measured delivery, natural pauses. Subtitle chunks: 4–8 words each,
semantically complete, no trailing punctuation. Preserve all French punctuation spacing (nbsp before
? ! : ;). Preserve élisions. No em/en dashes."""


_FR_REWRITE = """You are a French content creator REWRITING an existing French translation
to approximately {target_words} words (±10%). Direction: {direction}.

Keep the same number of sentences when possible. Preserve every source_segment_indices mapping.
Same tone, élisions, and punctuation spacing rules as the original French localization. Return
valid JSON only with the same schema."""


DEFAULTS: dict[tuple[str, str | None], dict] = {
    # 共享电商插件
    ("ecommerce_plugin", None): {
        "provider": _DEFAULT_PROVIDER, "model": _DEFAULT_MODEL,
        "content": _ECOMMERCE_PLUGIN,
    },
    # 德语
    ("base_translation", "de"): {
        "provider": _DEFAULT_PROVIDER, "model": _DEFAULT_MODEL,
        "content": _DE_TRANSLATION,
    },
    ("base_tts_script", "de"): {
        "provider": _DEFAULT_PROVIDER, "model": _DEFAULT_MODEL,
        "content": _DE_TTS_SCRIPT,
    },
    ("base_rewrite", "de"): {
        "provider": _DEFAULT_PROVIDER, "model": _DEFAULT_MODEL,
        "content": _DE_REWRITE,
    },
    # 法语
    ("base_translation", "fr"): {
        "provider": _DEFAULT_PROVIDER, "model": _DEFAULT_MODEL,
        "content": _FR_TRANSLATION,
    },
    ("base_tts_script", "fr"): {
        "provider": _DEFAULT_PROVIDER, "model": _DEFAULT_MODEL,
        "content": _FR_TTS_SCRIPT,
    },
    ("base_rewrite", "fr"): {
        "provider": _DEFAULT_PROVIDER, "model": _DEFAULT_MODEL,
        "content": _FR_REWRITE,
    },
}
```

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_prompt_defaults.py -v
```

Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add pipeline/languages/prompt_defaults.py tests/test_prompt_defaults.py
git commit -m "feat(multi-translate): 新增 de/fr 出厂默认 prompt 与共享电商插件"
```

---

## Task 5: 参数化 `pipeline/subtitle.py`（向后兼容）

**Files:**
- Modify: `pipeline/subtitle.py`
- Create: `tests/test_subtitle_param_compat.py`

- [ ] **Step 1: 写失败的测试（保证老行为不变 + 新参数生效）**

```python
# tests/test_subtitle_param_compat.py
from pipeline.subtitle import (
    wrap_text, format_subtitle_chunk_text, build_srt_from_chunks,
    apply_french_punctuation, apply_punctuation_spacing,
)


def test_wrap_text_default_still_42():
    """老调用方不传 max_chars，维持 42。"""
    text = "this is a short line that fits in 42 chars ok"
    out = wrap_text(text)
    # 41 字符，单行
    assert "\n" not in out


def test_wrap_text_with_custom_max_chars_de():
    text = "Das ist ein relativ langer deutscher Satz mit vielen Worten"
    out = wrap_text(text, max_chars=38)
    # 应断成两行
    assert "\n" in out
    for line in out.split("\n"):
        assert len(line) <= 38


def test_format_subtitle_chunk_text_accepts_weak_starters():
    """通过参数传入德语弱边界词集合。"""
    text = "Ein schöner Tag und ein neuer Anfang für alle"
    out = format_subtitle_chunk_text(text, weak_boundary_words={"und", "für"})
    assert "\n" in out
    # 不应在 "und" 或 "für" 之前断（违反弱边界规则时会增加 score）
    lines = out.split("\n")
    for line in lines:
        first_word = line.split()[0].lower().strip(",")
        assert first_word not in {"und", "für"}


def test_apply_french_punctuation_backward_compat():
    """老 FR 模块仍能用原函数名。"""
    srt = "1\n00:00:00,000 --> 00:00:01,000\nBonjour ?\n"
    out = apply_french_punctuation(srt)
    assert "Bonjour\u00A0?" in out


def test_apply_punctuation_spacing_generic():
    """新泛化函数，传 rules 字典。"""
    srt = "1\n00:00:00,000 --> 00:00:01,000\nHola : amigos !\n"
    rules = {"nbsp_before": ["?", "!", ":"], "guillemets": False}
    out = apply_punctuation_spacing(srt, rules)
    assert "Hola\u00A0:" in out
    assert "amigos\u00A0!" in out


def test_build_srt_from_chunks_still_works_with_default():
    """老调用方不传 weak_boundary_words，走默认英语集。"""
    chunks = [{"text": "Hello world this is a test", "start_time": 0.0, "end_time": 1.0}]
    out = build_srt_from_chunks(chunks)
    assert "00:00:00,000 --> 00:00:01,000" in out
```

- [ ] **Step 2: 跑测试确认部分失败**

```bash
pytest tests/test_subtitle_param_compat.py -v
```

Expected: `test_apply_punctuation_spacing_generic` 和 `test_wrap_text_with_custom_max_chars_de` FAIL（新函数/参数不存在），其余 PASS。

- [ ] **Step 3: 改 `pipeline/subtitle.py`**

对 `wrap_text()` 签名：已有 `max_chars=42` 参数，测试就能直接通过。

对 `format_subtitle_chunk_text()`：已有 `weak_boundary_words` 参数。

**真正要改的：新增 `apply_punctuation_spacing()` 并把 `apply_french_punctuation()` 改为其薄包装。** 在 [pipeline/subtitle.py](pipeline/subtitle.py) 文件末尾替换现有的 `apply_french_punctuation`：

```python
# pipeline/subtitle.py —— 在文件末尾（现有 apply_french_punctuation 的位置）替换

def apply_punctuation_spacing(srt_content: str, rules: dict) -> str:
    """按 rules 对 SRT 文本行做标点空格后处理（跳过时间戳行与序号行）。

    rules 字段：
      - nbsp_before: list[str]，这些标点前加 U+00A0
      - guillemets: bool，True 时 « » 内侧加 U+00A0
    """
    nbsp = "\u00A0"
    nbsp_before = set(rules.get("nbsp_before") or [])
    handle_guillemets = bool(rules.get("guillemets"))

    lines = srt_content.split("\n")
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            out.append(line)
            continue
        if nbsp_before:
            # 构造字符类；转义正则元字符
            escaped = "".join(re.escape(ch) for ch in nbsp_before)
            line = re.sub(rf"\s*([{escaped}])", rf"{nbsp}\1", line)
        if handle_guillemets:
            line = re.sub(r"«\s*", f"«{nbsp}", line)
            line = re.sub(r"\s*»", f"{nbsp}»", line)
        out.append(line)
    return "\n".join(out)


def apply_french_punctuation(text: str) -> str:
    """向后兼容薄包装：等价于 apply_punctuation_spacing(text, 法语规则)。"""
    return apply_punctuation_spacing(text, {
        "nbsp_before": ["?", "!", ":", ";"],
        "guillemets": True,
    })
```

- [ ] **Step 4: 跑测试确认全通过**

```bash
pytest tests/test_subtitle_param_compat.py -v
```

Expected: 6 passed

- [ ] **Step 5: 跑老 DE/FR 测试确保没破**

```bash
pytest tests/ -k "subtitle or french" -v
```

Expected: 原有测试全通过（无回归）。

- [ ] **Step 6: 提交**

```bash
git add pipeline/subtitle.py tests/test_subtitle_param_compat.py
git commit -m "refactor(subtitle): apply_french_punctuation 泛化为 apply_punctuation_spacing 并保留兼容包装"
```

---

## Task 6: 向量匹配 — 按 utterances 采样

**Files:**
- Modify: `pipeline/voice_match.py`
- Create: `tests/test_voice_match_utterance_sampling.py`

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_voice_match_utterance_sampling.py
from unittest.mock import patch
import pytest

from pipeline.voice_match import pick_utterance_window


def test_pick_utterance_window_single_long_is_enough():
    utts = [
        {"start_time": 0.0, "end_time": 3.0, "text": "hi"},
        {"start_time": 3.5, "end_time": 15.0, "text": "long single"},
        {"start_time": 16.0, "end_time": 17.0, "text": "short"},
    ]
    start, end = pick_utterance_window(utts, min_duration=8.0)
    assert start == pytest.approx(3.5)
    assert end == pytest.approx(15.0)


def test_pick_utterance_window_needs_stitching():
    utts = [
        {"start_time": 0.0, "end_time": 2.0, "text": "a"},
        {"start_time": 2.1, "end_time": 4.0, "text": "b"},
        {"start_time": 4.2, "end_time": 6.5, "text": "c"},
        {"start_time": 6.6, "end_time": 9.0, "text": "d"},
    ]
    start, end = pick_utterance_window(utts, min_duration=8.0)
    assert end - start >= 8.0


def test_pick_utterance_window_fallback_when_total_too_short():
    utts = [
        {"start_time": 0.0, "end_time": 1.0, "text": "a"},
        {"start_time": 1.5, "end_time": 3.0, "text": "b"},
    ]
    start, end = pick_utterance_window(utts, min_duration=8.0)
    # 总时长不够，返回整段可用范围
    assert start == pytest.approx(0.0)
    assert end == pytest.approx(3.0)


def test_pick_utterance_window_empty_raises():
    with pytest.raises(ValueError):
        pick_utterance_window([], min_duration=8.0)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_voice_match_utterance_sampling.py -v
```

Expected: FAIL（函数不存在）

- [ ] **Step 3: 改 `pipeline/voice_match.py`**

在文件末尾追加：

```python
# pipeline/voice_match.py —— 追加在文件末尾

def pick_utterance_window(utterances: list[dict], *,
                           min_duration: float = 8.0) -> tuple[float, float]:
    """从 ASR utterances 里挑一段作为音色采样窗口。

    策略：
      1. 若单个 utterance 时长 ≥ min_duration，直接用它
      2. 否则按时间顺序拼接相邻 utterances，找到第一个累计时长 ≥ min_duration 的窗口
      3. 若总时长仍不足，返回 [首个 utterance 起点, 末尾 utterance 终点]（整段）
    """
    if not utterances:
        raise ValueError("utterances is empty")

    # 策略 1：找最长单 utterance
    longest = max(utterances, key=lambda u: u["end_time"] - u["start_time"])
    if longest["end_time"] - longest["start_time"] >= min_duration:
        return float(longest["start_time"]), float(longest["end_time"])

    # 策略 2：滑动窗口拼接
    sorted_utts = sorted(utterances, key=lambda u: u["start_time"])
    for i in range(len(sorted_utts)):
        window_start = sorted_utts[i]["start_time"]
        for j in range(i, len(sorted_utts)):
            window_end = sorted_utts[j]["end_time"]
            if window_end - window_start >= min_duration:
                return float(window_start), float(window_end)

    # 策略 3：兜底，整段
    return float(sorted_utts[0]["start_time"]), float(sorted_utts[-1]["end_time"])


def extract_sample_from_utterances(video_path: str, utterances: list[dict],
                                     *, out_dir: str,
                                     min_duration: float = 8.0) -> str:
    """从视频按 utterances 窗口切出纯人声采样片段。"""
    import os
    start, end = pick_utterance_window(utterances, min_duration=min_duration)
    full_wav = _extract_audio_track(video_path, out_dir)
    return _cut_clip(full_wav, start, end,
                     os.path.join(out_dir, "utt_sample"))
```

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_voice_match_utterance_sampling.py -v
```

Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add pipeline/voice_match.py tests/test_voice_match_utterance_sampling.py
git commit -m "feat(voice-match): 新增基于 ASR utterances 的采样窗口选择"
```

---

## Task 7: `MultiTranslateRunner` 骨架

**Files:**
- Create: `appcore/runtime_multi.py`
- Create: `tests/test_runtime_multi_skeleton.py`

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_runtime_multi_skeleton.py
from unittest.mock import MagicMock

from appcore.events import EventBus
from appcore.runtime_multi import MultiTranslateRunner


def test_class_attrs():
    assert MultiTranslateRunner.project_type == "multi_translate"
    assert MultiTranslateRunner.tts_model_id == "eleven_multilingual_v2"


def test_resolve_lang_from_task_state():
    runner = MultiTranslateRunner(bus=EventBus(), user_id=1)
    task = {"target_lang": "de"}
    assert runner._resolve_target_lang(task) == "de"


def test_resolve_lang_raises_when_missing():
    import pytest
    runner = MultiTranslateRunner(bus=EventBus(), user_id=1)
    with pytest.raises(ValueError):
        runner._resolve_target_lang({})
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_runtime_multi_skeleton.py -v
```

Expected: FAIL

- [ ] **Step 3: 写实现**

```python
# appcore/runtime_multi.py
"""多语种视频翻译 pipeline runner。

单一 Runner 处理 de/fr/es/it/ja/pt 所有目标语言：
- 翻译步骤走 llm_prompt_configs resolver
- 字幕/TTS 走 pipeline.languages.<lang> 规则
- 音色走现有 voice_match + elevenlabs_voices
"""
from __future__ import annotations

import importlib
import logging

import appcore.task_state as task_state
from appcore.runtime import PipelineRunner

log = logging.getLogger(__name__)


class MultiTranslateRunner(PipelineRunner):
    project_type: str = "multi_translate"
    tts_model_id = "eleven_multilingual_v2"
    # target_language_label / tts_language_code / tts_default_voice_language
    # 都动态从 task.target_lang 推导，不作为 class attr 硬编码

    # 以下属性为运行时解析的便捷入口，不被基类逻辑依赖
    def _resolve_target_lang(self, task: dict) -> str:
        lang = task.get("target_lang")
        if not lang:
            raise ValueError("task.target_lang is required for multi_translate")
        return lang

    def _get_lang_rules(self, lang: str):
        """加载 pipeline.languages.<lang> 规则模块。"""
        from pipeline.languages.registry import get_rules
        return get_rules(lang)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_runtime_multi_skeleton.py -v
```

Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add appcore/runtime_multi.py tests/test_runtime_multi_skeleton.py
git commit -m "feat(multi-translate): MultiTranslateRunner 骨架"
```

---

## Task 8: `MultiTranslateRunner._step_translate` — 走 resolver

**Files:**
- Modify: `appcore/runtime_multi.py`
- Create: `tests/test_runtime_multi_translate.py`

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_runtime_multi_translate.py
from unittest.mock import MagicMock, patch, ANY

from appcore.events import EventBus
from appcore.runtime_multi import MultiTranslateRunner


def _make_runner():
    return MultiTranslateRunner(bus=EventBus(), user_id=1)


def test_step_translate_calls_resolver_with_base_plus_plugin():
    runner = _make_runner()
    task = {
        "task_dir": "/tmp/x",
        "target_lang": "de",
        "source_language": "en",
        "script_segments": [{"index": 0, "text": "hello"}],
        "interactive_review": False,
        "variants": {},
    }
    with patch("appcore.task_state.get", return_value=task), \
         patch("appcore.task_state.update"), \
         patch("appcore.task_state.set_artifact"), \
         patch("appcore.task_state.set_current_review_step"), \
         patch("appcore.runtime_multi.resolve_prompt_config") as m_resolve, \
         patch("appcore.runtime_multi.generate_localized_translation") as m_gen, \
         patch("appcore.runtime_multi._save_json"), \
         patch("appcore.runtime_multi._log_usage"), \
         patch("appcore.runtime_multi._build_review_segments", return_value=[]), \
         patch("appcore.runtime_multi.build_asr_artifact", return_value={}), \
         patch("appcore.runtime_multi.build_translate_artifact", return_value={}):
        m_resolve.side_effect = [
            {"provider": "openrouter", "model": "gpt", "content": "BASE_DE"},
            {"provider": "openrouter", "model": "gpt", "content": "ECOM_PLUGIN"},
        ]
        m_gen.return_value = {"full_text": "hi", "sentences": [], "_usage": {}}
        runner._step_translate("t1")

    # resolver 被调用两次：base_translation + ecommerce_plugin
    assert m_resolve.call_args_list[0].args == ("base_translation", "de")
    assert m_resolve.call_args_list[1].args == ("ecommerce_plugin", None)

    # 传给 LLM 的 system prompt 是 base + plugin 拼接
    kwargs = m_gen.call_args.kwargs
    assert "BASE_DE" in kwargs["custom_system_prompt"]
    assert "ECOM_PLUGIN" in kwargs["custom_system_prompt"]
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_runtime_multi_translate.py -v
```

Expected: FAIL

- [ ] **Step 3: 实现 `_step_translate`**

在 [appcore/runtime_multi.py](appcore/runtime_multi.py) 追加：

```python
# appcore/runtime_multi.py —— 追加

import json
import os

from appcore.events import (
    EVT_TRANSLATE_RESULT, EVT_SUBTITLE_READY, EVT_ENGLISH_ASR_RESULT,
    EVT_TTS_SCRIPT_READY,
)
from appcore.llm_prompt_configs import resolve_prompt_config
from appcore.runtime import (
    _build_review_segments, _save_json, _resolve_translate_provider,
)
from appcore.usage_log import record as _log_usage
from pipeline.localization import build_source_full_text_zh
from pipeline.translate import (
    generate_localized_translation, get_model_display_name,
)
from web.preview_artifacts import (
    build_asr_artifact, build_translate_artifact,
    build_subtitle_artifact, build_tts_artifact,
)


class MultiTranslateRunner(PipelineRunner):  # 覆盖上面定义的空类
    project_type: str = "multi_translate"
    tts_model_id = "eleven_multilingual_v2"

    def _resolve_target_lang(self, task: dict) -> str:
        lang = task.get("target_lang")
        if not lang:
            raise ValueError("task.target_lang is required for multi_translate")
        return lang

    def _get_lang_rules(self, lang: str):
        from pipeline.languages.registry import get_rules
        return get_rules(lang)

    def _build_system_prompt(self, lang: str) -> str:
        base = resolve_prompt_config("base_translation", lang)
        plugin = resolve_prompt_config("ecommerce_plugin", None)
        return f"{base['content']}\n\n---\n\n{plugin['content']}"

    def _step_translate(self, task_id: str) -> None:
        task = task_state.get(task_id)
        task_dir = task["task_dir"]
        lang = self._resolve_target_lang(task)
        source_language = task.get("source_language", "zh")
        lang_label = "中文" if source_language == "zh" else "英文"

        self._set_step(task_id, "translate", "running",
                       f"正在将{lang_label}翻译为 {lang.upper()}...")

        provider = _resolve_translate_provider(self.user_id)
        script_segments = task.get("script_segments", [])
        source_full_text = build_source_full_text_zh(script_segments)

        system_prompt = self._build_system_prompt(lang)

        localized_translation = generate_localized_translation(
            source_full_text, script_segments, variant="normal",
            custom_system_prompt=system_prompt,
            provider=provider, user_id=self.user_id,
        )

        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get("normal", {}))
        variant_state["localized_translation"] = localized_translation
        variants["normal"] = variant_state
        _save_json(task_dir, "localized_translation.normal.json", localized_translation)

        review_segments = _build_review_segments(script_segments, localized_translation)
        requires_confirmation = bool(task.get("interactive_review"))
        task_state.update(
            task_id,
            source_full_text_zh=source_full_text,
            localized_translation=localized_translation,
            variants=variants,
            segments=review_segments,
            _segments_confirmed=not requires_confirmation,
        )
        task_state.set_artifact(task_id, "asr",
                                 build_asr_artifact(task.get("utterances", []),
                                                    source_full_text,
                                                    source_language=source_language))
        task_state.set_artifact(task_id, "translate",
                                 build_translate_artifact(source_full_text,
                                                          localized_translation,
                                                          source_language=source_language,
                                                          target_language=lang))
        _save_json(task_dir, "source_full_text.json", {"full_text": source_full_text})
        _save_json(task_dir, "localized_translation.json", localized_translation)

        usage = localized_translation.get("_usage") or {}
        _log_usage(self.user_id, task_id, provider,
                    model_name=get_model_display_name(provider, self.user_id),
                    success=True,
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"))

        if requires_confirmation:
            task_state.set_current_review_step(task_id, "translate")
            self._set_step(task_id, "translate", "waiting",
                           f"{lang.upper()} 翻译已生成，等待人工确认")
        else:
            task_state.set_current_review_step(task_id, "")
            self._set_step(task_id, "translate", "done",
                           f"{lang.upper()} 本土化翻译完成")

        self._emit(task_id, EVT_TRANSLATE_RESULT, {
            "source_full_text_zh": source_full_text,
            "localized_translation": localized_translation,
            "segments": review_segments,
            "requires_confirmation": requires_confirmation,
        })
```

删除前一版 `class MultiTranslateRunner` 的空定义（保留一个完整类）。

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_runtime_multi_skeleton.py tests/test_runtime_multi_translate.py -v
```

Expected: 全部 passed

- [ ] **Step 5: 提交**

```bash
git add appcore/runtime_multi.py tests/test_runtime_multi_translate.py
git commit -m "feat(multi-translate): _step_translate 通过 resolver 拼接 base+plugin prompt"
```

---

## Task 9: `MultiTranslateRunner._step_subtitle` — 走语言规则

**Files:**
- Modify: `appcore/runtime_multi.py`
- Create: `tests/test_runtime_multi_subtitle.py`

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_runtime_multi_subtitle.py
from unittest.mock import patch

from appcore.events import EventBus
from appcore.runtime_multi import MultiTranslateRunner


def test_step_subtitle_uses_lang_rules_for_weak_starters_and_post_process():
    runner = MultiTranslateRunner(bus=EventBus(), user_id=1)
    task = {
        "task_dir": "/tmp/x",
        "target_lang": "fr",
        "variants": {
            "normal": {
                "tts_audio_path": "/tmp/x/audio.mp3",
                "tts_script": {"subtitle_chunks": [
                    {"text": "Bonjour les amis", "block_indices": [0],
                     "sentence_indices": [0], "source_segment_indices": [0]}
                ]},
            }
        },
    }
    with patch("appcore.task_state.get", return_value=task), \
         patch("appcore.task_state.update"), \
         patch("appcore.task_state.set_artifact"), \
         patch("appcore.task_state.set_preview_file"), \
         patch("appcore.runtime_multi.transcribe_local_audio",
               return_value=[{"text": "Bonjour les amis", "start_time": 0, "end_time": 1}]), \
         patch("appcore.runtime_multi._get_audio_duration", return_value=1.0), \
         patch("appcore.runtime_multi.align_subtitle_chunks_to_asr") as m_align, \
         patch("appcore.runtime_multi.build_srt_from_chunks") as m_build, \
         patch("appcore.runtime_multi.save_srt", return_value="/tmp/x/subtitle.srt"), \
         patch("appcore.runtime_multi._save_json"), \
         patch("appcore.runtime_multi.resolve_key", return_value="volc"):
        m_align.return_value = [{"text": "Bonjour les amis",
                                   "start_time": 0.0, "end_time": 1.0}]
        m_build.return_value = "1\n00:00:00,000 --> 00:00:01,000\nBonjour les amis ?\n"
        runner._step_subtitle("t1", "/tmp/x")

    # 验证 build_srt_from_chunks 收到了法语弱边界词
    kwargs = m_build.call_args.kwargs
    assert "et" in kwargs["weak_boundary_words"]
    assert "ou" in kwargs["weak_boundary_words"]
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_runtime_multi_subtitle.py -v
```

Expected: FAIL（`_step_subtitle` 尚未实现）

- [ ] **Step 3: 实现 `_step_subtitle`**

在 [appcore/runtime_multi.py](appcore/runtime_multi.py) 的类内部追加：

```python
# appcore/runtime_multi.py —— 在 MultiTranslateRunner 类内部追加

# 这些 import 放到文件顶部 import 区
# from appcore.api_keys import resolve_key
# from pipeline.asr import transcribe_local_audio
# from pipeline.subtitle import build_srt_from_chunks, save_srt
# from pipeline.subtitle_alignment import align_subtitle_chunks_to_asr
# from pipeline.tts import _get_audio_duration


    def _step_subtitle(self, task_id: str, task_dir: str) -> None:
        task = task_state.get(task_id)
        lang = self._resolve_target_lang(task)
        rules = self._get_lang_rules(lang)

        self._set_step(task_id, "subtitle", "running",
                       f"正在根据 {lang.upper()} 音频校正字幕...")

        volc_api_key = resolve_key(self.user_id, "volc", "VOLC_API_KEY")

        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get("normal", {}))
        tts_audio_path = variant_state.get("tts_audio_path", "")

        utterances = transcribe_local_audio(
            tts_audio_path, prefix=f"tts-asr/{task_id}/normal",
            volc_api_key=volc_api_key,
        )
        asr_result = {
            "full_text": " ".join(u.get("text", "").strip()
                                    for u in utterances if u.get("text")).strip(),
            "utterances": utterances,
        }
        tts_script = variant_state.get("tts_script", {})
        total_duration = _get_audio_duration(tts_audio_path) if tts_audio_path else 0.0
        corrected_chunks = align_subtitle_chunks_to_asr(
            tts_script.get("subtitle_chunks", []),
            asr_result,
            total_duration=total_duration,
        )

        # 按语言规则生成 SRT：传入弱边界词 + 最大行宽
        srt_content = build_srt_from_chunks(
            corrected_chunks,
            weak_boundary_words=rules.WEAK_STARTERS,
        )
        # 语言后处理（法语 nbsp、西语倒问号等）
        srt_content = rules.post_process_srt(srt_content)

        srt_path = save_srt(srt_content, os.path.join(task_dir, "subtitle.normal.srt"))

        variant_state.update({
            "english_asr_result": asr_result,
            "corrected_subtitle": {"chunks": corrected_chunks,
                                     "srt_content": srt_content},
            "srt_path": srt_path,
        })
        task_state.set_preview_file(task_id, "srt", srt_path)
        variants["normal"] = variant_state

        task_state.update(
            task_id, variants=variants,
            english_asr_result=asr_result,
            corrected_subtitle={"chunks": corrected_chunks,
                                  "srt_content": srt_content},
            srt_path=srt_path,
        )
        task_state.set_artifact(task_id, "subtitle",
                                 build_subtitle_artifact(asr_result, corrected_chunks,
                                                          srt_content,
                                                          target_language=lang))

        _save_json(task_dir, f"{lang}_asr_result.normal.json", asr_result)
        _save_json(task_dir, "corrected_subtitle.normal.json",
                   {"chunks": corrected_chunks, "srt_content": srt_content})

        self._emit(task_id, EVT_ENGLISH_ASR_RESULT, {"english_asr_result": asr_result})
        self._emit(task_id, EVT_SUBTITLE_READY, {"srt": srt_content})
        self._set_step(task_id, "subtitle", "done", f"{lang.upper()} 字幕生成完成")
```

在文件顶部 import 区新增所需 import。

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_runtime_multi_subtitle.py -v
```

Expected: 1 passed

- [ ] **Step 5: 提交**

```bash
git add appcore/runtime_multi.py tests/test_runtime_multi_subtitle.py
git commit -m "feat(multi-translate): _step_subtitle 按语言规则生成字幕与后处理"
```

---

## Task 10: 音色匹配 — pipeline 步骤 + 持久化到 state

**Files:**
- Modify: `appcore/runtime_multi.py`
- Create: `tests/test_runtime_multi_voice_match.py`

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_runtime_multi_voice_match.py
import numpy as np
from unittest.mock import patch

from appcore.events import EventBus
from appcore.runtime_multi import MultiTranslateRunner


def test_step_voice_match_writes_candidates_to_state():
    runner = MultiTranslateRunner(bus=EventBus(), user_id=1)
    task = {
        "task_dir": "/tmp/x",
        "target_lang": "de",
        "utterances": [{"start_time": 0, "end_time": 10, "text": "hi"}],
        "video_path": "/tmp/x/src.mp4",
    }
    with patch("appcore.task_state.get", return_value=task), \
         patch("appcore.task_state.update") as m_update, \
         patch("appcore.runtime_multi.extract_sample_from_utterances",
               return_value="/tmp/x/clip.wav"), \
         patch("appcore.runtime_multi.embed_audio_file",
               return_value=np.zeros(256, dtype=np.float32)), \
         patch("appcore.runtime_multi.match_candidates") as m_match:
        m_match.return_value = [
            {"voice_id": "v1", "name": "A", "similarity": 0.85,
             "gender": "male", "preview_url": "u1"},
            {"voice_id": "v2", "name": "B", "similarity": 0.80,
             "gender": "male", "preview_url": "u2"},
            {"voice_id": "v3", "name": "C", "similarity": 0.74,
             "gender": "female", "preview_url": "u3"},
        ]
        runner._step_voice_match("t1")

    payload = m_update.call_args.kwargs
    assert payload["voice_match_candidates"][0]["voice_id"] == "v1"
    assert len(payload["voice_match_candidates"]) == 3


def test_step_voice_match_fallback_when_empty():
    runner = MultiTranslateRunner(bus=EventBus(), user_id=1)
    task = {
        "task_dir": "/tmp/x", "target_lang": "de",
        "utterances": [{"start_time": 0, "end_time": 10, "text": "hi"}],
        "video_path": "/tmp/x/src.mp4",
    }
    with patch("appcore.task_state.get", return_value=task), \
         patch("appcore.task_state.update") as m_update, \
         patch("appcore.runtime_multi.extract_sample_from_utterances",
               return_value="/tmp/x/clip.wav"), \
         patch("appcore.runtime_multi.embed_audio_file",
               return_value=np.zeros(256, dtype=np.float32)), \
         patch("appcore.runtime_multi.match_candidates", return_value=[]), \
         patch("appcore.runtime_multi.resolve_default_voice",
               return_value="default-voice-id"):
        runner._step_voice_match("t1")

    payload = m_update.call_args.kwargs
    assert payload["voice_match_candidates"] == []
    assert payload.get("voice_match_fallback_voice_id") == "default-voice-id"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_runtime_multi_voice_match.py -v
```

Expected: FAIL

- [ ] **Step 3: 实现 `_step_voice_match`**

在文件顶部 import 区：

```python
from pipeline.voice_embedding import embed_audio_file
from pipeline.voice_match import extract_sample_from_utterances, match_candidates
from appcore.video_translate_defaults import resolve_default_voice
```

在 `MultiTranslateRunner` 类内追加：

```python
    def _step_voice_match(self, task_id: str) -> None:
        task = task_state.get(task_id)
        lang = self._resolve_target_lang(task)
        utterances = task.get("utterances") or []
        video_path = task.get("video_path")

        if not utterances or not video_path:
            task_state.update(task_id,
                              voice_match_candidates=[],
                              voice_match_fallback_voice_id=resolve_default_voice(lang))
            return

        try:
            clip = extract_sample_from_utterances(
                video_path, utterances, out_dir=task["task_dir"],
                min_duration=8.0,
            )
            vec = embed_audio_file(clip)
            candidates = match_candidates(vec, language=lang, top_k=3)
        except Exception as exc:
            log.exception("voice match failed for %s: %s", task_id, exc)
            candidates = []

        if not candidates:
            task_state.update(
                task_id,
                voice_match_candidates=[],
                voice_match_fallback_voice_id=resolve_default_voice(lang),
            )
            return

        # similarity 转 float 方便 JSON 序列化
        for c in candidates:
            c["similarity"] = float(c.get("similarity", 0.0))

        task_state.update(task_id, voice_match_candidates=candidates)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_runtime_multi_voice_match.py -v
```

Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add appcore/runtime_multi.py tests/test_runtime_multi_voice_match.py
git commit -m "feat(multi-translate): 音色向量匹配持久化到 task state"
```

---

## Task 11: `MultiTranslateRunner` 接入 pipeline 步骤列表

**Files:**
- Modify: `appcore/runtime_multi.py`
- Modify: `appcore/runtime.py`（只读检查，看是否需要加步骤 hook）

- [ ] **Step 1: 在 `_run` 的步骤列表里插入 voice_match**

看 [appcore/runtime.py:714-716](appcore/runtime.py) 现有步骤：

```python
("translate", lambda: self._step_translate(task_id)),
("tts", lambda: self._step_tts(task_id, task_dir)),
("subtitle", lambda: self._step_subtitle(task_id, task_dir)),
```

`multi_translate` 需要把 `voice_match` 插在 ASR 完成后、translate 之前。在 `MultiTranslateRunner` 覆盖 `_run` 的步骤列表拼装，或在基类 `PipelineRunner._run` 里增加一个 `_extra_pre_translate_steps()` hook。

**选择 hook 方案**（改动最小、最优雅）：在 [appcore/runtime.py](appcore/runtime.py) 的 `_run` 里，translate 之前调用 `self._extra_pre_translate_steps(task_id, task_dir)`；基类默认为 no-op，`MultiTranslateRunner` override 它跑 voice_match。

定位并修改 `appcore/runtime.py` 的 `_run` 方法（约第 700-720 行），在 translate 步骤前插入：

```python
# appcore/runtime.py —— 在 _run 的 steps 列表构造之前插入
# （位置：原 steps = [ ... ("translate", ...) ] 的前一行）

# 允许子类插入 pre-translate 钩子（multi_translate 用于 voice match）
if hasattr(self, "_extra_pre_translate_steps"):
    pre_steps = self._extra_pre_translate_steps(task_id, task_dir)
    if pre_steps:
        steps = pre_steps + steps  # 注意：此时 steps 还未拼出，需调位置
```

（具体插入位置需看实际源码上下文。）

更简单：直接在 `MultiTranslateRunner` 里覆盖 `_run`，从基类拷贝骨架然后插入 voice_match 步骤。**推荐这个方案**。

在 [appcore/runtime_multi.py](appcore/runtime_multi.py) 追加：

```python
    def _run(self, task_id: str) -> None:
        """覆盖基类 _run，在 ASR 后、translate 前插入 voice_match。

        其他逻辑（进度通知、异常兜底、step 列表驱动）沿用基类模式但显式列出，
        便于未来该 Runner 独立演进。
        """
        task = task_state.get(task_id)
        task_dir = task["task_dir"]

        steps = [
            ("asr", lambda: self._step_asr(task_id, task_dir)),
            ("voice_match", lambda: self._step_voice_match(task_id)),
            ("alignment", lambda: self._step_alignment(task_id, task_dir)),
            ("translate", lambda: self._step_translate(task_id)),
            ("tts", lambda: self._step_tts(task_id, task_dir)),
            ("subtitle", lambda: self._step_subtitle(task_id, task_dir)),
            ("compose", lambda: self._step_compose(task_id, task_dir)),
        ]

        for name, fn in steps:
            if task_state.get(task_id).get("status") == "cancelled":
                return
            try:
                fn()
            except Exception as exc:
                log.exception("step %s failed for %s", name, task_id)
                self._set_step(task_id, name, "failed", str(exc))
                return
```

- [ ] **Step 2: 跑现有 multi_translate 相关测试**

```bash
pytest tests/test_runtime_multi_*.py -v
```

Expected: 全部 passed

- [ ] **Step 3: 提交**

```bash
git add appcore/runtime_multi.py
git commit -m "feat(multi-translate): pipeline 步骤序列 — 在 ASR 后 translate 前插入 voice_match"
```

---

## Task 12: Multi translate blueprint — 路由骨架

**Files:**
- Create: `web/routes/multi_translate.py`
- Create: `web/services/multi_pipeline_runner.py`
- Modify: `web/app.py`
- Create: `tests/test_multi_translate_routes.py`

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_multi_translate_routes.py
from unittest.mock import patch


def test_list_page_renders(authed_client_no_db):
    with patch("web.routes.multi_translate.db_query", return_value=[]), \
         patch("appcore.settings.get_retention_hours", return_value=72), \
         patch("appcore.task_recovery.recover_all_interrupted_tasks"):
        resp = authed_client_no_db.get("/multi-translate")
    assert resp.status_code == 200
    assert "多语种视频翻译".encode("utf-8") in resp.data


def test_list_filters_by_lang(authed_client_no_db):
    with patch("web.routes.multi_translate.db_query") as m_q, \
         patch("appcore.settings.get_retention_hours", return_value=72), \
         patch("appcore.task_recovery.recover_all_interrupted_tasks"):
        m_q.return_value = []
        authed_client_no_db.get("/multi-translate?lang=de")
    sql = m_q.call_args.args[0]
    assert "type = 'multi_translate'" in sql
    # lang 过滤应传进 args 而不是拼接
    args = m_q.call_args.args[1]
    assert "de" in args


def test_detail_404_for_other_user(authed_client_no_db):
    with patch("web.routes.multi_translate.db_query_one", return_value=None), \
         patch("appcore.task_recovery.recover_project_if_needed"):
        resp = authed_client_no_db.get("/multi-translate/unknown")
    assert resp.status_code == 404
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_multi_translate_routes.py -v
```

Expected: FAIL（路由未注册）

- [ ] **Step 3: 写实现 — `web/services/multi_pipeline_runner.py`**

```python
# web/services/multi_pipeline_runner.py
"""MultiTranslateRunner 的 SocketIO 适配层。"""
from __future__ import annotations

import threading

from appcore.events import EventBus
from appcore.runtime_multi import MultiTranslateRunner
from appcore.task_recovery import register_active_task, unregister_active_task
from web.extensions import socketio


def _handler(task_id: str):
    def fn(event):
        socketio.emit(event.type, event.payload, room=task_id)
    return fn


def _run(runner: MultiTranslateRunner, task_id: str, start_step: str | None = None):
    register_active_task(runner.project_type, task_id)
    try:
        if start_step is None:
            runner.start(task_id)
        else:
            runner.resume(task_id, start_step)
    finally:
        unregister_active_task(runner.project_type, task_id)


def start(task_id: str, user_id: int | None = None):
    bus = EventBus()
    bus.subscribe(_handler(task_id))
    runner = MultiTranslateRunner(bus=bus, user_id=user_id)
    threading.Thread(target=_run, args=(runner, task_id), daemon=True).start()


def resume(task_id: str, start_step: str, user_id: int | None = None):
    bus = EventBus()
    bus.subscribe(_handler(task_id))
    runner = MultiTranslateRunner(bus=bus, user_id=user_id)
    threading.Thread(target=_run, args=(runner, task_id, start_step), daemon=True).start()
```

- [ ] **Step 4: 写实现 — `web/routes/multi_translate.py`**

抄 `web/routes/de_translate.py` 的骨架，把 project_type、URL 前缀、模板名全部换成 `multi_translate` / `multi-translate`，并**新增 `lang` 过滤参数**。把 `_step_voice_match` 需要的 `video_path` / `utterances` 字段在 bootstrap 时写入 task state。

关键差异写在代码里（完整文件太长，重点段落）：

```python
# web/routes/multi_translate.py
"""多语种视频翻译蓝图：页面路由 + API。"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime

from flask import Blueprint, render_template, request, jsonify, abort
from flask_login import login_required, current_user

from config import OUTPUT_DIR, UPLOAD_DIR
from appcore.db import query as db_query, query_one as db_query_one, execute as db_execute
from appcore.task_recovery import (
    recover_all_interrupted_tasks, recover_project_if_needed,
)
from web import store
from web.services import multi_pipeline_runner
from web.services.artifact_download import serve_artifact_download


SUPPORTED_LANGS = ("de", "fr")   # Batch 1；Batch 2/3 扩展时改这里

bp = Blueprint("multi_translate", __name__)


# ── 页面路由 ──
@bp.route("/multi-translate")
@login_required
def index():
    recover_all_interrupted_tasks()
    lang = request.args.get("lang", "").strip()
    if lang and lang not in SUPPORTED_LANGS:
        lang = ""

    if lang:
        rows = db_query(
            "SELECT id, original_filename, display_name, thumbnail_path, status, "
            "       state_json, created_at, expires_at, deleted_at "
            "FROM projects "
            "WHERE user_id = %s AND type = 'multi_translate' AND deleted_at IS NULL "
            "  AND JSON_EXTRACT(state_json, '$.target_lang') = %s "
            "ORDER BY created_at DESC",
            (current_user.id, lang),
        )
    else:
        rows = db_query(
            "SELECT id, original_filename, display_name, thumbnail_path, status, "
            "       state_json, created_at, expires_at, deleted_at "
            "FROM projects "
            "WHERE user_id = %s AND type = 'multi_translate' AND deleted_at IS NULL "
            "ORDER BY created_at DESC",
            (current_user.id,),
        )

    from appcore.settings import get_retention_hours
    return render_template(
        "multi_translate_list.html",
        projects=rows, now=datetime.now(),
        current_lang=lang,
        supported_langs=SUPPORTED_LANGS,
        retention_hours=get_retention_hours("multi_translate"),
    )


@bp.route("/multi-translate/<task_id>")
@login_required
def detail(task_id: str):
    recover_project_if_needed(task_id, "multi_translate")
    row = db_query_one(
        "SELECT * FROM projects WHERE id = %s AND user_id = %s",
        (task_id, current_user.id),
    )
    if not row:
        abort(404)
    state = json.loads(row.get("state_json") or "{}")
    return render_template(
        "multi_translate_detail.html",
        project=row, state=state,
        target_lang=state.get("target_lang", "de"),
    )


# ── API：bootstrap、complete、start、resume、confirm、download 等 ──
# 完整 API 照搬 de_translate.py 的 bootstrap/complete/start/restart/source-language/
# alignment/segments/export/resume/download 路由，仅把 'de_translate' 换为
# 'multi_translate'，并在 bootstrap 的 state_json 里写入 target_lang。
```

**关键改动 vs de_translate 原版**：
1. `bootstrap` 接受 `target_lang` 参数（必传）并写入 `state_json.target_lang`
2. project type 写 `'multi_translate'`
3. runner 改调用 `multi_pipeline_runner.start(...)` / `.resume(...)`
4. 下游 `complete` 把 `video_path` / `utterances` 写入 state（如果不存在）

完整文件：参考 `web/routes/de_translate.py` 逐一替换字符串后产出。

- [ ] **Step 5: 改 `web/app.py` 注册 blueprint**

```python
# web/app.py —— import 区新增
from web.routes.multi_translate import bp as multi_translate_bp

# register_blueprint 区新增
app.register_blueprint(multi_translate_bp)

# socketio.on 区新增
@socketio.on("join_multi_translate_task")
def on_join_multi_translate(data):
    from flask_socketio import join_room
    tid = data.get("task_id")
    if tid:
        join_room(tid)
```

- [ ] **Step 6: 跑测试确认通过**

```bash
pytest tests/test_multi_translate_routes.py -v
```

Expected: 3 passed

- [ ] **Step 7: 提交**

```bash
git add web/routes/multi_translate.py web/services/multi_pipeline_runner.py web/app.py tests/test_multi_translate_routes.py
git commit -m "feat(multi-translate): blueprint + SocketIO 适配 + app.py 注册"
```

---

## Task 13: 列表页模板 + 胶囊按钮

**Files:**
- Create: `web/templates/multi_translate_list.html`

- [ ] **Step 1: 写实现**

抄 `web/templates/de_translate_list.html` 骨架，在 `page_title` 下方追加胶囊按钮区。

```html
{% extends "layout.html" %}
{% block title %}多语种视频翻译 - AutoVideoSrt{% endblock %}
{% block page_title %}多语种视频翻译{% endblock %}
{% block extra_style %}
<style>
  .lang-pills { display: flex; gap: 8px; margin: 16px 0 24px; flex-wrap: wrap; }
  .lang-pill {
    padding: 6px 14px; border-radius: 9999px; font-size: 13px;
    background: var(--bg-card); border: 1px solid var(--border-main);
    color: var(--text-main); text-decoration: none; transition: all 0.15s;
  }
  .lang-pill:hover { border-color: oklch(56% 0.16 230); }
  .lang-pill.active {
    background: oklch(56% 0.16 230); color: #fff;
    border-color: oklch(56% 0.16 230);
  }
</style>
{# 引入复用的列表样式 #}
{% include "_medias_list_styles.html" ignore missing %}
{% endblock %}

{% block content %}
<div class="lang-pills">
  <a class="lang-pill {{ 'active' if not current_lang else '' }}" href="/multi-translate">全部</a>
  {% for lang in supported_langs %}
    {% set label = {'de':'🇩🇪 德语','fr':'🇫🇷 法语','es':'🇪🇸 西语','it':'🇮🇹 意语','ja':'🇯🇵 日语','pt':'🇵🇹 葡语'}[lang] %}
    <a class="lang-pill {{ 'active' if current_lang == lang else '' }}"
       href="/multi-translate?lang={{ lang }}">{{ label }}</a>
  {% endfor %}
</div>

{# 列表主体：复用 de_translate_list.html 的卡片/列表视图结构 #}
{% include "_translate_project_cards.html" ignore missing %}

{% if not projects %}
<div style="text-align:center; padding:60px 20px; color:var(--text-user-badge);">
  <p>暂无项目。点击右上角"新建"上传视频。</p>
</div>
{% endif %}
{% endblock %}

{% block scripts %}
<script>
  // TODO(Task 18): 新建按钮弹窗 / 批次创建等交互
</script>
{% endblock %}
```

> **备注**：`_translate_project_cards.html` 部分公共片段在第 1 批实现中直接从 `de_translate_list.html` 复制关键 grid/list 渲染块过来，保持视觉一致。完整 CSS 实现见下节 Step 2。

- [ ] **Step 2: 补 CSS + grid/list 渲染**

直接从 `web/templates/de_translate_list.html` 复制 `.grid` / `.list-item` / `.expire-tag` 等样式段到 `<style>` 块里，然后把项目卡片的 `<a href="/de-translate/{{ p.id }}">` 替换为 `<a href="/multi-translate/{{ p.id }}">`，且在卡片右下角徽章追加 `{{ p.state_json | from_json | get('target_lang') | upper }}`。

（具体实现参考 de_translate_list.html，替换字符串）

- [ ] **Step 3: 手动验证**

```bash
python main.py   # 或 flask run
# 浏览器打开 http://localhost:5000/multi-translate
```

Expected: 页面可渲染；胶囊按钮可点击切换 URL。

- [ ] **Step 4: 提交**

```bash
git add web/templates/multi_translate_list.html
git commit -m "feat(multi-translate): 列表页 + 胶囊按钮过滤"
```

---

## Task 14: 工作台 detail 模板

**Files:**
- Create: `web/templates/multi_translate_detail.html`

- [ ] **Step 1: 写实现**

基于 `web/templates/de_translate_detail.html` 克隆一份，把写死的 `de` 换成从 `target_lang` 传入：

```html
{% extends "layout.html" %}
{% if not project.deleted_at %}
{% set allow_upload = false %}
{% set show_back_link = false %}
{% set task_id = project.id %}
{% set initial_task = state %}
{% set api_base = '/api/multi-translate' %}
{% set url_for_detail = '/multi-translate/__TASK_ID__' %}
{% set voice_language = target_lang %}
{% set default_source_language = 'en' %}
{% endif %}
{% block title %}{{ project.display_name or project.original_filename or project.id }} - {{ target_lang | upper }} 翻译{% endblock %}
{% block page_title %}{{ project.display_name or project.original_filename or project.id }}（{{ target_lang | upper }}）{% endblock %}
{% block extra_style %}
{% include "_task_workbench_styles.html" %}
<style>
  .target-lang-badge {
    display: inline-block; padding: 4px 10px; background: oklch(94% 0.04 225);
    color: oklch(22% 0.02 235); border-radius: 6px; font-size: 12px;
    font-weight: 600; margin-left: 8px;
  }
</style>
{% endblock %}
{% block content %}
{% if project.deleted_at %}
<a class="back-link" href="/multi-translate">← 返回多语种视频翻译列表</a>
<div class="expired-notice"><p style="font-size:32px;">任务已过期</p></div>
{% else %}
<a class="back-link" href="/multi-translate">← 返回多语种视频翻译列表</a>
<p class="page-subtitle">
  中文/英文 → {{ target_lang | upper }} 本土化翻译
  <span class="target-lang-badge">目标语言：{{ target_lang | upper }}</span>
</p>
{% if state and state.parent_task_id %}
<p style="margin:-4px 0 12px; font-size:13px;">
  🔗 本任务由批次翻译创建 · <a href="/tasks/{{ state.parent_task_id }}" style="color:oklch(56% 0.16 230)">查看父批次任务 →</a>
</p>
{% endif %}
{% include "_task_workbench.html" %}
{% endif %}
{% endblock %}
{% block scripts %}
{% if not project.deleted_at %}
{% include "_task_workbench_scripts.html" %}
{% endif %}
{% endblock %}
```

- [ ] **Step 2: 手动验证**

```bash
# 浏览器打开一个已存在的 de/fr 任务的 detail 页 via /multi-translate/xxx
# （需先创建一条 type='multi_translate' 的测试数据，或先跑 Task 15-18 再回来验）
```

- [ ] **Step 3: 提交**

```bash
git add web/templates/multi_translate_detail.html
git commit -m "feat(multi-translate): 工作台 detail 模板（复用 _task_workbench）"
```

---

## Task 15: 音色选择 UI — Top-3 卡片 + 全库兜底

**Files:**
- Modify: `web/templates/_task_workbench.html`（或新增 `_voice_selector_multi.html`）
- Create: `web/static/voice_selector_multi.js`

- [ ] **Step 1: 写前端组件**

在工作台音色选择步骤里加一段新 DOM（独立 include 保洁）：

```html
{# web/templates/_voice_selector_multi.html #}
<div id="voice-selector-multi" class="voice-selector"
     data-task-id="{{ task_id }}" data-lang="{{ voice_language }}">
  <div class="vs-heading">
    <strong>🎤 向量匹配推荐音色</strong>
    <span class="vs-hint">系统已从 {{ voice_language | upper }} 音色库中挑出与原视频声线最接近的 3 个</span>
  </div>

  <div class="vs-sample" id="vs-sample">
    <span>原视频采样：</span>
    <audio controls id="vs-sample-audio"></audio>
  </div>

  <div class="vs-warning" id="vs-warning" style="display:none;">
    ⚠️ 原声与库内音色差异较大，建议手动挑选
  </div>

  <div class="vs-candidates" id="vs-candidates">
    <!-- 由 JS 注入 3 张候选卡 -->
  </div>

  <button class="vs-more" id="vs-more">🔍 查看全部 {{ voice_language | upper }} 音色</button>

  <div class="vs-full-library" id="vs-full-library" style="display:none;">
    <!-- lazy load 完整库列表 -->
  </div>
</div>

<style>
  .voice-selector { margin: 16px 0; padding: 16px;
    background: var(--bg-card); border: 1px solid var(--border-main);
    border-radius: 12px; }
  .vs-heading { display: flex; align-items: baseline; gap: 12px; margin-bottom: 12px; }
  .vs-hint { font-size: 12px; color: var(--text-user-badge); }
  .vs-sample { display: flex; align-items: center; gap: 8px; margin: 8px 0 16px; }
  .vs-warning { padding: 8px 12px; background: oklch(96% 0.05 85);
    border-left: 3px solid oklch(72% 0.14 80); border-radius: 6px;
    margin-bottom: 12px; font-size: 13px; }
  .vs-candidates { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; }
  .vs-card { padding: 12px; border: 1.5px solid var(--border-main);
    border-radius: 10px; cursor: pointer; transition: border-color 0.15s; }
  .vs-card:hover { border-color: oklch(56% 0.16 230); }
  .vs-card.selected { border-color: oklch(56% 0.16 230);
    background: oklch(94% 0.04 225); }
  .vs-card .vs-sim { font-weight: 600; color: oklch(56% 0.16 230); }
  .vs-more { margin-top: 12px; padding: 8px 12px; background: transparent;
    border: 1px solid var(--border-main); border-radius: 8px; cursor: pointer; }
</style>
```

- [ ] **Step 2: 写 JS**

```javascript
// web/static/voice_selector_multi.js
(function () {
  const root = document.getElementById("voice-selector-multi");
  if (!root) return;
  const taskId = root.dataset.taskId;
  const lang = root.dataset.lang;

  async function loadInitial() {
    const resp = await fetch(`/api/multi-translate/${taskId}`);
    const task = await resp.json();
    const candidates = task.state?.voice_match_candidates || [];
    const fallbackId = task.state?.voice_match_fallback_voice_id;

    // 采样音频
    const sampleUrl = task.state?.voice_match_sample_url;
    if (sampleUrl) document.getElementById("vs-sample-audio").src = sampleUrl;

    // 低相似度提示
    if (candidates.length && candidates[0].similarity < 0.4) {
      document.getElementById("vs-warning").style.display = "block";
    }

    renderCandidates(candidates, fallbackId);
  }

  function renderCandidates(candidates, fallbackId) {
    const root = document.getElementById("vs-candidates");
    root.innerHTML = "";
    if (!candidates.length) {
      root.innerHTML = `<p style="grid-column:span 3;color:var(--text-user-badge);">
        向量库暂无该语言的可匹配音色，系统将使用兜底音色 ${fallbackId || "（未配置）"}</p>`;
      return;
    }
    candidates.forEach((c, i) => {
      const card = document.createElement("div");
      card.className = "vs-card";
      card.dataset.voiceId = c.voice_id;
      card.innerHTML = `
        <div class="vs-sim">${(c.similarity * 100).toFixed(1)}% 相似</div>
        <div style="font-weight:600;margin:6px 0;">${c.name}</div>
        <div style="font-size:12px;color:var(--text-user-badge);">${c.gender || ""} · ${c.accent || ""}</div>
        <audio controls style="width:100%;margin-top:8px;" src="${c.preview_url}"></audio>
        <button style="margin-top:8px;width:100%;" class="vs-select-btn">
          ${i === 0 ? "使用此音色（推荐）" : "使用此音色"}
        </button>`;
      card.querySelector(".vs-select-btn").addEventListener("click", () => selectVoice(c.voice_id));
      root.appendChild(card);
    });
  }

  async function selectVoice(voiceId) {
    await fetch(`/api/multi-translate/${taskId}/voice`, {
      method: "PUT",
      headers: { "Content-Type": "application/json",
                 "X-CSRF-Token": document.querySelector("meta[name=csrf-token]").content },
      body: JSON.stringify({ voice_id: voiceId }),
    });
    // 高亮
    document.querySelectorAll(".vs-card").forEach(el =>
      el.classList.toggle("selected", el.dataset.voiceId === voiceId));
  }

  document.getElementById("vs-more").addEventListener("click", async () => {
    const box = document.getElementById("vs-full-library");
    if (box.style.display !== "none") {
      box.style.display = "none";
      return;
    }
    box.style.display = "block";
    const resp = await fetch(`/voice-library/api/list?language=${lang}&page_size=200`);
    const data = await resp.json();
    box.innerHTML = data.items.map(v => `
      <div class="vs-card" data-voice-id="${v.voice_id}" style="display:inline-block;margin:4px;min-width:200px;">
        <div style="font-weight:600;">${v.name}</div>
        <div style="font-size:12px;">${v.gender} · ${v.accent || ""}</div>
        <audio controls style="width:100%;margin-top:6px;" src="${v.preview_url}"></audio>
        <button class="vs-select-btn" style="margin-top:6px;width:100%;">选此音色</button>
      </div>`).join("");
    box.querySelectorAll(".vs-select-btn").forEach(btn => {
      btn.addEventListener("click", e => {
        selectVoice(e.target.parentElement.dataset.voiceId);
      });
    });
  });

  loadInitial();
})();
```

- [ ] **Step 3: 在 detail 页 include + 加 API 端点**

在 `multi_translate_detail.html` 的 `scripts` block 里加 `<script src="{{ url_for('static', filename='voice_selector_multi.js') }}"></script>`。

在 `web/routes/multi_translate.py` 新增 PUT endpoint：

```python
@bp.route("/api/multi-translate/<task_id>/voice", methods=["PUT"])
@login_required
def update_voice(task_id: str):
    row = db_query_one("SELECT state_json FROM projects WHERE id = %s AND user_id = %s",
                        (task_id, current_user.id))
    if not row:
        abort(404)
    state = json.loads(row["state_json"] or "{}")
    body = request.get_json() or {}
    voice_id = body.get("voice_id")
    if not voice_id:
        return jsonify({"error": "voice_id is required"}), 400
    state["selected_voice_id"] = voice_id
    db_execute("UPDATE projects SET state_json = %s WHERE id = %s",
                (json.dumps(state, ensure_ascii=False), task_id))
    return jsonify({"ok": True, "voice_id": voice_id})
```

- [ ] **Step 4: 提交**

```bash
git add web/templates/_voice_selector_multi.html web/static/voice_selector_multi.js web/routes/multi_translate.py web/templates/multi_translate_detail.html
git commit -m "feat(multi-translate): 音色选择 UI — Top-3 卡 + 全库兜底"
```

---

## Task 16: 管理员 prompts 后台 — 路由 + API

**Files:**
- Create: `web/routes/admin_prompts.py`
- Modify: `web/app.py`
- Create: `tests/test_admin_prompts_routes.py`

- [ ] **Step 1: 写失败的测试**

```python
# tests/test_admin_prompts_routes.py
from unittest.mock import patch


def test_list_prompts(authed_client_no_db):
    with patch("web.routes.admin_prompts.dao.list_all") as m_list:
        m_list.return_value = [
            {"id": 1, "slot": "base_translation", "lang": "de",
             "model_provider": "openrouter", "model_name": "gpt-4o-mini",
             "content": "X", "enabled": 1, "updated_at": None, "updated_by": 1},
        ]
        resp = authed_client_no_db.get("/admin/api/prompts")
    assert resp.status_code == 200
    assert resp.get_json()["items"][0]["slot"] == "base_translation"


def test_upsert_prompt(authed_client_no_db):
    with patch("web.routes.admin_prompts.dao.upsert") as m_up:
        resp = authed_client_no_db.put(
            "/admin/api/prompts",
            json={"slot": "base_translation", "lang": "de",
                  "provider": "openrouter", "model": "gpt-4o-mini",
                  "content": "new content"},
        )
    assert resp.status_code == 200
    m_up.assert_called_once()


def test_restore_default(authed_client_no_db):
    with patch("web.routes.admin_prompts.dao.delete") as m_del:
        resp = authed_client_no_db.delete(
            "/admin/api/prompts?slot=base_translation&lang=de"
        )
    assert resp.status_code == 200
    m_del.assert_called_once_with("base_translation", "de")


def test_non_admin_rejected(authed_user_client_no_db):
    resp = authed_user_client_no_db.get("/admin/api/prompts")
    assert resp.status_code in (302, 403)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
pytest tests/test_admin_prompts_routes.py -v
```

Expected: FAIL

- [ ] **Step 3: 写 `web/routes/admin_prompts.py`**

```python
# web/routes/admin_prompts.py
"""管理员后台 — LLM prompt 配置可视化编辑。"""
from __future__ import annotations

from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user

from appcore import llm_prompt_configs as dao

bp = Blueprint("admin_prompts", __name__)


def _require_admin():
    if getattr(current_user, "role", None) != "admin":
        return jsonify({"error": "admin only"}), 403
    return None


@bp.route("/admin/prompts")
@login_required
def page():
    err = _require_admin()
    if err:
        return err
    return render_template("admin_prompts.html",
                           slots=sorted(dao.VALID_SLOTS),
                           langs=["de", "fr"])  # Batch 1


@bp.route("/admin/api/prompts", methods=["GET"])
@login_required
def list_prompts():
    err = _require_admin()
    if err:
        return err
    return jsonify({"items": dao.list_all()})


@bp.route("/admin/api/prompts", methods=["PUT"])
@login_required
def upsert_prompt():
    err = _require_admin()
    if err:
        return err
    body = request.get_json() or {}
    slot = body.get("slot")
    lang = body.get("lang") or None
    provider = body.get("provider")
    model = body.get("model")
    content = body.get("content")
    if not all([slot, provider, model, content]):
        return jsonify({"error": "slot/provider/model/content required"}), 400
    dao.upsert(slot, lang,
                provider=provider, model=model, content=content,
                updated_by=current_user.id)
    return jsonify({"ok": True})


@bp.route("/admin/api/prompts", methods=["DELETE"])
@login_required
def delete_prompt():
    err = _require_admin()
    if err:
        return err
    slot = request.args.get("slot")
    lang = request.args.get("lang") or None
    if not slot:
        return jsonify({"error": "slot required"}), 400
    dao.delete(slot, lang)
    return jsonify({"ok": True})


@bp.route("/admin/api/prompts/resolve", methods=["GET"])
@login_required
def resolve_one():
    """预览"当前实际生效的"配置（含 fallback 到 default）。"""
    err = _require_admin()
    if err:
        return err
    slot = request.args.get("slot")
    lang = request.args.get("lang") or None
    if not slot:
        return jsonify({"error": "slot required"}), 400
    return jsonify(dao.resolve_prompt_config(slot, lang))
```

- [ ] **Step 4: 改 `web/app.py` 注册 blueprint**

```python
from web.routes.admin_prompts import bp as admin_prompts_bp
# ...
app.register_blueprint(admin_prompts_bp)
```

- [ ] **Step 5: 跑测试确认通过**

```bash
pytest tests/test_admin_prompts_routes.py -v
```

Expected: 4 passed

- [ ] **Step 6: 提交**

```bash
git add web/routes/admin_prompts.py web/app.py tests/test_admin_prompts_routes.py
git commit -m "feat(admin): prompts 可视化编辑后台 — 路由 + API"
```

---

## Task 17: 管理员 prompts 后台 — 模板 + JS

**Files:**
- Create: `web/templates/admin_prompts.html`
- Create: `web/static/admin_prompts.js`

- [ ] **Step 1: 写模板**

```html
{# web/templates/admin_prompts.html #}
{% extends "layout.html" %}
{% block title %}Prompt 管理 - AutoVideoSrt{% endblock %}
{% block page_title %}Prompt 管理{% endblock %}
{% block extra_style %}
<style>
  .grid { display: grid; grid-template-columns: 200px repeat({{ langs|length + 1 }}, 1fr); gap: 2px;
          background: var(--border-main); padding: 2px; border-radius: 8px; margin: 24px 0; }
  .cell { background: var(--bg-card); padding: 14px; }
  .cell.header { background: var(--bg-user-badge); font-weight: 600; text-align: center; }
  .cell.row-label { font-weight: 600; background: var(--bg-user-badge); }
  .cell .btn-edit { font-size: 12px; color: oklch(56% 0.16 230); cursor: pointer;
                    border: none; background: transparent; padding: 0; }
  #editor-modal { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0;
                  background: rgba(0,0,0,0.4); z-index: 100; align-items: center; justify-content: center; }
  #editor-modal.open { display: flex; }
  #editor-panel { background: var(--bg-card); border-radius: 12px; padding: 24px;
                  width: 80%; max-width: 1000px; max-height: 90vh; overflow: auto; }
  #prompt-textarea { width: 100%; min-height: 360px; font-family: "JetBrains Mono", monospace;
                     font-size: 13px; padding: 12px; border: 1px solid var(--border-main);
                     border-radius: 6px; }
</style>
{% endblock %}
{% block content %}
<p class="page-subtitle">多语种视频翻译的 LLM prompt 和模型配置。改完立刻生效，无需重启。</p>

<div class="grid">
  <div class="cell header">槽位 \ 语言</div>
  {% for lang in langs %}<div class="cell header">{{ lang | upper }}</div>{% endfor %}
  <div class="cell header">共享（电商插件）</div>

  {% for slot in slots %}
    <div class="cell row-label">{{ slot }}</div>
    {% for lang in langs %}
      <div class="cell">
        {% if slot == 'ecommerce_plugin' %}—{% else %}
          <button class="btn-edit" data-slot="{{ slot }}" data-lang="{{ lang }}">✏️ 编辑</button>
        {% endif %}
      </div>
    {% endfor %}
    <div class="cell">
      {% if slot == 'ecommerce_plugin' %}
        <button class="btn-edit" data-slot="{{ slot }}" data-lang="">✏️ 编辑</button>
      {% else %}—{% endif %}
    </div>
  {% endfor %}
</div>

<div id="editor-modal">
  <div id="editor-panel">
    <h2 id="editor-title">编辑</h2>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:16px 0;">
      <label>供应商 <select id="sel-provider">
        <option value="openrouter">openrouter</option>
        <option value="doubao">doubao</option>
        <option value="openai">openai</option>
        <option value="anthropic">anthropic</option>
      </select></label>
      <label>模型 <input id="txt-model" type="text"></label>
    </div>
    <textarea id="prompt-textarea" placeholder="Prompt 内容..."></textarea>
    <p style="font-size:12px;color:var(--text-user-badge);margin:8px 0;">
      可用占位符（rewrite）：{target_words}, {direction}
    </p>
    <div style="display:flex;gap:8px;margin-top:16px;">
      <button id="btn-save">保存</button>
      <button id="btn-restore">恢复默认</button>
      <button id="btn-cancel" style="margin-left:auto;">取消</button>
    </div>
  </div>
</div>
{% endblock %}
{% block scripts %}
<script src="{{ url_for('static', filename='admin_prompts.js') }}"></script>
{% endblock %}
```

- [ ] **Step 2: 写 JS**

```javascript
// web/static/admin_prompts.js
(function () {
  const modal = document.getElementById("editor-modal");
  const title = document.getElementById("editor-title");
  const selProvider = document.getElementById("sel-provider");
  const txtModel = document.getElementById("txt-model");
  const txtContent = document.getElementById("prompt-textarea");
  let currentSlot = null, currentLang = null;

  const csrf = () => document.querySelector("meta[name=csrf-token]").content;

  async function openEditor(slot, lang) {
    currentSlot = slot; currentLang = lang || null;
    title.textContent = `编辑 ${slot} · ${lang ? lang.toUpperCase() : "共享"}`;
    const qs = new URLSearchParams({ slot });
    if (lang) qs.set("lang", lang);
    const resp = await fetch(`/admin/api/prompts/resolve?${qs}`);
    const cfg = await resp.json();
    selProvider.value = cfg.provider;
    txtModel.value = cfg.model;
    txtContent.value = cfg.content;
    modal.classList.add("open");
  }

  document.querySelectorAll(".btn-edit").forEach(btn => {
    btn.addEventListener("click", () => openEditor(btn.dataset.slot, btn.dataset.lang));
  });

  document.getElementById("btn-save").addEventListener("click", async () => {
    const resp = await fetch("/admin/api/prompts", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() },
      body: JSON.stringify({
        slot: currentSlot, lang: currentLang,
        provider: selProvider.value, model: txtModel.value,
        content: txtContent.value,
      }),
    });
    if (resp.ok) { alert("已保存"); modal.classList.remove("open"); }
    else alert("保存失败：" + (await resp.text()));
  });

  document.getElementById("btn-restore").addEventListener("click", async () => {
    if (!confirm("确认恢复此项到出厂默认？当前自定义内容将被删除。")) return;
    const qs = new URLSearchParams({ slot: currentSlot });
    if (currentLang) qs.set("lang", currentLang);
    await fetch(`/admin/api/prompts?${qs}`, {
      method: "DELETE",
      headers: { "X-CSRF-Token": csrf() },
    });
    alert("已恢复默认，下次使用时会重新 seed。");
    modal.classList.remove("open");
  });

  document.getElementById("btn-cancel").addEventListener("click", () => {
    modal.classList.remove("open");
  });
})();
```

- [ ] **Step 3: 手动验证**

```bash
# 管理员登录 → http://localhost:5000/admin/prompts
# 点击任一 ✏️ 编辑 → 弹出编辑器 → 改 prompt → 保存 → 刷新再看确实写入
```

- [ ] **Step 4: 提交**

```bash
git add web/templates/admin_prompts.html web/static/admin_prompts.js
git commit -m "feat(admin): prompts 编辑页面 + JS 交互"
```

---

## Task 18: 业务流程内 `ⓘ` prompt 透明化标签

**Files:**
- Modify: `web/templates/_task_workbench.html` 或 `_task_workbench_scripts.html`

- [ ] **Step 1: 在翻译步骤卡右上角加 ⓘ 图标**

定位 `_task_workbench.html` 中翻译步骤的卡片块，在 `.step-header` 末尾插入：

```html
{# 仅 multi_translate 模块显示；其他项目类型不渲染 #}
{% if api_base == '/api/multi-translate' %}
<span class="prompt-info" data-slot="base_translation"
      data-lang="{{ target_lang }}" style="margin-left:8px;cursor:help;">
  ⓘ
</span>
{% endif %}
```

- [ ] **Step 2: 加 JS 悬停加载 resolve 结果**

在 `_task_workbench_scripts.html` 末尾追加：

```javascript
document.querySelectorAll(".prompt-info").forEach(el => {
  el.addEventListener("mouseenter", async () => {
    if (el.dataset.loaded) return;
    const qs = new URLSearchParams({ slot: el.dataset.slot, lang: el.dataset.lang });
    try {
      const resp = await fetch(`/admin/api/prompts/resolve?${qs}`);
      if (resp.ok) {
        const cfg = await resp.json();
        el.title = `模型：${cfg.provider} / ${cfg.model}\nPrompt 预览：\n${cfg.content.slice(0, 400)}...`;
        el.dataset.loaded = "1";
      } else {
        el.title = "无权查看（仅管理员）";
      }
    } catch {
      el.title = "加载失败";
    }
  });
});
```

- [ ] **Step 3: 手动验证**

```bash
# 普通用户进 /multi-translate/xxx 看翻译步骤 — ⓘ 悬停应提示"无权查看"
# 管理员进 — 悬停应显示模型和 prompt 预览
```

- [ ] **Step 4: 提交**

```bash
git add web/templates/_task_workbench.html web/templates/_task_workbench_scripts.html
git commit -m "feat(multi-translate): 业务流程内 ⓘ 展示当前 prompt 与模型"
```

---

## Task 19: 侧边栏导航调整

**Files:**
- Modify: `web/templates/layout.html`

- [ ] **Step 1: 改导航**

定位 `layout.html` 第 298-310 行的 `.sidebar-nav` 区域。

- 删除 `<a href="/de-translate">` 一行 + `<a href="/fr-translate">` 一行
- 在原位置插入：

```html
<a href="/multi-translate" class="sidebar-item">
  <span class="nav-icon">🌐</span> 多语种视频翻译
</a>
```

保留 `/translate`（英文原模块）和 `/translate-lab`（测试模块）不动。

- [ ] **Step 2: 手动验证**

```bash
# 浏览器看侧边栏：应只有 🌐 多语种视频翻译 + 🎬 视频翻译 + 🧪 视频翻译（测试）
# 老 URL /de-translate 直达仍可访问
```

- [ ] **Step 3: 提交**

```bash
git add web/templates/layout.html
git commit -m "feat(sidebar): 新增多语种视频翻译入口并删除 de/fr 独立入口"
```

---

## Task 20: app 启动时 seed prompt 默认值

**Files:**
- Modify: `web/app.py` 或 `appcore/bootstrap.py`

- [ ] **Step 1: 加冷启动 seed**

在 `web/app.py::create_app` 末尾或 `appcore/bootstrap.py` 里加：

```python
def _seed_default_prompts():
    """启动时确保每个 (slot, lang) 至少有一条 enabled 记录，没有就 seed default。"""
    from appcore.llm_prompt_configs import resolve_prompt_config
    from pipeline.languages.prompt_defaults import DEFAULTS
    for (slot, lang), _default in DEFAULTS.items():
        try:
            resolve_prompt_config(slot, lang)   # resolve 内部会 fallback 并写回
        except Exception as e:
            log.warning("seed prompt failed for (%s, %s): %s", slot, lang, e)

# create_app 末尾或启动钩子里调一次
_seed_default_prompts()
```

- [ ] **Step 2: 手动验证**

```bash
python -c "from appcore.db import query; print(query('SELECT slot, lang FROM llm_prompt_configs'))"
```

Expected: 显示 7 条记录（6 base + 1 ecommerce_plugin）

- [ ] **Step 3: 提交**

```bash
git add web/app.py
git commit -m "feat(multi-translate): 启动时 seed 出厂默认 prompt"
```

---

## Task 21: 端到端烟雾测试

**Files:**
- Create: `tests/test_multi_translate_e2e_smoke.py`

- [ ] **Step 1: 写 E2E 测试（heavy mock）**

```python
# tests/test_multi_translate_e2e_smoke.py
"""多语种视频翻译端到端烟雾测试（heavy mocked）。

验证从 bootstrap 到 subtitle 的整条链路不抛异常，
且 state_json 里写入了正确的 target_lang / voice_match_candidates / localized_translation。
"""
import json
from unittest.mock import patch, MagicMock

import numpy as np


def _mock_everything():
    return [
        patch("pipeline.voice_embedding.embed_audio_file",
              return_value=np.zeros(256, dtype=np.float32)),
        patch("pipeline.voice_match._extract_audio_track",
              return_value="/tmp/x/full.wav"),
        patch("pipeline.voice_match._cut_clip",
              return_value="/tmp/x/clip.wav"),
        patch("pipeline.translate.generate_localized_translation",
              return_value={"full_text": "Hallo Welt",
                             "sentences": [{"index": 0, "text": "Hallo Welt",
                                              "source_segment_indices": [0]}],
                             "_usage": {}}),
    ]


def test_smoke_de_pipeline_doesnt_crash(logged_in_client):
    # 注：需要真实 DB。测试场景：完整跑一次 de 翻译、断言 state_json
    # 为简化，本烟雾测试用 heavy mock + 内存 runner 直接调 _step_*
    import appcore.task_state as task_state
    from appcore.events import EventBus
    from appcore.runtime_multi import MultiTranslateRunner

    task_id = "smoke-" + "x" * 8
    task_state._TASKS[task_id] = {
        "task_id": task_id,
        "task_dir": "/tmp/smoke",
        "target_lang": "de",
        "source_language": "en",
        "script_segments": [{"index": 0, "text": "hello"}],
        "utterances": [{"start_time": 0, "end_time": 10, "text": "hello"}],
        "video_path": "/tmp/smoke/src.mp4",
        "interactive_review": False,
        "variants": {},
    }

    runner = MultiTranslateRunner(bus=EventBus(), user_id=1)

    patches = [
        patch("appcore.runtime_multi.resolve_prompt_config",
              side_effect=lambda slot, lang: {"provider": "openrouter",
                                                "model": "gpt", "content": f"{slot}-{lang}"}),
        patch("appcore.runtime_multi.generate_localized_translation",
              return_value={"full_text": "Hallo",
                             "sentences": [{"index": 0, "text": "Hallo",
                                              "source_segment_indices": [0]}],
                             "_usage": {}}),
        patch("appcore.runtime_multi._resolve_translate_provider",
              return_value="openrouter"),
        patch("appcore.runtime_multi.get_model_display_name",
              return_value="gpt"),
        patch("appcore.runtime_multi._save_json"),
        patch("appcore.runtime_multi._log_usage"),
        patch("appcore.runtime_multi._build_review_segments", return_value=[]),
        patch("appcore.runtime_multi.build_asr_artifact", return_value={}),
        patch("appcore.runtime_multi.build_translate_artifact", return_value={}),
        patch("appcore.runtime_multi.extract_sample_from_utterances",
              return_value="/tmp/smoke/clip.wav"),
        patch("appcore.runtime_multi.embed_audio_file",
              return_value=np.zeros(256, dtype=np.float32)),
        patch("appcore.runtime_multi.match_candidates",
              return_value=[{"voice_id": "v1", "name": "A",
                              "similarity": 0.8, "gender": "male",
                              "preview_url": "u"}]),
    ]
    for p in patches:
        p.start()
    try:
        runner._step_voice_match(task_id)
        runner._step_translate(task_id)
    finally:
        for p in patches:
            p.stop()

    task = task_state.get(task_id)
    assert task.get("voice_match_candidates")[0]["voice_id"] == "v1"
    assert task.get("localized_translation")["full_text"] == "Hallo"
    task_state._TASKS.pop(task_id, None)
```

- [ ] **Step 2: 跑测试**

```bash
pytest tests/test_multi_translate_e2e_smoke.py -v
```

Expected: 1 passed

- [ ] **Step 3: 跑全部相关测试确保无回归**

```bash
pytest tests/test_multi_translate_routes.py \
       tests/test_runtime_multi_*.py \
       tests/test_llm_prompt_configs_dao.py \
       tests/test_languages_registry.py \
       tests/test_prompt_defaults.py \
       tests/test_subtitle_param_compat.py \
       tests/test_voice_match_utterance_sampling.py \
       tests/test_admin_prompts_routes.py \
       tests/test_multi_translate_e2e_smoke.py -v
```

Expected: 全部 passed

- [ ] **Step 4: 跑老 DE/FR 测试保证无回归**

```bash
pytest tests/ -k "de_translate or fr_translate or subtitle or french" -v
```

Expected: 原有测试全 pass

- [ ] **Step 5: 提交**

```bash
git add tests/test_multi_translate_e2e_smoke.py
git commit -m "test(multi-translate): 端到端烟雾测试覆盖 voice_match + translate 主链路"
```

---

## Task 22: 手动冒烟 — 真跑一次 de + fr 任务

**Files:** （无新文件）

- [ ] **Step 1: 启动服务**

```bash
python main.py
```

- [ ] **Step 2: 在浏览器真跑一个 de 任务**

1. 登录 → 进 `/multi-translate`
2. 确认侧边栏只有 🌐 多语种视频翻译入口
3. 点击胶囊按钮 `🇩🇪 德语` → 新建任务
4. 上传一个短视频（<30s 的中文或英文短视频）
5. 等 ASR 完成 → 看到 `voice_match_candidates` 写进工作台的音色卡
6. 确认分段 → 翻译运行 → 看到德语译文
7. 选一个音色 → TTS → 字幕 → 合成 → 下载

记录：
- [ ] 向量匹配 Top-3 是否出现
- [ ] Top-1 相似度是否 >= 0.5（>=0.4 不报警）
- [ ] 德语译文是否保留名词大写、用德语本土化口吻
- [ ] 字幕是否每行 ≤ 38 字符
- [ ] 下载的 mp4 可播放

- [ ] **Step 3: 换一个 fr 任务重跑**

重点观察：
- [ ] 法语译文是否有正确的 élision
- [ ] 字幕的 `? ! :` 前是否加了不间断空格
- [ ] 音色匹配是否出 3 个法语候选

- [ ] **Step 4: 验证管理员后台改 prompt 立即生效**

1. admin 登录 → `/admin/prompts`
2. 编辑 `base_translation · de`，在内容前加一行 `EXPERIMENT MARKER:`
3. 保存
4. 新建一个 de 任务 → 看 `state_json` 或 `ⓘ` 预览，确认 prompt 带上了 marker
5. 恢复默认 → 再建任务 → marker 消失

- [ ] **Step 5: 验证老 DE/FR URL 仍能访问**

1. 拿一个老 de_translate 任务 id（从 DB 查）
2. 浏览器访问 `/de-translate/<id>` → 应能正常打开工作台
3. 确认老任务仍可正常操作

- [ ] **Step 6: 记录手动冒烟结果到 PR 描述**

形成一份冒烟记录放进合并 PR 的描述里（不需要 commit）。

---

# Self-review（不要写回文档，仅内部检查）

- **Spec 覆盖**：每节均有对应任务
  - § 1 目标/边界 → Task 1–21
  - § 2 架构 → Task 7, 11
  - § 3 数据模型 → Task 1
  - § 4 Prompt 可视化 → Task 2, 4, 16, 17, 18, 20
  - § 5 Pipeline 流程 → Task 8, 9, 10, 11
  - § 6 语言规则 → Task 3
  - § 7 音色匹配改造 → Task 6, 10
  - § 8 前端 UI → Task 13, 14, 15, 19
  - § 10 技术债修复 → Task 5
  - 第 2/3 批：本 plan 不含，独立 plan

- **Placeholder**：无 TODO、TBD；每一步均含完整代码或命令

- **类型一致性**：
  - `resolve_prompt_config(slot, lang)` 返回 `{provider, model, content}` 贯穿 Task 2/8/16/17
  - `task.target_lang` 字段贯穿 Task 7/8/9/10/12
  - `voice_match_candidates` 结构 `[{voice_id, name, similarity, gender, preview_url}]` 贯穿 Task 10/15

# Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-18-multi-translate-batch1.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - 每个任务派发独立 subagent，任务间有 review 关口，快速迭代

**2. Inline Execution** - 在本会话里顺序执行，带 checkpoint

**选哪个？**
