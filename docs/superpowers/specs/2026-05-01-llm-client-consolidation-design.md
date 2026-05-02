# LLM 旧调用路径收敛 设计方案

> 本文档只设计、不落地代码。落地实现要再开 plan，分阶段做。
>
> **基线 commit：** `c395cf53`（线上 / 测试环境同此版本）。
> **配套分支：** `docs/llm-client-consolidation-spec`。
> **不依赖项：** 不连本机 MySQL / 127.0.0.1:3306；只读代码与已存在的 spec/plan 文档。

---

## 1. 目标 & 范围

### 目标

收敛仍然绕开 `appcore.llm_client` 直连 OpenAI / Google `genai` SDK 的业务路径，使「业务代码 → use_case → adapter → SDK」成为唯一允许的调用链，达成：

- 仓库中只剩 `appcore/llm_providers/*_adapter.py` 出现 `OpenAI(...)` / `genai.Client(...)`；其它生产代码全部走 `llm_client.invoke_chat / invoke_generate`。
- 业务模块新增 LLM 调用时，没有"绕路 + 自带 ai_billing"这种第二条捷径——只有 use_case 一条路。
- usage_log / ai_billing 记录字段统一，由 `llm_client._log_usage` 一处生产，便于后续审计与对账。
- `pipeline/translate.py` 与 `appcore/gemini.py` 不再被 adapter 反向依赖（当前 adapter 还在 `from pipeline.translate import _call_vertex_json`、`from appcore import gemini as gemini_api`），让旧文件能真正退役。

### 非目标（本期不做）

1. 不新增 provider/adapter（OpenRouter / 豆包 / Gemini AI Studio / Vertex / Vertex ADC 共 5 个保持不变）。
2. 不动 `link_check_desktop/gemini_client.py`（独立桌面端，不属于 web 路径）。
3. 不动 `scripts/debug_vertex*.py` 调试脚本。
4. 不重构 `llm_provider_configs` / `llm_use_case_bindings` 表结构。
5. 不引入流式 `invoke_stream` 接口；`appcore.gemini.generate_stream` 暂时保留旧实现（仅有的流式调用方）。
6. 不改变现有 `LANGUAGE_LABELS` / `IMAGE_MODELS_BY_CHANNEL` 等业务枚举。

---

## 2. 现状盘点

### 2.1 已有"统一三层"组件（不动，仅在文档里复述）

| 层 | 文件 | 行号 | 责任 |
|----|------|------|------|
| UseCase 注册表 | `appcore/llm_use_cases.py` | 56–482 | 39 个 use_case，含 default_provider / model / usage_log_service / units_type |
| Binding DAO | `appcore/llm_bindings.py` | 30–95 | `llm_use_case_bindings` 表 resolve / upsert / delete / list_all |
| Provider Adapter | `appcore/llm_providers/__init__.py`、`base.py` | 全部 | 5 个 adapter：openrouter / doubao / gemini_aistudio / gemini_vertex / gemini_vertex_adc |
| 调用入口 | `appcore/llm_client.py` | `invoke_chat` 162–225、`invoke_generate` 228–314 | `binding → adapter`，统一 `_sanitize_messages` / `_log_usage` |
| 计费写入 | `appcore/ai_billing.py:log_request` | 15–80 | use_case → module + service → `usage_log.record + record_payload` |

### 2.2 已迁好的调用方（保持现状即可）

`grep -n "invoke_chat\|invoke_generate"` 命中（节选）：

| 文件 | 行号 | use_case |
|------|------|----------|
| `pipeline/asr_clean.py` | 133 | `asr_clean.purify_*` |
| `pipeline/asr_normalize.py` | 135 / 212 | `asr_normalize.detect_language` 等 |
| `pipeline/av_translate.py` | 315 / 414 | `video_translate.av_localize / av_rewrite` |
| `pipeline/av_source_normalize.py` | 148 | `video_translate.source_normalize` |
| `appcore/copywriting_translate_runtime.py` | 171 | `copywriting_translate.generate` |
| `appcore/image_translate_runtime.py` | 266 | `image_translate.detect` |
| `appcore/link_check_gemini.py` | 67 | `link_check.analyze` |
| `appcore/link_check_same_image.py` | 85 | `link_check.same_image` |
| `tools/audit_copywriting_translation.py` | 247 / 255 / 341 | （审计脚本，已合规）|

### 2.3 仍直连 SDK 的业务路径（本期目标）

`grep` 命中清单（行号锚定 c395cf53）：

#### A. `pipeline/translate.py`

- 顶部 `from openai import OpenAI`（L5）。
- `resolve_provider_config()` L112–148：返回 `(OpenAI(...), model)`，仅供 `_call_openai_compat` 使用。
- `_call_vertex_json()` L217–294：函数内 `from google import genai` + `genai.Client(vertexai=...)`（L269 / L271）。
- `_call_openai_compat()` L317–355：直接 `client.chat.completions.create(...)`。
- 三个业务对外函数：`generate_localized_translation` (L510)、`generate_tts_script` (L661)、`generate_localized_rewrite` (L895)。入参 `provider` 是老式字符串（`vertex_*` / `openrouter` / `doubao` / `gpt_5_mini` / `gemini_31_flash` ...），通过 `_resolve_use_case_provider`（L69–109）做"use_case code → 老式 provider"反向映射。

