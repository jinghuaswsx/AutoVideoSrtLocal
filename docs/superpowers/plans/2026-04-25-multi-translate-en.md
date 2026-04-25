# 多语种视频翻译 — 接入英语（en-US）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `multi_translate` 流程里把英语（en-US）作为新增目标语言挂上，主线英语流程（`appcore/runtime.py` + `pipeline/localization.py`）零改动。

**Architecture:** 沿用现有 `MultiTranslateRunner` + `pipeline/languages/<lang>.py` + `llm_prompt_configs` DB seed 三件套，按 9 种已有语言同构地补一份 `en` 模块、三段 en-US prompt、白名单与前端选项。完全不动数据库 schema。

**Tech Stack:** Python 3 / Flask / Jinja2 / pytest（mock-only，不连 MySQL）/ JavaScript（vanilla）。

**Spec:** [docs/superpowers/specs/2026-04-25-multi-translate-en-design.md](../specs/2026-04-25-multi-translate-en-design.md)

---

## 文件结构总览

| 文件 | 创建 / 修改 | 责任 |
|------|------------|------|
| `pipeline/languages/en.py` | **创建** | 英语字幕/TTS 规则常量 + WEAK_STARTERS + pre/post-process |
| `pipeline/languages/registry.py` | 修改 | `SUPPORTED_LANGS` 加 `"en"` |
| `pipeline/languages/prompt_defaults.py` | 修改 | 新增 `_EN_TRANSLATION` / `_EN_TTS_SCRIPT` / `_EN_REWRITE`；`DEFAULTS` 注册三条 |
| `appcore/video_translate_defaults.py` | 修改 | `VIDEO_SUPPORTED_LANGS` 集合加 `"en"` |
| `web/routes/multi_translate.py` | 修改 | 模块级 `SUPPORTED_LANGS` 元组加 `"en"` |
| `web/templates/multi_translate_list.html` | 修改 | pill 字典 + modal `<option>` 字典 + JS `supported` 数组 |
| `tests/test_languages_registry.py` | **创建** | 守住 registry 把 `en` 视作合法语言 |
| `tests/test_prompt_defaults.py` | **创建** | 守住 `DEFAULTS` 三条 en 条目存在且含 en-US 关键词 |
| `tests/test_video_translate_defaults.py` | 修改 | 已有的 `VIDEO_SUPPORTED_LANGS` 断言扩到含 `en` |
| `tests/test_multi_translate_routes.py` | 修改 | 增 `target_lang="en"` 走 upload-and-start 用例 + 模板含 en label 用例 |
| `tests/test_runtime_multi_translate.py` | 修改 | 增 `target_lang="en"` 跑通 translate 步骤的用例 |

---

## Task 1: 创建 `pipeline/languages/en.py`

**Files:**
- Create: `pipeline/languages/en.py`
- Test (Task 2 创建): `tests/test_languages_registry.py`

- [ ] **Step 1: 创建模块文件**

写入 `pipeline/languages/en.py`：

```python
"""English (en-US) 字幕/TTS 规则。Prompt 见 llm_prompt_configs slot='base_*' lang='en'。"""
from __future__ import annotations

TTS_MODEL_ID = "eleven_multilingual_v2"
TTS_LANGUAGE_CODE = "en"

# 字幕 — Netflix EN 标准
MAX_CHARS_PER_LINE = 42
MAX_CHARS_PER_SECOND = 17
MAX_LINES = 2

# 弱起始词：避免字幕断在 the/a/to 这类附着前置词之前
WEAK_STARTERS = {
    "a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "at",
    "for", "with", "from", "by", "as", "that", "this",
    "is", "are", "was", "were", "be",
    "i", "you", "we", "they", "he", "she", "it",
}
WEAK_STARTER_PHRASES: list[str] = []


def pre_process(text: str) -> str:
    """English 无需前处理。"""
    return text


def post_process_srt(srt_content: str) -> str:
    """English 无需后处理。"""
    return srt_content
```

- [ ] **Step 2: 提交**

