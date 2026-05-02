# LLM SDK 直连点白名单

> 配套 [`docs/superpowers/specs/2026-05-01-llm-client-consolidation-design.md`](../specs/2026-05-01-llm-client-consolidation-design.md)。
>
> 后续 PR 加入新的 `from openai import OpenAI` / `from google import genai` /
> `OpenAI(...)` / `genai.Client(...)` 必须在本清单里登记，并在 PR 描述说明
> 为什么不能走 `appcore.llm_client.invoke_chat / invoke_generate`。

## 一、对外业务唯一调用入口

业务代码（`pipeline/*` / `appcore/*_runtime*.py` / `web/routes/*` / `tools/*`）
**唯一允许的 LLM 调用方式**：

```python
from appcore.llm_client import invoke_chat, invoke_generate

result = invoke_chat(
    "video_translate.localize",
    messages=[...],
    user_id=...,
    response_format=...,
    temperature=...,
    max_tokens=...,
    provider_override=...,   # 可选，A/B 评测脚本用
    model_override=...,      # 可选
)
# result 形态：{"text", "json", "raw", "usage"}
```

不允许：
- `from openai import OpenAI` + `OpenAI(...)`
- `from google import genai` + `genai.Client(...)`
- `from appcore import gemini` + `gemini.generate(...)`（generate 是历史兼容入口，新代码不要用）

## 二、白名单文件清单

只有这些文件允许直接 import OpenAI / google.genai SDK（已被
`tests/test_architecture_boundaries.py::test_direct_provider_sdk_imports_stay_in_adapter_or_legacy_files`
强制守卫）：

| 文件 | 角色 | 允许的直连内容 |
|------|------|----------------|
| `appcore/llm_providers/openrouter_adapter.py` | OpenRouter / 豆包 chat adapter | `OpenAI(api_key, base_url)` |
| `appcore/llm_providers/gemini_aistudio_adapter.py` | Google AI Studio adapter | `genai.Client(api_key=...)` |
| `appcore/llm_providers/gemini_vertex_adapter.py` | Vertex AI（Express + ADC）adapter | `genai.Client(vertexai=True, ...)` |
| `appcore/llm_providers/_helpers/vertex_json.py` | text → Vertex `generate_content` 转换 + retry | `genai.Client(vertexai=True, ...)` |
| `appcore/llm_providers/_helpers/gemini_calls.py` | Gemini `_build_config` / `_build_contents` / `get_image_client` 等共享 helper | `genai.Client(...)`（image 通道复用） |
| `appcore/llm_providers/_helpers/openrouter_image.py` | OpenRouter image2 客户端薄封装 | `OpenAI(api_key, base_url)` |
| `appcore/llm_providers/_helpers/openai_compat.py` | OpenAI 兼容（OpenRouter / 豆包 LLM）客户端薄封装 | `OpenAI(api_key, base_url)` |
| `appcore/gemini_image.py` | image generate 顶层入口（5 channel 路由） | 不再直 import，只 import 上面 helper |
| `appcore/gemini.py` | 历史 generate / generate_stream / resolve_config 兼容入口 | `genai.Client(...)`（兼容老 service= 路径，待 follow-up 删除） |
| `pipeline/translate.py` | 历史 generate_localized_translation 兼容入口 | C-3 后不再直接 `OpenAI(...)`；通过 `_helpers/openai_compat.make_openai_compat_client` 创建 |
| `pipeline/video_csk.py` / `video_review.py` / `video_score.py` | 视频分析业务 | 仅 import gemini_api 拿 `resolve_config` 等读取工具，不再直接创建客户端（Phase B-3 完成迁移） |
| `link_check_desktop/gemini_client.py` | 独立桌面端工具（不属于 web 路径） | `genai.Client(api_key=...)` |
| `scripts/debug_vertex.py` / `scripts/debug_vertex_image.py` | 调试脚本 | `genai.Client(...)` |

## 三、巡检命令

新增/修改 LLM 相关代码后，至少跑：

```bash
# 1) Python 守卫测试
pytest tests/test_architecture_boundaries.py -v

# 2) grep 抽查（以下输出必须全在白名单内）
git grep -nE "^(from openai import|^from google import genai|^from google\.genai)" \
    appcore pipeline web tools
git grep -nE "\bOpenAI\s*\(|\bgenai\.Client\s*\(" \
    appcore pipeline web tools
```

如果 grep 命中清单外的文件 → PR 必须解释为什么需要新增白名单，并在
`tests/test_architecture_boundaries.py` 的 `allowed_paths` 同步加入。

## 四、过渡期保留入口（待 Phase C-3 删除）

下面这些是 2026-04-19 LLM 调用统一时为兼容老调用方而保留的入口，新代码
不要使用：

| 入口 | 仍存原因 | 删除条件 |
|------|---------|---------|
| `pipeline.translate.resolve_provider_config` / `_call_openai_compat` / `_call_vertex_json`（re-export） | `pipeline/copywriting.py`、`web/routes/copywriting.py`、`web/routes/text_translate.py` 仍依赖；tools 评测 A/B 切 provider 已通过 `provider_override`/`model_override` 走 `invoke_chat`，但用户级 OpenRouter Key 覆盖路径未迁 | C-2: copywriting / text_translate 改 `invoke_chat`；C-3: 删除 |
| `pipeline.translate.generate_localized_translation` / `generate_tts_script` / `generate_localized_rewrite`（`use_case=` 入口已存在） | A-3 阶段 runtime / web 调用方已切 `use_case=`，但 `provider=` 老入参仍被 tools 评测和 `pipeline/text_translate.py` 兼容路径使用 | 所有调用方都不传 `provider=` 后删除 else 分支 |
| `appcore.gemini.generate` / `generate_stream` / `resolve_config` / `is_configured` | runtime.py / runtime_v2.py / video_review.py 仍 import；其中 generate 已无业务调用方但还在测试中 | runtime / video_review 改为直接读 `llm_bindings.resolve(use_case)` 后删除 |
| `appcore.gemini.VIDEO_CAPABLE_MODELS` / `model_display_name`（C-1 已 re-export 自 `appcore.llm_models`） | 历史调用方还在 import `appcore.gemini.*`；新代码应直接 import `appcore.llm_models` | 灰度后 grep 确认无 `from appcore.gemini import VIDEO_CAPABLE_MODELS\|model_display_name` 后删 re-export |

## 五、何时更新本文档

- 新增 adapter 或 helper 文件 → 加入二节表格
- 新增过渡期兼容入口 → 加入四节表格
- Phase C-2 / C-3 完成、过渡期入口删除 → 把对应行从四节移除（或全表删除）
- 任何 PR 修改 `tests/test_architecture_boundaries.py::test_direct_provider_sdk_imports_stay_in_adapter_or_legacy_files` 的 `allowed_paths` → 同步更新二节表格

最后更新：2026-05-02