调用方（要在 Phase A 顺势迁）：
- `appcore/runtime.py` (L544 / L1641)
- `appcore/runtime_v2.py`（多处）
- `appcore/runtime_de.py` (L71 / L85)
- `appcore/runtime_fr.py` (L71 / L85)
- `appcore/runtime_omni.py` (L342 / L387)
- `appcore/runtime_multi.py` (L42 / L210)
- `web/routes/task.py` (L921 / L937)
- `tools/translate_quality_eval.py`、`tools/tts_script_quality_eval.py`
- `pipeline/text_translate.py`（已经引用了 `_resolve_use_case_provider`）

#### B. `appcore/gemini.py`

- `from google import genai` + `from google.genai import types/errors`（L21–23）。
- `genai.Client(...)` 直连：`_get_client_for_service` L181 / L187 / L189。
- 业务入口 `generate(...)` L390–502（一次性）+ `generate_stream` L505–566（流式）。
- 自带 ai_billing：`_log_gemini_usage` L355–387。
- 公共 helper（被 adapter 反向使用）：`_build_config` / `_build_contents` / `_extract_gemini_tokens` / `_is_retryable` / `_guess_mime` / `_to_part` / `_upload_and_wait`、`GeminiError`、`genai_types`。

被 **`appcore/llm_providers/`** 反向引用：
- `gemini_aistudio_adapter.py:6` — `from appcore import gemini as gemini_api`（generate / 流式都委托回它）。
- `gemini_vertex_adapter.py:171 _generate_with_media` — 同样 `from appcore import gemini as gemini_api`。

被业务调用：
- `pipeline/shot_decompose.py` (L11)、`pipeline/video_score.py` (L8)、`pipeline/video_review.py` (L10)、`pipeline/video_csk.py` (L8)、`pipeline/tts_v2.py` (L9)、`pipeline/translate_v2.py` (L8)
- `appcore/runtime.py` (L2297)、`appcore/runtime_v2.py` (L161 / L286)
- `web/routes/settings.py` (L25 — 仅取 `VIDEO_CAPABLE_MODELS` 常量)

#### C. `appcore/gemini_image.py`

- `from google import genai` + `genai_types`（L19–20）。
- `genai.Client(...)` 直连：`_get_image_client` L217 / L223 / L225。
- 函数内 `from openai import OpenAI` + `OpenAI(...)`（L577 / L579，OpenRouter 图片通道）。
- 顶层入口 `generate_image()` L857–997，自带 channel 分发（aistudio / cloud / openrouter / doubao seedream / apimart）和 ai_billing。

调用方：
- `appcore/image_translate_runtime.py`、`tools/shopify_image_localizer/...`、`pipeline/...`（多处）。

#### D. `appcore/llm_providers/gemini_vertex_adapter.py` 内部反向依赖

- L107–113 `_call` — `from pipeline.translate import _call_vertex_json`。
- L260–262 `_call_with_adc` — `from pipeline.translate import _extract_gemini_schema, _split_oai_messages, parse_json_content`。
- L171 `_generate_with_media` — `from appcore import gemini as gemini_api`。
- 这是阻塞「老文件能否退役」的关键反向依赖。

### 2.4 暂不动的直连点（合规）

| 文件 | 直连内容 | 处理 |
|------|---------|------|
| `appcore/llm_providers/openrouter_adapter.py` L19 / L113 / L333 | `OpenAI(...)` | adapter 内部，保留 |
| `appcore/llm_providers/gemini_vertex_adapter.py` L15 / L70 / L76 | `genai.Client(vertexai=...)` | adapter 内部，保留 |
| `link_check_desktop/gemini_client.py` L11 / L32 | `genai.Client(api_key=...)` | 桌面端独立项目，不动 |
| `scripts/debug_vertex.py / debug_vertex_image.py` | `genai.Client(...)` | 调试脚本，不动 |

---

## 3. 设计原则

1. **adapter 是唯一允许直连 SDK 的地方。** 业务代码、`pipeline/*`、`appcore/*_runtime*` 一律走 `llm_client.invoke_*`。
2. **adapter 不允许反向 import 老业务文件。** 当前 `from pipeline.translate import ...`、`from appcore import gemini as gemini_api` 必须断掉，改为 import `appcore/llm_providers/_helpers/`。
3. **业务函数保留兼容签名，但内部 thin-wrapper 化。** `generate_localized_translation` / `appcore.gemini.generate` 保留入口，让 runtime 不必同时改；改造后内部一行 `return llm_client.invoke_chat(...)`。
4. **usage_log 字段对齐。** 保持 `use_case_code / module / provider / model / input_tokens / output_tokens / request_units / units_type / cost_cny / cost_source / extra_data` 完全一致；`request_payload` / `response_payload` 由 `llm_client._log_usage` 生成（结构和 sanitize 已就位）。
5. **图片生成是单独问题域。** `gemini_image.generate_image` 牵涉 5 个 channel + 图片字节字段，本期不强行套进 `invoke_generate`；只把 SDK 直连搬到一个新的 image-only adapter 文件，让"adapter 内部直连"原则仍然成立。
6. **小步、可回滚。** 每阶段每文件单独 commit；任何一阶段被 abort，前面 commit 都仍构成可发布版本。
7. **不破坏现有测试。** 每阶段必须 `pytest tests/ -q` 全绿（涉及到的具体测试见每阶段「测试」段）。
8. **不引入新的 provider 字符串。** 老式 `provider="vertex_*" / "openrouter" / "gpt_5_mini"` 在 Phase A 末才下线；前期 use_case 与老 provider **共存**。

