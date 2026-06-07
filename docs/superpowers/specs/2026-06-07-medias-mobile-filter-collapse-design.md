# 素材管理移动端筛选折叠设计

- 状态：已确认
- 日期：2026-06-07
- 页面：`/medias` 产品管理与视频素材管理 tab

## 文档锚点

- [AGENTS.md](../../../AGENTS.md)：素材管理位于 `web/`，改动前先落文档，改动后按项目验证顺序执行。
- [web/static/CLAUDE.md](../../../web/static/CLAUDE.md)：`/medias` 前端遵守 Ocean Blue 控件规范，不新增后端路径或 CSRF 变更。
- [2026-05-20 视频素材 sticky 分页](2026-05-20-media-video-material-sticky-pagination-design.md)：列表滚动发生在 `.oc-list` 内，筛选区、分页和表头需要与内部滚动容器协同。
- [2026-06-06 产品表头位置修复](2026-06-06-medias-product-table-header-position-fix.md)：产品与视频列表表头固定在 `.oc-list` 容器顶部，不恢复页面级克隆表头或全页 sticky 偏移。

## 背景

移动端 `/medias` 首屏里，产品标题、操作按钮、tab、搜索框、ROAS、投放、来源、创建时间和链接检测等筛选项连续占据较多高度。运营向下查看数据时，实际可见的数据区域过小，表格表头和数据行需要更早进入视口。

## 目标

1. 移动端为产品管理和视频素材管理的筛选区增加统一的展开 / 收起入口。
2. 筛选区默认保持展开，方便首屏调整搜索、日期和下拉筛选；用户可以手动收起。
3. 用户在数据列表内向下滚动时自动收起当前 tab 的筛选控件，只保留一行紧凑入口。
4. 用户点击紧凑入口后可重新展开并管理搜索框、日期和各类下拉筛选项。
5. 桌面端保持现有筛选区布局和 sticky / 内部滚动行为。

## 实现

`web/templates/medias_list.html`：

- 在产品管理 `.oc-toolbar` 和视频素材管理 `.oc-vm-toolbar` 内加入移动端专用 `.oc-mobile-filter-toggle`。
- 产品管理筛选控件保留在 `.oc-toolbar-filter-row`，视频素材筛选控件放入 `.oc-vm-filter-row`，由 `.is-filter-collapsed` 控制折叠显示。
- 移动端折叠时隐藏具体筛选控件，只显示一行按钮；桌面端隐藏折叠按钮并保持原布局。
- 监听当前 tab 的 `.oc-list` 内部滚动。移动端检测到向下滚动超过阈值后，自动给当前筛选栏添加 `.is-filter-collapsed`。
- 展开 / 收起按钮同步 `aria-expanded` 和按钮文案；切换 tab 时只影响当前 tab 的筛选栏。

## 非目标

- 不改变 `/medias/api/products`、`/medias/api/video-materials` 或分页参数。
- 不改变筛选字段、默认值、搜索防抖、日期范围面板或链接检测逻辑。
- 不恢复页面级滚动或克隆表头。

## 验证

1. 静态测试确认产品管理和视频素材管理都有移动端筛选折叠按钮。
2. 静态测试确认移动端 CSS 只在小屏折叠筛选控件，桌面布局保持。
3. 静态测试确认内部 `.oc-list` 向下滚动会自动收起当前 tab 筛选区。
4. 执行：

```bash
pytest tests/test_medias_list_filters.py tests/test_media_video_materials_routes.py::test_medias_page_renders_video_material_management_tab -q
node --check web/static/medias.js
```
