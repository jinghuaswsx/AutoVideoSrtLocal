# 文案封面生成 1.0 设计

## 背景

新增菜单「文案封面生成」。用户只输入商品链接和视频文件，系统从商品链接程序化提取商品标题与商品主图，作为后续视频分析、产品分析、文案创作和封面生成的补充输入。

## 1.0 范围

- 新增后台页面 `/video-cover`，仅登录管理员可访问。
- 表单输入只保留：商品链接、视频文件。
- 项目详情页按固定 4 步自动执行：视频分析、产品分析、文案创作、封面生成；普通执行过程不再要求用户逐步选择供应商和模型。
- 后端从商品链接抓取商品标题与商品主图；Shopify 链接优先尝试 `.json` 商品接口，其他链接回退 HTML/JSON-LD/OG meta 解析。
- 后端从上传视频抽取一帧缩略图，并与商品主图合成一张 9:16 参考图。
- 封面生成默认调用本地 OpenAI-compatible 图片接口，生成一张通用于 Facebook Reels / Instagram Reels / TikTok / Shorts 的 9:16 竖版封面。
- 结果统一后处理为 `1080x1920` PNG，保存到 `local_media_storage`，页面返回预览和下载 URL。

## 1.1 项目工作流调整

- `/video-cover` 从一次性工具页调整为项目列表页，入口只创建项目：商品链接 + 视频文件。
- 管理员在 `/video-cover` 查看 `video_cover` 类型下全局所有未删除项目，不按创建人过滤；项目卡片展示创建人，便于区分来源。
- 新建项目的视频输入框采用全能视频翻译一致的交互：竖版拖拽区，可拖入视频，也可点击打开文件选择；选中后显示 9:16 视频预览、文件名和移除按钮。
- 新建项目时必须提供本次生成封面张数选择，使用 1 / 2 / 3 / 4 四个胶囊按钮；默认选中 2 张，选中态为蓝色，未选中为白底描边。
- 每个项目落库到 `projects.type='video_cover'`，项目详情页按固定前后关系管理 4 个步骤：`video_analysis`、`product_analysis`、`ad_copy`、`cover_generation`。
- 创建项目时必须从商品链接提取商品标题和商品主图 URL，下载商品主图并程序化标准化为 `400x400` JPG，写入项目状态；后续步骤复用这些项目输入，不在详情页展示时重新抓取。
- 项目详情页第一步「视频分析」卡片上方固定展示项目输入卡片：第一行商品标题；第二行商品链接预览与「复制」「跳转访问」按钮；第三行展示商品主图和可播放视频框。
- 提交项目后后端必须直接按 1 → 2 → 3 → 4 自动串行执行，最终输出封面与文案结果；用户不需要选择模型或手动点击每一步。
- 后续步骤必须等待前序步骤完成；重新运行上游步骤会清空其后的结果，并自动继续执行后续步骤，避免过期分析被继续用于生成。
- 若任一步骤失败，该步骤卡片显示错误和“重新跑”按钮；点击后从该步骤重新开始，并自动继续执行后续步骤。
- 任务详情页顶部进度卡片参考多语种视频翻译的顶部任务卡片：固定 sticky 在内容区顶部，不透明白底，使用多状态颜色区分 pending / running / done / error；进度卡片上方必须有“强制重新开始”按钮，按钮前方同样提供 1 / 2 / 3 / 4 张胶囊选择。强制重新开始按钮必须使用红色危险样式，点击后先弹出确认提示；用户确认后清空全部中间状态、按当前选中的张数更新 `image_count`，并从第 1 步重新执行。
- 视频分析调用大模型前必须使用共享 LLM 视频优化器转成 480p、15fps、H.264 600k 级别的临时文件，保留压缩音频以支持 voiceover 判断；转码失败时沿用优化器的原视频回退行为。
- 视频封面详情页的普通 GET 不做“中断恢复”；运行中的步骤不能因为用户刷新或打开详情页被误标为失败，真正的进程中断由启动恢复逻辑处理。
- 封面生成完成后，最终结果显示在“封面生成”卡片内：左侧居中展示封面图和图片操作，右侧展示对应文案和一键复制文案。

## 1.2 过程可视化与结构化结果