```bash
git -C .worktrees/multi-translate-en add pipeline/languages/en.py
git -C .worktrees/multi-translate-en commit -m "$(cat <<'EOF'
feat(multi-translate): add en-US language rules module

Subtitle char/line + WEAK_STARTERS calibrated for English; passthrough pre/post
processing matches the de/fr/... template.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 在 `registry.SUPPORTED_LANGS` 注册 `"en"`（TDD）

**Files:**
- Modify: `pipeline/languages/registry.py:10`
- Create: `tests/test_languages_registry.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_languages_registry.py`：

```python
"""pipeline.languages.registry 守护测试。"""
import pytest

from pipeline.languages.registry import SUPPORTED_LANGS, get_rules


def test_supported_langs_includes_existing_nine_plus_en():
    assert set(SUPPORTED_LANGS) == {"de", "fr", "es", "it", "pt", "ja", "nl", "sv", "fi", "en"}


def test_get_rules_for_en_returns_module_with_required_attrs():
    mod = get_rules("en")
    assert mod.TTS_MODEL_ID == "eleven_multilingual_v2"
    assert mod.TTS_LANGUAGE_CODE == "en"
    assert mod.MAX_CHARS_PER_LINE == 42
    assert "the" in mod.WEAK_STARTERS
    assert mod.post_process_srt("foo\n") == "foo\n"
    assert mod.pre_process("foo") == "foo"


def test_get_rules_unknown_lang_raises():
    with pytest.raises(LookupError):
        get_rules("klingon")
```

- [ ] **Step 2: 运行测试，验证 `test_supported_langs_includes_existing_nine_plus_en` 失败、`test_get_rules_for_en` 失败（LookupError）**

```bash
pytest .worktrees/multi-translate-en/tests/test_languages_registry.py -v
```

预期：两个用例 FAIL（"en" not in SUPPORTED_LANGS / unsupported language: en）。

- [ ] **Step 3: 在 registry 元组追加 `"en"`**

修改 `pipeline/languages/registry.py:10`：

```python
SUPPORTED_LANGS = ("de", "fr", "es", "it", "pt", "ja", "nl", "sv", "fi", "en")
```

- [ ] **Step 4: 重跑测试，验证全部通过**

```bash
pytest .worktrees/multi-translate-en/tests/test_languages_registry.py -v
```

预期：3 passed。

- [ ] **Step 5: 提交**

```bash
git -C .worktrees/multi-translate-en add pipeline/languages/registry.py tests/test_languages_registry.py
git -C .worktrees/multi-translate-en commit -m "$(cat <<'EOF'
feat(multi-translate): register en in language registry

Adds "en" to pipeline.languages.registry SUPPORTED_LANGS so MultiTranslateRunner
can dispatch English jobs through the same path as the other nine languages.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 在 `prompt_defaults.DEFAULTS` 注册三段 en-US prompt（TDD）

**Files:**
- Modify: `pipeline/languages/prompt_defaults.py`
- Create: `tests/test_prompt_defaults.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_prompt_defaults.py`：

```python
"""pipeline.languages.prompt_defaults DEFAULTS 守护测试。"""
from pipeline.languages.prompt_defaults import DEFAULTS


def test_defaults_contains_three_en_entries():
    assert ("base_translation", "en") in DEFAULTS
    assert ("base_tts_script", "en") in DEFAULTS
    assert ("base_rewrite", "en") in DEFAULTS


def test_en_translation_prompt_targets_en_us_market():
    content = DEFAULTS[("base_translation", "en")]["content"]
    assert "US" in content or "American" in content
    # en-US specific vocabulary anchors
    for token in ("sneakers", "apartment", "elevator"):
        assert token in content
    # forbidden patterns
    assert "link in bio" in content.lower() or "no cta" in content.lower()
    # JSON schema hint
    assert "source_segment_indices" in content


def test_en_tts_script_prompt_mentions_subtitle_chunks():
    content = DEFAULTS[("base_tts_script", "en")]["content"]
    assert "subtitle_chunks" in content
    assert "blocks" in content


def test_en_rewrite_prompt_has_word_count_constraint():
    content = DEFAULTS[("base_rewrite", "en")]["content"]
    assert "{target_words}" in content
    assert "{direction}" in content
    assert "source_segment_indices" in content
```

