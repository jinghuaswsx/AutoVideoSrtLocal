# LLM 视频/媒体上传优化设计

日期：2026-05-14

## 文档锚点

- `AGENTS.md` §主题指引：LLM 调用统一入口为 `appcore.llm_client`；新业务在 `appcore/llm_use_cases.py` 注册 use case。
- `docs/superpowers/specs/2026-05-01-llm-client-consolidation-design.md` §1：业务代码通过 `use_case -> adapter -> SDK` 调用，不再绕开统一入口。
- `docs/superpowers/specs/2026-05-14-omni-shot-decompose-480p-preprocess-design.md`：`shot_decompose.run` 已验证 OpenRouter base64 视频请求在 480p/15fps/600k/去音频后显著降体积与耗时。
- 本 spec 固化 2026-05-14 的新增要求：复查全项目所有调用大模型时上传视频/媒体的模块，按功能分批优化；每个功能完成后单独跑测试，全部验证通过后再执行上线流程。
- 2026-05-14 追加决策：采用“方案 A”，所有 LLM 视频预处理默认先统一为当前 Omni 镜头分析配置：480p、15fps、H.264 600k。需要音频的场景只保留 AAC mono 音频，不再默认使用 700k 或动态 720p/18MiB 视频策略；后续若某个 use case 识别质量不足，再单独开 spec 调整。

## 背景

项目中多模态 LLM 调用已经大多收敛到 `appcore.llm_client.invoke_generate()`，但本地媒体进入不同 provider adapter 后的传输方式不同：

- OpenRouter：本地图片/视频读成 `data:*;base64,<payload>` 放入请求体，视频体积会直接放大 JSON 请求体。
- Doubao：视频/图片先上传到公网交换 URL，再由 Ark Responses API 拉取。
- Gemini AI Studio：图片 inline，小视频/视频走 Gemini Files API。
- Gemini Vertex / Vertex ADC：当前媒体统一 inline bytes，视频会受 inline 体积限制影响。

既有 `shot_decompose.run` 已单独实现 480p/15fps/低码率/去音频预处理，但同类视觉型视频调用仍有请求体过大、上传耗时长、失败后缺少清晰降级和 debug 信息的问题。

## 目标

1. 建立共享的 LLM 视频输入优化层，业务调用在传给 `llm_client` 前得到一个“仅用于 LLM 请求”的轻量视频路径。
2. 对只做视觉理解、不需要音频的调用，优先使用 480p、15fps、低码率、去音频。
3. 对需要判断语音/音画/TTS 的调用，降低分辨率、帧率、码率，但保留压缩音频。
4. 对 OpenRouter base64 视频请求，重点降低实际请求体字节数。
5. 对 Gemini 原生通道，保留 AI Studio Files API 现状；Vertex/ADC inline 通道也先按方案 A 默认 480p/15fps/600k 处理，失败时降级使用原路径并记录 warning。
6. 不改变现有 use case 的 provider/model 默认绑定，不改变 prompt 语义，不改变业务输出 schema。
7. 任何压缩、裁剪、转码失败都不得直接让业务任务失败；应回退到原媒体、已有 keyframes 或文本兜底路径。
8. 每个功能单独 TDD：先写失败测试，再实现，再跑该功能相关测试；所有批次通过后再按 AGENTS 发布流程上线。

## 非目标

- 不新增 provider，不迁移模型绑定，不改 `/settings` 模型分配语义。
- 不把原视频替换为压缩视频用于 ASR、TTS、合成、导出或用户下载。
- 不删除现有 Gemini Files API 上传逻辑。
- 不优化普通业务文件上传路由、TOS 备份、ASR 音频上传、Seedance 视频生成结果下载。
- 不连接 Windows 本机 MySQL，不重启服务；仅在最终明确上线阶段执行发布命令。

## 现状清单