---

## 4. 分阶段迁移清单

> 阶段顺序硬约束：A → B → C。B 依赖 A 把 `pipeline.translate._call_vertex_json` 抽到 `_helpers/`；C 依赖 A、B 完成后没人再调老入口。

### Phase A：纯文本 chat 收口（消灭 `pipeline/translate.py` 直连）

#### A 阶段目标

- adapter 不再 `from pipeline.translate import ...`。
- `pipeline/translate.py` 内 `_call_openai_compat / _call_vertex_json` 删除（或退化为 thin wrapper 后删除）。
- `pipeline/translate.py` 顶部 `from openai import OpenAI` 删除。
- `pipeline/translate.py` 三个对外函数变成 `llm_client.invoke_chat` 的 thin wrapper，调用方仍用旧签名也工作。

#### A 阶段任务拆分

**A-1：抽 `_helpers/` 文件，承接旧 `pipeline.translate` 内被 adapter 反向使用的纯函数。**

- 新增：`appcore/llm_providers/_helpers/openai_compat.py`
  - 迁入：`pipeline/translate.py` 的 `_call_openai_compat` 内"已经独立于 _resolve_use_case_provider 的部分"——也就是把 `OpenAI(...)` 客户端创建、`response_format` 处理、`response-healing` 插件、`usage` 提取都搬过来，函数签名改为 `(client, model, messages, response_format, temperature, max_tokens) → (payload, usage, raw_text, model)`。
- 新增：`appcore/llm_providers/_helpers/vertex_json.py`
  - 迁入：`pipeline/translate.py` 的 `_call_vertex_json`、`_extract_gemini_schema`、`_split_oai_messages`、`parse_json_content`、`_strip_unsupported_schema`。这些是"messages → Vertex generate_content → JSON payload"的纯转换，已经不依赖 `pipeline.translate` 的业务函数。
- 修改：`appcore/llm_providers/gemini_vertex_adapter.py`
  - 把 L107–113、L260–262 的 `from pipeline.translate import ...` 改为 `from appcore.llm_providers._helpers.vertex_json import ...`。
- **不改 `pipeline/translate.py` 自己**：让它继续 `from appcore.llm_providers._helpers.* import *` 反向引用 helper，确保此步**零行为变化**，只是位置搬家。

**测试：**
- 跑 `pytest tests/test_llm_providers_gemini_vertex.py tests/test_translate_vertex_schema.py tests/test_translate_use_case_binding.py tests/test_localization.py tests/test_pipeline_runner.py -q`。
- 用 `grep` 确认 `appcore/llm_providers/` 下不再有 `from pipeline.translate import` 字串。

**风险：**
- 抽出来的 helper 中 `parse_json_content` 也被 `pipeline/translate.py` 自身 L347 调用（`payload = parse_json_content(raw_content)`）。要么把 `pipeline.translate.parse_json_content` 改成 `from appcore.llm_providers._helpers.vertex_json import parse_json_content` 的 re-export；要么干脆把它留在 `pipeline/translate.py` 不迁，只迁 `_call_vertex_json` 等真正被 adapter 用到的部分。建议保 re-export，避免业务代码出现 import 漂移。

**回滚：** 单 commit `git revert`；helper 文件删除即可。

---

**A-2：`pipeline/translate.py` 三函数支持 `use_case=` 入口。**

- 修改：`pipeline/translate.py`：`generate_localized_translation` / `generate_tts_script` / `generate_localized_rewrite`。
  - 新增 keyword `use_case: str | None = None`。
  - 当 `use_case` 命中 `appcore.llm_use_cases.USE_CASES` 时，**直接构造 messages 走 `llm_client.invoke_chat(use_case_code=use_case, messages=..., user_id=..., response_format=..., temperature=..., max_tokens=...)`**，跳过 `_resolve_use_case_provider` 老映射、`_call_openai_compat / _call_vertex_json` 老代码。
  - 旧 `provider=` 参数继续兼容；优先级：`use_case` > `provider`。
- 不改 runtime 调用方，让旧 `provider="vertex_..."` 调用继续走老路。

**测试：**
- 新增：`tests/test_translate_use_case_kwarg.py`，覆盖三函数 `use_case=` 路径走 `invoke_chat`、不走 `_call_*`。
- 跑：`tests/test_translate_use_case_binding.py`、`tests/test_localization.py`、`tests/test_pipeline_text_translate.py`、`tests/test_runtime_multi_translate.py`、`tests/test_tts_duration_loop.py`。

**风险：**
- 旧 `provider=` 路径里有 `openrouter_api_key` 单参覆盖（用户级 OpenRouter Key），新 use_case 路径里 `invoke_chat` 没有同名参数。短期保持"传 use_case 时不接受 openrouter_api_key 覆盖；要覆盖请继续走 provider= 老路"。需要 user-key 覆盖能力的调用点（如 admin 测试控制台）必须留在 provider= 兼容路径上。

**回滚：** revert 单 commit。

---

**A-3：runtime 调用方按文件逐个切到 `use_case=` 调用。**

每个 runtime 文件单独一个 commit：

