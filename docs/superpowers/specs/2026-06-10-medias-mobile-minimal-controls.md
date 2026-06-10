# 素材管理移动端最小控件

- 状态：已确认
- 日期：2026-06-10
- 页面：`/medias/` 产品管理与 `/medias/video` 视频素材管理

## 文档锚点

- [AGENTS.md](../../../AGENTS.md)：素材管理位于 `web/`，代码改动前先落文档，改动后执行 focused 验证。
- [web/static/CLAUDE.md](../../../web/static/CLAUDE.md)：`/medias` 前端遵守 Ocean Blue 控件规范，不新增后端路由或 CSRF 变更。
- [2026-05-10 素材管理顶部工具区紧凑布局](2026-05-10-medias-toolbar-mobile-layout.md)：移动端顶部工具区需要压缩纵向空间。
- [2026-06-07 素材管理移动端筛选折叠](2026-06-07-medias-mobile-filter-collapse-design.md)：移动端筛选区已有展开 / 收起容器。
- [2026-06-07 素材管理移动端适配](2026-06-07-medias-mobile-adaptation-design.md)：移动端保留表格结构，通过内部横向滚动查看数据。

## 背景

移动端打开素材管理时，顶部三个操作按钮和多项筛选控件占用首屏过多高度，列表数据被下推。移动端运营主要需要快速搜索产品或素材，再横向查看表格数据；下载插件、下载自动换图工具、添加产品素材和复杂筛选适合留给桌面端。

## 目标

1. 移动端隐藏素材管理页头部三个操作按钮：下载采购洞察插件、下载自动换图工具、添加产品素材。
2. 移动端产品管理筛选区只保留关键词搜索框。
3. 移动端视频素材管理筛选区只保留关键词搜索框。
4. 移动端继续保留筛选展开 / 收起入口；展开后只展示搜索框，收起后隐藏搜索框。
5. 桌面端保持现有顶部按钮、筛选项、下载链接、添加入口、筛选参数和列表行为。

## 实现

`web/templates/medias_list.html`：

- 在移动端 media query 中隐藏 `.oc-header-actions`，不删除 DOM，桌面端仍可正常渲染三个操作入口。
- 在移动端 media query 中隐藏 `.oc-toolbar-filter-row` 和 `.oc-vm-filter-row` 里除第一个 `.oc-search` 外的直接子项。
- 搜索框仍使用现有 `#kw` 和 `#vmKeyword` 输入逻辑，不改 `web/static/medias.js` 或视频素材前端脚本。

## 非目标

- 不改产品管理或视频素材管理 API。
- 不改筛选参数、默认值、URL 同步或搜索防抖。
- 不改桌面端布局。
- 不移除相关下载能力，只在移动端隐藏入口。

## 验证

1. 静态测试确认新文档锚点存在，移动端 CSS 隐藏头部操作区。
2. 静态测试确认移动端产品管理和视频素材管理筛选区只显示第一个搜索控件。
3. 执行：

```bash
pytest tests/test_medias_list_filters.py -q
node --check web/static/medias.js
```