- 任务详情页右侧主界面除顶部 4 步大进度条外，只由四张动态高度步骤卡片组成：视频分析、产品分析、文案创作、封面生成。
- 顶部进度卡片必须提供“全部报文预览”按钮；点击后用 Modal 汇总展示 4 个步骤的请求输入、模型配置、prompt / messages、原始返回和结构化结果。
- 每张步骤卡片必须包含：
  - 顶部中间的时间进度：运行态动态显示“已运行 Ns”，完成后显示“耗时 Ns”；后端在状态中记录 `started_at`、`finished_at`、`elapsed_seconds`，前端轮询时用当前时间补足运行中秒数。
  - 标题旁的“提示词”按钮：打开 Modal，展示该步骤本次请求的输入、模型选择、媒体输入摘要、prompt / messages、原始返回和结构化结果。
  - “可视化展现”：主流程只展示按结构化字段设计的前端布局，不直接展示完整原始报文或密集 JSON。
  - “重新跑”按钮：步骤失败或已完成后可用；运行中禁用。
- 步骤卡片的执行状态视觉必须高保真复刻多语种视频翻译任务执行页的 step 卡片结构和动效：卡片用同款圆角、边框、图标圆点、`step-name-row`、`step-msg` 和 running spinner。状态色按文案封面当前需求落地为：正在执行浅绿色、已完成深绿色、报错红色、堵塞等待确认橙色；pending 保持白底等待态。
- 每张步骤卡片的耗时信息必须改成更显眼的标题行 badge，放在卡片标题右侧并与标题约 100px 间隔，整体靠近卡片左侧而不是居中。运行中展示“已运行 Ns”并带旋转 spinner，完成后展示“耗时 Ns”，字体加粗。
- `video_analysis`、`product_analysis`、`ad_copy` 必须要求模型返回结构化 JSON；后端保存 `raw_response` 和 `structured_result`，前端优先用 `structured_result` 渲染。
- 视频分析可视化建议字段：`video_text`、`voiceover`、`cover_reference`、`actions`、`composition`、`authenticity_cues`、`ignore_elements`、`cover_suggestions`。
- 产品分析可视化建议字段：`information_check`、`product_definition`、`core_functions`、`usage_analysis`、`physical_features`、`western_scene_suggestions`、`visual_category`、`cover_decision`、`ad_copy_direction`、`overall_judgment`。
- 文案创作可视化字段沿用 `ad_copy_sets` 五组结构化文案，卡片化展示 angle、英文 headline / body_text / cta、中文翻译和使用建议。
- 封面生成根据 `image_count` 生成 1 到 4 张封面。每张封面记录自己的 `index`、`object_key`、`width`、`height`、`source_ad_copy_id`、`hook`、`copy`；前端用缩略图切换，左图右文案保持一一对应。
- 图片下方提供“保存图片”和“复制图片”胶囊按钮；复制图片优先使用浏览器 Clipboard API，不支持时提示使用保存图片兜底。文案区提供“一键复制文案”按钮。

## 1.3 全局默认模型配置

- `/video-cover` 项目列表页在“新建项目”旁边增加“默认配置”按钮，仅 `current_user.is_superadmin` 为真时可见；普通管理员和其他用户不可见。
- 默认配置是全局共享配置，不按用户隔离。超级管理员修改后，所有用户后续新建文案封面项目都使用这套最新默认配置。
- 默认配置支持分别配置四个步骤的模型供应商和模型 ID：`video_analysis`、`product_analysis`、`ad_copy`、`cover_generation`。
- 默认配置保存到 `system_settings`，值为结构化 JSON；读取失败、缺失或字段非法时回退到代码内置默认模型。
- 新建项目时必须把当前默认配置快照写入项目 `state_json.model_defaults`。该项目后续自动执行、失败重试、强制重新开始都使用这份项目级快照，避免管理员后续改默认配置影响已创建项目。
- “默认配置”弹窗内的模型 ID 必须是供应商联动下拉框。用户先选择步骤供应商，再从该供应商在当前步骤可用的模型池中选择模型；保存时提交并落库实际调用的 `model_id`，不提交展示名或内部别名。
- 文本步骤的模型池必须按场景给出多个可选模型。视频分析可选 Gemini 3.1 Pro Preview、Gemini 3 Flash、Gemini 3.1 Flash-Lite；产品分析和文案创作同样提供 Gemini 3 系列，其中文案创作在 OpenRouter 下额外提供 Claude Sonnet、GPT-5.5、GPT-5 Mini 等文本模型。
- 封面生成的模型池必须提供图片生成模型候选。本地接口和 OpenRouter 都至少提供 GPT-Image-2、Nano Banana 2、Nano Banana Pro；OpenRouter 可额外提供 OpenAI Image 2 low / mid / high 质量档位和 Nano Banana 1 兜底。
- 如果历史配置中存在当前模型池未收录但仍可被后端规范化保留的 `model_id`，弹窗应临时显示“当前历史值”选项，避免打开配置后静默覆盖旧值；用户主动切换供应商或模型后，再回到预设模型池。
- 保存接口必须使用 `@superadmin_required`；普通管理员直接请求读取或保存接口返回 403。

