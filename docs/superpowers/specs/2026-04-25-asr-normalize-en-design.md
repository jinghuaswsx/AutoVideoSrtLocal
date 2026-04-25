# 多语种视频翻译 — ASR 后置 en-US 标准化（asr_normalize）

- **日期**：2026-04-25
- **范围**：仅扩展 `multi_translate` 路径，在 ASR 之后插入新 step `asr_normalize`，把任意源语言的 ASR 文本统一标准化为 en-US。**不动** `bulk_translate`、`runtime.py`（v1 主线英语流程）、`runtime_v2.py`（音画同步）三条线。
- **关联**：与 `docs/superpowers/specs/2026-04-25-multi-translate-en-design.md`（接入英语**目标**语言）正交但互补；本 spec 解决英语**源**侧的统一标准化。

## 1. 背景与动机

`/multi-translate` 当前只能处理**中文/英文**素材：

- `web/routes/multi_translate.py:400-401` 把 `source_language` 硬性限定为 `"zh"` / `"en"`。
- 上传时跑一次 zh/en 二选一的快速判别，结果存到 `task["source_language"]` 仅作 LLM prompt 标签。

**结果**：用户买到西班牙语、葡萄牙语、法语等素材进 `/multi-translate` 流水线，ASR 转写出非中英文文本后，pipeline 仍把它当 zh 或 en 强行往下走 —— prompt 告诉模型"原文是中文/英文"，与真实情况脱节，下游本地化（如西语 → 德语）频繁收敛失败。已知卡死案例：[/multi-translate/b3fa903d-b299-434f-965a-6b4ba39512ff](http://172.30.254.14/multi-translate/b3fa903d-b299-434f-965a-6b4ba39512ff)（西班牙语素材 → 德语本地化失败）。

**本次目标**：在 ASR 后插入 `asr_normalize` step，先用 Gemini 检测原文语言，再用 Claude Sonnet 把任意源语言的 ASR 文本统一标准化为高质量 en-US，下游 alignment / translate / tts / subtitle 全部从"英文源"出发，prompt 库收敛到单一起点。中文素材保留现有 `source_language=zh` 直跑路径不动。

## 2. 范围（YAGNI 边界）

### 做

| 文件 | 改动 |
|------|------|
| `pipeline/asr_normalize.py` | **新建** — 封装 detect_language / translate_to_en；提供 `run_asr_normalize(task_id, user_id) -> ArtifactDict` |
| `appcore/runtime_multi.py` | 新增 `_step_asr_normalize` 方法，串到状态机；alignment 入口的 utterances 读取处加 `or` fallback |
| `appcore/llm_use_cases.py` | 新增 4 条 use_case（`asr_normalize.detect_language` + 三条 translate） |
| `pipeline/languages/prompt_defaults.py` | 新增 4 条 default prompt（detect / translate_zh_en / translate_es_en / translate_generic_en），key 第二项 lang 用空字符串 `""` 占位 |
| `web/routes/multi_translate.py` | RESUMABLE_STEPS 加 `"asr_normalize"`；删除上传时 zh/en 自动判别逻辑；任务创建时不再写入 `source_language` |
| `web/templates/multi_translate_detail.html` | 进度条加一格"原文标准化"；详情区新增 artifact 展示卡片 |
| `web/templates/multi_translate_list.html` | 上传弹窗的"自动识别中文/英文"提示文案改为"自动识别原视频语言并标准化" |
| `tests/test_asr_normalize.py` | **新建** — 单元测试 |
| `tests/test_runtime_multi_asr_normalize.py` | **新建** — runner 集成测试 |
| `tests/test_asr_normalize_use_cases.py` | **新建** — use_case 注册守护 |
| `tests/test_asr_normalize_prompts.py` | **新建** — prompt_defaults 守护 |
| `tests/test_multi_translate_routes.py` | 修改 — 删除老的"上传时识别 zh/en"用例，加 RESUMABLE_STEPS 含 `asr_normalize` 的用例 |
| `tests/test_runtime_multi_translate.py` | 修改 — alignment 入口 fallback 写法（`utterances_en or utterances`）用例 |

### 不做

- 不动 `bulk_translate` / `runtime.py` / `runtime_v2.py` 三条线
- 不做老任务（包括 b3fa903d）数据迁移；非中英文老任务用户自行作废重建
- 不做 task state schema 版本号字段 / lazy migration 逻辑
- 不做 utterances_en 的 UI 编辑能力（本步骤是 transparent 内部规范化，不是创意决策点）
- 不做 pt / fr / it / ja / nl / sv / fi 各自的源语言专修 prompt（先一条通用兜底扛住）
- 不做 detect 模型可后台切换 UI（管理员通过现有 `/settings?tab=bindings` 改 use_case 绑定即可）
- 不合并 detect 到 ASR 步骤（状态机职责分离）
- 不做"原文+英文"双语字幕预览
- 不接 Anthropic 直连 API（继续走 OpenRouter，统一计费、统一 use_case 注册）
- `asr_normalize.translate_zh_to_en` use_case 注册但 runner **不路由到此 use_case**（zh 是 skip 路径），保留 use_case 仅为未来策略变更预留

## 3. 详细设计

### 3.1 状态机：新增 `asr_normalize` step

`web/routes/multi_translate.py:486` 当前：

```python
RESUMABLE_STEPS = ["extract", "asr", "voice_match", "alignment", "translate", "tts", "subtitle", "compose", "export"]
```

改为：

```python
RESUMABLE_STEPS = ["extract", "asr", "asr_normalize", "voice_match", "alignment", "translate", "tts", "subtitle", "compose", "export"]
```

### 3.2 Runner：`_step_asr_normalize`

新增 `appcore/runtime_multi.py::MultiTranslateRunner._step_asr_normalize(task_id)`，逻辑：

```python
def _step_asr_normalize(self, task_id: str) -> None:
    task = task_state.get(task_id)
    utterances = task.get("utterances") or []

    # 边界 1：ASR 没跑完
    if not utterances:
        # E6 路径：空 utterances 短路 done
        task_state.set_step(task_id, "asr_normalize", "done", "无音频文本，跳过标准化")
        return

    # 边界 2：resume 幂等
    if task.get("utterances_en") or task.get("source_language") in ("en", "zh"):
        # 已经跑过了或是 zh/en skip 路径，幂等返回
        if not task_state.get_step(task_id, "asr_normalize"):
            task_state.set_step(task_id, "asr_normalize", "done", "已标准化（resume 跳过）")
        return

    task_state.set_step(task_id, "asr_normalize", "running", "正在识别原文语言…")
    user_id = task.get("_user_id")

    # 调 detect + 路由 + translate（封装在 pipeline.asr_normalize.run_asr_normalize）
    try:
        artifact = pipeline.asr_normalize.run_asr_normalize(
            task_id=task_id, user_id=user_id, utterances=utterances,
        )
    except pipeline.asr_normalize.UnsupportedSourceLanguageError as exc:
        task_state.set_step(task_id, "asr_normalize", "failed", str(exc))
        task_state.update(task_id, error=str(exc))
        return
    except Exception as exc:  # detect / translate API 重试后仍失败
        task_state.set_step(task_id, "asr_normalize", "failed", f"原文标准化失败：{exc}")
        task_state.update(task_id, error=f"原文标准化失败：{exc}")
        return

    # 写回 task state
    # 1) 拆 artifact：_utterances_en 是内部字段，单独写到 task["utterances_en"]，不进 artifact 落盘
    utterances_en = artifact.pop("_utterances_en", None)
    updates = {
        "detected_source_language": artifact["detected_source_language"],
    }
    if artifact["route"] == "en_skip":
        updates["source_language"] = "en"
    elif artifact["route"] == "zh_skip":
        updates["source_language"] = "zh"
    else:
        # es_specialized / generic_fallback / generic_fallback_low_confidence / generic_fallback_mixed
        updates["source_language"] = "en"
        updates["utterances_en"] = utterances_en
    task_state.update(task_id, **updates)

    msg_map = {
        "en_skip": "原文为英文，跳过标准化",
        "zh_skip": "原文为中文，走中文路径",
        "es_specialized": "西班牙语 → 英文标准化完成",
        "generic_fallback": f"{artifact['detected_source_language']} → 英文标准化完成（通用）",
        "generic_fallback_low_confidence": f"{artifact['detected_source_language']} → 英文标准化完成（低置信兜底）",
        "generic_fallback_mixed": "混合语言 → 英文标准化完成（兜底）",
    }
    task_state.set_step(
        task_id, "asr_normalize", "done",
        msg_map.get(artifact["route"], "原文标准化完成"),
    )
    task_state.set_artifact(task_id, "asr_normalize", artifact)
```

**Resume 行为**：

- 用户从 `asr_normalize` resume：清掉 `utterances_en` / `source_language` / `detected_source_language` / `asr_normalize_artifact` 后再调上面方法。
- 用户从 `asr` resume：上游已经清掉 `utterances`，`asr_normalize` 自然重跑。

### 3.3 数据结构

#### task state 字段

| 字段 | 类型 | 何时写入 | 何时读取 | 备注 |
|------|------|---------|---------|------|
| `task["utterances"]` | `list[dict]` | ASR 步骤 | 始终保留作为"ASR 原始文本"真相源 | 新方案下不再被覆盖 |
| `task["utterances_en"]` | `list[dict]` 或缺失 | `asr_normalize` | alignment 入口 | 仅当源语言是非中英文白名单语言时写入；en/zh 路径下不存在此字段 |
| `task["source_language"]` | `"en"` / `"zh"` | `asr_normalize` | runtime_multi 翻译 prompt 标签 | 取值范围**收窄到 zh/en 两种** |
| `task["detected_source_language"]` | `str`（语言代码） | `asr_normalize` | UI 展示用（"原文为西班牙语"） | detect 出来的真实语言（如 `es`） |
| `task["asr_normalize_artifact"]` | `dict` | `asr_normalize` | UI 详情页 | 见 3.4 |

**不变量**：

1. `utterances_en` 存在 ⇒ `source_language == "en"` 且 `detected_source_language not in ("en", "zh")`
2. `source_language == "zh"` ⇒ `utterances_en` 不存在 且 `detected_source_language == "zh"`
3. `source_language == "en"` 且 `utterances_en` 不存在 ⇒ `detected_source_language == "en"`（原文就是英文）
4. `source_language == "en"` 且 `utterances_en` 存在 ⇒ `detected_source_language` 是其他白名单语言（中转过的）

#### artifact 字段

```json
{
  "detected_source_language": "es",
  "confidence": 0.97,
  "is_mixed": false,
  "route": "es_specialized",
  "input": {
    "language_label": "西班牙语",
    "full_text_preview": "前 200 字符…",
    "utterance_count": 42
  },
  "output": {
    "full_text_preview": "前 200 字符英文…",
    "utterance_count": 42
  },
  "tokens": {
    "detect": {"input_tokens": 320, "output_tokens": 40},
    "translate": {"input_tokens": 1850, "output_tokens": 1620}
  },
  "elapsed_ms": 8420,
  "model": {
    "detect": "gemini-3.1-flash-lite-preview",
    "translate": "anthropic/claude-sonnet-4.6"
  }
}
```

`route` 枚举（仅成功路径会写入 artifact）：`"en_skip"` / `"zh_skip"` / `"es_specialized"` / `"generic_fallback"` / `"generic_fallback_low_confidence"` / `"generic_fallback_mixed"`。失败路径（detect 失败 / unsupported / translate 失败 / schema 错位）一律 raise 异常，artifact **完全不写入**——所有信息只通过 `task["error"]` + `step_message` + 应用日志暴露。

LLM 写回 task 用的中间字段 `_utterances_en`（带下划线前缀）只在内存里流转，不进 artifact 对外暴露。

#### 下游兼容点

`web/routes/multi_translate.py:424`：

```python
# 改前
script_segments = build_script_segments(task.get("utterances", []), break_after)
# 改后
source_utterances = task.get("utterances_en") or task.get("utterances", [])
script_segments = build_script_segments(source_utterances, break_after)
```

`appcore/runtime_multi.py` 里所有读 `task["utterances"]` 用于**翻译/分段**目的的地方（grep `task.*utterances` 全部排查）按相同 fallback 改写。展示用途（如 UI 详情页显示原始 ASR 文本、字幕预览原文）继续读 `utterances`。

### 3.4 LLM 编排

#### 3.4.1 Use case 注册

追加到 `appcore/llm_use_cases.py::USE_CASES`：

```python
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

#### 3.4.2 Detect 调用

```python
# pipeline/asr_normalize.py

DETECT_SUPPORTED_LANGS = ("en", "zh", "es", "pt", "fr", "it", "ja", "nl", "sv", "fi")
LOW_CONFIDENCE_THRESHOLD = 0.6

def detect_language(full_text: str, *, task_id: str, user_id: int | None) -> tuple[dict, dict]:
    """返回 (parsed_result, usage_tokens)。parsed_result 含 language/confidence/is_mixed。"""
    last_exc: Exception | None = None
    for attempt in range(2):  # 共 2 次（首次 + 重试 1 次）
        try:
            result = llm_client.invoke_chat(
                "asr_normalize.detect_language",
                messages=[
                    {"role": "system", "content": resolve_prompt_config("asr_normalize.detect", "")["content"]},
                    {"role": "user", "content": full_text[:4000]},  # detect 不需要全文
                ],
                user_id=user_id, project_id=task_id,
                temperature=0.0,
                response_format={
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
                },
            )
            parsed = _parse_detect_result(result["text"])
            usage = result.get("usage") or {"input_tokens": None, "output_tokens": None}
            return parsed, usage
        except Exception as exc:
            last_exc = exc
            if attempt == 0:
                time.sleep(2)  # 重试前回退 2s
                continue
    raise DetectLanguageFailedError(f"detect_language failed after 2 attempts: {last_exc}")
```

#### 3.4.3 Translate 调用（句级 + 全文 context）

```python
def translate_to_en(
    utterances: list[dict],
    detected_language: str,
    *,
    route: str,
    task_id: str,
    user_id: int | None,
) -> tuple[list[dict], dict]:
    """返回 (utterances_en, usage_tokens)。utterances_en 结构同 utterances，text 替换为英文。"""
    use_case_code = {
        "es_specialized": "asr_normalize.translate_es_to_en",
        "generic_fallback": "asr_normalize.translate_generic_to_en",
        "generic_fallback_low_confidence": "asr_normalize.translate_generic_to_en",
        "generic_fallback_mixed": "asr_normalize.translate_generic_to_en",
    }[route]
    prompt_slot = {
        "es_specialized": "asr_normalize.translate_es_en",
        "generic_fallback": "asr_normalize.translate_generic_en",
        "generic_fallback_low_confidence": "asr_normalize.translate_generic_en",
        "generic_fallback_mixed": "asr_normalize.translate_generic_en",
    }[route]

    full_text = " ".join(u["text"] for u in utterances)
    user_payload = {
        "source_language": detected_language,
        "is_mixed": route == "generic_fallback_mixed",
        "low_confidence": route == "generic_fallback_low_confidence",
        "full_text": full_text,
        "utterances": [{"index": i, "text": u["text"]} for i, u in enumerate(utterances)],
    }

    result = llm_client.invoke_chat(
        use_case_code,
        messages=[
            {"role": "system", "content": resolve_prompt_config(prompt_slot, "")["content"]},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        user_id=user_id, project_id=task_id,
        temperature=0.2,
        response_format={
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
        },
    )

    payload = json.loads(result["text"])
    items = payload["utterances_en"]

    # 校验：长度 1:1、index 完整覆盖、text_en 非空
    if len(items) != len(utterances):
        raise TranslateOutputInvalidError(
            f"length mismatch: input={len(utterances)} output={len(items)}",
        )
    by_index = {item["index"]: item["text_en"] for item in items}
    if set(by_index.keys()) != set(range(len(utterances))):
        raise TranslateOutputInvalidError(
            f"index coverage mismatch: missing {set(range(len(utterances))) - set(by_index.keys())}",
        )

    utterances_en = [
        {
            "index": i,
            "start": utterances[i]["start"],
            "end": utterances[i]["end"],
            "text": by_index[i],  # 注意：utterances_en 列表里字段叫 text（与 utterances 同形态），不叫 text_en；
                                  # text_en 仅出现在 LLM 输出 schema 中，runner 内部消化掉。
        }
        for i in range(len(utterances))
    ]
    usage_tokens = result.get("usage") or {"input_tokens": None, "output_tokens": None}
    return utterances_en, usage_tokens
```

#### 3.4.4 主入口 `run_asr_normalize`

```python
def run_asr_normalize(
    *,
    task_id: str,
    user_id: int | None,
    utterances: list[dict],
) -> dict:
    t0 = time.monotonic()
    full_text = " ".join(u["text"] for u in utterances)

    # 1. detect
    detect_result, detect_tokens = detect_language(full_text, task_id=task_id, user_id=user_id)
    lang = detect_result["language"]
    conf = detect_result["confidence"]
    is_mixed = detect_result["is_mixed"]

    # 2. 路由
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
    elif lang == "es" and not is_mixed and conf >= LOW_CONFIDENCE_THRESHOLD:
        route = "es_specialized"
    elif is_mixed:
        route = "generic_fallback_mixed"
    elif conf < LOW_CONFIDENCE_THRESHOLD:
        route = "generic_fallback_low_confidence"
    else:
        route = "generic_fallback"

    # 3. translate（en/zh skip 路径不调）
    utterances_en = None
    translate_tokens = {}
    if route not in ("en_skip", "zh_skip"):
        utterances_en, translate_tokens = translate_to_en(
            utterances, detected_language=lang, route=route,
            task_id=task_id, user_id=user_id,
        )
        # translate_to_en 同时返回结果列表和 _usage dict（{"input_tokens", "output_tokens"}）

    # 4. artifact
    artifact = {
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
        artifact["_utterances_en"] = utterances_en  # 内部字段，runner 拿走后从 artifact 删掉再 set_artifact
    return artifact
```

### 3.5 Prompt 库（4 条 default）

新增到 `pipeline/languages/prompt_defaults.py::DEFAULTS`，key 第二项 lang 字段使用空字符串 `""` 占位（这些 prompt 不绑定目标语言）：

#### `_DETECT_PROMPT`

```
You are a language identification expert for short-form video ASR transcripts (TikTok / Reels / Shorts e-commerce content).

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

Return JSON only. No prose. No markdown fences.
```

#### `_TRANSLATE_ZH_TO_EN`（注册但 runner 当前不路由调用）

```
You are a US-based short-form commerce content creator translating a Chinese ASR transcript into natural en-US for downstream localization.

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
- Keep each utterance roughly the same word count as its Chinese counterpart (downstream alignment relies on per-utterance pacing).
```

#### `_TRANSLATE_ES_TO_EN`

```
You are a US-based short-form commerce content creator translating a Spanish ASR transcript into natural en-US for downstream localization.

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

Recreate, don't translate literally. Preserve meaning faithfully; do NOT invent product features.
```

#### `_TRANSLATE_GENERIC_TO_EN`（兜底）

```
You are a US-based short-form commerce content creator translating an ASR transcript into natural en-US for downstream localization.

INPUT FORMAT (JSON in user message):
{
  "source_language": "<ISO code: pt/fr/it/ja/nl/sv/fi/...>",
  "is_mixed": true/false,        // true means transcript has 30%+ tokens from another language
  "low_confidence": true/false,  // true means detection was uncertain
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

Recreate, don't translate literally. Preserve meaning faithfully; do NOT invent product features.
```

#### DEFAULTS 字典追加

```python
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

`appcore/llm_prompt_configs.resolve_prompt_config(slot, lang)` 在第一次访问时会按现有逻辑自动从 DEFAULTS 兜底并 seed 一行到 DB。**无需迁移脚本**。管理员后台 prompt 页面会自动出现这 4 条新 prompt，可改 + 可"恢复默认"。

### 3.6 触发条件 & 现有代码处置

**新建任务（multi_translate 上传）**

- `web/routes/multi_translate.py` 上传入口里**删除**当前的"自动检测中文/英文"代码段。任务 state 创建时不再写入 `source_language` 字段；该字段由 `asr_normalize` step 唯一负责写入。
- 模板 `multi_translate_list.html` 上传弹窗里"上传后将自动识别视频源语言（中文/英文）"提示文案改为"上传后将自动识别原视频语言并标准化"。
- `PUT /api/multi-translate/<task_id>/source-language` 路由（`web/routes/multi_translate.py:392`）**保留**——管理员仍可手动覆盖（zh/en 两选项），覆盖后 `asr_normalize` 已经跑完则需要管理员手动从 `asr_normalize` step resume。
- `PUT /api/multi-translate/<task_id>/alignment` 路由里"接受 source_language=zh/en 覆盖"逻辑（第 419-421 行）**保留**作为兜底纠错口子。

**老任务**

按 §A8 决策**不做任何迁移**：

- 老任务结构里 `source_language` 已经是 zh/en 之一，`utterances_en` 字段缺失。
- 老任务点 resume 时按现有逻辑走（不会经过 `asr_normalize` step——因为它不在该任务的状态机记录里）。
- 中文/英文素材的老任务能正常跑完。
- 非中英文素材的老任务（如 b3fa903d）会继续卡在原位 → 用户自行作废重建。
- **不做**版本号字段、不做"如果 v1 任务则跳过新 step"判断。

## 4. 数据流（西语素材 → 德语本地化为例）

```
upload(es 视频)  ── 不写 source_language
  ↓
extract → asr  ──→ task.utterances = [{idx,start,end,text:"Hola, este..."}, ...]
  ↓
asr_normalize  ──┐
  ├─ Gemini 3.1 Flash Lite detect_language(full_text)
  │    → {"language":"es","confidence":0.97,"is_mixed":false}
  ├─ route = "es_specialized"
  └─ Claude Sonnet 4.6 translate_to_en (es 专用 prompt)
       → task.utterances_en = [{idx,start,end,text:"Hi, this..."}, ...]
       → task.source_language = "en"
       → task.detected_source_language = "es"
       → task.asr_normalize_artifact = {...}
  ↓
voice_match (英语音色候选)
  ↓
alignment ── build_script_segments(task.utterances_en or task.utterances, ...)
  ↓
translate (en → de，prompt 标签：原文为英文)
  ↓
tts (德语 ElevenLabs) → subtitle (de 规则) → compose → export
```

## 5. 异常路径

| 编号 | 触发 | 行为 | UI 表现 |
|------|------|------|--------|
| E1 | detect API 失败 | 内部重试 1 次（共 2 次），间隔 2s；仍失败 → `run_asr_normalize` 抛 `DetectLanguageFailedError`，runner 标记 step failed；artifact 不写入（构造未完成） | 红 banner，`task["error"]="原文标准化失败：detect_language failed after 2 attempts: ..."`；step_message 同 |
| E2 | detect 出 `language="other"` | 不重试，立即抛 `UnsupportedSourceLanguageError`；runner 标记 step failed；artifact 不写入 | 红 banner，`task["error"]="原视频语言检测为「other」(confidence=0.XX)，当前流水线仅支持..."`；step_message 同 |
| E3 | `confidence < 0.6` 或 `is_mixed=true` | 不 fail，走通用兜底 prompt，user payload 里 `low_confidence` / `is_mixed` 字段告知 Claude；artifact 正常写入 | 任务正常 done；artifact `route="generic_fallback_low_confidence"` 或 `"generic_fallback_mixed"` |
| E4 | Claude translate API 失败（`llm_client` 内置重试已耗尽） | `translate_to_en` 抛异常向上冒泡；runner 标记 step failed；artifact 不写入 | 红 banner，`task["error"]="原文标准化翻译失败：..."`；step_message 同 |
| E5 | translate 输出 schema 不合法（长度对不上 / index 缺漏 / text_en 为空） | `translate_to_en` 抛 `TranslateOutputInvalidError`；runner 标记 step failed；artifact 不写入 | 红 banner，`task["error"]="原文标准化输出结构异常，已重试仍不合法"`；step_message 同；坏输出前 500 字符进**应用日志**（不进 task state） |
| E6 | utterances 为空（视频静音 / ASR 全漏） | runner 短路 done，整个新 step 直接跳过；artifact 不写入；utterances_en/source_language/detected_source_language 都不写 | 进度条该格灰显"无音频文本，跳过标准化"；下游 alignment 自然走"无内容"路径 |

**统一原则**：失败时 `task["error"]` 字段写入人话错误信息，UI 红 banner 直接展示；管理员可在任务详情页"系统日志"区看到 raw 错误堆栈和完整 artifact。任何 fail 都让 step 停在 `failed` 态——不静默吞、不 fallback 到"假装跑过了"。

## 6. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| Claude Sonnet 句级翻译时偶尔丢失上下文连贯性（代词、专有名词） | 中：英文文本不连贯，下游再翻德语会带上偏差 | 通过 §C 方案——messages 里同时塞 full_text 作为 context；单条不连贯不致命，整体语义保持即可 |
| Gemini detect 对极短素材（<10s）置信度低 | 低：会触发 generic_fallback 而不是 es_specialized | 阈值 0.6 设计就是为此——低置信走兜底是 feature 不是 bug |
| Claude 翻译 token 消耗（句级 + 全文 context 双输入） | 中：单次 translate 调用 input 翻倍 | 已知成本；用户可在 `/settings?tab=bindings` 改 model 到便宜 LLM；兜底任务更长可拆批，本次不做 |
| 老任务（含 b3fa903d）作废 → 用户体验差 | 中：非中英文老任务用户白等 | 接受。非中英文老任务在新代码上线前**本来就跑不通**，只是表现从"prompt 错路"变成"卡 alignment"；用户重建任务的损失只是已上传素材，可接受 |
| 上传时检测代码删除可能影响其他模块依赖 | 低：仅 multi_translate 路由依赖 | 全仓库 grep `source_language` + `auto_detect`/`detect_lang` 确认引用范围，仅在 multi_translate.py 范围内 |
| `source_language` 字段语义改变（从"中英二选"变为"asr_normalize 唯一写入口"）影响下游 | 中：下游可能依赖该字段在上传时就有值 | runtime_multi 内部通过 `task.get("source_language")` 兜底空值；alignment 入口允许空 source_language（走 utterances_en fallback 已经覆盖） |
| 新 step 失败导致原本能跑（zh/en 素材误判）的任务卡住 | 低 | detect 对中英文素材置信度极高（>0.95），误判风险接近 0；管理员可通过 `PUT /source-language` 路由强制覆盖 |

## 7. 测试计划

按项目惯例（mock-only，不连 MySQL）。

### 7.1 单元测试

**`tests/test_asr_normalize.py`** — `pipeline/asr_normalize.py` 单元测试（mock `llm_client.invoke_chat`）：

- `test_detect_language_normal_returns_parsed_dict`
- `test_detect_language_retries_once_on_api_error`
- `test_detect_language_fails_after_two_attempts`
- `test_detect_language_handles_invalid_json_in_response`
- `test_translate_to_en_preserves_timestamps_per_utterance`
- `test_translate_to_en_raises_on_length_mismatch`
- `test_translate_to_en_raises_on_index_gap`
- `test_translate_to_en_raises_on_empty_text_en`
- `test_run_asr_normalize_routes_en_to_en_skip`
- `test_run_asr_normalize_routes_zh_to_zh_skip`
- `test_run_asr_normalize_routes_es_to_specialized`
- `test_run_asr_normalize_routes_pt_to_generic_fallback`
- `test_run_asr_normalize_routes_low_confidence_to_fallback`
- `test_run_asr_normalize_routes_mixed_to_fallback`
- `test_run_asr_normalize_raises_unsupported_on_other`

**`tests/test_runtime_multi_asr_normalize.py`** — `_step_asr_normalize` 集成测试：

- `test_step_asr_normalize_writes_source_language_en_for_es_route`
- `test_step_asr_normalize_skips_when_utterances_en_already_present`
- `test_step_asr_normalize_short_circuits_on_empty_utterances`
- `test_step_asr_normalize_marks_failed_on_unsupported_language`
- `test_step_asr_normalize_marks_failed_on_detect_exhaustion`
- `test_step_asr_normalize_writes_artifact_with_route_and_tokens`

**`tests/test_asr_normalize_use_cases.py`** — `appcore/llm_use_cases.py` 守护：

- `test_four_new_use_cases_registered`
- `test_detect_use_case_uses_gemini_aistudio_flash_lite`
- `test_translate_use_cases_use_openrouter_claude_sonnet_46`

**`tests/test_asr_normalize_prompts.py`** — `prompt_defaults.DEFAULTS` 守护：

- `test_four_new_prompts_registered_with_empty_lang_key`
- `test_detect_prompt_includes_supported_lang_enum`
- `test_es_translate_prompt_includes_en_us_vocab_anchors`
- `test_generic_translate_prompt_handles_is_mixed_and_low_confidence_flags`

### 7.2 修改既有测试

**`tests/test_multi_translate_routes.py`**：

- 删除/调整老的"上传时识别 zh/en"测试用例
- 新增 `test_upload_does_not_write_source_language_field`
- 新增 `test_resumable_steps_includes_asr_normalize`

**`tests/test_runtime_multi_translate.py`**：

- 新增 `test_alignment_reads_utterances_en_when_present`
- 新增 `test_alignment_falls_back_to_utterances_when_en_missing`

### 7.3 手测清单（落地后）

- [ ] 上传一段西语素材，目标德语 → asr_normalize 完成 → artifact 显示 `route="es_specialized"` + es→en 前后对照
- [ ] 上传一段中文素材，目标德语 → asr_normalize 显示 `route="zh_skip"`，下游走中文 prompt 路径
- [ ] 上传一段英文素材，目标德语 → asr_normalize 显示 `route="en_skip"`，下游 source_language=en
- [ ] 上传一段法语素材，目标德语 → asr_normalize 显示 `route="generic_fallback"`，英文质量可读
- [ ] 上传一段超短（5s）素材 → asr_normalize 走 `route="generic_fallback_low_confidence"`，不 fail
- [ ] 上传一段俄语/韩语素材 → asr_normalize fail，error banner 提示语言不支持
- [ ] 任务详情页进度条多一格"原文标准化"，artifact 卡片可点开看 detect/translate 详情
- [ ] 管理员 prompt 页面出现 4 条 `asr_normalize.*` 新 prompt，可改 + 可恢复默认

### 7.4 运行命令

```bash
cd g:/Code/AutoVideoSrtLocal && python -m pytest \
  tests/test_asr_normalize.py \
  tests/test_runtime_multi_asr_normalize.py \
  tests/test_asr_normalize_use_cases.py \
  tests/test_asr_normalize_prompts.py \
  tests/test_multi_translate_routes.py \
  tests/test_runtime_multi_translate.py \
  -q 2>&1 | tail -10
```

## 8. 落地顺序

按依赖逐步：

1. `pipeline/languages/prompt_defaults.py` — 4 条 prompt 注册（含 DEFAULTS）
2. `appcore/llm_use_cases.py` — 4 条 use_case 注册
3. `pipeline/asr_normalize.py` — 新建模块（detect / translate / run_asr_normalize / 异常类）
4. `appcore/runtime_multi.py` — 新增 `_step_asr_normalize`；alignment 入口 utterances 读取处加 fallback
5. `web/routes/multi_translate.py` — RESUMABLE_STEPS 加 `"asr_normalize"`；删除上传时 source_language 自动检测；任务创建时不再写 source_language
6. `web/templates/multi_translate_detail.html` — 进度条加格 + artifact 展示卡
7. `web/templates/multi_translate_list.html` — 上传弹窗文案调整
8. 测试增量（顺序与代码一致，TDD：先写失败测试再写实现，按 task 在 plan 里展开）
9. 手测清单跑一遍 → 合并 master → 部署

## 9. 验收标准

- 用户在 `/multi-translate` 上传任意 zh/en/es/pt/fr/it/ja/nl/sv/fi 素材都能跑通完整流水线
- 西语素材跑完后字幕英文质量可读、风格符合 en-US 电商风格
- 任务详情页能看到"原文标准化"进度条 + artifact 前后对照
- 管理员能在 prompt 页编辑 4 条新 prompt，"恢复默认"按钮回到本设计稿值
- 上传非白名单语言（俄/韩/越等）任务在 asr_normalize 步骤明确失败，error 信息清晰
- 现有 zh/en 素材任务行为完全不变（detect 跑过 + 走 skip 路径，性能开销 <2s）
- `bulk_translate` / 主线英语流程 / 音画同步流程零影响
- 已有非中英文老任务（如 b3fa903d）保持原状（卡死状态不变），不做迁移

## 10. 后续可能的演进（不在本次范围）

- 把 pt / fr / ja 等高频语言从通用兜底升级为专修 prompt
- 给 `bulk_translate` 接入相同 asr_normalize 步骤
- "原文 + 英文" 双语字幕预览
- ASR 结合 detect 的合并优化（豆包 ASR 本身就有语言检测能力，可省一次 LLM 调用）
- 评估替换 detect 模型为更便宜的本地分类器（fastText / langdetect）
- en/zh 素材也走 detect 但允许"用户手动声明跳过"以节省 1-2s
