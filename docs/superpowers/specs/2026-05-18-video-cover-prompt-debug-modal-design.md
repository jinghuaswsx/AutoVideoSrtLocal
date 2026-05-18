# 文案封面提示词调试弹窗增强设计

日期：2026-05-18
状态：已实现

## 文档锚点

- `AGENTS.md#Verification`：改动后必须跑相关 pytest，并检查未登录 302、登录后 200、POST 带 CSRF。
- `AGENTS.md#硬红线`：文档驱动代码；本次实现必须以本规格和既有文案封面规格为锚点。
- `web/templates/CLAUDE.md#CSRF / 路由守卫`：模板里的 mutating fetch 必须带 `X-CSRFToken`；返回明文 API key 的调试接口只允许超级管理员访问。
- `web/static/CLAUDE.md#Ocean Blue 设计系统`：弹窗、Tab、按钮、输入框沿用后台蓝色管理系统视觉，不引入紫色。
- `docs/superpowers/specs/2026-05-14-video-cover-generation-design.md#1.2 过程可视化与结构化结果`：每张步骤卡片的「提示词」弹窗展示请求输入、模型配置、媒体输入摘要、prompt/messages、原始返回和结构化结果。
- `docs/superpowers/specs/2026-05-15-video-cover-copy-format-overlay-design.md#可审计性`：第 4 步必须保存每张封面实际使用的 prompt 和标准化文案，`step_requests.cover_generation.image_prompts` 是实际请求。
- `docs/superpowers/specs/2026-05-15-video-cover-image-provider-concurrency-design.md#验收`：状态接口和“全部报文预览”能看到第四步实际使用的 `provider`、`model_id` 和 `execution_mode`。

## 背景

文案封面详情页当前的「提示词」按钮只用一个 `pre` 展示 `{request, result}` JSON。实际排查封面生成时，需要同时看清结构化输入、真正请求图片模型的完整报文、模型返回数据和原始响应，并能用同一份报文临时换 URL/API key 调试生成新结果。

本次只增强详情页提示词弹窗和封面生成调试能力，不改变四步自动执行链路、默认模型配置、封面生成 prompt 合同、最终图片保存规则或项目列表行为。

## 目标

1. 提示词弹窗放大到接近截图红框范围，桌面端宽度约为视口 80%，左右各保留约 10% 空余；高度不超过视口，内部滚动。
2. 弹窗顶层新增 `请求` / `结果` Tab。
3. `请求` Tab 内新增二级 Tab：`请求数据` / `完整报文`。
4. `结果` Tab 内新增二级 Tab：`返回数据` / `返回结果报文`。
5. `请求数据` 用结构化方式展示本步骤输入，文字、图片、视频、参考帧、文案、模型配置逐项列清楚。
6. `完整报文` 展示请求大模型的完整可复现报文，包括请求 URL、method、headers、Authorization/API key、body/form data、文件字段或媒体数据摘要。
7. `完整报文` 底部新增调试窗口，可输入新的请求 URL 和 API key，用同一份报文发起一次调试生成，展示新结果。
8. `返回数据` 结构化展示模型返回后的业务数据，例如封面、文案、参考图、模型、耗时、错误。
9. `返回结果报文` 直接展示完整返回报文，使用 JSON 格式化，方便查看嵌套结构。

## 非目标

- 不把调试生成的新结果写回项目正式 `state_json.result.covers`。
- 不新增单图重试、任务取消或覆盖正式封面结果。
- 不改变封面生成 prompt 内容、图片后处理、`1080x1920` 输出规则。
- 不开放给非超级管理员；不新增普通用户访问面。
- 不把 API key 长期持久化到 `state_json`、项目 artifact 或日志文件。

## 设计

### 弹窗布局

`#vcdPromptModal` 改为宽屏调试弹窗：

- 桌面端 modal 宽度 `min(80vw, 1600px)`，最小宽度受移动端自适应约束；左右自然留下约 10% 视口空余。
- 最大高度 `90vh`，头部固定，内容区滚动。
- 标题仍显示 `${步骤名}提示词`。
- 顶层 Tab 固定在标题下方：`请求`、`结果`。
- 二级 Tab 显示在各自顶层 Tab 内：
  - 请求：`请求数据`、`完整报文`
  - 结果：`返回数据`、`返回结果报文`
- Tab 必须支持点击切换、键盘 focus、Esc/遮罩/关闭按钮关闭。

### 请求数据

前端从 `currentState.step_requests[step]` 和当前任务状态组合结构化展示：

- 通用信息：步骤名、状态、provider、model/model_id、alias、execution_mode、image_count。
- 文字输入：product title、product URL、main image URL、product/video analysis、ad copy sets、prompt/messages。
- 媒体输入：
  - 商品主图 URL/本地路径。
  - 源视频文件名、路径和 `video_info`。
  - 封面生成参考图、参考帧列表、每帧 timestamp/type/source/path/visual_content。
- 封面生成特有内容：
  - `image_prompts` 按 index 展示，每张包含实际 prompt、source_ad_copy_id、reference_frames。
  - selected ad copy/title/message/description 逐项展示。

没有数据时展示 empty 状态，不出现空白弹窗。

### 完整报文

后端新增管理员接口，按步骤实时构造调试报文，不依赖前端猜测：

- `GET /video-cover/api/<task_id>/debug-payload/<step>`
- 权限：`@login_required + @superadmin_required`，复用项目访问校验。
- 返回：
  - `request_data`：结构化请求输入。
  - `full_request`：完整报文对象。
  - `response_data`：结构化返回数据。
  - `raw_response`：完整返回报文。
  - `replay`：是否支持调试重放、默认 URL、默认 model、默认 headers/body/files 摘要。