- [ ] **Step 2: 运行测试，验证 4 个用例均失败（`KeyError`）**

```bash
pytest .worktrees/multi-translate-en/tests/test_prompt_defaults.py -v
```

预期：4 FAILED — `("base_translation", "en")` 不在 `DEFAULTS`。

- [ ] **Step 3: 在 `prompt_defaults.py` 葡萄牙语 `_PT_REWRITE` 之后、日语 `_JA_TRANSLATION` 之前插入三段 en prompt**

在 `pipeline/languages/prompt_defaults.py` 中新增（与德/法等同构）：

```python
# ── 英语 base prompts（en-US 默认）──
_EN_TRANSLATION = """You are a US-based short-form commerce content creator. Return valid JSON only,
shaped as {"full_text": "...", "sentences": [{"index": 0, "text": "...", "source_segment_indices": [...]}]}.

You are NOT translating — you are RECREATING the script the way a US creator would
naturally say it on TikTok / Reels / Shorts / Facebook for an American audience.

VOCABULARY (en-US, words Americans actually use & search):
- Beauty: lipstick, foundation, mascara, blush, moisturizer, face mask
- Storage/home: storage box, organizer, basket, container, drawer organizer
- Tech: smartphone, tablet, headphones, charger, gadget
- Clothing: sneakers (NOT trainers), pants (NOT trousers), hoodie, T-shirt, bag/purse
- Apartment / elevator (NOT flat / lift); fall (season, NOT autumn); trash can (NOT bin)
- Spelling: color/favorite/organize (US, never colour/favourite/organise)
- Currency: $ before number ($9.99); imperial measurements (inches, oz, lbs) when natural
Pick ONE term per concept and stay consistent. NEVER literal-translate product category names.

TONE:
- Casual, conversational, like a friend recommending something they actually use.
- Default to "you" (second person); contractions are natural ("you'll", "it's", "don't").
- NO hype phrases ("you NEED this", "literally amazing", "game-changer", "obsessed",
  "last chance", "act fast"). US TikTok audiences are increasingly burned out on
  hard-sell language.
- NO "link in bio" / "swipe up" / "shop now" CTA — a universal CTA clip will be
  appended later.
- Emphasize practicality, real use cases, honest value.

HOOK PATTERNS (first sentence — pick whatever fits the product):
- "You know what's actually changed my..."
- "I tried this and..."
- "This is the [thing] I never knew I needed."
- "Here's what nobody tells you about..."
Avoid shock openers like "OMG you HAVE to see this" — feels dated and pushy.

FORMATTING:
- Prefer 6–12 words per sentence; avoid run-on sentences.
- ASCII punctuation only. No em-dashes, no en-dashes, no curly quotes.
- Numbers in US convention (2.5 not 2,5; 1,000 not 1.000).
- Every sentence must preserve source meaning and include source_segment_indices.
- No CTA at the end."""


_EN_TTS_SCRIPT = """Prepare English text for ElevenLabs TTS and on-screen subtitles. Return valid JSON only:
{"full_text": "...", "blocks": [...], "subtitle_chunks": [...]} with the same schema as other language variants.

Blocks: natural US speaking rhythm — energetic on the hook, measured & confident on
benefit blocks. Use contractions where a US creator would say them aloud.

Subtitle chunks: 4–8 words each, semantically complete, no trailing punctuation.
Do NOT start a chunk with a weak attaching word (a / an / the / to / of / and / or)
unless unavoidable. No em-dashes / en-dashes / curly quotes."""


_EN_REWRITE = """You are a US-based content creator REWRITING an existing English localization.
Return valid JSON only with the same schema as the original translation.

HARD WORD COUNT CONSTRAINT — NON-NEGOTIABLE:
Target: EXACTLY {target_words} whitespace-separated words in full_text.
Allowed range: [{target_words}-5, {target_words}+5]. HARD CAP.
Note: contractions like "you'll" / "don't" count as ONE word.
SELF-CHECK: count tokens; if outside the window, rewrite before returning.
FAILURES: asked for 80 → returning 100+ is FAILURE. Asked for 70 → returning 55 is FAILURE.

DIRECTION: {direction} (shrink = remove modifiers/repetitions; expand = add natural
elaborations like a concrete example, never invent new facts).

STRUCTURAL: keep the same number of sentences when possible; preserve every
source_segment_indices mapping.

STYLE: casual conversational US English, default "you", contractions allowed,
US spelling (color/favorite), no hype, no CTA, no em/en-dashes, ASCII punctuation only."""
```

