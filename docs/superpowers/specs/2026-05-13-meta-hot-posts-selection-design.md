# Meta 热帖选品设计

最后更新：2026-05-13

## 背景

选品中心已有明空选品、新品选择、今日推荐和 TABCUT。新增「Meta热帖」Tab，用 wedev 热帖列表作为数据源，把 Facebook 热帖卡片和商品页补充信息沉淀到本地，供后台筛选与定时分析。

本设计遵守项目规则：

- 不连接 Windows 本机 MySQL `127.0.0.1:3306`。
- 不在主工作目录改代码，开发走 git worktree。
- wedev 数据只使用已有登录凭据可访问的接口，不绕过登录、权限或风控。
- 每次 wedev 接口请求和商品页抓取之间至少间隔 3 秒，默认 3.2 秒。
- 新增定时任务必须登记到 `appcore/scheduled_tasks.py`。

## 数据源探索结论

wedev 页面 `https://os.wedev.vip/spy/hot-posts` 是 SPA，热帖列表通过以下接口加载：

- `GET /api/spy/hot/posts`

前端实际调用模块：

- `hotPosts: params => get("/spy/hot/posts", params)`
- Axios `baseURL="/api"`

未带 wedev 登录凭据时，接口返回 HTTP 200，但业务体为：

```json
{"data": null, "is_guest": true, "message": "登录已失效", "status": 0}
```

因此后端必须复用项目现有 wedev 凭据来源：

- `appcore.pushes.build_localized_texts_headers()`
- `tools/wedev_sync.py` 从 Chrome 同步 Cookie / Bearer 到系统设置
- `web.routes.medias.mk_selection._build_mk_request_headers()` 中已有明空代理请求头模式

## wedev 请求参数

热帖列表支持的主要查询参数：

- `page`
- `period_hours`，默认 72，表示更新时间间隔不超过 N 小时。
- `fans_max`，默认 10000。
- `ads_min` / `ads_max`
- `creatives_min` / `creatives_max`
- `q`，关键词。
- `date_min`，帖子创建时间下限，wedev 前端默认最近 2 个月。
- `only_starred`
- `media_type`：空、`image`、`video`。
- `reactions_min` / `reactions_max`，互动变化数区间。
- `seen_min` / `seen_max`，发现时间区间。

## 需要落库的热帖字段

本地保存 wedev 卡片中展示和跳转所需的所有关键字段：

- `id`：wedev 热帖 ID。
- `page_id`
- `post_id`
- `bm_page_id`
- `product_url`
- `creation_time`
- `last_synced_at`
- `likes`
- `comments`
- `shares`
- `latest_likes`
- `latest_comments`
- `latest_shares`
- `sync_period_likes`
- `sync_period_hours`
- `copycat`
- `select`
- `video`
- `image`
- `invisible`
- `invisible_region`
- `message`
- `raw_json`

派生链接：

- 帖子 URL：`https://facebook.com/{page_id}/posts/{post_id}`
- 主页广告 URL：`https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=ALL&media_type=all&search_type=page&source=page-transparency-widget&view_all_page_id={bm_page_id}`
- 商品链接：`product_url` 缺协议时补 `https://`

## 商品页分析

基于热帖商品链接创建本地分析任务，逐个商品链接处理：

1. URL 归一化。
2. 每个商品页请求至少间隔 3 秒。
3. 优先抓 Shopify 商品 JSON：
   - `/products/<handle>.json`
   - 当前 URL 追加 `.json`
4. 兜底解析 HTML：
   - `application/ld+json` 中的 `Product`
   - `og:title` / `og:image`
   - Shopify script 中的 `variants`
5. 提取并保存：
   - 商品主图
   - 商品标题
   - 起售价格 `price_min`
   - 最高 SKU 价 `price_max`
   - 货币
   - SKU 列表与对应价格
   - 原始提取摘要和错误信息

## Gemini 分类

新增 LLM use case：

- `meta_hot_posts.categorize`
- 默认 provider：`openrouter`
- 默认 model：`google/gemini-3.1-flash-lite-preview`

调用参数只传：

- 商品标题

不再把商品链接传给模型。类目判断只基于商品标题，降低 prompt 复杂度和 JSON
解析失败率。

输出只要求返回一个 TikTok Shop US 一级类目名称，不再强制使用 Gemini
`response_schema`。代码侧负责把返回文本规整到枚举池；不命中时保存为空类目并记录
原始返回，不能把解析失败误写成 `Other`。