`full_request` 至少包含：

```json
{
  "method": "POST",
  "url": "http://172.30.254.14:82/v1/images/edits",
  "headers": {
    "Authorization": "Bearer <真实 API key>",
    "Content-Type": "multipart/form-data"
  },
  "api_key": "<真实 API key>",
  "body": {
    "model": "gpt-image-2",
    "prompt": "Make one 9:16 UGC-style social cover using the selected ad copy title as the only readable hook.",
    "n": "1",
    "size": "1024x1536"
  },
  "files": [
    {
      "field": "image",
      "filename": "reference.png",
      "content_type": "image/png",
      "source": "artifacts/video_cover/8/task-1/reference.png"
    }
  ]
}
```

用户明确需要看到 API key，因此 `full_request` 在超级管理员弹窗内明文展示 API key。实现必须避免把这份明文 key 写入 `state_json` 或项目文件；只在当前请求响应中返回。

文本步骤如果无法稳定复现 SDK 内部协议，也必须展示当前系统保存的 prompt/messages、provider/model、schema/media 摘要；`replay.supported=false`。

### 调试窗口

`完整报文` 底部新增调试区：

- 输入框：请求 URL。
- 输入框：API key。
- 按钮：`调试生成`。
- 状态：loading/error/success。
- 结果区：新图片预览、返回 JSON、错误信息。

调试区默认使用 `debug-payload` 返回的 URL 和 API key，可修改后提交。

新增接口：

- `POST /video-cover/api/<task_id>/debug-replay/<step>`
- 权限：`@login_required + @superadmin_required`。
- CSRF：前端 fetch 必须带 `X-CSRFToken`。
- 仅 `cover_generation` 支持调试重放；其他步骤返回 400 或 `supported=false` 的明确错误。
- 请求体：

```json
{
  "request_url": "https://example.com/v1/images/edits",
  "api_key": "sk-example-debug-key",
  "prompt_index": 1
}
```

后端使用项目内已保存的 reference image 和对应 `image_prompts[prompt_index]` 重新构造同一份 multipart/form-data 报文，只替换 URL 和 API key。调试生成的图片只返回给当前 HTTP 响应：

```json
{
  "ok": true,
  "image": {
    "data_url": "data:image/png;base64,iVBORw0KGgo=",
    "mime": "image/png"
  },
  "raw_response": {"data": [{"b64_json": "iVBORw0KGgo="}]},
  "request_url": "https://example.com/v1/images/edits",
  "prompt_index": 1
}
```

该接口不调用 `save_project_state()`，不写正式 artifact，不覆盖现有封面图。

### 返回数据

`返回数据` 从 `step_results[step].structured_result`、`state.result` 和步骤 timing 中组合：

- 文本步骤展示结构化字段卡片。
- 文案步骤展示 `ad_copy_sets`。
- 封面生成展示：
  - reference 图和 frames。
  - covers 列表：index、hook、formatted_copy、width、height、object_key、预览图、下载链接。
  - models.cover_generation 的 provider/model_id/execution_mode。
  - image_count 和耗时。

### 返回结果报文

直接展示 `step_results[step]` 的完整内容，并补充当前 `state.result` 中封面 URL/download URL 后的视图数据。展示使用 `JSON.stringify(value, null, 2)`，保留深层结构。

## 错误处理

- 调试报文加载失败：弹窗内显示错误，不影响主页面轮询。
- 调试生成失败：调试区显示 HTTP 状态、上游错误 message 和原始响应片段。
- 缺少 API key、URL 或 reference image：返回明确错误。
- `prompt_index` 非法：返回 400。
- 非封面生成步骤重放：返回 400，提示“该步骤暂不支持调试生成”。

## 验收

- 桌面端提示词弹窗宽度约 80vw，左右各保留约 10% 空余。
- 弹窗顶层存在 `请求` / `结果` Tab。
- `请求` 内存在 `请求数据` / `完整报文` 二级 Tab。
- `结果` 内存在 `返回数据` / `返回结果报文` 二级 Tab。
- `请求数据` 能结构化看到文字、图片、视频、参考帧、文案和每张图实际 prompt。
- `完整报文` 能看到 URL、headers、Authorization/API key、body/form data 和文件字段。
- `完整报文` 底部可修改 URL/API key，点击调试生成后显示新图片或错误报文。
- 调试生成不覆盖页面正式封面、不写项目状态。
- `返回数据` 能结构化展示封面生成结果。
- `返回结果报文` 展示格式化 JSON。
- 未登录访问新增接口跳登录或返回未授权；普通管理员和非管理员不能访问明文 API key 调试接口。
- mutating 调试接口前端请求带 `X-CSRFToken`。

## 测试

- `tests/test_video_cover_generation.py`
  - 模板断言：弹窗宽度样式、顶层 Tab、二级 Tab、调试区 URL/API key 输入、`返回数据` / `返回结果报文` 文案存在。
  - 路由断言：`GET debug-payload` 返回封面生成完整请求报文，包含 URL、headers、api_key、body、files、image_prompts。
  - 路由断言：`POST debug-replay` 需要管理员与 CSRF，使用新 URL/API key 调用图片接口，返回 data URL 和 raw response，不调用 `save_project_state()`。
  - 路由断言：非 cover step 调试重放返回明确错误。
  - 现有文案封面详情页和状态接口测试继续通过。