并在 `DEFAULTS` 字典末尾（`fi` 三条之后）追加：

```python
    # 英语
    ("base_translation", "en"): {
        "provider": _DEFAULT_PROVIDER, "model": _DEFAULT_MODEL,
        "content": _EN_TRANSLATION,
    },
    ("base_tts_script", "en"): {
        "provider": _DEFAULT_PROVIDER, "model": _DEFAULT_MODEL,
        "content": _EN_TTS_SCRIPT,
    },
    ("base_rewrite", "en"): {
        "provider": _DEFAULT_PROVIDER, "model": _DEFAULT_MODEL,
        "content": _EN_REWRITE,
    },
```

- [ ] **Step 4: 重跑测试，验证全部通过**

```bash
pytest .worktrees/multi-translate-en/tests/test_prompt_defaults.py -v
```

预期：4 passed。

- [ ] **Step 5: 提交**

```bash
git -C .worktrees/multi-translate-en add pipeline/languages/prompt_defaults.py tests/test_prompt_defaults.py
git -C .worktrees/multi-translate-en commit -m "$(cat <<'EOF'
feat(multi-translate): add en-US prompts to prompt_defaults

Three new entries (base_translation / base_tts_script / base_rewrite) for lang='en',
written for US TikTok / Reels short-form commerce: en-US vocabulary, second-person
casual tone, anti-hype, no in-app CTA, ASCII-only punctuation, hard word-count
constraint on rewrite. Auto-seeded into llm_prompt_configs by resolve_prompt_config.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 扩 `VIDEO_SUPPORTED_LANGS` 含 `"en"`（TDD）

**Files:**
- Modify: `appcore/video_translate_defaults.py:53`
- Modify: `tests/test_video_translate_defaults.py:52-54`

- [ ] **Step 1: 修改既有测试，使其期望 en 在集合内**

替换 `tests/test_video_translate_defaults.py` 第 52–54 行：

```python
def test_video_supported_langs_match_multi_translate_languages():
    """视频翻译支持集应覆盖当前多语种视频流水线（含 en-US）。"""
    assert VIDEO_SUPPORTED_LANGS == {"de", "fr", "es", "it", "pt", "ja", "nl", "sv", "fi", "en"}
```

- [ ] **Step 2: 跑测试，验证此用例失败**

```bash
pytest .worktrees/multi-translate-en/tests/test_video_translate_defaults.py::test_video_supported_langs_match_multi_translate_languages -v
```

预期：FAIL — 当前集合不含 `"en"`。

- [ ] **Step 3: 修改常量**

`appcore/video_translate_defaults.py:53` 改为：

```python
VIDEO_SUPPORTED_LANGS = {"de", "fr", "es", "it", "pt", "ja", "nl", "sv", "fi", "en"}
```

- [ ] **Step 4: 重跑该测试文件全部用例**

```bash
pytest .worktrees/multi-translate-en/tests/test_video_translate_defaults.py -v
```

预期：所有用例 PASS（reload 那条要确认依旧通过 14）。

- [ ] **Step 5: 提交**

```bash
git -C .worktrees/multi-translate-en add appcore/video_translate_defaults.py tests/test_video_translate_defaults.py
git -C .worktrees/multi-translate-en commit -m "$(cat <<'EOF'
feat(multi-translate): include en in VIDEO_SUPPORTED_LANGS

