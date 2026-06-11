# Tabcut 今日新增与沉浸式刷视频设计

最后更新：2026-06-11

## 背景

运营在选品中心使用 Type Card/Tabcut 看视频时，需要每天快速查看系统今天新采集到的视频，并且在手机上像 Meta 热帖一样连续沉浸式刷视频。用户已确认：

- Typecard 对应本项目的 Tabcut 模块。
- 「今日新增」按系统今天第一次采集/入库到 `tabcut_videos.first_seen_at` 的视频计算，而不是按 TikTok 视频发布时间 `create_time`。
- 采用方案 1：保留 Tabcut 现有列表卡片、筛选、标注、AI 评估和任务入口；移动端从视频卡片进入沉浸浮层连续刷。

## 锚点

- `AGENTS.md#文档驱动代码`：新需求先固化为 spec，再作为代码锚点。
- `AGENTS.md#主题指引`：选品中心相关行为以 `docs/superpowers/specs/` 为事实来源。
- `docs/superpowers/specs/2026-05-12-tabcut-crawler-design.md#数据库`：`tabcut_videos.first_seen_at` 是视频维表首次发现时间。
- `docs/superpowers/specs/2026-05-15-meta-hot-posts-today-new-tab-design.md#SQL Semantics`：Meta 热帖「今日新增」按 `first_seen_at` 的服务器当天范围筛选。
- `docs/superpowers/specs/2026-06-10-meta-hot-posts-mobile-video-overlay-controls.md#设计`：移动端全屏浮层支持下载、关闭、上下滑切换、文案/商品信息展开收起。
- `docs/superpowers/specs/2026-06-10-tabcut-video-new-material-task-integration.md#前端行为`：Tabcut 视频卡已有本地视频就绪判断、素材入库和任务按钮。
- `web/templates/CLAUDE.md#CSRF / 路由守卫`：新增接口和模板行为保持登录和管理员守卫；POST 继续带 CSRF。
- `web/static/CLAUDE.md#Ocean Blue 设计系统`：新增移动端控件沿用现有 Tabcut/Meta 选品页面色系和密度，不引入新色板。
- `docs/superpowers/specs/2026-06-08-targeted-pytest-verification.md#AutoVideoSrtLocal pytest 最小化规则`：改动后运行相关 focused tests，不默认全量 pytest。

## 目标

1. Tabcut 增加子 Type「今日新增」，让运营每天快速查看系统当天首次发现的视频。
2. 「今日新增」复用 Tabcut 视频卡片的数据结构、卡片渲染、标注、AI 评估和任务入口。
3. Tabcut 默认视频榜和「今日新增」都支持移动端沉浸式刷视频。
4. 移动端浮层支持上下滑切换前后视频，便于连续查看 Type Card/Tabcut 采集到的视频。
5. 浮层左上角显示产品概要信息，默认收起，支持展开和收起。
6. 浮层右上角提供下载按钮和关闭按钮。
7. 关闭浮层后回到当前播放视频对应的卡片位置。

## 非目标

- 不改变 Tabcut 采集器、定时任务、Tabcut 登录态或视频下载流程。
- 不新增数据库 schema；使用既有 `tabcut_videos.first_seen_at`。
- 不把移动端 Tabcut 页面整体改成默认全屏 Feed；仍保留列表、筛选和卡片操作。
- 不改变商品榜和推荐产品20个的主体展示逻辑。
- 不改变 Tabcut 分享页的权限边界；分享页是否展示沉浸入口按现有页面能力复用，不新增公开下载能力。

## 信息架构

Tabcut 页面保留一级选品中心 Tab，并在 Tabcut 内部的视图导航增加：

- `视频榜`：默认加载视频候选数据，保持原筛选和分页。
- `今日新增`：加载系统当天首次发现的视频。
- `商品榜`：保持现状。
- `推荐产品20个`：保持现状。

「今日新增」是视频子视图，不是商品子视图。进入该视图时：

- 视频筛选控件仍可用，包括搜索、类目、标注、发布时间、数据来源、销量、GMV、价格和排序。
- 商品榜日期、商品榜类目、商品榜类型/周期控件继续只在商品榜启用。
- 分页和每页条数沿用 Tabcut 视频榜。

## 数据契约

新增后端响应构建函数：

- `appcore.tabcut_selection.store.list_today_new_video_candidates(args)`
- `appcore.tabcut_selection.service.build_today_new_videos_response(args)`

新增 API：

- `GET /xuanpin/api/tabcut/today-new`
- 如需要保持 medias 旧层一致性，可增加 `GET /medias/api/tabcut-selection/today-new` 作为内部别名。

查询语义：

```sql
WHERE v.first_seen_at >= CURDATE()
  AND v.first_seen_at < DATE_ADD(CURDATE(), INTERVAL 1 DAY)
```

排序语义：

