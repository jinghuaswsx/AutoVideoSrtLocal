# 视频素材广告表现列设计

- 状态：已确认
- 日期：2026-06-05
- 页面：`/medias/video`
- 接口：`GET /medias/api/video-materials`

## 文档锚点

- [AGENTS.md](../../../AGENTS.md)：素材管理位于 `web/`，改动前先落文档，改动后按项目验证顺序执行。
- [web/static/CLAUDE.md](../../../web/static/CLAUDE.md)：`/medias` 前端路径、Ocean Blue 设计系统和静态资源约束。
- [2026-05-28 素材管理产品投放汇总缓存](2026-05-28-medias-product-ad-status-cache-design.md)：现有产品行投放、ROAS、实时开放日并入口径。
- [2026-06-05 素材管理单量情况列](2026-06-05-medias-order-stats-column-design.md)：今天 / 昨天 / 7天 / 30天均使用 Meta 业务日窗口。
- [2026-04-28 True ROAS attribution migration](../../../db/migrations/2026_04_28_true_roas_attribution.sql)：`meta_ad_daily_ad_metrics` 的 `spend_usd`、`purchase_value_usd`、`roas_purchase`、`meta_business_date` 字段。
- [2026-05-08 market country migration](../../../db/migrations/2026_05_08_meta_market_country.sql)：`market_country` 是从广告命名解析出的运营市场国家，不是 Meta API geo breakdown。

## 背景

视频素材管理列表已经能按素材名判断是否存在广告计划，但当前只取第一条匹配广告用于展示 campaign 链接。运营需要在视频素材维度直接看到该素材名匹配到的所有 AD 数据，而不是产品维度数据。

## 目标

1. 视频素材列表新增三列：`总消耗`、`ROAS`、`国家情况`。
2. 每个视频素材按素材名匹配所有广告 AD，并聚合该素材的：
   - 总消耗
   - 今天消耗
   - 昨天消耗
   - 最近 7 天消耗
   - 最近 30 天消耗
   - 总 ROAS
3. `国家情况` 一行一个国家；每行展示该国家的消耗和 ROAS。
4. 数据必须基于视频素材维度，不能复用产品行缓存里的产品级消耗。
5. 列表请求保持快速：当前页素材只做批量广告候选查询，不按素材逐条请求。

## 数据关系

视频素材主表是 `media_items`。广告 AD 数据来自 `meta_ad_daily_ad_metrics`，开放业务日可补充 `meta_ad_realtime_daily_ad_metrics` 的最新 `realtime_partial` 快照。

广告与视频素材的匹配规则：

- `media_items.product_id = ad_metrics.product_id`
- 且广告名或归一化广告名包含素材 `filename` 或 `display_name`
- 为兼容文件扩展名差异，后端匹配时也允许不含扩展名的 `filename` / `display_name`
- 只统计 `spend_usd > 0` 的广告行
- 同一素材匹配同一广告 metric row 多个条件时只计一次

国家数据：

- 日终表使用 `meta_ad_daily_ad_metrics.market_country`
- 实时表使用 `meta_ad_realtime_daily_ad_metrics.country_code`
- 空国家仍计入素材总消耗和总 ROAS，但不进入国家明细
- `MULTI` 作为命名解析出的多国市场保留展示，不拆成多个国家

## 日期窗口

日期使用 Meta 业务日。

- 今天：`current_meta_business_date()`
- 昨天：今天减 1 个 Meta 业务日
- 7天：含今天在内最近 7 个 Meta 业务日
- 30天：含今天在内最近 30 个 Meta 业务日

日终表按 `COALESCE(meta_business_date, report_date)` 统计。若实时 AD 表存在，则对每个 `(business_date, ad_account_id)` 取最新 `snapshot_at` 的 `realtime_partial` 行并入；同一产品、同一账号、同一开放业务日已有实时数据时，日终行跳过，避免重复，同时不误删同账号下其他产品的日终数据。

## 返回结构

每个视频素材 JSON 新增：