| 文件 | 入参变化 | use_case |
|------|----------|----------|
| `appcore/runtime.py` (L1641+, L544+) | 把 `provider="vertex_31_flash_lite"` 等改 `use_case="video_translate.localize"` 等 | `video_translate.localize` / `video_translate.tts_script` / `video_translate.rewrite` |
| `appcore/runtime_v2.py` | 同上，注意 v2 用 `video_translate.av_localize / av_rewrite`（已迁；本步只动 localize） | `video_translate.localize` / `video_translate.tts_script` |
| `appcore/runtime_de.py` (L71 / L85) | 德语本土化 | `video_translate.localize` |
| `appcore/runtime_fr.py` (L71 / L85) | 法语本土化 | 同上 |
| `appcore/runtime_omni.py` (L342 / L387) | 全能视频翻译 | 同上 |
| `appcore/runtime_multi.py` (L42 / L210) | 多语种 | 同上 |
| `web/routes/task.py` (L921 / L937) | 单条任务测试入口 | 同上 |
| `pipeline/text_translate.py` | 已经在用 `_resolve_use_case_provider`，去掉中转直接调 `invoke_chat` | `text_translate.generate` / `title_translate.generate` |
| `tools/translate_quality_eval.py`、`tools/tts_script_quality_eval.py` | 离线评测脚本 | 评测专用 use_case 沿用现有 `video_translate.localize` |

**测试：** 每文件单 commit 后跑相关 runtime 测试：`tests/test_appcore_runtime.py`、`tests/test_runtime_multi_translate.py`、`tests/test_pipeline_runner.py`、`tests/test_task_routes.py`、`tests/test_pipeline_text_translate.py`、`tests/test_localization.py`、`tests/test_translate_use_case_binding.py`。

**风险：**
- runtime 在某些路径上同时传 `provider` 和 `model_override`；新 use_case 路径要支持 `invoke_chat(... provider_override=, model_override=)`（已支持，见 `llm_client.py:172-173`）。
- `tools/audit_copywriting_translation.py` 已是 use_case 路径，不要再动。

**回滚：** 按文件 revert；若中途发现某个 runtime 不能立即迁，**就停在那一阶段，上面 A-1/A-2 的成果照样能发布**。

---

**A-4：拆掉 `pipeline/translate.py` 老路径。**

前提：A-3 全部迁完，且至少 1 周线上验证（用 `extra_data->use_case` 可以在 ai_usage 后台过滤）。

- 删除 `pipeline/translate.py` 的 `_resolve_use_case_provider` 内"use_case → 老 provider"分支，只留 identity（保留 `_resolve_use_case_provider` 函数本身一段时间，避免 `pipeline/text_translate.py` 等还在 import）。
- 删除 `_call_openai_compat`、`_call_vertex_json`、`resolve_provider_config`（确认 `get_model_display_name` 改为查 binding）。
- 删除顶部 `from openai import OpenAI`。
- `parse_json_content` 等公共 helper 用 re-export 保留（避免业务文件的 import 漂移）。

**测试：**
- 全量 `pytest tests/ -q`。
- 用 `grep -n "_call_openai_compat\|_call_vertex_json\|resolve_provider_config" pipeline appcore web tests` 必须为空（除 tests 中的旧 fixtures，逐一更新）。
- 用 `grep -n "from openai import" pipeline appcore` 应只剩 `appcore/llm_providers/openrouter_adapter.py`。

**风险：**
- 离线工具或人工脚本仍在传老 `provider=`；要在文档里写明"迁移日期 + 老 provider 已废弃"，并保留兼容期 1 个 release。

**回滚：** revert；老 helper 还可以从 git history 里取回。

#### A 阶段完成验收

- `grep "OpenAI()" pipeline appcore --include='*.py'` 只命中 `appcore/llm_providers/openrouter_adapter.py`。
- `grep "from pipeline.translate import" appcore/llm_providers` 为空。
- `pytest tests/ -q` 全绿。

---

### Phase B：视频/图片多模态收口（消灭 `appcore/gemini.py` 与 `appcore/gemini_image.py` 直连）

> B 阶段是更"硬"的部分：`appcore/gemini.py` 同时承担「业务入口 + adapter helper」两个角色，且支持流式输出；`appcore/gemini_image.py` 是 5-channel 分发器，且 channel 字段正在被 system_settings 实时切换。

#### B 阶段目标

- `appcore/gemini.py` 不再被 adapter import；它只负责"老业务签名 → invoke_generate"的兼容入口 + `generate_stream`（流式）的旧实现。
- `appcore/gemini_image.py` 顶部 `from google import genai` 与函数内 `from openai import OpenAI` 都退役；SDK 直连下沉到新的 image adapter。
- `gemini.generate` 内部 SDK 直连改为走 `llm_client.invoke_generate` 或 helper。
- 流式 `generate_stream` 暂保留单独直连，但隔离到 `appcore/llm_providers/_helpers/gemini_stream.py` 里（避免和 `gemini.generate` 共用 SDK 调用）。

#### B 阶段任务拆分

**B-1：抽 `_helpers/gemini_calls.py`。**