```sql
ORDER BY v.first_seen_at DESC,
         COALESCE(vs.play_count, c.play_count, 0) DESC,
         c.score DESC,
         c.video_id ASC
```

查询应继续：

- 只返回每个 `video_id` 的去重候选卡片。
- 复用 `list_video_candidates` 已有筛选白名单和 SQL 参数化方式。
- 返回卡片需要的本地视频字段：`local_video_path`、`local_video_status`、`local_video_cover_path`、`local_video_duration_seconds`。
- 返回产品概要字段：`primary_item_name`、`primary_item_id`、`primary_item_pic_url`、`primary_item_price_min/max`、`currency_symbol`、`primary_item_sold_count`、`goods_sold_count_7d`、`goods_gmv_7d`、`category_l1/2/3_name`、`score`。

## 前端行为

### 子 Type 加载

- `tabcutView` 增加 `today_new`。
- 点击「今日新增」加载 `/xuanpin/api/tabcut/today-new`。
- `paramsFor(page)` 对 `videos` 和 `today_new` 走同一套视频筛选参数。
- `renderTabcut(data)` 对 `videos` 和 `today_new` 都渲染 `renderVideoCard(row)`。
- 页面状态文案对「今日新增」显示 `今日新增 · 当前页 X 条视频 · 共 Y 页 · 总 Z 条视频`。

### 卡片入口

- Tabcut 视频卡片保留当前封面、播放按钮、详情页、标注、Fine AI、任务按钮和产品 mini 卡。
- 对本地视频已就绪的卡片，封面上增加沉浸播放入口；移动端点击封面也可直接进入沉浸浮层。
- 本地视频未就绪时，保持当前 TikTok iframe/外链兜底，不展示下载能力。

### 沉浸浮层

复用 Meta 热帖浮层的交互模型，但数据来自 Tabcut 当前渲染列表：

- 打开浮层前暂停 Tabcut 页面内其它 `<video>`。
- 浮层固定覆盖 viewport，视频使用 `controls autoplay playsinline preload="metadata"`。
- 上滑切换当前列表里的下一个可直接播放本地 MP4 的 Tabcut 视频。
- 下滑切换上一个可直接播放本地 MP4 的 Tabcut 视频。
- 到达首尾不循环。
- 右上角下载按钮使用 `/xuanpin/api/tabcut/videos/<video_id>/local-video`。
- 右上角关闭按钮暂停并移除浮层视频。
- 点击背景和 `Escape` 可关闭浮层。
- 关闭后滚动回 `.tabcut-video-card[data-video-id="<video_id>"]`。

### 左上角产品概要

默认收起态：

- 单行或两行显示商品名。
- 显示核心指标摘要：价格、销量、类目或评分中优先有值的 2-3 项。

展开态：

- 显示商品主图。
- 显示商品名、商品 ID、价格区间、商品销量、视频播放/点赞/分享/评论、类目、发布时间、首次发现时间。
- 展开区域限制最大高度并可内部滚动，避免遮挡视频主体。

## 移动端样式

- `max-width: 768px` 下沉浸浮层使用 `100vw x 100dvh`。
- 浮层顶部考虑 `safe-area-inset-*`。
- 下载和关闭按钮使用透明/半透明深色底，保持触控尺寸不小于 40px。
- 左上信息层半透明深色背景，文字白色，默认不超过右上按钮区域。
- 不新增可见操作说明文案，避免遮挡视频。

## 错误处理

- 今日新增 API 无数据时返回空列表，前端展示「今日暂无新抓到的视频」。
- API 失败时复用现有 `Load failed` 状态，显示后端错误。
- 浮层打开时找不到本地视频 URL 则 no-op，不替换当前页面状态。
- 上下滑找不到可播放前后项时保持当前视频。
- 下载链接如浏览器因同源或响应头限制改为打开新窗口，可以接受。

## 验证

自动化：

```bash
python3 scripts/pytest_related.py --base origin/master --run
pytest tests/test_tabcut_selection_store.py tests/test_xuanpin_routes.py tests/test_tabcut_selection_routes.py -q
python -m compileall appcore/tabcut_selection web/routes tests -q
git diff --check
```

手动回归：

- 未登录访问 `/xuanpin/tabcut` 继续 302。
- 管理员访问 `/xuanpin/tabcut` 返回 200，默认视频榜加载。
- 点击「今日新增」后请求 `/xuanpin/api/tabcut/today-new`，空数据展示「今日暂无新抓到的视频」。
- 移动端在视频榜打开本地视频浮层后，可上下滑切换当前页前后可播放视频。
- 移动端在「今日新增」打开本地视频浮层后，可上下滑切换当前页前后可播放视频。
- 浮层左上产品概要可展开/收起，右上下载和关闭可用。
- 关闭浮层后回到当前视频卡片位置。

全量 pytest 默认跳过；本次属于 Tabcut store、路由和模板局部改动，按 targeted pytest 规则运行 focused tests。