Aligns the video-translate language whitelist with pipeline.languages.registry,
so resolve_default_voice and the bulk-translate profile resolver accept English.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: 在 `multi_translate.py` 路由把 `"en"` 加入白名单（TDD）

**Files:**
- Modify: `web/routes/multi_translate.py:33`
- Modify: `tests/test_multi_translate_routes.py`（新增一条用例）

- [ ] **Step 1: 写失败测试**

在 `tests/test_multi_translate_routes.py` 文件末尾追加：

```python
def test_multi_translate_start_accepts_target_lang_en(tmp_path, authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.multi_translate.OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr("web.routes.multi_translate.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("web.routes.multi_translate.db_query_one", lambda sql, args: None)
    monkeypatch.setattr("web.routes.multi_translate.db_execute", lambda sql, args: None)
    started = {}
    monkeypatch.setattr(
        "web.routes.multi_translate.multi_pipeline_runner.start",
        lambda task_id, user_id=None: started.update({"task_id": task_id, "user_id": user_id}),
    )

    response = authed_client_no_db.post(
        "/api/multi-translate/start",
        data={
            "target_lang": "en",
            "video": (io.BytesIO(b"english-video"), "demo-en.mp4"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    payload = response.get_json()
    from web import store

    task = store.get(payload["task_id"])
    assert task["type"] == "multi_translate"
    assert task["target_lang"] == "en"
    assert started["task_id"] == payload["task_id"]
```

- [ ] **Step 2: 跑测试，验证失败（400）**

```bash
pytest .worktrees/multi-translate-en/tests/test_multi_translate_routes.py::test_multi_translate_start_accepts_target_lang_en -v
```

预期：FAIL — 路由返回 400 `"target_lang must be one of ['de', 'fr', ...]"`。

- [ ] **Step 3: 修改路由白名单**

`web/routes/multi_translate.py:33` 改为：

```python
SUPPORTED_LANGS = ("de", "fr", "es", "it", "pt", "ja", "nl", "sv", "fi", "en")
```

- [ ] **Step 4: 重跑该用例 + 既有用例**

```bash
pytest .worktrees/multi-translate-en/tests/test_multi_translate_routes.py -v
```

预期：所有用例 PASS。

- [ ] **Step 5: 提交**

```bash
git -C .worktrees/multi-translate-en add web/routes/multi_translate.py tests/test_multi_translate_routes.py
git -C .worktrees/multi-translate-en commit -m "$(cat <<'EOF'
feat(multi-translate): accept target_lang=en in routes

Module-level SUPPORTED_LANGS in the multi_translate blueprint now includes "en";
upload-and-start, list filter, set-default-voice all flow through.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: 模板加 EN 选项 + 标签 + JS 默认（TDD）

**Files:**
- Modify: `web/templates/multi_translate_list.html`（pill 字典 + modal `<option>` 字典 + JS `_getInitialTargetLang.supported`）
- Modify: `tests/test_multi_translate_routes.py`（增渲染断言）

模板有 **两处独立** 的标签字典需要补 `en`：
- 第 191 行 lang pill 字典：`{'de':'🇩🇪 德语', ...}`，加 `'en':'🇺🇸 英语'`
- 第 304 行 modal `<option>` 字典：`{'de':'DE 德语', ...}`，加 `'en':'EN 英语'`（沿用兄弟项无 emoji 的纯文本风格，符合 CLAUDE.md "禁止 emoji 出现在表单 label" 原则）

JS 在 `_getInitialTargetLang()` 里独立维护 supported 数组（见 369 行），同步加 `'en'`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_multi_translate_routes.py` 文件末尾追加：

