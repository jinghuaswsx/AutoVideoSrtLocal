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

问题来自旧的全页 sticky 表头偏移机制与后续列表容器滚动方案叠加：

1. 旧代码把真实表格 `.oc-table thead th` 的 `top` 设为 `--sticky-table-thead-top`，该变量按页面顶部 header / tab / toolbar / pager 高度计算。
2. 远端最新实现已经把 `/medias` 改为页面自身不滚动、`.oc-list` 作为内部滚动容器，并在容器内使用 `position: sticky; top: 0` 固定真实表头。

在内部滚动容器方案下，真实 `<thead>` 只能相对 `.oc-list` 顶部 sticky，不能继续使用页面级 `--sticky-table-thead-top`，否则刷新或初始化时会把表头推入表体中部。

## 目标

1. 产品管理列表刷新后，真实表格 `<thead>` 保持在 `.oc-list` 顶部，不被 `--sticky-table-thead-top` 推到表体中间。
2. 产品管理与视频素材管理都使用内部滚动容器内的原生 sticky 表头：`position: sticky; top: 0`。
3. 横向滚动仍由 `.oc-list` 自身承担，不新增克隆表头。
4. 不改产品列表 API、分页状态、列顺序、列宽、后端查询或数据库。

## 实现

- `web/templates/medias_list.html`：
  - 保留顶部容器滚动方案里的 `.oc-table thead th, .oc-vm-table thead th { position: sticky !important; top: 0 !important; }`。
  - `.oc-table thead th` 的基础样式只保留背景、字体、对齐等视觉声明。
  - 移除基础样式里的 `top: var(--sticky-table-thead-top, 0px)` 和旧 sticky `z-index`，避免页面级 sticky 偏移覆盖容器级 `top:0`。

## 验证

1. 静态回归测试确认容器级表头使用 `sticky top:0`，且基础 `.oc-table thead th` 不再引用 `--sticky-table-thead-top`。
2. 路由测试确认 `/medias/` 未登录仍 302，已登录仍能渲染。
3. JS 语法检查确认 `web/static/medias.js` 未被破坏。
4. Chrome 真实页面量测：
   - 初始加载 `/medias/?page=3` 后，真实表头 top 位于列表容器顶部，第一行紧跟表头。
   - 滚动 `.oc-list` 后，真实表头仍固定在列表容器顶部。

执行命令：

```bash
pytest tests/test_medias_edit_modal_layout.py::test_medias_product_table_header_sticks_inside_list_container tests/test_medias_pages_routes.py -q
node --check web/static/medias.js
```