输出示例：

```text
Home Supplies
```

类目池使用 TikTok Shop US 一级类目。代码侧维护稳定枚举，并预留后续通过 TikTok Shop Open API `GET /product/202309/categories` 更新一级类目的入口。当前枚举包含常见 US 一级类目，例如 Beauty & Personal Care、Home Supplies、Kitchenware、Phones & Electronics、Pet Supplies、Tools & Hardware、Automotive & Motorcycle 等。

Gemini 分类失败不能丢弃已经提取到的商品页数据。商品页提取成功但类目判断失败时，分析任务仍保存商品标题、主图、价格和 SKU，状态记为 `done`，类目保持为空，并在错误信息中记录分类失败原因；只有商品页请求或解析失败时才标记为 `failed`。

LLM 返回的类目必须命中枚举；不命中时类目保持为空并保留原始响应。`Other`
只用于模型明确返回 `Other` 且没有解析错误的情况。

Gemini 分类必须走统一 LLM use case 和账单链路：

- use case：`meta_hot_posts.categorize`
- 默认 provider：`openrouter`
- 默认 model：`google/gemini-3.1-flash-lite-preview`
- 默认绑定写入 `llm_use_case_bindings`，并进入 `/settings` 的用例绑定管理。
- 定时任务调用时必须传入账单归属用户，默认取 active admin / superadmin 中的 `admin` 账号优先；因此每次 Gemini 请求都会进入 `usage_logs` 和 `usage_log_payloads`，并在 API 账单中按 use case 展示。

历史重跑策略：

- 已经提取成功、`product_title` 非空、但 `last_error` 以 `category failed:` 开头的记录，需要支持“只重算类目”。
- 旧逻辑误写成 `Other` 的记录也可纳入重算，但重算时不重新抓商品页。
- 重算成功后清空 `last_error`，更新 `category_l1`、`category_confidence`、`category_reason`、`llm_provider`、`llm_model` 和 `llm_response_json`。
- 类目重算成功或失败都必须写入当前分类链路的 `llm_provider=openrouter`、`llm_model=google/gemini-3.1-flash-lite-preview`，避免同一轮修复后仍被当成旧模型/未处理记录反复重算。
- 旧 ADC 分类链路写入的 `llm_provider=gemini_vertex_adc` / `llm_model=gemini-3.1-flash-lite-preview` 不再视为当前链路；当其类目为空、`Other` 或 `category failed:` 时，必须纳入 OpenRouter 重算。
- 如果类目重算遇到全局 provider 配置、OpenRouter 凭据错误或上游限流，本轮只标记当前记录失败并立即停止，不能继续扫完 100 条，避免在基础通道异常时批量消耗请求。
- 商品页分析批次中的 Gemini 类目调用遇到同类全局错误时，也必须保存当前商品页提取结果后停止本轮，不继续分析下一条。

定时商品分析节流策略：

- APScheduler 仍每 10 分钟触发一轮 `meta_hot_posts_analysis_tick`。
- 每轮最多处理 30 条商品分析记录。
- 同一轮内串行处理，两条记录之间等待 20 秒；最后一条之后不等待。
- DB 单例守护保留：上一轮 1 小时以内仍在跑则下一轮跳过；超过 1 小时才接管旧任务。
- 手动全量补跑可以使用同一入口覆盖节流为 10 秒/条，直到没有待分析商品为止。
- 手动“只重算类目”补跑可覆盖为 5 并发且不等待条间间隔；默认定时任务仍保持串行，避免商品页抓取和常规任务突然放大请求量。

后台类目展示：

- 数据库和 API 筛选参数继续使用 TikTok Shop US 英文一级类目值，例如 `Kitchenware`。
- 后台筛选下拉、卡片展示和列表 API 同时提供中文展示名，例如 `Kitchenware` 对应 `厨房用品`。
- 中文名由代码维护稳定映射，不让 Gemini 输出中文，也不把中文写入 `category_l1`。

## 数据模型

新增迁移 `db/migrations/2026_05_13_meta_hot_posts.sql`：

- `meta_hot_post_sync_runs`：记录 wedev 热帖同步运行。
- `meta_hot_posts`：保存热帖卡片、派生链接、筛选指标和原始 JSON。
- `meta_hot_post_product_analyses`：保存商品页提取、SKU 价格、Gemini 分类和任务状态。

唯一键：

- `meta_hot_posts.wedev_post_id`
- `meta_hot_post_product_analyses.product_url_hash`

