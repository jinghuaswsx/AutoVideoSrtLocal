# 产品 AI 评估视频上传与请求体积方案

最后更新：2026-04-28

## 背景

产品维度 AI 评估目前需要同时提供商品主图、商品链接、英语短视频和目标语种列表。此前失败的核心风险不是单纯视频时长，而是视频被 Base64 内联到请求体后会膨胀约 4/3；例如 18.6 MB 的 15 秒 MP4，视频 Base64 本身约 24.9 MB，再叠加图片、prompt、schema 和 JSON 包装后容易触发网关或上游解析失败。

本次代码先做了短期稳定方案：

- 视频先从 0 秒截到最多 15 秒。
- 估算主图、视频、prompt、system、schema 进入请求后的 Base64 请求体积。
- 目标请求体控制在 10 MB 内，硬上限按 15 MB 处理。
- 超出目标时，将 15 秒视频重编码为 H.264/AAC，最高约 720p，视频码率按剩余预算动态计算并限制在 500 kbps 到 4 Mbps。
- “视频评估”默认模型通道切到 Vertex AI 的 `gemini-3.1-pro-preview`。
- Google Search grounding 不直接和最终结构化多模态评估混用，而是在同一次产品评估里先走一个 Vertex Google Search 预检，再把联网摘要放入最终图片+视频评估 prompt，避免 Search tool 与结构化 JSON / 多模态组合的兼容风险。

## 官方限制依据

Google AI Developer 文档的 Gemini Video Understanding 页面列出四种视频输入方式：Files API、Cloud Storage Registration、Inline Data 和 YouTube URLs。其中 Inline Data 标注为小文件，适合 100 MB 以下和短视频；同页代码示例仍提示 “Only for videos of size <20Mb”，并明确建议当总请求体超过 20 MB 时使用 Files API。参考：[Gemini API Video Understanding](https://ai.google.dev/gemini-api/docs/video-understanding)。

Files API 文档说明：当总请求大小超过 100 MB 时应使用 Files API；Files API 项目级存储上限 20 GB，单文件上限 2 GB，文件保存 48 小时。参考：[Gemini API Files API](https://ai.google.dev/gemini-api/docs/files)。

Vertex AI 的视频理解文档支持通过 `Part.from_uri(file_uri="gs://...", mime_type="video/mp4")` 传入 Cloud Storage 文件；Gemini 3.1 Pro preview 支持视频理解，带音频最长约 45 分钟，无音频最长约 1 小时，单 prompt 最多 10 个视频。参考：[Vertex AI Video Understanding](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/multimodal/video-understanding)。

Vertex AI 的模型请求参考说明 `fileData.fileUri` 可指向 Cloud Storage、公开 HTTP URL 或 YouTube URL；HTTP URL 的音频、视频、文档限制为 15 MB，而 Cloud Storage URI 对部分 Gemini 2.0 模型列出 2 GB 限制。参考：[Vertex AI Generate Content API](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/model-reference/inference)。

Google Search grounding 官方示例通过 `types.Tool(google_search=types.GoogleSearch())` 接入，Gemini 3.1 Pro Preview 在支持列表中。Grounded response 会带 `groundingMetadata`，其中可包含 web search queries、grounding chunks 和 Search Suggestions。参考：[Vertex AI Grounding with Google Search](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/grounding/grounding-with-google-search) 与 [Gemini API Google Search](https://ai.google.dev/gemini-api/docs/google-search)。

## 推荐的中长期方案

### 方案 A：Vertex AI + Google Cloud Storage URI

这是产品评估后续最推荐的正式方案。

流程：

1. 本地素材仍保留在现有 `appcore.local_media_storage`。
2. 调用 Gemini Vertex 适配器前，如果视频超过内联预算，先上传到指定 GCS bucket，例如 `gs://autovideosrt-gemini-staging/material-evaluation/{product_id}/{hash}.mp4`。
3. 生成内容请求里不放 Base64，改用 `types.Part.from_uri(file_uri=gcs_uri, mime_type="video/mp4")`。
4. 主图可以继续 inline，小图不需要进入 GCS；如果主图也较大，再统一走 GCS。
5. 在 `usage_log_payloads` 里记录 `gcs_uri`、文件大小、hash、上传耗时和是否复用。
6. 给 GCS bucket 设置生命周期，例如 2 到 7 天自动删除临时素材。

优点：

- 避开 Base64 请求体膨胀和网关报文限制。
- 与 Vertex AI 官方平台匹配，适合服务端生产环境。
- 可按 hash 复用同一视频，减少重复上传。
- 后续可支持更长视频，用 `videoMetadata` 控制 0 到 15 秒片段，而不是一定物理截断。

需要开发：

- 新增 `appcore/google_media_staging.py`，封装 `upload_for_gemini(path, mime_type, namespace, ttl_days)`。
- Provider 配置新增 `gemini_cloud_image.extra_config.gcs_bucket` 或独立 system setting。
- `appcore.gemini._to_part()` 根据 Vertex backend 和配置决定：小图 inline，大视频走 GCS URI。
- 新增表或缓存字段记录 `sha256 -> gcs_uri -> expires_at`，避免重复上传。
- 单元测试覆盖：小图 inline、大视频 GCS、上传失败回退为重编码、payload 日志脱敏。

### 方案 B：Gemini Files API

适合 AI Studio / Gemini Developer API 通道，或者未来如果 Vertex 客户端稳定支持同样的 `client.files.upload` 语义，也可以作为补充。

流程：

1. 用 `client.files.upload(file=path)` 上传。
2. 轮询 `files.get` 等待状态不再是 `PROCESSING`。
3. 用返回的 `file.uri` 放入 `generate_content`。
4. 文件 48 小时自动过期，也可主动 `files.delete`。

优点：

- API 简单，不需要自己维护 GCS bucket。
- 官方明确支持 20 GB 项目级、2 GB 单文件、48 小时保留。

限制：

- 更偏 Gemini Developer API / AI Studio 形态；当前用户明确希望用 Vertex AI 官方平台，所以它不作为第一优先级。
- 文件只保留 48 小时，不适合长期复用。
- 需要确认服务端当前 Vertex 凭据模式下 `google-genai` 的 `client.files.upload` 是否行为稳定。

## 当前代码与下一步

当前代码已经先把易失败的 Base64 请求体问题收口：即使仍走 OpenRouter 或其他内联通道，也会优先把 15 秒视频压到预算内。切到 Vertex 后，短期仍保留这个压缩层，作为上游报文、代理或 SDK 差异的兜底。

下一步建议优先做方案 A：

1. 在测试 GCP 项目建 GCS 临时 bucket，并给 Vertex AI Service Agent 和服务端运行身份授予读写权限。
2. 增加 `google-cloud-storage` 依赖，或者直接用 `google.cloud.storage` 已有环境依赖。
3. 改造 Gemini Vertex adapter：视频优先 GCS URI，只有很小且非视频素材才 inline。
4. 添加运维开关：`gemini_vertex_media_mode = auto | inline | gcs | files_api`，默认 `auto`。
5. 在测试环境用两个产品验证：usage log 中 provider 为 `gemini_vertex`，request payload 不再出现巨大 Base64，response payload 能看到 grounding metadata 或联网预检日志。