| 功能入口/任务类型 | use_case_code | 默认 provider/model | 当前上传方式 | 媒体 | 现有压缩/降帧/去音频 | 请求体风险 | 是否复用 480p/低码率/去音频 | 优先级 |
|---|---|---|---|---|---|---|---|---|
| Omni 分镜 `pipeline/shot_decompose.py` | `shot_decompose.run` | openrouter / `google/gemini-3-flash-preview` | OpenRouter base64 | 视频 | 已 480p/15fps/600k/去音频 | 低 | 已复用，改为共享 helper | P1 |
| AV 句级画面笔记 `pipeline/shot_notes.py` | `video_translate.shot_notes` | openrouter / `google/gemini-3.1-pro-preview` | OpenRouter base64 | 视频 | 无 | 高 | 是，去音频 | P0 |
| 文案生成 `pipeline/copywriting.py` | `copywriting.generate` | openrouter / `google/gemini-3-flash-preview` | OpenRouter base64 或 Doubao URL | 视频/图片 | 50MB base64 上限，无视频压缩 | 高 | 视频优先压缩；OpenRouter 去音频，Doubao 按模型 URL 拉取策略传轻量视频 | P0 |
| AI 视频分析 `pipeline/video_ai_review.py` + `appcore/video_ai_review.py` | `video_ai_review.assess` | gemini_vertex_adc / `gemini-3.1-pro-preview` | Vertex inline bytes | 源/目标视频、图片 | media_item 下载后曾有 720/480 动态压缩；task 视频仅 warn | 高 | 保留音频，按方案 A 统一 480p/15fps/600k | P0 |
| 素材评估 `appcore/material_evaluation.py` | `material_evaluation.evaluate` | gemini_vertex_adc / `gemini-3.1-pro-preview` | Vertex inline；可绑定 OpenRouter base64 | 图片 + 15s 视频 | 只 `-t 15 -c copy` | 中高 | 保留音频，15s 后转低码率 | P0 |
| 新品评估 `appcore/new_product_review.py` | `material_evaluation.evaluate` | gemini_vertex_adc / `gemini-3.1-pro-preview` | 同素材评估 | 图片 + 15s 视频 | 复用素材评估 15s copy | 中高 | 跟随素材评估 | P0 |
| 推送质量视频前 5 秒 `appcore/push_quality_checks.py` | `push_quality.check` | openrouter / `google/gemini-3.1-flash-lite-preview` | OpenRouter base64 | 视频+音频 | 5s copy | 中 | 保留音频，5s 后转低码率 | P1 |
| Omni AV 成片理解 `pipeline/omni_av_sync_audit.py` | `omni_av_sync.understand` | doubao / `doubao-seed-2-0-lite-260215` | TOS public URL | 视频+音频 | 无 | 中 | 保留音频，传轻量 URL | P1 |
| 成品视频评分 `pipeline/video_score.py` | `video_score.run` | gemini_aistudio / `gemini-3.1-pro-preview` | Gemini Files API | 视频+音频 | 无 | 中 | 保留音频，降低 Files API 上传体积 | P1 |
| CSK 深度分析 `pipeline/video_csk.py` | `video_csk.analyze` | gemini_aistudio / `gemini-3.1-pro-preview` | Gemini Files API | 视频+音频 | 无 | 中 | 保留音频，降低 Files API 上传体积 | P1 |
| 视频评测 `pipeline/video_review.py` | `video_review.analyze` | gemini_aistudio / `gemini-3.1-pro-preview` | Gemini Files API | 视频+音频 | 无 | 中 | 保留音频，降低 Files API 上传体积 | P1 |
| 图片检测/链接审核/同图判定 | `image_translate.detect`, `link_check.analyze`, `link_check.same_image`, `push_quality.check` | Gemini/OpenRouter | inline bytes/base64 | 图片 | 无 | 低 | 不做视频策略，仅保持 debug/估算一致 | P2 |

## 设计

### 共享优化器

新增 `appcore/llm_media_optimizer.py`，只负责为 LLM 请求准备轻量媒体，不负责业务文件生命周期。核心数据结构：

- `VideoOptimizationPolicy`：描述 `max_height`、`fps`、`video_bitrate`、`maxrate`、`bufsize`、`drop_audio`、`audio_bitrate`、`target_bytes`、`timeout_seconds`、`suffix_label`。
- `OptimizedMedia`：包含 `original_path`、`llm_path`、`optimized`、`cleanup_path`、`original_bytes`、`llm_bytes`、`command`、`error`、`policy_name`。
- `prepare_video_for_llm(video_path, policy, output_dir=None)`：成功返回优化产物；失败返回原路径并记录错误。
- `cleanup_optimized_media(media)`：删除仅用于 LLM 请求的临时产物。

默认策略：

- `visual_480p_silent`：`scale=-2:min(480\,ih),fps=15`，H.264 `600k/maxrate 800k/bufsize 1200k`，`-an`。
- `review_480p_audio`：`scale=-2:min(480\,ih),fps=15`，H.264 `600k/maxrate 800k/bufsize 1200k`，AAC mono `64k`。
- `short_clip_audio`：用于 5s/15s 已裁剪片段，`480p/15fps/600k`，AAC mono `64k`。
- `vertex_inline_audio`：同样默认 `480p/15fps/600k`，保留 AAC mono；不再先走 720p 或按 18MiB 动态抬高码率。

所有策略都必须满足：源文件缺失不吞掉业务原有错误；ffmpeg 缺失、超时、非零退出、输出为空时回退原路径；debug payload 能看到实际传给 LLM 的路径。

### 模块接入规则