## 模型与平台决策

当前 1.0 按步骤内置默认模型运行；超级管理员可以通过 `/video-cover` 列表页的“默认配置”覆盖全局默认值：

- 视频分析：默认 `GOOGLE VERTEX ADC` / `gemini-3.1-pro-preview`；OpenRouter 对应 `google/gemini-3.1-pro-preview`。同供应商还可选择 Gemini 3 Flash 和 Gemini 3.1 Flash-Lite。
- 产品分析：默认 `OPENROUTER` / `google/gemini-3-flash-preview`；Google Vertex ADC 对应 `gemini-3-flash-preview`。同供应商还可选择 Gemini 3.1 Pro Preview 和 Gemini 3.1 Flash-Lite。
- 文案创作：默认 `OPENROUTER` / `google/gemini-3-flash-preview`；Google Vertex ADC 对应 `gemini-3-flash-preview`。OpenRouter 文案池还可选择 `anthropic/claude-sonnet-4.6`、`openai/gpt-5.5`、`openai/gpt-5-mini`。
- 封面生成：默认 `本地接口` / `gpt-image-2`，本地接口默认 base URL 为 `http://172.30.254.14:82/v1`，API key 存放在 `llm_provider_configs.video_cover_local_image`；OpenRouter 可选 OpenAI Image 2 low / mid / high、Nano Banana 2、Nano Banana Pro、Nano Banana 1 兜底。本地接口可选 GPT-Image-2、Nano Banana 2、Nano Banana Pro、Nano Banana 1 兜底。
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

- `product_analysis`：调用 `video_cover.product_analysis`，prompt 明确声明输入已包含商品标题、商品主图 URL 和标准化后的 `400x400` JPG 商品主图文件；media 使用该标准化 JPG，基于标题、描述、主图、价格线索和产品分析提示词生成产品分析报告。
- `video_analysis`：调用 `video_cover.video_analysis`，prompt 明确声明输入已包含商品标题、商品主图 URL、标准化 `400x400` JPG 商品主图文件和上传视频；media 同时传入优化后视频与标准化商品主图，生成视频素材分析。
- `ad_copy_sets`：先调用 `video_cover.ad_copy` 文案创作提示词，prompt 带上商品标题、商品主图 URL、`product_analysis`、`video_analysis` 和当前日期，输出 5 组英文广告文案 JSON，再作为封面 hook 方向输入。

## 数据流

1. 管理员打开 `/video-cover` 查看项目列表；列表查询 `projects.type='video_cover' AND deleted_at IS NULL`，管理员不加 `user_id` 条件。
2. 管理员点击“新建项目”，在弹窗输入商品链接并拖入或点击选择视频。
3. `POST /video-cover/api/projects` 校验商品链接和视频扩展名，提取商品标题与商品主图 URL，下载商品主图并标准化保存为 `400x400` JPG；将视频保存到本地上传目录，抽取缩略图，写入 `projects`：`type='video_cover'`、`status='uploaded'`、`task_dir`、`thumbnail_path`、`state_json`。
4. 用户进入 `/video-cover/<task_id>` 项目详情页后按步骤运行：视频分析、产品分析、文案创作、封面生成。
5. “视频分析”步骤使用项目保存的视频文件、商品标题和标准化商品主图，调用所选文本模型。
6. “产品分析”步骤复用项目创建时保存的商品标题、商品主图 URL 和标准化商品主图，并把商品信息发给所选文本模型。
7. “文案创作”步骤调用 `POST /video-cover/api/<task_id>/run/ad_copy`，基于商品标题、商品主图 URL、`product_analysis`、`video_analysis` 和当前日期输出 5 组合法 `ad_copy_sets` JSON。
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

- 服务层：校验商品链接解析、商品主图标准化为 `400x400` JPG、模型映射、产品/视频/文案/封面调用顺序、参考图生成、平台 prompt、输出 1080x1920。
- 路由层：未登录跳登录，普通用户 403，管理员列表页 200；管理员列表查询全局 `video_cover` 项目；新建项目校验商品链接和视频扩展名并写入项目；详情页只允许管理员访问；步骤接口传递模型配置并返回结果。
- 模板层：侧栏出现「文案封面生成」；列表页出现“新建项目”；超级管理员列表页出现“默认配置”，普通管理员不可见；新建弹窗视频输入框包含拖拽区、隐藏 file input、预览 video、移除按钮；详情页出现项目输入卡片，展示商品标题、链接复制/跳转按钮、商品主图和视频播放框；详情页出现 4 张过程卡片和项目级模型配置快照。
