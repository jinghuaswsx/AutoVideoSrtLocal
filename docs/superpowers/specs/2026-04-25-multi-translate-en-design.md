# 多语种视频翻译 — 接入英语（en-US）目标语言

- **日期**：2026-04-25
- **范围**：仅扩展 `multi_translate` 路径，把英语作为新增目标语言。**不动**主线英语流程（`appcore/runtime.py` + `pipeline/localization.py`）。
- **决策**：方案 A（仅扩列表）+ en-US 单一市场定位。
- **关联**：与 `docs/superpowers/specs/2026-04-18-multi-translate-design.md`、`docs/superpowers/plans/2026-04-18-multi-translate-batch1/2/3.md` 同构。

## 1. 背景与动机

仓库当前并存两条英语翻译路径：

1. **主线**（中文 → 英语）：`appcore/runtime.py` + `pipeline/localization.py`，prompt 写死在 Python 模块里，是项目最早的实现。
2. **多语种统一流程**：`appcore/runtime_multi.py` 单一 Runner 串起 de/fr/es/it/pt/ja/nl/sv/fi 九种语言；prompt 走 DB 表 `llm_prompt_configs`，可在 `/settings?tab=bindings` 与提示词页改；源语言可 zh / en。

本次需求：把英语也接入「多语种统一流程」，用户在 `/multi-translate` 页面新建任务时可选 🇺🇸 英语作为目标语言。**主线英语流程保留不动**，两条路径并存，前端用户可自由选择。后续如稳定运行可再评估归并。

## 2. 范围（YAGNI 边界）

### 做

| 文件 | 改动 |
|------|------|
| `pipeline/languages/en.py` | **新建** — 字幕规则模块，与 `pipeline/languages/de.py` 同构 |
| `pipeline/languages/registry.py` | `SUPPORTED_LANGS` 元组追加 `"en"` |
| `pipeline/languages/prompt_defaults.py` | 新增 `_EN_TRANSLATION` / `_EN_TTS_SCRIPT` / `_EN_REWRITE` 三段 prompt，`DEFAULTS` 字典注册三条 `("base_*", "en")` |
| `web/routes/multi_translate.py` | 模块级常量 `SUPPORTED_LANGS` 加 `"en"` |
| `web/templates/multi_translate_list.html` | Jinja `label` 字典加 `'en':'🇺🇸 英语'`；前端 `_getTargetLang()` 的 `supported` 数组同步加 `'en'` |
| `appcore/video_translate_defaults.py` | `VIDEO_SUPPORTED_LANGS` 集合追加 `"en"` |
| `tests/test_runtime_multi_translate.py` | 增量加 `lang="en"` 走通用例（mock LLM/TTS） |
| `tests/test_multi_translate_routes.py` | 增量加 `lang="en"` 走 upload+start 通用例 |

### 不做

- 不动 `appcore/runtime.py`、`pipeline/localization.py`、`web/routes/task.py` 等主线英语相关路径
- 不动数据库 schema（`projects.target_lang` 走 `state_json`，无 ENUM 约束）
- 不引入多目标市场参数（en-GB / en-AU 留待将来）
- 不写英语专属默认音色；走"用户曾选过 → 音色库第一个 en"的通用 fallback
- 不扩 `appcore/bulk_translate_runtime.py` 的语言白名单，本次不接英语批量
- 不写 DB 迁移脚本：新 prompt 由 `appcore/llm_prompt_configs.resolve_prompt_config()` 首次访问时按现有逻辑自动 seed

## 3. 详细设计

### 3.1 `pipeline/languages/en.py`（en-US 字幕/TTS 规则）

```python
"""English (en-US) 字幕/TTS 规则。Prompt 见 llm_prompt_configs slot='base_*' lang='en'。"""
from __future__ import annotations

TTS_MODEL_ID = "eleven_multilingual_v2"
TTS_LANGUAGE_CODE = "en"

# 字幕 — 英文标准
MAX_CHARS_PER_LINE = 42        # Netflix EN 标准
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

**设计决策**：
- `TTS_MODEL_ID` 选 `eleven_multilingual_v2` 与其他 9 种语言一致，**不**复用主线英语单语模型。理由：保持 `runtime_multi` 单一模型矩阵；英语单语模型效果更精，但本次目标是统一接入而非追求最佳音质，可在后续按用户反馈替换。
- `MAX_CHARS_PER_LINE = 42` 是 Netflix EN 标准；其他语言（de=38）短是因德语复合词更长。
- WEAK_STARTERS 包含常见英语功能词与代词；不进入 `WEAK_STARTER_PHRASES`，因为英语没有像法语 élision 那样的强约束短语。

### 3.2 `pipeline/languages/registry.py`

```python
SUPPORTED_LANGS = ("de", "fr", "es", "it", "pt", "ja", "nl", "sv", "fi", "en")
```

在元组末尾追加 `"en"`，保留其他 9 项与现有顺序。

### 3.3 `pipeline/languages/prompt_defaults.py` — 三段 en-US prompt

#### `_EN_TRANSLATION`

```
You are a US-based short-form commerce content creator. Return valid JSON only,
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
- No CTA at the end.
```

#### `_EN_TTS_SCRIPT`

```
Prepare English text for ElevenLabs TTS and on-screen subtitles. Return valid JSON only:
{"full_text": "...", "blocks": [...], "subtitle_chunks": [...]} with the same schema as other language variants.

