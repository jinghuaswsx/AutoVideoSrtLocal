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
- 默认 provider：`gemini_vertex`
- 默认 model：`gemini-3-flash-preview`

调用参数只传：

- 商品标题
- 商品链接地址

输出 JSON：

```json
{
  "category": "Home Supplies",
  "confidence": 0.82,
  "reason": "The product title and URL indicate a household storage item."
}
```

类目池使用 TikTok Shop US 一级类目。代码侧维护稳定枚举，并预留后续通过 TikTok Shop Open API `GET /product/202309/categories` 更新一级类目的入口。当前枚举包含常见 US 一级类目，例如 Beauty & Personal Care、Home Supplies、Kitchenware、Phones & Electronics、Pet Supplies、Tools & Hardware、Automotive & Motorcycle 等。

Gemini 分类失败不能丢弃已经提取到的商品页数据。商品页提取成功但类目判断失败时，分析任务仍保存商品标题、主图、价格和 SKU，状态记为 `done`，类目降级为 `Other`，并在错误信息中记录分类失败原因；只有商品页请求或解析失败时才标记为 `failed`。

LLM 返回的类目必须命中枚举；不命中时保存为 `Other` 并保留原始响应。

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
- `POST /xuanpin/api/meta-hot-posts/refresh`
- `POST /xuanpin/api/meta-hot-posts/analyze`

权限：

- 页面与 API 都需要登录。
- 页面、刷新、分析入口需要 admin。

页面交互：

- 选品中心 Tab 增加「Meta热帖」。
- 卡片布局参考 wedev 热帖卡片。
- 展示帖子 URL、文案、商品链接、wedev 卡片指标、视频或图片。
- 附加展示商品主图、商品标题、起售价格、SKU 数、类目、分析状态。
- 筛选支持：类目、价格范围、当前交互数、当前评论数、帖子创建时间。

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
- 每次最多分析少量商品，逐个执行商品页抓取和 Gemini 分类。
- 商品页请求与 LLM 分类串行执行，避免并发触发下游风控。

两个任务都写入 `scheduled_task_runs`。

## 验证

- `pytest tests/test_meta_hot_posts_client.py tests/test_meta_hot_posts_product_analysis.py tests/test_meta_hot_posts_store.py tests/test_meta_hot_posts_routes.py tests/test_appcore_scheduled_tasks.py tests/test_xuanpin_routes.py -q`
- 不在开发机连接 Windows 本机 MySQL。
- 未登录访问新页面应返回 302。
- 管理员登录后新页面返回 200。
- 新增 POST API 在 `xuanpin` 蓝图内保持现有 CSRF 豁免策略。