```json
{
  "ad_performance": {
    "total_spend_usd": 0.0,
    "today_spend_usd": 0.0,
    "yesterday_spend_usd": 0.0,
    "last_7d_spend_usd": 0.0,
    "last_30d_spend_usd": 0.0,
    "purchase_value_usd": 0.0,
    "today_roas": null,
    "yesterday_roas": null,
    "last_7d_roas": null,
    "last_30d_roas": null,
    "roas": null,
    "matched_ad_count": 0,
    "countries": [
      {
        "country": "DE",
        "spend_usd": 0.0,
        "purchase_value_usd": 0.0,
        "roas": null,
        "matched_ad_count": 0
      }
    ]
  },
  "mk_cover_url": "/medias/api/mk-media?path=uploads2%2Fposter.jpg",
  "source_raw_cover_url": "/medias/raw-sources/322/cover",
  "preview_cover_url": "/medias/raw-sources/322/cover"
}
```

`has_ad_plan` 和 `ad_plan_detail` 保持兼容，仍以匹配广告中最新且消耗最高的 campaign 作为深链入口。

`preview_cover_url` 是视频素材列表唯一可用于预览列的封面图片地址，优先级为：绑定明控素材封面图 `mk_cover_url`、翻译视频关联的原始素材封面 `source_raw_cover_url`、当前素材手动图片封面 `cover_url`。它不得等于视频播放地址，也不得指向明显的视频对象；`thumbnail_url` 是视频抽帧缩略图，不用于列表预览。`video_url` 只允许在用户点击封面播放时赋给播放器。

## 前端设计

`web/static/media_video_materials.js`：

- 表头在“广告计划”后新增：
  - `总消耗`
  - `ROAS`
  - `国家情况`
- `总消耗` 单元格显示五个紧凑指标：总、今、昨、7天、30天。
- `总消耗` 单元格内部渲染为三行六列的小表格：第一列是说明列，第一行依次是 `说明`、`今天`、`昨天`、`7天`、`30天`、`总消耗`；第二行是 `广告消耗` 和对应金额；第三行是 `ROAS` 和对应时间窗口 ROAS；不显示额外的 `总计` 标签。
- `ROAS` 单元格参考产品管理 ROAS 指标样式显示总 ROAS；无消耗时显示 `-`。
- `国家情况` 单元格参考产品管理“语种和投放情况”的国家/语种分行样式，按消耗倒序渲染国家行：`德(DE) 消耗 $x ROAS y`。
- 无国家数据时显示 `-`。
- 视频素材预览封面使用 180x320 竖版尺寸，中间显示播放按钮；列表行只渲染 `preview_cover_url` 图片，不渲染或预加载视频，不使用视频第一帧缩略图；点击后才在页面内弹出视频播放器播放该素材原视频，不跳出列表页。

`web/templates/medias_list.html`：

- 扩展 `.oc-vm-table` 最小宽度和新增列宽。
- 新增紧凑指标样式，使用现有 token，不引入新色板。
- 视频素材列表分页或重载后必须重置外层横向/纵向滚动容器，表头保持在列表滚动容器顶部可见。
- 新增视频播放弹窗样式，列表行内只加载封面，不预加载所有视频。

## 非目标

- 不新增数据库表。
- 不改变 Meta 同步逻辑。
- 不改变产品管理列表的产品级投放缓存。
- 不新增筛选项或排序项。
- 不把广告国家当作 Meta API geo breakdown。

## 验证

1. 后端单元测试覆盖：
   - 当前页素材只触发批量广告查询。
   - 一个素材可匹配多条广告并聚合总消耗、购买价值和 ROAS。
   - 今天 / 昨天 / 7天 / 30天按 Meta 业务日窗口聚合。
   - 国家明细按国家聚合，空国家只进总计。
   - 同一广告 metric row 匹配多个条件时只计一次。
2. 路由测试确认 `/medias/api/video-materials` 仍透传筛选参数。
3. 静态测试确认视频素材表新增三列表头与渲染函数。
4. 执行：

```bash
pytest tests/test_media_video_materials.py tests/test_media_video_materials_routes.py tests/test_medias_pages_routes.py tests/test_order_analytics_template_layout.py -q
node --check web/static/media_video_materials.js
```
