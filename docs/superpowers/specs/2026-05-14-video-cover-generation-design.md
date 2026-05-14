# 文案封面生成 1.0 设计

## 背景

新增菜单「文案封面生成」。用户只输入商品链接和视频文件，系统从商品链接程序化提取商品标题与商品主图，作为后续视频分析、产品分析、文案创作和封面生成的补充输入。

## 1.0 范围

- 新增后台页面 `/video-cover`，仅登录管理员可访问。
- 表单输入只保留：商品链接、视频文件。
- 页面提供 4 个按钮：视频分析、产品分析、文案创作、封面生成；右侧工作窗口顶部可为当前步骤选择供应商和模型。
- 后端从商品链接抓取商品标题与商品主图；Shopify 链接优先尝试 `.json` 商品接口，其他链接回退 HTML/JSON-LD/OG meta 解析。
- 后端从上传视频抽取一帧缩略图，并与商品主图合成一张 9:16 参考图。
- 封面生成默认调用本地 OpenAI-compatible 图片接口，生成一张通用于 Facebook Reels / Instagram Reels / TikTok / Shorts 的 9:16 竖版封面。
- 结果统一后处理为 `1080x1920` PNG，保存到 `local_media_storage`，页面返回预览和下载 URL。

## 1.1 项目工作流调整

- `/video-cover` 从一次性工具页调整为项目列表页，入口只创建项目：商品链接 + 视频文件。
- 管理员在 `/video-cover` 查看 `video_cover` 类型下全局所有未删除项目，不按创建人过滤；项目卡片展示创建人，便于区分来源。
- 新建项目的视频输入框采用全能视频翻译一致的交互：竖版拖拽区，可拖入视频，也可点击打开文件选择；选中后显示 9:16 视频预览、文件名和移除按钮。
- 每个项目落库到 `projects.type='video_cover'`，项目详情页按固定前后关系管理 4 个步骤：`video_analysis`、`product_analysis`、`ad_copy`、`cover_generation`。
- 后续步骤必须等待前序步骤完成；重新运行上游步骤会清空其后的结果，避免过期分析被继续用于生成。
- 视频分析调用大模型前必须使用共享 LLM 视频优化器转成 480p、15fps、H.264 600k 级别的临时文件，保留压缩音频以支持 voiceover 判断；转码失败时沿用优化器的原视频回退行为。
- 封面生成完成后，最终封面结果显示在项目详情页最顶部，并提供直接下载按钮。

## 模型与平台决策

当前 1.0 按步骤固定默认模型，同时允许用户在工作窗口顶部切换供应商：

- 视频分析：默认 `GOOGLE VERTEX ADC` / `gemini-3.1-pro-preview`；OpenRouter 对应 `google/gemini-3.1-pro-preview`。
- 产品分析：默认 `OPENROUTER` / `google/gemini-3-flash-preview`；Google Vertex ADC 对应 `gemini-3-flash-preview`。
- 文案创作：默认 `OPENROUTER` / `google/gemini-3-flash-preview`；Google Vertex ADC 对应 `gemini-3-flash-preview`。
- 封面生成：默认 `本地接口` / `gpt-image-2`，本地接口默认 base URL 为 `http://172.30.254.14:82/v1`，API key 存放在 `llm_provider_configs.video_cover_local_image`；OpenRouter 可选 `gpt-image-2`、`nano_banana_2`、`nano_banana_pro` 的映射模型。
- 本地图片生成接口按接口文档使用图生图编辑能力：`POST /images/edits`，请求为 `multipart/form-data`，字段包含 `model`、`prompt`、`n`、`size`，参考图通过 `image` 文件上传；9:16 原始生成尺寸使用 `1024x1536`，响应支持 `b64_json` 或 `url`。
- 这个功能需要基于商品主图和视频画面做图片生成/编辑；视频分析阶段读取上传视频文件，封面生成阶段使用商品主图与精选视频帧组成的 9:16 参考图。
- 输出后处理强制为平台常用的 `1080x1920` 竖版 PNG，模型原始输出尺寸不直接暴露给用户。

平台约束：

- Meta Reels/Stories 创意按 9:16 竖版设计，关键卖点和产品主体放在安全区域内。
- TikTok US 按竖版信息流创意设计，标题和产品主体放在中心区域，减少个人主页/信息流裁切风险。

