# 视频素材广告计划详情跳转

## Goal

在 `素材管理 -> 视频素材管理` 列表中，让“有广告计划”的单元格显示完整，并点击后新开浏览器 tab，聚焦到对应的广告分析 Campaign 详情页。

## Anchors

- `AGENTS.md`：文档驱动代码、隔离 worktree、验证顺序。
- `web/static/CLAUDE.md`：Ocean Blue 控件与静态资源约束。
- `docs/superpowers/specs/2026-05-13-media-video-material-bindings-design.md`：视频素材管理 tab、广告计划筛选和列表接口。
- `docs/superpowers/specs/2026-05-08-ads-analytics-tabs-design.md`：`数据分析 -> 广告分析 -> Campaign` 列表与详情页。

## Scope

做：

- “广告计划”列放宽列宽，徽标下方小字拆成两行，不使用省略号截断日期时间。
- 有广告计划且能拿到 Campaign code 的素材，把广告计划单元格渲染成可点击控件。
- 点击该控件时由用户点击事件直接调用 `window.open(url, "_blank")`，并在返回窗口句柄时调用 `focus()`。
- 新开的 `/order-analytics` 页面根据 URL 参数自动激活“广告分析”顶层 tab、“Campaign”子 tab，并进入对应 Campaign 详情。
- 从素材列表深链进入 Campaign 详情时，详情页日期默认选择最近一个月：结束日期使用当前 Meta 业务日，开始日期使用结束日期前一个日历月的同日。
- 视频素材接口优先返回最近的 `meta_ad_daily_campaign_metrics` Campaign code/name/account；查不到时回退到产品编码作为 Campaign code。

不做：

- 不新增数据库表。
- 不改变“有广告计划”的现有判定口径：仍以 `media_items.pushed_at IS NOT NULL` 或成功 `media_push_logs` 为准。
- 不新增 Meta 后台外链。
- 不改变广告分析接口返回结构。

## URL Contract

视频素材列表生成内部深链：

```text
/order-analytics?tab=ads&ads_level=campaign&ads_code=<normalized_campaign_code>&ads_name=<campaign_name>&ad_account_id=<optional>
```

参数含义：

- `tab=ads`：打开广告分析顶层 tab。
- `ads_level=campaign`：打开 Campaign 子 tab。
- `ads_code`：传给 `/order-analytics/ads/detail?level=campaign&code=...`。
- `ads_name`：详情页标题的预填展示值，接口返回 name 后可覆盖。
- `ad_account_id`：可选；存在时详情页广告户筛选预选该账户。

## UI Behavior

- 无广告计划：保持灰色“没有广告计划”，小字显示 `-`。
- 有广告计划且有 URL：显示绿色“有广告计划”，下方显示两行日期时间；鼠标悬浮时有可点击状态。
- 有广告计划但无 URL：仍显示绿色状态和两行日期时间，但不可点击。
- 点击绑定按钮不受行点击影响；本次只让广告计划单元格触发跳转。
- 只有通过 `tab=ads&ads_level=campaign&ads_code=...` 深链自动进入详情时，才覆盖详情日期为最近一个月；普通打开广告分析、列表筛选和手动切换详情沿用原日期默认逻辑。

## Testing

- `appcore/media_video_materials.py`：序列化结果包含 `ad_plan_detail`，优先使用 Campaign 字段，缺失时用产品编码回退。
- `web/static/media_video_materials.js`：渲染包含打开新 tab 并 focus 的点击逻辑，以及两行小字 class。
- `web/templates/order_analytics.html`：包含读取 URL 参数并自动打开 Campaign 详情的逻辑；深链详情进入前将详情日期覆盖为最近一个月。
- 路由页面测试覆盖 `/medias/` 和 `/order-analytics` 的模板片段。

## Verification

```bash
pytest tests/test_media_video_materials.py tests/test_media_video_materials_routes.py tests/test_order_analytics_ads.py tests/test_order_analytics_template_layout.py -q
```