- 迁入 `appcore/gemini.py` 内被 adapter 反向使用的纯函数：`_build_config`、`_build_contents`、`_extract_gemini_tokens`、`_is_retryable`、`_guess_mime`、`_to_part`、`_upload_and_wait`、`GeminiError`、`genai_types` 公共常量。
- 修改 `appcore/llm_providers/gemini_aistudio_adapter.py`、`gemini_vertex_adapter.py`：把 `from appcore import gemini as gemini_api` 改为 `from appcore.llm_providers._helpers.gemini_calls import ...`。
- `appcore/gemini.py` 自己改为 `from appcore.llm_providers._helpers.gemini_calls import ...`，保持对外签名不变（`generate(...)` / `generate_stream(...)` / `resolve_config(...)` / `is_configured(...)` / `model_display_name(...)` / `VIDEO_CAPABLE_MODELS`）。
- 注意 `_clients` 缓存字典：当前 `appcore/gemini.py` 和 `appcore/llm_providers/gemini_vertex_adapter.py` 各自维护一份。Phase B 不合并它们，等 B-3 后再统一。

**测试：**
- `pytest tests/test_gemini_client.py tests/test_gemini_image.py tests/test_gemini_resolve_use_case.py tests/test_llm_providers_gemini.py tests/test_llm_providers_gemini_vertex.py -q`。
- `grep "from appcore import gemini" appcore/llm_providers` 应为空。
- `grep "from appcore.gemini import" appcore/llm_providers` 应为空。

**风险：** `gemini.py` 内 helper 同时被自己和 adapter 用，迁出后两边 import 路径变。要先确认 `_clients` / `_DEFAULT_*_PROVIDER` 这种常量是否需要共享（建议先复制一份到 `_helpers/`，等 B-3 业务入口空壳化再合并）。

---

**B-2：`gemini.generate(...)` 改为 thin wrapper（保留对外签名）。**

- `appcore/gemini.generate(...)` 内部把"构造 contents + cfg + client + retry + log"改为：
  1. service 含 '.' → 直接 `llm_client.invoke_generate(use_case_code=service, prompt=..., system=..., media=..., response_schema=..., temperature=..., max_output_tokens=..., user_id=..., project_id=..., google_search=...)`。
  2. service 不含 '.' →（兼容期）退化为按 `_resolve_provider_code(service)` 解析出一个虚拟 use_case_code，然后走 1。
  3. `return_payload=True` 时把 `invoke_generate` 的返回 `{text/json/raw/usage}` 直接返；False 时回退老返回值（payload 或 text）。
- 由于 `_log_usage` 在 `llm_client` 已经写过 ai_billing，本步删除 `_log_gemini_usage` 重复写入。**这一步是行为改变**：旧 `appcore/gemini.py` 同时写 `_log_gemini_usage`+ `ai_billing.log_request`（含 `request_payload`），新路径走 `llm_client._log_usage` → `ai_billing.log_request`，字段相同，但 payload 由 llm_client 生成。

**测试：**
- `pytest tests/test_gemini_client.py tests/test_gemini_resolve_use_case.py -q`。
- 比对 `usage_logs` 表的实际字段（手工做一次 video_score 调用，diff 行）。
- 全量 smoke：视频评分（`video_score.run`）/ 视频评测（`video_review.analyze`）/ 分镜拆解（`shot_decompose.run`）/ CSK（`video_csk.analyze`）/ TTS v2（`translate_lab.tts_refine`）/ 翻译实验室（`translate_lab.shot_translate`）。

**风险：**
- `appcore/gemini.generate` 入参 `default_model`、`return_payload` 等老参数需要在 wrapper 里继续支持；不能直接改签名。
- `google_search=True` 在 `invoke_generate` 已经分流到 OpenRouter / Vertex 两种 tools 描述，行为应保持一致。
- 如果某个 use_case 默认 provider=`gemini_aistudio` 但调用方传了 `service="gemini_video_analysis"`，service 不含 '.'，走兼容路径——这一支路要保留单测覆盖。

**回滚：** 单 commit revert；`_log_gemini_usage` 一并恢复。

---

**B-3：业务调用方迁离 `appcore.gemini`。**

按文件单 commit：

| 文件 | 现在用 | 改为 |
|------|--------|------|
| `pipeline/shot_decompose.py` (L11) | `from appcore.gemini import generate` | `from appcore.llm_client import invoke_generate` + use_case `shot_decompose.run` |
| `pipeline/video_score.py` (L8) | `from appcore import gemini` | `invoke_generate("video_score.run", ...)` |
| `pipeline/video_review.py` (L10 / L214) | `gemini_api.resolve_config / gemini_api.generate` | `invoke_generate("video_review.analyze", ...)`；`VIDEO_CAPABLE_MODELS` 临时仍从 `appcore.gemini` 导入 |
| `pipeline/video_csk.py` (L8) | 同上 | `invoke_generate("video_csk.analyze", ...)` |
| `pipeline/tts_v2.py` (L9) | `gemini_generate` | `invoke_generate("translate_lab.tts_refine", ...)` |
| `pipeline/translate_v2.py` (L8) | 同上 | `invoke_generate("translate_lab.shot_translate", ...)` |
| `appcore/runtime.py` (L2297) | `resolve_config / model_display_name` | 临时保留 `appcore.gemini.resolve_config` 兼容入口；最终在 C 期改为读 `llm_bindings.resolve(use_case)` |
| `appcore/runtime_v2.py` (L161 / L286) | `resolve_config` | 同上 |
| `web/routes/settings.py` (L25) | `VIDEO_CAPABLE_MODELS` | 在 C-2 中迁到 `appcore/llm_models.py`，本期不动 |