## 提示词合同

封面生成使用用户提供的创意总监提示词，核心要求：

- 基于上传产品图片、精选视频帧、`product_analysis`、`video_analysis`、`ad_copy_sets` 生成封面。
- 画面必须像真实爆款短视频中最值得停留的一帧，不做电商主图、海报、影棚产品照或截图。
- 产品形状、颜色、材质、比例和功能部件必须忠实于产品图片。
- 使用方式必须可信，必要时展示手部、身体互动、安装位置或可见结果。
- 画面要有西方生活方式和社交平台原生感。
- 画面中必须且只能包含一句简短英文 hook，优先从 `ad_copy_sets` 选择或缩写。
- 禁止平台 UI、用户名、假评论框、红圈、箭头、价格/折扣、CTA、多句 hook、海报式排版和重度图形装饰。

1.0 中的上下文构造：

- `product_analysis`：调用 `video_cover.product_analysis`，基于商品标题、描述、主图、价格线索和产品分析提示词生成产品分析报告。
- `video_analysis`：调用 `video_cover.video_analysis`，基于上传视频文件和补充商品信息生成视频素材分析。
- `ad_copy_sets`：先调用 `video_cover.ad_copy` 文案创作提示词，基于 `product_analysis`、`video_analysis` 和当前日期生成 5 组英文广告文案 JSON，再作为封面 hook 方向输入。

## 数据流

1. 管理员打开 `/video-cover` 查看项目列表；列表查询 `projects.type='video_cover' AND deleted_at IS NULL`，管理员不加 `user_id` 条件。
2. 管理员点击“新建项目”，在弹窗输入商品链接并拖入或点击选择视频。
3. `POST /video-cover/api/projects` 校验商品链接和视频扩展名，将视频保存到本地上传目录，抽取缩略图，写入 `projects`：`type='video_cover'`、`status='uploaded'`、`task_dir`、`thumbnail_path`、`state_json`。
4. 用户进入 `/video-cover/<task_id>` 项目详情页后按步骤运行：视频分析、产品分析、文案创作、封面生成。
5. “视频分析”步骤使用项目保存的视频文件，调用所选文本模型。
6. “产品分析”步骤调用 `fetch_product_analysis()` 解析商品标题与主图 URL，并把商品主图和商品信息发给所选文本模型。
7. “文案创作”步骤调用 `POST /video-cover/api/<task_id>/run/ad_copy`，基于 `product_analysis`、`video_analysis` 和当前日期输出 5 组合法 `ad_copy_sets` JSON。
8. “封面生成”步骤下载商品主图、抽取视频帧并合成 9:16 参考图。
9. 构造创意总监 prompt，替换 `product_analysis`、`video_analysis`、`ad_copy_sets` 占位。
10. 调用本地接口或 OpenRouter 图片模型生成通用社媒封面。
11. 输出图片居中裁切/扩展为 `1080x1920`，保存到 `artifacts/video_cover/<user>/<task_id>/`。
12. 步骤完成后将输出、模型选择和状态写回项目 `state_json`，详情页顶部固定展示最终封面和下载按钮。

## 错误处理

- 商品链接为空或不是 HTTP(S)：返回 400。
- 视频扩展名不在 `mp4/mov/mpeg/mpg/avi/webm/m4v`：返回 400。
- 商品页面无法提取标题或主图：返回 400，并给出具体错误。
- 视频抽帧失败：返回 400。
- 产品分析、视频分析、文案创作或封面模型调用失败：返回 502，保留上游错误信息，避免页面 500。

## 测试

- 服务层：校验商品链接解析、模型映射、产品/视频/文案/封面调用顺序、参考图生成、平台 prompt、输出 1080x1920。
- 路由层：未登录跳登录，普通用户 403，管理员列表页 200；管理员列表查询全局 `video_cover` 项目；新建项目校验商品链接和视频扩展名并写入项目；详情页只允许管理员访问；步骤接口传递模型配置并返回结果。
- 模板层：侧栏出现「文案封面生成」；列表页出现“新建项目”；新建弹窗视频输入框包含拖拽区、隐藏 file input、预览 video、移除按钮；详情页出现 4 个步骤按钮和供应商/模型配置。
