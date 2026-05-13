# Meta 热帖可见分页控件设计

最后更新：2026-05-13

## 背景

`/xuanpin/meta-hot-posts` 列表接口已经支持 `page`、`page_size`、`total`，页面也已经固定每页请求 50 条并按互动变化数排序。当前页面只在状态文本中显示 `Page N`，没有可点击的分页选择器。运营无法从页面直接翻页或跳到末页。

## 目标

- 在 Meta 热帖卡片展示区顶部和底部都显示分页选择器。
- 两处分页选择器展示同一份分页状态，点击任一处都会加载对应页。
- 分页选择器包含：首页、上一页、当前页附近页码、下一页、末页。
- 同步、筛选、重置后回到第 1 页；分析商品后停留当前页。
- 空结果时禁用翻页，并显示总数为 0。

## 设计

- 在 `web/templates/meta_hot_posts.html` 中，在卡片 grid 上方新增 `id="mhPagerTop"`，在 grid 下方新增 `id="mhPagerBottom"`。
- 使用 `.mh-pager` 样式控制分页条布局，按钮复用页面内已有视觉语义，但保持 32px 左右高度，适配移动端换行。
- 增加 `renderMetaHotPager(data)`：
  - 从 API 响应读取 `total`、`page`、`page_size`。
  - 使用 `Math.ceil(total / page_size)` 计算总页数。
  - 渲染：首页、上一页、页码窗口、下一页、末页和 `第 X / Y 页 · 共 N 条 · 每页 50 条`。
  - 页码窗口最多展示当前页前后各 2 页。
- `loadMetaHotPosts(page)` 在卡片渲染后调用 `renderMetaHotPager(data)`，并把 `mhPage` 规范到 API 返回的页码。

## 验证

- `pytest tests/test_meta_hot_posts_routes.py tests/test_meta_hot_posts_store.py tests/test_xuanpin_routes.py -q`
- 模板测试必须覆盖顶部/底部分页容器、分页渲染函数、分页按钮文案和 `page_size` 计算。
- 未登录访问 `/xuanpin/meta-hot-posts` 应返回 302。