**测试：** 按文件跑相关 pytest，并 smoke 一遍每个 use_case 真实调用（开发库即可）。

**风险：** 流式调用 `generate_stream` 在 `pipeline/translate_v2.py` / `pipeline/tts_v2.py` 不应被波及（它们调的是 `generate`，非流式）。grep 确认调用名后再迁。

---

**B-4：`gemini_image.py` 的 SDK 直连下沉到新 adapter。**

- 新增：`appcore/llm_providers/openrouter_image_adapter.py`，内部 `OpenAI(...)`，承接现 `_generate_via_openrouter` 的全部逻辑。注意它和现有 `OpenRouterAdapter` 不复用：image2 / image quality / chat completions modalities 是图片专属。
- `appcore/gemini_image.py` 修改：
  - 删除顶部 `from openai import OpenAI` 与 `from google import genai` / `genai_types`。
  - `_get_image_client` 整段删除，改为 `from appcore.llm_providers._helpers.gemini_calls import _get_client_for_service`（B-1 抽出来）。
  - `_generate_via_genai` 内部 `client.models.generate_content` 改为复用 `_helpers/gemini_calls`。
  - `_generate_via_openrouter` 改为 `OpenRouterImageAdapter` 实例方法调用。
  - `_generate_via_seedream` / `_generate_via_apimart` 留在文件里**不动**：它们用 `requests` 直接 POST，不属于 OpenAI/genai SDK 直连；属于 image-only HTTP 客户端，可以保留。
- `generate_image(...)` 顶层签名不变，channel 分发逻辑不变；ai_billing 写入路径保留（因为 image use_case 的 `units_type='images'`，与文本 token 计费不同，直接共用 `_log_usage` 会丢字段，本期保留 `gemini_image._log_usage`）。

**测试：**
- `pytest tests/test_gemini_image.py -q`（约 60+ 用例）。
- smoke：`image_translate.detect` / `image_translate.generate` 两条线，分别在 aistudio / cloud / openrouter / doubao / apimart 五个 channel 各跑一次。

**风险：**
- channel=`openrouter` 时 `_extract_openrouter_cost_cny`（L281–298）依赖 OpenAI SDK 返回结构；改 adapter 后必须保持返回 `usage` 字段一致。
- `is_openrouter_openai_image2_model` / `parse_openrouter_openai_image2_model` 是 image 专用 model_id 路由，新 adapter 内沿用。
- adapter 把 image bytes 走 `data:base64` 让 token 用量按 OpenRouter 算；不要在 adapter 里做 image 重压缩。

---

**B-5：流式 `generate_stream` 隔离。**

- 新增：`appcore/llm_providers/_helpers/gemini_stream.py`，承接 `generate_stream` 的客户端创建 + chunk 迭代 + ai_billing 写入。
- `appcore/gemini.py:generate_stream` 改为 thin wrapper，转发到 helper。
- 这是**唯一保留 SDK 直连的非 adapter 业务入口**，原因：`invoke_chat / invoke_generate` 当前不支持流式 yield；引入 `invoke_stream` 是 Phase B 之外的工作。文档显式承认这一例外。

**测试：** 跑 `tests/test_gemini_client.py` 的流式分支；保留运行时手工验证（streaming UI）。

**风险：** 此 helper 不在 adapter 体系中，需要在 `docs/superpowers/specs/` 里写明白"为什么 gemini_stream 不走 adapter"，让未来巡检不误删。

#### B 阶段完成验收

- `grep "from openai import\|from google import genai" appcore` 只命中 `appcore/llm_providers/`（含 `_helpers/gemini_calls.py`、`_helpers/gemini_stream.py`、`gemini_*_adapter.py`、`openrouter_adapter.py`、`openrouter_image_adapter.py`）。
- `appcore/gemini.py` 的 `generate(...)` 没有 `client.models.generate_content(...)` 字符串。
- `appcore/gemini_image.py` 的 `_generate_via_genai` 不再直接 `genai.Client(...)`。
- `pytest tests/ -q` 全绿。

---

### Phase C：历史兼容入口下线

#### C 阶段目标

- 把上面阶段为了"小步迁移"保留的兼容代码全部清掉。
- 让 `pipeline/translate.py` 真正只剩"业务函数 + invoke_chat 调用"。
- 让 `appcore/gemini.py` 退化为 thin compat shim（或拆掉，仅保 `appcore/llm_models.py` 中的纯枚举）。

#### C 阶段任务拆分

**C-1：删除 `pipeline/translate.py` 的老 provider 兼容。**

- 前提：A-3 之后 1 个 release 内观察 `usage_logs.extra_data->use_case` 字段是否还有命中老 provider 的记录。
- 删除 `_resolve_use_case_provider` 函数及 `_OPENROUTER_PREF_MODELS / _VERTEX_PREF_MODELS` 字典；调用方一律传 `use_case=`。
- 删除 `pipeline/text_translate.py` 对 `_resolve_use_case_provider` 的 import。

**测试：** `pytest tests/test_translate_use_case_binding.py tests/test_pipeline_text_translate.py -q`。

**风险：** 离线工具脚本可能还在传老 provider；提前在 `tools/` 下 grep + 改造。

---

**C-2：`appcore/gemini.py` 拆解。**

