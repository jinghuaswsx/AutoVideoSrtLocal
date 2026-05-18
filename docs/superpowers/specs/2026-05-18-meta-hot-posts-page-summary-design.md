# Meta 热帖页码素材数摘要设计

日期：2026-05-18

## 背景

`/xuanpin/meta-hot-posts` 的素材库已经通过分页控件展示 `第 X / Y 页 · 共 N 条 · 每页 50 条`。页面左侧红框位置目前只显示 `Page 1`，运营在扫卡片时无法直接看到当前页实际展示了多少条视频、总页数和素材总量。

## 目标

- 在卡片区上方的状态位置显示当前页视频条数、总页数和总视频素材数。
- 素材库与今日新增沿用分页接口返回的 `total`、`page`、`page_size`，当前页条数使用本次响应 `items.length`。
- 欧洲Top50、美国Top50 没有分页时显示当前列表条数和总视频素材数，不显示页数。
- 保留现有分页控件、筛选条件、卡片渲染和 API 路径。

## 展示口径

- 素材库示例：`当前页 50 条视频 · 共 65 页 · 总 3228 条视频素材`
- 今日新增示例：`今日新增 · 当前页 12 条视频 · 共 1 页 · 总 12 条视频素材`
- Top50 示例：`欧洲Top50 · 当前 50 条视频素材`

当 `total=0` 时显示 `当前页 0 条视频 · 共 0 页 · 总 0 条视频素材`。

## 设计

- 在 `web/templates/meta_hot_posts.html` 中新增前端函数 `renderMetaHotPageSummary(data, items, label = '')`。
- 该函数只负责生成状态文案：
  - `currentCount = items.length`
  - `total = Number(data.total || 0)`
  - `pageSize = Number(data.page_size || mhPageSize)`
  - `totalPages = total > 0 ? Math.ceil(total / pageSize) : 0`
- `loadMetaHotPosts()` 和 `loadTodayNewMaterials()` 在渲染卡片、分页后，将 `#mhStatus` 更新为摘要文案。
- `loadEuropeTopMaterials()` 和 `loadUsTopMaterials()` 使用无分页摘要，避免出现 `共 1 页` 的误导。

## 验证

- `pytest tests/test_meta_hot_posts_routes.py tests/test_xuanpin_routes.py -q`
- 未登录访问 `/xuanpin/meta-hot-posts` 应返回 302。
- 管理员或有权限用户访问 `/xuanpin/meta-hot-posts` 应返回 200。
