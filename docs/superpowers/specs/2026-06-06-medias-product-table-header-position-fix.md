# 素材管理产品表头位置修复

- 状态：已确认
- 日期：2026-06-06
- 页面：`/medias/?page=<n>` 的产品管理 tab

## 文档锚点

- [AGENTS.md](../../../AGENTS.md)：素材管理位于 `web/`，改动前先落文档，改动后按项目验证顺序执行。
- [web/static/CLAUDE.md](../../../web/static/CLAUDE.md)：`/medias` 前端路径与 Ocean Blue 设计系统约束。
- [2026-05-20 视频素材 sticky 分页](2026-05-20-media-video-material-sticky-pagination-design.md)：分页或重载后滚动容器应回到顶部，表头保持在列表区域顶部可见。
- [2026-06-05 素材管理列表 SKU 与时间列精简](2026-06-05-medias-list-sku-and-time-columns-design.md)：产品管理列表表头与列宽是当前调整对象。

## 背景

刷新 `http://172.16.254.106/medias/?page=3` 后，产品管理表格的表头可能出现在列表中部，覆盖在产品行之间，而不是位于表格顶部或固定在筛选区下方。

当前模板同时存在两套表头固定机制：

1. 真实表格的 `.oc-table thead th` 使用 `position: sticky`，并把 `top` 设为 `--sticky-table-thead-top`。
2. `oc-sticky-header-wrapper` 会克隆当前表格的 `<thead>`，用于全页滚动时在筛选区下方显示浮动表头。

在 `.oc-list { overflow:auto }` 的横向滚动容器内，真实 `<thead>` 的 sticky top 会被计算到页面 sticky 偏移，初始化或刷新时可能把真实表头推入表体中部。克隆表头已经负责全页滚动时的固定展示，真实表头不应再承担 sticky 行为。

## 目标

1. 产品管理列表刷新后，真实表格 `<thead>` 保持在表格顶部，不被 `--sticky-table-thead-top` 推到表体中间。
2. 滚动到表格上方内容越过 sticky 边界后，继续使用现有 `oc-sticky-header-wrapper` 克隆表头作为浮动表头。
3. 横向滚动同步逻辑保持不变。
4. 不改产品列表 API、分页状态、列顺序、列宽、后端查询或数据库。

## 实现

- `web/templates/medias_list.html`：
  - `.oc-table thead th` 保留背景、字体、对齐等表头样式。
  - 移除真实 `<thead>` 单元格的 `position: sticky`、`top: var(--sticky-table-thead-top, 0px)` 和对应 sticky `z-index`。
  - 保留 `oc-sticky-header-wrapper` 及 `syncFloatingHeader()`，让克隆表头继续承担全页滚动时的固定展示。

## 验证

1. 静态回归测试确认 `.oc-table thead th` 不再设置 sticky/top，且克隆表头 wrapper 仍存在。
2. 路由测试确认 `/medias/` 未登录仍 302，已登录仍能渲染。
3. JS 语法检查确认 `web/static/medias.js` 未被破坏。
4. Chrome 真实页面量测：
   - 初始加载 `/medias/?page=3` 后，真实表头 top 大于等于表格 top，第一行紧跟表头。
   - 滚动后 `stickyHeaderWrapper` 显示在 `--sticky-table-thead-top` 附近。

执行命令：

```bash
pytest tests/test_medias_edit_modal_layout.py::test_medias_product_table_uses_single_floating_header_mechanism tests/test_medias_pages_routes.py -q
node --check web/static/medias.js
```
