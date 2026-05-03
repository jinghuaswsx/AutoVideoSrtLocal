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

**Phase D 终态：业务代码 0 处直接 import openai / google.genai / appcore.gemini。**

不允许：
- `from openai import OpenAI` + `OpenAI(...)`
- `from google import genai` + `genai.Client(...)`
- `from appcore import gemini` + `gemini.generate(...)`（D-3 已删除整个文件）
- `from pipeline.translate import resolve_provider_config / _call_openai_compat / _resolve_use_case_provider / _OPENROUTER_PREF_MODELS / _VERTEX_PREF_MODELS`（D-4 已删除全部）

## 二、白名单文件清单

只有这 7 个文件允许直接 import openai / google.genai SDK（被
`tests/test_architecture_boundaries.py::test_direct_provider_sdk_imports_stay_in_adapter_or_legacy_files`
强制守卫）：

| 文件 | 角色 | 允许的直连内容 |
|------|------|----------------|
| `appcore/llm_providers/openrouter_adapter.py` | OpenRouter / 豆包 chat adapter | `OpenAI(api_key, base_url)` |
| `appcore/llm_providers/gemini_aistudio_adapter.py` | Google AI Studio adapter | `genai.Client(api_key=...)` |
| `appcore/llm_providers/gemini_vertex_adapter.py` | Vertex AI（Express + ADC）adapter | `genai.Client(vertexai=True, ...)` |
| `appcore/llm_providers/_helpers/openai_compat.py` | OpenAI 兼容客户端薄封装 | `OpenAI(api_key, base_url)` |
| `appcore/llm_providers/_helpers/openrouter_image.py` | OpenRouter image2 客户端薄封装 | `OpenAI(api_key, base_url)` |
| `appcore/llm_providers/_helpers/gemini_calls.py` | Gemini `_build_config` / `_build_contents` / `get_image_client` 等共享 helper | `genai.Client(...)` |
| `appcore/llm_providers/_helpers/vertex_json.py` | text → Vertex `generate_content` 转换 + retry | `genai.Client(vertexai=True, ...)` |

**白名单外但项目内仍直连的（不在守卫扫描范围内的目录）**：
| 文件 | 角色 |
|------|------|
| `link_check_desktop/gemini_client.py` | 独立桌面端工具（不属于 web 路径）；守卫只扫 appcore/pipeline/web/tools |
| `scripts/debug_vertex.py` / `scripts/debug_vertex_image.py` | 调试脚本；守卫只扫 appcore/pipeline/web/tools |

## 三、巡检命令

新增/修改 LLM 相关代码后，至少跑：

```bash
# 1) Python 守卫测试（包含 google.genai 检查）
pytest tests/test_architecture_boundaries.py::test_direct_provider_sdk_imports_stay_in_adapter_or_legacy_files -v

# 2) grep 抽查（以下输出必须全在白名单内）
git grep -nE "^(from openai import|^from google import genai|^from google\.genai)" \
    appcore pipeline web tools
git grep -nE "\bOpenAI\s*\(|\bgenai\.Client\s*\(" \
    appcore pipeline web tools
```

如果 grep 命中清单外的文件 → PR 必须解释为什么需要新增白名单，并在
`tests/test_architecture_boundaries.py` 的 `allowed_paths` 同步加入。

## 四、过渡期保留入口

| 入口 | 仍存原因 | 删除条件 |
|------|---------|---------|
| `pipeline.translate.get_model_display_name(provider, user_id)` | runtime_de/fr/omni/multi/runtime/_helpers.py + web/routes/task.py 仍调用它显示 model_tag。D-4 重写为 thin wrapper，内部用 `appcore.llm_models.LEGACY_PROVIDER_MODEL_MAP` + binding 解析，不再依赖删除的 `resolve_provider_config`。 | runtime / web 改为直接读 binding 后删除（不阻塞 spec 验收） |
| `pipeline.translate.parse_json_content` / `_call_vertex_json` / `_extract_gemini_schema` / `_split_oai_messages` / `_strip_unsupported_schema` / `_GEMINI_VERTEX_UNSUPPORTED_SCHEMA_KEYS`（re-export） | `web/routes/text_translate.py`、`tests/test_pipeline_robustness.py`、`tests/test_translate_vertex_schema.py`、`tests/test_llm_providers_gemini_vertex.py` 仍用 `from pipeline.translate import` 这些 helper | 调用方迁到直接 `from appcore.llm_providers._helpers.vertex_json import ...` 后删 re-export |
| `pipeline.translate.generate_localized_translation/tts_script/rewrite` 的 `provider=` / `openrouter_api_key=` kwargs（已废弃，值忽略） | runtime_de/fr/omni/multi/runtime 旧调用方仍传 `provider=`；删除会引发 TypeError | 灰度 1 个 release 后删除老 kwarg + 调用方清理 |

## 五、何时更新本文档

- 新增 adapter 或 helper 文件 → 加入二节表格
- 新增过渡期兼容入口 → 加入四节表格
- 过渡期入口删除 → 把对应行从四节移除
- 任何 PR 修改 `tests/test_architecture_boundaries.py` 的 `allowed_paths` → 同步更新二节表格

最后更新：2026-05-03