- `VIDEO_CAPABLE_MODELS`、`model_display_name` 迁到新文件 `appcore/llm_models.py`。`web/routes/settings.py` 与 `pipeline/video_review.py` 改 import。
- `resolve_config` / `is_configured` / `_binding_lookup` / `_resolve_provider_code` 等全部下线（`appcore/runtime.py` 与 `runtime_v2.py` 改为 `llm_bindings.resolve(use_case)` + `llm_provider_configs.get_provider_config(...)`）。
- `generate(...)` 已是 thin wrapper，确认无业务调用方后删除函数；然后 `appcore/gemini.py` 整体可以删（`generate_stream` 已迁到 `_helpers/gemini_stream.py`，业务直接 import 那里）。

**测试：** 全量 `pytest tests/ -q` + `grep -n "from appcore import gemini\|from appcore.gemini import"` 应为空。

**风险：** runtime 调用 `resolve_config` 是为了拿 `(api_key, model)`；新路径下，runtime 应该不再需要 api_key——它只负责把 use_case 传给 `invoke_*`，由 adapter 自己解析凭据。任何还需要 api_key 的调用点都说明迁移没做干净。

---

**C-3：图片域留尾巴清理。**

- `appcore/gemini_image.py` 的 `_resolve_seedream_credentials` / `_resolve_apimart_*` 等如有仍在直接读 `system_settings`，迁到 `appcore/image_translate_settings.py` 并通过 use_case binding 接管。本期可作为 follow-up 单独 spec。

**测试：** `pytest tests/test_gemini_image.py -q`，外加生产线下 5-channel smoke。

---

**C-4：仓库级巡检。**

- 跑 `grep -rn "OpenAI(\|genai.Client(" appcore pipeline web tools tests --include='*.py'`，期望命中只剩：
  - `appcore/llm_providers/openrouter_adapter.py`
  - `appcore/llm_providers/openrouter_image_adapter.py`（B-4 新增）
  - `appcore/llm_providers/gemini_vertex_adapter.py`
  - `appcore/llm_providers/_helpers/gemini_calls.py`
  - `appcore/llm_providers/_helpers/gemini_stream.py`
  - `appcore/llm_providers/_helpers/vertex_json.py`（A-1 新增）
  - `link_check_desktop/gemini_client.py`（桌面端独立）
  - `scripts/debug_vertex*.py`
- 把这个白名单写入 `docs/superpowers/notes/llm-direct-sdk-allowlist.md`，作为后续 PR review checklist。

#### C 阶段完成验收

- `grep` 白名单巡检通过。
- `pytest tests/ -q` 全绿。
- 线上 1 个 release 内 `usage_logs` 的 `provider` 字段只出现 5 个合规值：`openrouter / doubao / gemini_aistudio / gemini_vertex / gemini_vertex_adc`（外加 `doubao_asr / elevenlabs` 两个非 LLM）。

---

## 5. 跨阶段 contract（迁移期间不允许动摇）

| 契约 | 实施 |
|------|------|
| `usage_logs` 字段 | `use_case_code / module / provider / model / input_tokens / output_tokens / request_units / units_type / cost_cny / cost_source / extra_data->use_case / success` 一律由 `ai_billing.log_request` 写入；不允许业务侧手写 SQL |
| `usage_log_payloads` 字段 | `request_payload`（`type / model / messages|prompt / system / media / response_format / response_schema / network_route_intent / network_estimate`）+ `response_payload`（`text / json / usage / error`），由 `llm_client._log_usage` 统一构造，`_sanitize_messages` 替换 base64 |
| `network_route_intent` | `_PROXY_REQUIRED_PROVIDERS = {anthropic, gemini_*, openai, openrouter}`，`doubao*` 走 `direct_preferred`；迁移阶段不能丢这一字段（运维诊断用） |
| `ProviderConfigError` 中文提示 | "请在 /settings 的「服务商接入」页填写"必须保留；adapter 重写后也要返回该消息 |
| 错误重试 | OpenRouter 网络重试（`_call_with_network_retry`，3 次指数退避）+ Vertex `_is_retryable`（429/500/502/503/504）保留语义 |
| 流式输出 | `generate_stream` 仅在 Phase B 隔离 helper 后保留；不允许新业务再开新的流式直连 |

---

## 6. 风险矩阵

| 风险 | 阶段 | 影响 | 缓解 |
|------|------|------|------|
| adapter 抽 helper 时漏迁某个被反向使用的小函数 | A-1 / B-1 | adapter import 报错 | 用 `pytest -q` + `python -c "import appcore.llm_providers"` 验证；CI 加 import 烟雾 |
| runtime 调用方传 `openrouter_api_key` 用户级覆盖 | A-2 / A-3 | 用户自定义 OpenRouter Key 失效 | 保留 `provider=` 老路径直到 admin 控制台支持 user-level binding；写 release note |
| `appcore/gemini.generate` 迁 thin wrapper 后 ai_billing 字段微变 | B-2 | 计费报表口径变化 | 迁前后做一次 diff（开发库手工跑一次相同 prompt，比对 `usage_logs` 行）|
| `gemini_image.py` 五通道分发 + 三家厂商 ai_billing 字段不一致 | B-4 | image 计费失真 | image 用例 `units_type='images'`，保留 `gemini_image._log_usage` 不强行套通用 `_log_usage`；后续单独 spec |
| 流式调用被遗忘 | B-5 | 字幕生成 / 文案流式预览失效 | 迁移期保留 `_helpers/gemini_stream.py` + 标注；C 期不删 |
| 调用方还在传老 provider 字符串没迁完 | C-1 | 删除老 helper 后线上 500 | 用 `usage_logs.extra_data` 一周观察期；迁移最终 PR 做"老 provider 字符串清零"门禁 |
| adapter 内 `_clients` 缓存重复 | B-1 / C-2 | 同一 provider 起两份 client（内存浪费 / 凭据漂移） | C-2 时合并 `appcore/gemini._clients` 与 `_helpers/gemini_calls._clients` |
| 文档与代码漂移 | 全期 | 后续巡检判断错位 | C-4 把白名单文件 commit 进仓库；CI 加 grep 守门 |