```python
def test_multi_translate_list_template_exposes_en_label_in_pills_and_modal_select():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "multi_translate_list.html").read_text(encoding="utf-8")

    # Pill 字典含 EN 旗帜版
    assert "'en':'🇺🇸 英语'" in template
    # Modal <option> 字典含纯文本 EN（无 emoji，匹配兄弟项风格）
    assert "'en':'EN 英语'" in template
    # JS supported 数组含 'en'
    assert "['de', 'fr', 'es', 'it', 'pt', 'ja', 'nl', 'sv', 'fi', 'en']" in template


def test_multi_translate_list_renders_en_pill_when_supported(authed_client_no_db):
    with patch("web.routes.multi_translate.db_query", return_value=[]), \
         patch("appcore.settings.get_retention_hours", return_value=72), \
         patch("appcore.task_recovery.recover_all_interrupted_tasks"):
        resp = authed_client_no_db.get("/multi-translate")

    assert resp.status_code == 200
    assert "🇺🇸 英语".encode("utf-8") in resp.data
    assert "EN 英语".encode("utf-8") in resp.data
```

- [ ] **Step 2: 跑测试，验证两个用例均失败**

```bash
pytest .worktrees/multi-translate-en/tests/test_multi_translate_routes.py::test_multi_translate_list_template_exposes_en_label_in_pills_and_modal_select .worktrees/multi-translate-en/tests/test_multi_translate_routes.py::test_multi_translate_list_renders_en_pill_when_supported -v
```

预期：两个 FAIL（模板还没有 en label）。

- [ ] **Step 3: 修改模板 — pill 字典（约第 191 行）**

把：

```jinja
    {% set label = {'de':'🇩🇪 德语','fr':'🇫🇷 法语','es':'🇪🇸 西语','it':'🇮🇹 意语','ja':'🇯🇵 日语','pt':'🇵🇹 葡语','nl':'🇳🇱 荷兰语','sv':'🇸🇪 瑞典语','fi':'🇫🇮 芬兰语'}[lang] %}
```

改为：

```jinja
    {% set label = {'de':'🇩🇪 德语','fr':'🇫🇷 法语','es':'🇪🇸 西语','it':'🇮🇹 意语','ja':'🇯🇵 日语','pt':'🇵🇹 葡语','nl':'🇳🇱 荷兰语','sv':'🇸🇪 瑞典语','fi':'🇫🇮 芬兰语','en':'🇺🇸 英语'}[lang] %}
```

- [ ] **Step 4: 修改模板 — modal `<option>` 字典（约第 304 行）**

把：

```jinja
            {% set label = {'de':'DE 德语','fr':'FR 法语','es':'ES 西语','it':'IT 意语','ja':'JP 日语','pt':'PT 葡语','nl':'NL 荷兰语','sv':'SE 瑞典语','fi':'FI 芬兰语'}[lang] %}
```

改为：

```jinja
            {% set label = {'de':'DE 德语','fr':'FR 法语','es':'ES 西语','it':'IT 意语','ja':'JP 日语','pt':'PT 葡语','nl':'NL 荷兰语','sv':'SE 瑞典语','fi':'FI 芬兰语','en':'EN 英语'}[lang] %}
```

- [ ] **Step 5: 修改 JS `_getInitialTargetLang.supported` 数组（约第 368 行）**

把：

```javascript
  var supported = ['de', 'fr', 'es', 'it', 'pt', 'ja', 'nl', 'sv', 'fi'];
```

改为：

```javascript
  var supported = ['de', 'fr', 'es', 'it', 'pt', 'ja', 'nl', 'sv', 'fi', 'en'];
```

- [ ] **Step 6: 重跑测试**

```bash
pytest .worktrees/multi-translate-en/tests/test_multi_translate_routes.py -v
```

预期：所有用例 PASS（包括既有的 `test_list_filters_by_lang` 等）。

- [ ] **Step 7: 提交**