- `shot_decompose.run`：迁到共享 `visual_480p_silent`，保持现有行为和测试。
- `video_translate.shot_notes`：使用 `visual_480p_silent`，因为 prompt 使用 ASR 时间轴，画面笔记不需要音频。
- `copywriting.generate`：视频输入使用 `visual_480p_silent`；如果压缩失败且 OpenRouter base64 仍超过既有 50MB 上限，降级到 keyframes + 商品图 + 文本，不让任务失败。
- `video_ai_review.assess`：源/目标视频使用 `vertex_inline_audio`，按方案 A 默认 480p/15fps/600k，保留音频以判断 TTS/音画；产品图不改。
- `material_evaluation.evaluate` / `new_product_review.evaluate_product`：继续先取 15s 片段，再用 `short_clip_audio` 降体积；失败回退 15s copy 或原视频。
- `push_quality.check` 视频：继续先取 5s，再用 `short_clip_audio`；失败回退 5s copy。
- `omni_av_sync.understand`：用 `review_480p_audio` 后再走 Doubao URL 上传；失败用原视频 URL 上传。
- `video_score.run` / `video_csk.analyze` / `video_review.analyze`：用 `review_480p_audio` 降低 Gemini Files API 上传体积；保留音频，因为评分、音画质量、voiceover 字段依赖音频。

### Debug 与计费 payload

- `llm_client.invoke_generate()` 的 `request_payload.media` 记录实际传入 LLM 的路径。
- 模块级 debug payload 的 `input_snapshot` 增加 `original_video_path`、`llm_video_path`、`optimized`、`policy_name`、`original_bytes`、`llm_bytes`、`ffmpeg_command`、`optimization_error`。
- OpenRouter base64 请求不把完整 data URL 写入 debug；继续只保存占位或截断信息。
- `network_estimate` 以实际 LLM path 估算，便于在 usage payload 中看见优化后体积。

## 测试要求

每个功能必须独立 TDD：

1. 先写该功能失败测试，断言 LLM 收到优化后路径、ffmpeg 命令包含预期参数、失败时回退、debug payload 记录原路径与 LLM 路径。
2. 运行该测试，确认因功能缺失失败。
3. 实现最小代码。
4. 运行该功能相关测试通过。
5. 进入下一个功能前，记录测试命令和结果。

建议测试文件：

- `tests/test_llm_media_optimizer.py`
- `tests/test_shot_decompose.py`
- `tests/test_shot_notes.py`
- `tests/test_copywriting_pipeline.py` 或 `tests/test_pipeline_robustness.py`
- `tests/test_material_evaluation.py`
- `tests/test_push_quality_checks.py`
- `tests/test_video_csk.py`、新增 `tests/test_video_score.py`、`tests/test_video_review_pipeline.py`
- `tests/test_llm_client_invoke.py`
- `tests/test_llm_providers_openrouter.py`
- `tests/test_llm_providers_gemini_vertex.py`

最终汇总验证至少包含：

```bash
pytest tests/test_llm_media_optimizer.py \
  tests/test_shot_decompose.py \
  tests/test_shot_notes.py \
  tests/test_copywriting_pipeline.py \
  tests/test_pipeline_robustness.py \
  tests/test_material_evaluation.py \
  tests/test_push_quality_checks.py \
  tests/test_video_csk.py \
  tests/test_llm_client_invoke.py \
  tests/test_llm_providers_openrouter.py \
  tests/test_llm_providers_gemini_vertex.py -q
```

若新增或命中 route 行为，还需按 AGENTS 验证未登录 302、登录后 200、POST CSRF。本次优化默认不新增路由。

## 分批上线门禁

- P0、P1、P2 每批内部按功能单独测试，失败不得进入下一功能。
- 全部功能完成后跑汇总验证；若汇总失败，先修复再发布。
- 用户已明确要求“确保没问题以后上线”，因此最终允许按 `AGENTS.md` 发布节执行测试环境与生产环境上线；上线前不连接 Windows 本机 MySQL，不调用 `deploy/publish.sh`。
- 发布验收必须看到测试环境服务 active + HTTP 200/302，生产服务 active + HTTP 200/302；若 404/500/000 或服务非 active，停止并回报。

## 剩余风险

- 压缩视频可能降低 OCR 或细节识别准确度；方案 A 先统一到 480p/15fps/600k，若后续发现细节不足，可按 use case 单独调整。
- Gemini Vertex inline 对长视频仍可能超过 inline 限制；方案 A 优先统一压缩参数，失败回退原路径并保留业务原错误语义。若长视频仍高频超限，再单独评估更低码率或分段策略。
- Doubao URL 拉取依赖临时公网交换，压缩只减少对象大小，不改变凭据或 URL 有效期。