Blocks: natural US speaking rhythm — energetic on the hook, measured & confident on
benefit blocks. Use contractions where a US creator would say them aloud.

Subtitle chunks: 4–8 words each, semantically complete, no trailing punctuation.
Do NOT start a chunk with a weak attaching word (a / an / the / to / of / and / or)
unless unavoidable. No em-dashes / en-dashes / curly quotes.
```

#### `_EN_REWRITE`

```
You are a US-based content creator REWRITING an existing English localization.
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
US spelling (color/favorite), no hype, no CTA, no em/en-dashes, ASCII punctuation only.
```

#### `DEFAULTS` 字典追加

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

### 3.4 `web/routes/multi_translate.py`

模块级常量 `SUPPORTED_LANGS` 改为：

```python
SUPPORTED_LANGS = ("de", "fr", "es", "it", "pt", "ja", "nl", "sv", "fi", "en")
```

`upload_and_start()` 校验 `target_lang in SUPPORTED_LANGS` 自动覆盖；`set_user_default_voice_route()` 同。

### 3.5 `web/templates/multi_translate_list.html`

#### Jinja 标签字典

```jinja
{% set label = {'de':'🇩🇪 德语','fr':'🇫🇷 法语','es':'🇪🇸 西语','it':'🇮🇹 意语','ja':'🇯🇵 日语','pt':'🇵🇹 葡语','nl':'🇳🇱 荷兰语','sv':'🇸🇪 瑞典语','fi':'🇫🇮 芬兰语','en':'🇺🇸 英语'}[lang] %}
```

#### 前端 `_getTargetLang()`

```javascript
var supported = ['de', 'fr', 'es', 'it', 'pt', 'ja', 'nl', 'sv', 'fi', 'en'];
```

新建项目弹窗的"上传后将自动识别视频源语言（中文/英文）"提示**保持不变**——`source_language` 自动检测逻辑对 `target_lang=en` 同样适用，因为 `runtime_multi.py` 的源语言判别只用作 LLM 标签而不影响流程（见 [appcore/runtime_multi.py:129-130](appcore/runtime_multi.py#L129-L130)）。

### 3.6 `appcore/video_translate_defaults.py`

```python
VIDEO_SUPPORTED_LANGS = {"de", "fr", "es", "it", "pt", "ja", "nl", "sv", "fi", "en"}
```

`TTS_VOICE_DEFAULTS` **不**追加 `"en"`：让 `resolve_default_voice("en", user_id=...)` 走"用户级 → 音色库第一个英语音色"的通用 fallback。前提：`elevenlabs_voices` 表里有 `language="en"` 的音色记录；如果没有，runner 在 `voice_match` 步骤会拿到空候选并 fallback `None`，跟 nl/sv/fi 的处理一致。

### 3.7 数据库 seed

`appcore/llm_prompt_configs.py` 的 `resolve_prompt_config(slot, lang)` 在 DB 中找不到对应行时会自动从 `prompt_defaults.DEFAULTS` 兜底并 seed 一行。新加的三条 `("base_*", "en")` 在第一次创建英语任务跑到 translate / tts / rewrite 步骤时分别 seed 进 DB。**无需迁移脚本**。

管理员后台 `/llm-prompts`（或对应 admin 页面）会自动出现新的"英语"分组与"恢复默认"按钮，与现有 9 种语言体验一致。

## 4. 数据流

英语任务的运行轨迹（与德/法等完全同构）：

```
POST /api/multi-translate/start  body.target_lang=en
  → store.create + store.update(target_lang="en", type="multi_translate")
  → multi_pipeline_runner.start
    → MultiTranslateRunner._step_extract / asr / voice_match / alignment
    → _step_translate
        → resolve_prompt_config("base_translation", "en")  # 命中 DB 或 seed
        → generate_localized_translation(..., custom_system_prompt=...)
    → _step_tts
        → _PromptLocalizationAdapter("en").build_tts_script_messages
            → resolve_prompt_config("base_tts_script", "en")
        → ElevenLabs TTS, model=eleven_multilingual_v2, lang_code=en
    → _step_subtitle
        → align_subtitle_chunks_to_asr  +  pipeline.languages.en.post_process_srt
        → build_srt_from_chunks(weak_boundary_words=en.WEAK_STARTERS)
    → _step_compose / export