```bash
git -C .worktrees/multi-translate-en add web/templates/multi_translate_list.html tests/test_multi_translate_routes.py
git -C .worktrees/multi-translate-en commit -m "$(cat <<'EOF'
feat(multi-translate): expose en option in list page

Adds 🇺🇸 英语 pill, EN modal option (no flag — matches sibling form-label style),
and 'en' in the JS default-language fallback array.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: `MultiTranslateRunner` 跑通 `target_lang="en"`（集成单测）

**Files:**
- Modify: `tests/test_runtime_multi_translate.py`（新增一条用例）

- [ ] **Step 1: 在文件末尾追加一条测试**

```python
def test_step_translate_resolves_en_prompt_and_uses_eleven_multilingual():
    """target_lang='en' 应当走 ('base_translation','en') resolver 并使用 eleven_multilingual_v2 TTS 模型。"""
    runner = _make_runner()
    task = {
        "task_dir": "/tmp/x",
        "target_lang": "en",
        "source_language": "zh",
        "script_segments": [{"index": 0, "text": "你好"}],
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
         patch("appcore.runtime.ai_billing.log_request"), \
         patch("appcore.runtime_multi._build_review_segments", return_value=[]), \
         patch("appcore.runtime._translate_billing_model", return_value="gpt"), \
         patch("appcore.runtime_multi._resolve_translate_provider", return_value="openrouter"), \
         patch("appcore.runtime_multi.get_model_display_name", return_value="gpt"), \
         patch("pipeline.extract.get_video_duration", return_value=1.0), \
         patch("appcore.runtime_multi.build_asr_artifact", return_value={}), \
         patch("appcore.runtime_multi.build_translate_artifact", return_value={}):
        m_resolve.side_effect = [
            {"provider": "openrouter", "model": "gpt", "content": "BASE_EN"},
            {"provider": "openrouter", "model": "gpt", "content": "ECOM_PLUGIN"},
        ]
        m_gen.return_value = {"full_text": "hi", "sentences": [], "_usage": {}}
        runner._step_translate("t1")

    assert m_resolve.call_args_list[0].args == ("base_translation", "en")
    kwargs = m_gen.call_args.kwargs
    assert "BASE_EN" in kwargs["custom_system_prompt"]


def test_runner_lang_rules_for_en_use_multilingual_tts_and_en_code():
    """_get_tts_model_id / _get_tts_language_code 对英语任务返回 multilingual_v2 + 'en'。"""
    runner = _make_runner()
    task = {"target_lang": "en"}
    assert runner._get_tts_model_id(task) == "eleven_multilingual_v2"
    assert runner._get_tts_language_code(task) == "en"
```

- [ ] **Step 2: 跑该文件，验证两个新用例 PASS（registry / prompt_defaults / VIDEO_SUPPORTED_LANGS 已就位）**

```bash
pytest .worktrees/multi-translate-en/tests/test_runtime_multi_translate.py -v
```

预期：所有用例 PASS（包括既有 5 条 + 新 2 条）。

如果失败：检查 Task 1–4 是否已落地，特别是 `pipeline/languages/registry.py` 含 `"en"`。

- [ ] **Step 3: 提交**

```bash
git -C .worktrees/multi-translate-en add tests/test_runtime_multi_translate.py
git -C .worktrees/multi-translate-en commit -m "$(cat <<'EOF'
test(multi-translate): exercise en path through MultiTranslateRunner

Two unit tests pin the en-US wiring: the translate step must call
resolve_prompt_config('base_translation','en'), and the runner reports
eleven_multilingual_v2 + lang_code='en' for English tasks.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: 跑全套相关测试，确认无回归

- [ ] **Step 1: 跑七个直接相关的测试文件**

```bash
pytest .worktrees/multi-translate-en/tests/test_languages_registry.py \
       .worktrees/multi-translate-en/tests/test_prompt_defaults.py \
       .worktrees/multi-translate-en/tests/test_video_translate_defaults.py \
       .worktrees/multi-translate-en/tests/test_multi_translate_routes.py \
       .worktrees/multi-translate-en/tests/test_runtime_multi_translate.py \
       -v
```

预期：全部 PASS。

- [ ] **Step 2: 跑 bulk_translate / medias multi-lang 等相邻测试，确认 VIDEO_SUPPORTED_LANGS 扩集合不破坏其他模块**

```bash
pytest .worktrees/multi-translate-en/tests/test_appcore_medias_multi_lang.py \
       .worktrees/multi-translate-en/tests/test_bulk_translate_runtime.py \
       .worktrees/multi-translate-en/tests/test_bulk_translate_routes.py \
       .worktrees/multi-translate-en/tests/test_bulk_translate_plan.py \
       .worktrees/multi-translate-en/tests/test_runtime_multi_voice_match.py \
       -v
```

预期：全部 PASS。如有 FAIL：

- 若 `bulk_translate_*` 假设白名单不含 en 而 hard-fail，按设计稿 §2 不扩 bulk_translate 白名单——回去 [appcore/bulk_translate_runtime.py](appcore/bulk_translate_runtime.py) 看下 bulk_translate 的语言校验是不是单独维护的（应该是），如果它独立校验则不会受 VIDEO_SUPPORTED_LANGS 影响；如果它复用 VIDEO_SUPPORTED_LANGS，需改为引用一份固定的"非 en 9 语集合"或在 bulk_translate 一侧 deny "en"。
- 这一步只是查验性质，不预先动 bulk_translate 代码。

---

## Task 9: 手动验收清单（不可自动化部分）

需要连测试环境 MySQL 才能跑，**实施者照清单本地验证或交给 QA**：

- [ ] 启动 `python main.py`（连测试环境 DB）后访问 `/multi-translate`
- [ ] 看到 🇺🇸 英语 pill；点击后 URL 变 `/multi-translate?lang=en`
- [ ] 点"+ 新建项目"，目标语言下拉里看到 "EN 英语"，且打开弹窗时若当前 URL 已是 `?lang=en` 则下拉默认选中 EN
- [ ] 上传一个 ≥ 10 秒中文/英文测试视频，提交后跳转任务详情页，`state_json.target_lang == "en"`
- [ ] 任务跑通 extract → asr → voice_match → alignment → translate → tts → subtitle → compose
- [ ] 字幕预览英文符合 en-US 风格、无 em-dash / curly quote、不以 the/a/to 起头
- [ ] 管理员进 prompt 配置页（具体 URL 见 [appcore/llm_prompt_configs.py](appcore/llm_prompt_configs.py) 的管理员蓝图），看到 `(base_translation, en)`、`(base_tts_script, en)`、`(base_rewrite, en)` 三条；改一处 → 重跑任务该 prompt 生效；点"恢复默认"回到 `_EN_*` 内容
- [ ] 音色选择：voice_match 候选展示英语音色（前提：`elevenlabs_voices` 表有 `language='en'` 行）；候选空时 fallback 提示用户从音色库选

---

## Task 10: 准备合并 master

- [ ] **Step 1: rebase 一次拉最新 master**

```bash
git -C .worktrees/multi-translate-en fetch origin master 2>/dev/null || true
git -C .worktrees/multi-translate-en rebase master
```

- [ ] **Step 2: 跑全量 multi_translate 相关测试最后一道关**

同 Task 8 Step 1 的命令。

- [ ] **Step 3: 调用 superpowers:finishing-a-development-branch skill 让用户决定合并方式**

由用户选择 cherry-pick / merge / PR。**不要自行 push 或 merge。**

---

## 自查清单

- [x] **Spec 覆盖**：spec §2 的 8 个改动文件 + 2 个测试文件全部对应到 Task 1–7
- [x] **Placeholder 扫描**：无 TBD / TODO / "implement later"；所有代码块含完整可执行内容
- [x] **类型一致**：`pipeline/languages/en.py` 暴露的属性（`TTS_MODEL_ID` / `TTS_LANGUAGE_CODE` / `MAX_CHARS_PER_LINE` / `MAX_CHARS_PER_SECOND` / `MAX_LINES` / `WEAK_STARTERS` / `WEAK_STARTER_PHRASES` / `pre_process` / `post_process_srt`）与 `pipeline/languages/de.py` 完全同构；`DEFAULTS` 三条 key 与 `resolve_prompt_config` 调用的 `(slot, lang)` 形式一致；测试断言里的常量值与代码里的字面值一一对应
- [x] **Spec 无新增需求**：实施期间发现的"模板有两个独立 label 字典"已在 Task 6 拆解，不需要回去改 spec