---

## 7. 测试策略汇总

| 测试类别 | 命令 / 文件 | 阶段 |
|---------|------------|------|
| Adapter 单测 | `pytest tests/test_llm_providers_openrouter.py tests/test_llm_providers_gemini.py tests/test_llm_providers_gemini_vertex.py -q` | A / B |
| Client 单测 | `pytest tests/test_llm_client_invoke.py -q` | A / B |
| UseCase / Binding | `pytest tests/test_llm_use_cases_registry.py tests/test_llm_bindings_dao.py tests/test_llm_provider_configs.py -q` | 各阶段 |
| Translate 业务 | `pytest tests/test_localization.py tests/test_translate_use_case_binding.py tests/test_pipeline_text_translate.py tests/test_pipeline_runner.py tests/test_runtime_multi_translate.py tests/test_tts_duration_loop.py tests/test_task_routes.py -q` | A |
| Gemini 业务 | `pytest tests/test_gemini_client.py tests/test_gemini_resolve_use_case.py tests/test_gemini_image.py -q` | B |
| Vertex schema | `pytest tests/test_translate_vertex_schema.py -q` | A |
| 全量 | `pytest tests/ -q` | 每阶段末 |
| Smoke 视频翻译 | 开发库跑 omni / multi / de / fr / 单语 各 1 次 | A-3 末 |
| Smoke 视频分析 | video_score / video_review / shot_decompose / video_csk 各 1 次 | B-3 末 |
| Smoke 图片翻译 | aistudio / cloud / openrouter / doubao / apimart 5 channel 各 1 次 | B-4 末 |

---

## 8. 不动清单（明确跳过的内容）

- `link_check_desktop/`（独立桌面工具）。
- `scripts/debug_vertex.py`、`scripts/debug_vertex_image.py`（调试，不上线）。
- `appcore/llm_provider_configs.py`、`appcore/llm_use_cases.py`、`appcore/llm_bindings.py`（已稳定，不重构）。
- `appcore/api_keys.py`（用户级 key 表，不在本期范围）。
- `pipeline/copywriting.py` 既有 provider picker（用户主动选 provider，UI 透传）：保留 provider 显式传参，不强行 use_case 化。
- `appcore/gemini_image.py` 的 Seedream / APIMART 直连（属于 HTTP 客户端，非 SDK 直连，不在收敛范围）。
- 流式：`appcore.gemini.generate_stream` 与 `_helpers/gemini_stream.py`（暂例外，标注于 B-5）。

---

## 9. 落地依赖与时间线（建议）

阶段顺序硬约束 A → B → C；**每阶段独立 PR**：

- **Phase A**：1 PR 抽 helper（A-1）+ 1 PR 三函数 use_case 入口（A-2）+ N 个小 PR runtime 切换（A-3，每 runtime 1 个）+ 1 PR 老路径删除（A-4）。
- **Phase B**：1 PR helper 抽离（B-1）+ 1 PR `gemini.generate` thin wrapper（B-2）+ N 个小 PR 业务调用方切换（B-3）+ 1 PR image adapter 拆分（B-4）+ 1 PR 流式 helper 隔离（B-5）。
- **Phase C**：1 PR 老 provider 字符串删除（C-1）+ 1 PR `appcore/gemini.py` 拆解（C-2）+ 1 PR image 留尾（C-3）+ 1 PR 巡检白名单 commit（C-4）。

任何阶段被叫停，都必须能停在 commit 边界上、线上保持可用。

---

## 10. 后续 follow-up（不在本期）

1. `invoke_stream` 接口设计：让 `appcore.gemini.generate_stream` 也可以走 adapter，B-5 的 helper 退役。
2. `gemini_image` 的 ai_billing 字段统一：把 `units_type='images'` 用例的 payload 字段塞进 `llm_client._log_usage` 或新设 `_log_image_usage`，统一巡检。
3. user-level binding：让某些 use_case 支持"用户私有 OpenRouter key"，去掉现在 `openrouter_api_key` 旁路参数。
4. 把 `appcore/llm_providers/_helpers/` 下的 helper 升级为独立模块（cred 解析、retry、payload 构造），在 5 个 adapter 间复用更多代码。
5. `link_check_desktop/gemini_client.py` 可以视情况做 `appcore/llm_client` 的 desktop port，但不强求。

---

> **执行入口：** 本 spec 确认后，写 `docs/superpowers/plans/YYYY-MM-DD-llm-client-consolidation-implementation.md`（带 task 粒度的 plan），按 Phase A → B → C 顺序逐步落地。
