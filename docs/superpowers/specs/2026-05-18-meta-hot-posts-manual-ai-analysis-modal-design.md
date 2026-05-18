# Meta 热帖卡片手动 AI 分析弹窗

日期：2026-05-18

## 背景

`/xuanpin/meta-hot-posts` 卡片已经透出美国视频可抄分析和欧洲评估结果，但运营在单条素材上排查时，无法直接看到本次 AI 请求发了什么视频、商品图、提示词和完整报文，也无法对某一条素材手动重跑。

欧洲按钮的业务含义不是单纯“欧洲适配打分”，而是判断该素材翻译成德国、法国、意大利、西班牙对应语言后，是否值得搬运到欧洲 Meta 投放。

## 目标

- 每张 Meta 热帖卡片底部新增两个手动分析按钮：
  - `美国操作分析`：复用 `meta_hot_posts.video_copyability`。
  - `欧洲翻译分析`：复用 `meta_hot_posts.europe_fit`，但 prompt/schema 按“翻译本土化后投放”优化。
- 点击按钮打开一个 AI 分析 Modal，包含 `请求数据` 和 `结果数据` 两个 Tab。
- 请求数据展示商品标题/链接、商品主图、可播放视频、system/user prompt、response schema、请求报文结构和完整请求报文入口。
- 结果数据展示归一化结果、完整返回报文和当前卡片刷新后的分析数据。
- 首次点击无结果时自动触发该单条素材的分析，并停留在请求数据 Tab；分析完成后自动切到结果数据 Tab。
- 再次点击已有结果时不调用模型，直接打开结果数据 Tab。
- Modal 右上角始终提供 `强制重新分析`，点击后只重跑当前卡片当前分析类型。
- 分析成功后只刷新当前卡片的数据，不整页刷新。

## Prompt 与结果结构

欧洲翻译分析继续写入 `meta_hot_post_europe_assessments`，保持 Top50 和既有调度兼容。新 prompt 必须明确：

- 目标是把视频翻译成本地语言后投放，而不是“原视频直接投放”。
- 目标市场和语言：Germany/German、France/French、Italy/Italian、Spain/Spanish。
- 评估口径包括产品欧洲市场适配、视频翻译可行性、口播依赖、屏幕文字依赖、字幕/配音/画面文字替换需求、合规/IP/夸张宣传风险、本土化工作量。
- `message_html` / `message_zh_html` 也进入 prompt，帮助判断广告文案翻译搬运风险。

欧洲返回 schema 在原字段基础上增加：

- `translation_fit_score`
- `best_language_markets`
- `source_language_detected`
- `speech_dependency`
- `on_screen_text_dependency`
- `needs_subtitle_translation`
- `needs_voiceover_or_dubbing`
- `needs_screen_text_replacement`
- `localization_difficulty`
- `country_localization_notes`

推荐值兼容旧字段，但新语义优先：

- `translate_and_launch`：翻译后可投。
- `adapt_before_translation`：先改素材再翻译。
- `not_recommended`：不建议翻译搬运。

## 接口设计

新增单条接口，全部沿用 `/xuanpin/meta-hot-posts` 的登录与 `meta_hot_posts` 权限门禁：

- `GET /xuanpin/api/meta-hot-posts/<post_id>/ai-analysis/<mode>/request-preview`
- `GET /xuanpin/api/meta-hot-posts/<post_id>/ai-analysis/<mode>/request-payload`
- `GET /xuanpin/api/meta-hot-posts/<post_id>/ai-analysis/<mode>/result`
- `POST /xuanpin/api/meta-hot-posts/<post_id>/ai-analysis/<mode>`

`mode` 仅允许：

- `us_copyability`
- `europe_translation`

POST 行为：

- `force=false` 且已有 `done` 结果时，直接返回结果，不调用模型。
- `force=true` 或无结果时，只运行当前 `post_id` 的单条分析。
- 普通模型错误按现有分析表记一次失败。
- rate limit / 429 / quota 错误恢复分析行原状态和 attempts，不把它变成自动队列后续反复捞取的失败行。
- 单条手动接口不调用批量统一队列，不启动批量任务。

## 非目标

- 不新增自动定时任务。
- 不改 Top50 排名算法。
- 不创建实际视频翻译任务，不自动投放 Meta 广告。
- 不连接 Windows 本机 MySQL 做验证。

## 验证

- `pytest tests/test_meta_hot_posts_europe_fit.py tests/test_meta_hot_posts_service.py tests/test_meta_hot_posts_store.py tests/test_meta_hot_posts_routes.py -q`
- 未登录 `/xuanpin/meta-hot-posts` 继续 302。
- 已登录且有 `meta_hot_posts` 权限用户可访问页面和新增 API。
- 卡片底部出现两个手动分析按钮；Modal 有请求/结果 Tab 和强制重新分析按钮。
