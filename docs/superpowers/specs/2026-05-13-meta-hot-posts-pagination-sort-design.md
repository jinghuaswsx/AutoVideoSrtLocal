# Meta 热帖分页与排序修正设计

最后更新：2026-05-13

## 背景

`/xuanpin/meta-hot-posts` 页面用于展示本地沉淀的 wedev Meta 热帖。既有设计已经把 wedev 的 `sync_period_likes` 保存为“互动变化数”，并在卡片顶部展示“互动变化”。

当前列表接口默认按 `latest_likes` 排序，前端每页请求 30 条。这会让页面优先显示当前累计交互更高的帖子，而不是近期互动变化更高的帖子；同时不满足运营查看一页 50 条的要求。

## 目标

- Meta 热帖列表默认每页展示 50 条。
- 列表默认按互动变化数量从高到低排序。
- 排序在后端 SQL 层完成，确保先全量排序再分页，不做前端单页内排序。
- 保留现有筛选条件、权限和 API 路径。

## 设计

- `web/templates/meta_hot_posts.html` 将 `mhPageSize` 从 30 调整为 50。
- `appcore.meta_hot_posts.store.list_hot_posts()` 保持接受 `page` / `page_size` 参数；前端传入 50 后沿用现有分页返回结构。
- 列表 SQL 默认排序改为：
  - `COALESCE(p.sync_period_likes, 0) DESC`
  - `p.creation_time DESC`
  - `p.id DESC`
- `creation_time` 和 `id` 继续作为稳定兜底排序，避免互动变化数相同或为空时结果抖动。

## 验证

- `pytest tests/test_meta_hot_posts_store.py tests/test_meta_hot_posts_routes.py -q`
- 未登录访问 `/xuanpin/meta-hot-posts` 应返回 302。
- 管理员登录访问 `/xuanpin/meta-hot-posts` 应返回 200。