筛选索引：

- 类目：`category_l1`
- 起售价格：`price_min`
- 交互数：`latest_likes`
- 评论数：`latest_comments`
- 帖子创建时间：`creation_time`

## 后台页面

新增页面：

- `/xuanpin/meta-hot-posts`

新增 API：

- `GET /xuanpin/api/meta-hot-posts`
- `GET /xuanpin/api/meta-hot-posts/categories`
- `GET /xuanpin/api/meta-hot-posts/category-prompt`
- `GET /xuanpin/api/meta-hot-posts/failures`
- `POST /xuanpin/api/meta-hot-posts/refresh`
- `POST /xuanpin/api/meta-hot-posts/analyze`

权限：

- 页面与 API 都需要登录。
- 页面、刷新、分析入口需要 admin。

页面交互：

- 选品中心 Tab 增加「Meta热帖」。
- 卡片布局参考 wedev 热帖卡片。
- 筛选栏上方增加工具按钮区，包含「同步」「分析商品」「类目分析提示词」「商品分析失败记录」。
- 「类目分析提示词」弹窗展示 Gemini 分类提示词模板和 TikTok Shop US 一级类目池。
- 「商品分析失败记录」弹窗展示失败商品链接、失败次数、错误原因和更新时间，便于后续排查重试。
- 展示帖子 URL、文案、商品链接、wedev 卡片指标、视频或图片。
- 视频卡片叠加醒目的红色时长标注；时长与首帧封面在视频本地化/元数据回填阶段持久化，页面只加载 `local_video_cover_url` / `tos_video_cover_url` 图片封面和播放按钮，点击后再加载真实视频播放器或 Facebook iframe。
- 每张帖子卡片右侧提供本地「行 / 不行」两个标注选项，点击正方形区域或文字即可切换；两个选项互斥，标注状态保存到本地库，不随 wedev 同步覆盖。
- 附加展示商品主图、商品标题、起售价格、SKU 数、类目、分析状态。
- 筛选支持：类目、标注状态（全部 / 行 / 不行）、价格范围、当前交互数、当前评论数、帖子创建时间。

交互数和评论数语义：

- 默认使用卡片第二行的当前指标：`latest_likes` 和 `latest_comments`。
- 原始累计指标 `likes` / `comments` 仍保存并展示。

## 定时任务

新增 APScheduler 任务登记：

- `meta_hot_posts_sync_tick`
- 每天北京时间 07:00 同步一轮 wedev 热帖列表。
- 每轮目标采集 500 条热帖；按页请求直到累计达到 500 条或上游无更多数据。
- 单请求最小间隔 3 秒，可通过命令行参数临时调整目标条数和最大页数。

新增分析任务登记：

- `meta_hot_posts_analysis_tick`
- 每 10 分钟扫描未完成商品分析。
- 每次最多取 100 个待分析商品链接；商品链接来自已采集 Meta 热帖卡片。
- 逐个执行商品页抓取和 Gemini 分类，商品页请求和 LLM 分类串行执行。
- 商品页请求与 LLM 分类串行执行，避免并发触发下游风控。
- 商品页抓取失败时保留失败记录；商品页抓取成功但 Gemini 分类失败时保存商品标题、主图、价格和 SKU，类目保持为空并记录分类错误。
- 分析任务支持只重算类目：每轮最多处理 100 个已提取商品标题但类目失败或旧 `Other` 的记录，不重新请求商品页。
- 分析任务必须做 DB 单例守护：启动前查询 `scheduled_task_runs` 中 `meta_hot_posts_analysis_tick` 的 `running` 记录。
- 如果存在 1 小时内启动的 running 记录，本轮直接跳过，不再启动新的商品抓取。
- 如果 running 记录已超过 1 小时，本轮先把旧 run 标记为 `failed`，错误说明为超时接管，再重置超时的商品分析 running 行，随后启动新 run。

两个任务都写入 `scheduled_task_runs`。

## 验证

- `pytest tests/test_meta_hot_posts_client.py tests/test_meta_hot_posts_product_analysis.py tests/test_meta_hot_posts_store.py tests/test_meta_hot_posts_routes.py tests/test_appcore_scheduled_tasks.py tests/test_xuanpin_routes.py -q`
- 不在开发机连接 Windows 本机 MySQL。
- 未登录访问新页面应返回 302。
- 管理员登录后新页面返回 200。
- 新增 POST API 在 `xuanpin` 蓝图内保持现有 CSRF 豁免策略。