```

## 5. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| 与主线英语流程结果差异引发用户困惑 | 中：两条路径不同 prompt 不同模型，输出风格不同 | 不解决——这是方案 A 的有意取舍。文档说明两条路径独立。 |
| 音色库内 `language="en"` 音色不足 | 中：voice_match 候选稀疏 | 由 `runtime_multi._step_voice_match` 现有 fallback 逻辑承接（候选空时回 default_voice → None；UI 可提示） |
| `eleven_multilingual_v2` 处理英语效果不如英语单语模型 | 低：质量略降但可用 | 后续若有反馈，单独切英语模型，改 `pipeline/languages/en.py:TTS_MODEL_ID` 即可 |
| 用户期待"中文→英语"主线，新增 en 选项后误用 multi_translate 走英语 | 低：两入口并存，前端文案保留主线英语入口位置不变 | 不动主线 UI；本次只在 `/multi-translate` 列表页新增 pill |
| `_ensure_source_transcript_is_actionable` 对英语源视频判定异常 | 低：当前阈值按词数 `0.45 * duration` 判定，英语词密度可能略低 | 不预防：现有逻辑已用同阈值跑英语源的多语种任务（zh/en 二选一无关），既然现状没问题，本次不调 |

## 6. 测试计划

### 6.1 单元/路由测试

- `tests/test_runtime_multi_translate.py`：复用现有德语用例 fixture，把 `target_lang` 参数化加 `"en"`，断言：
  - `MultiTranslateRunner._resolve_target_lang` 返回 `"en"`
  - `_get_lang_rules("en")` 不抛 `LookupError`
  - `_build_system_prompt("en")` 返回非空字符串、含 `_EN_TRANSLATION` 前缀（mock `resolve_prompt_config`）
- `tests/test_multi_translate_routes.py`：新增 `test_upload_and_start_lang_en` —— POST 上传 + `target_lang="en"`，期望 201 + state_json 含 `target_lang="en"`。
- `tests/test_video_translate_defaults.py`：扩 `VIDEO_SUPPORTED_LANGS` 断言。

### 6.2 手测清单（落地后）

- [ ] `/multi-translate` 列表页能看到 🇺🇸 英语 pill；点击过滤生效
- [ ] 新建项目弹窗在 `/multi-translate?lang=en` 上下文下，提交后任务 `target_lang=en`
- [ ] 任务跑通 extract → asr → voice_match → alignment → translate → tts → subtitle → compose
- [ ] 字幕预览英文断行符合 WEAK_STARTERS 规则（不以 the/a/to 起头）
- [ ] 管理员 prompt 页面出现"英语 / base_translation / base_tts_script / base_rewrite"三条；改后下次任务生效
- [ ] 音色库英语音色能在 voice_match 候选页被选中

## 7. 落地顺序

按依赖逐步：

1. `pipeline/languages/en.py` 新建
2. `pipeline/languages/registry.py` 加 `"en"`
3. `pipeline/languages/prompt_defaults.py` 三段 prompt + DEFAULTS 注册
4. `appcore/video_translate_defaults.py` `VIDEO_SUPPORTED_LANGS` 扩
5. `web/routes/multi_translate.py` `SUPPORTED_LANGS` 扩
6. `web/templates/multi_translate_list.html` 模板 + JS 同步
7. 测试增量
8. 手测一遍 → 合并 master → 部署

## 8. 验收标准

- 用户在 `/multi-translate` 选择 🇺🇸 英语后能创建任务并完整跑通
- 字幕、TTS、合成产物中字幕断行 / 标点 / 拼写符合 en-US 习惯
- 管理员能在 prompt 页编辑英语三段 prompt，"恢复默认"按钮回到本设计稿值
- 主线英语流程（`/task` 入口）行为完全不变
- 现有 9 种语言任务行为完全不变

## 9. 后续可能的演进（不在本次范围）

- 评估 ElevenLabs 英语单语模型替换
- 增加 en-GB / en-AU 等地区变体
- 把主线英语流程归并到 multi_translate 框架（方案 B）
- 给 `bulk_translate` 增加英语支持
