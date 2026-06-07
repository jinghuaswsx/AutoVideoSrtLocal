# 素材管理移动端适配

- 状态：已确认
- 日期：2026-06-07
- 页面：`/medias/`、`/medias/video`

## 文档锚点

- [AGENTS.md](../../../AGENTS.md)：素材管理位于 `web/`，改动前先落文档，改动后按项目验证顺序执行。
- [web/static/CLAUDE.md](../../../web/static/CLAUDE.md)：`/medias` 前端路径、Ocean Blue 设计系统和移动端单列约束。
- [2026-06-06 素材管理产品表头位置修复](2026-06-06-medias-product-table-header-position-fix.md)：产品管理与视频素材管理使用内部滚动容器和原生 sticky 表头。
- [2026-06-05 视频素材广告表现列设计](2026-06-05-video-material-ad-performance-design.md)：视频素材表保留广告表现、ROAS 和国家情况列，不改接口与聚合口径。

## 背景

移动端打开素材管理时，产品管理和视频素材管理需要保留 PC 端表格数据结构。运营更习惯按列名横向对照数据，移动端应通过左右滑动查看完整表格，而不是把表格行重排成卡片。

2026-06-07 首版移动端适配曾把产品管理和视频素材管理改为卡片式行布局，导致列名和数据脱离 PC 表格结构，移动端读数反而不清晰。本修订明确撤回卡片化，只保留外层头部、筛选和滚动容器的移动端收紧。

## 目标

1. 手机宽度下顶部说明、tab、操作按钮和筛选区不横向溢出。
2. `/medias/` 产品管理表在小屏保留 PC 端 `<thead>` / `<tbody>` 和列宽结构，用户通过 `.oc-list` 左右滑动查看完整表格。
3. `/medias/video` 视频素材表在小屏保留 PC 端表格结构，广告表现、ROAS、国家情况等列名必须与对应数据列对齐。
4. 页面高度使用动态视口和 safe area，避免移动浏览器底部工具栏遮住列表内容。
5. 桌面端表格列宽、sticky 表头、API、分页和筛选行为保持不变。

## 实现

- `web/templates/medias_list.html`：
  - 在 `max-width: 768px` 下压缩页面 padding、tab、toolbar 和列表间距，并使用 `100dvh` + `env(safe-area-inset-bottom)`。
  - 在 `max-width: 640px` 下禁止把 `.oc-table-medias` 与 `.oc-vm-table` 卡片化，不隐藏 `<thead>`，不改 `tr` / `td` display。
  - `.oc-list` 保持 `overflow:auto`，让产品表和视频素材表沿用桌面 `min-width` 横向滑动。
  - 移动端筛选折叠可保留，但不能改变表格列名和数据对齐。

## 非目标

- 不新增或调整产品、视频素材 API。
- 不改变列表字段、排序、分页、筛选参数或广告数据口径。
- 不改移动端导航栏、登录页或素材编辑弹窗。
- 不把产品管理或视频素材管理表重排为卡片。

## 验证

1. 静态测试确认移动端保留表格横向滑动结构，没有卡片化或隐藏表头 CSS。
2. 路由测试确认 `/medias/` 与 `/medias/video` 未登录仍 302，已登录仍可渲染。
3. JS 语法检查确认素材管理前端脚本未被破坏。
4. 本地 dev server 使用手机视口截图检查产品管理和视频素材管理可左右滑动，表头列名与数据列对齐。

执行命令：

```bash
pytest tests/test_medias_list_filters.py::test_medias_mobile_adaptation_keeps_tables_scrollable_and_aligned tests/test_medias_pages_routes.py -q
node --check web/static/medias.js
node --check web/static/media_video_materials.js
```
