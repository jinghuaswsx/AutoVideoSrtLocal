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

移动端打开素材管理时，产品管理和视频素材管理仍按桌面超宽表格渲染。390px 左右宽度下首屏只能看到 ID、主图和少量产品信息，筛选区与列表区域占用高度较大，浏览器底部工具栏也容易遮住列表尾部。

## 目标

1. 手机宽度下顶部说明、tab、操作按钮和筛选区不横向溢出。
2. `/medias/` 产品管理表在小屏改为卡片式行布局，首屏优先展示 ID、主图和产品信息，其余状态、投放、单量、推送和操作在卡片内分区展示。
3. `/medias/video` 视频素材表在小屏改为卡片式行布局，保留预览、产品、素材名、广告表现、ROAS、国家情况和绑定操作。
4. 页面高度使用动态视口和 safe area，避免移动浏览器底部工具栏遮住列表内容。
5. 桌面端表格列宽、sticky 表头、API、分页和筛选行为保持不变。

## 实现

- `web/templates/medias_list.html`：
  - 在 `max-width: 768px` 下压缩页面 padding、tab、toolbar 和列表间距，并使用 `100dvh` + `env(safe-area-inset-bottom)`。
  - 在 `max-width: 640px` 下把 `.oc-table-medias` 与 `.oc-vm-table` 从超宽表格转为卡片式行布局。
  - 通过 `nth-child` 给卡片单元格补移动端 label，不要求 JS 额外渲染移动端模板。
  - 缩小视频素材广告表现小表格列宽，避免手机宽度下再次横向溢出。

## 非目标

- 不新增或调整产品、视频素材 API。
- 不改变列表字段、排序、分页、筛选参数或广告数据口径。
- 不改移动端导航栏、登录页或素材编辑弹窗。

## 验证

1. 静态测试确认移动端卡片布局、动态视口和 safe area CSS 存在。
2. 路由测试确认 `/medias/` 与 `/medias/video` 未登录仍 302，已登录仍可渲染。
3. JS 语法检查确认素材管理前端脚本未被破坏。
4. 本地 dev server 使用手机视口截图检查产品管理和视频素材管理不再出现 2000px 横向表格首屏。

执行命令：

```bash
pytest tests/test_medias_list_filters.py::test_medias_mobile_adaptation_cardifies_product_and_video_tables tests/test_medias_pages_routes.py -q
node --check web/static/medias.js
node --check web/static/media_video_materials.js
```
