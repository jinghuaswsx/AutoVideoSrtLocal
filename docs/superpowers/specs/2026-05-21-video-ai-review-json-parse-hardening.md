# AI 视频分析 JSON 解析容错修复

## 背景

用户在“选品中心 → 明空选品 → 对应素材库 → 视频卡片底部 AI 视频分析”点击按钮后，前端弹窗显示：

`评估失败: Unterminated string starting at: line 70 column 7 ...`

该入口复用素材管理视频卡片的 AI 视频分析链路：前端调用 `/medias/api/items/<id>/video-ai-review/run`，后端通过 `appcore.video_ai_review` 调用 `pipeline.video_ai_review.assess`，再走 LLM provider 的 `invoke_generate`。

## 根因

Gemini 多模态调用配置了 JSON schema，但线上仍可能返回非严格 JSON 或 markdown code block 包裹内容。当前部分路径在 `response_schema` 场景下直接 `json.loads(resp.text)`，解析异常会被写入 `video_ai_reviews.error_text` 并原样透传到前端，导致用户看到底层 JSONDecodeError，而不是可理解的 AI 输出格式错误。

## 修复目标

1. AI 视频分析应能解析常见 markdown code block 包裹的 JSON。
2. 当模型返回截断或非法 JSON 时，后端应保存清晰、稳定的错误文案，避免把 `Unterminated string...` 这类底层异常直接作为主要提示。
3. 保留 raw response 预览用于排查，但不扩大前端交互范围。
4. 不改变素材管理视频卡片 UI、路由权限、CSRF 规则和数据库结构。

## 改动范围

- `pipeline/video_ai_review.py`
  - 增加 AI 视频分析专用 JSON 提取 helper。
  - `assess()` 在 `result["json"]` 缺失时使用 helper 解析 `result["text"]`。
  - 解析失败时抛出业务语义错误，附带短 raw 预览。
- Gemini provider adapter 只在必要时调整 schema fallback 行为，确保可解析 fenced JSON 不会失败。
- 测试覆盖 pipeline 解析行为和 provider schema fallback 行为。

## 验证

- `pytest tests/test_video_ai_review_pipeline.py tests/test_llm_providers_gemini_vertex.py tests/test_llm_providers_gemini_aistudio.py tests/test_pipeline_robustness.py::TestVideoReviewJsonParse -q`
- `python3 -m compileall pipeline/video_ai_review.py appcore/llm_providers/gemini_vertex_adapter.py appcore/llm_providers/gemini_aistudio_adapter.py`
- `git diff --check`
