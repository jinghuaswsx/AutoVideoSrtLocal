# Omni-Translate: Explicit Source Language Design

**日期**：2026-04-26
**作者**：Claude (with noobird)
**状态**：approved，开始实施

## 背景

当前 omni-translate 的源语言处理有两层 LLM 检测，**会覆盖用户的选择**：

1. ASR 后 `runtime_omni._step_asr` 调 `detect_language_llm`，置信度 ≥0.7 且不同就改写 `task.source_language`
2. `runtime_multi._step_asr_normalize` 调 `detect_language` 决定路由（en_skip / zh_skip / es_specialized / generic_fallback / mixed / low_confidence）

实际事故：用户上传**西语视频**、上传时选了 `es`，但因为 ASR 后 LID + asr_normalize 的判断把它路由到 `generic_fallback_mixed`（被认为含混语言），最终下游"翻译本土化"看到的是被通用 prompt 翻成的英文，质量不理想。

## 目标

- 用户上传时可以**明确指定语言**（含葡语 pt），明确指定后**两层 LLM 检测都不再覆盖**
- 用户可以在详情页**事后改语言**，自动从 `asr_normalize` 重跑（保留 ASR 文本不动）
- "不填"则完全保持当前行为（LLM 自动检测）

## 设计

### A. 数据模型

新增字段 `task["user_specified_source_language"]: bool`：
- `True` = 用户在上传或详情页明确选了某个语言（zh/en/es/pt 之一）
- `False` = 用户选了"自动检测"（前端 `value=""`）

### B. 创建任务时

**前端**（`web/templates/omni_translate_list.html`）：上传 modal 的 select 改为：
```html
<option value="" selected>自动检测（推荐）</option>
<option value="zh">中文（豆包 ASR）</option>
<option value="en">英文（豆包 ASR）</option>
<option value="es">西班牙语（Scribe）</option>
<option value="pt">葡萄牙语（Scribe）</option>
```
hint：选具体语言可跳过 LLM 自动检测，直接走对应路径。

**后端**（`web/routes/omni_translate.py:283-285`）：
- 接受 `""` / `zh` / `en` / `es` / `pt`
- 不填时 `source_language` 仍存为 `"zh"`（保留默认 ASR 引擎），但 `user_specified_source_language=False`
- 选了时 `user_specified_source_language=True`

### C. ASR 步骤跳过 LID

`appcore/runtime_omni.py:_step_asr` 第 130-157 行（LID 自动覆盖块）外面加闸门：
```python
if not task.get("user_specified_source_language"):
    # 当前的 LID 自动覆盖逻辑保持不变
    ...
```
user_specified=True 时**完全跳过** LID 调用（连 LLM 都不调），不会覆盖。

### D. asr_normalize 跳过 detect

`appcore/runtime_multi.py:_step_asr_normalize` 加 user_specified 短路径：

| user_specified | source_language | 行为 |
|---|---|---|
| True | zh | 走现有 `zh_skip` |
| True | en | 走现有 `en_skip` |
| True | es | **跳过 detect**，构造 fake artifact 直接走 `es_specialized` translate |
| True | pt | **跳过 detect**，构造 fake artifact 直接走 `generic_fallback` translate（YAGNI：不建葡语专精 prompt） |
| False | (any) | 当前完整行为（detect → 路由） |

Fake artifact 字段：
- `language`: 用户选的
- `confidence`: 1.0
- `is_mixed`: false
- `source`: `"user_specified"`（新增字段，区别 LLM detect 路径）

### E. 详情页改语言入口

**UI**：在新加的"原文标准化"卡片右上角加 `重选语言` 按钮，点开浮层显示 5 选项 select + "保存并重跑"按钮。
**user_specified=True** 时，卡片标签 `检测语言` 改成 `用户指定语言`，confidence 显示 `100%`。

**API**：`POST /api/omni-translate/<task_id>/source-language`
- body: `{source_language: "" | "zh" | "en" | "es" | "pt"}`
- 流程：
  1. 二次确认（前端弹窗）
  2. `task_state.update(task_id, source_language=新值, user_specified_source_language=非空)`
  3. **清掉**：`utterances_en=None`, `asr_normalize_artifact=None`, `detected_source_language=None`，及下游 step 的 artifacts（translate / tts / subtitle / compose）和 step state（设为 pending）
  4. 调 `omni_pipeline_runner.resume(task_id, "asr_normalize", user_id)`
- 不重跑 `_step_asr` —— 复用现有 ASR 文本（即使原 ASR 引擎"错配"，也由用户单独用 ASR "从此步继续" 按钮处理）

### F. Resume 起点策略

**统一从 `asr_normalize` 开始**（用户原话："选完语言后的下一步"）。
- 理由：ASR 文本与语言无关地存在；只重做语言路由 + 下游
- 不做"引擎要换则从 asr 重跑"的智能判断（用户明确否定）
- 二次保险：从 `asr_normalize` 重跑根本不会触发 `_step_asr`，所以 LID 自动也不会跑

### G. 改动文件清单

| 文件 | 改动 |
|---|---|
| `web/templates/omni_translate_list.html` | 上传 select +pt+`""` |
| `web/routes/omni_translate.py` | upload validation；新增 source-language POST 端点 |
| `appcore/runtime_omni.py` | _step_asr 加 LID 跳过闸门 |
| `appcore/runtime_multi.py` | _step_asr_normalize 加 user_specified 短路径 |
| `pipeline/asr_normalize.py` | 新增 `build_user_specified_artifact(lang)` 工具函数 |
| `web/templates/omni_translate_detail.html` | 卡片加重选语言浮层 + user_specified 标签切换 |
| `tests/test_runtime_multi_translate.py` | user_specified 路径覆盖 |
| `tests/test_omni_translate_routes.py`（如有） | 新端点测试 |

约 150 行，feature 改动，按 CLAUDE.md hard rule 开 worktree `feature/omni-explicit-source-language`。

## 不在范围

- 葡语专精 prompt（YAGNI）
- 智能 ASR 引擎切换重跑（用户否定）
- 多语种 hint UI 增强（弹窗仅简洁文案，不做 trade-off 详解）
- 历史任务的 user_specified 字段回填（默认 False，不影响新逻辑）
