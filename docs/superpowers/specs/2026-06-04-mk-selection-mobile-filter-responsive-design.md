# 明空选品移动端筛选区适配设计

日期：2026-06-04

## 背景

用户在手机浏览 `/xuanpin/mk#videos` 时反馈页面没有显示全，尤其是筛选区域。当前 `web/templates/mk_selection.html` 的筛选条仍按桌面横向工具栏处理：`oc-search` 在移动端保持 `flex-wrap: nowrap`，搜索框还带 `style="width:480px"`，多个 `select`、搜索框和按钮在 393px 宽度下会向右溢出。

## 锚点

- `AGENTS.md#文档驱动代码`：新要求先固化到仓库文档，再改代码。
- `web/templates/CLAUDE.md#CSRF / 路由守卫`：本次不新增 mutating 请求，不改变 CSRF 或权限。
- `web/static/CLAUDE.md#Ocean Blue 设计系统（管理后台视觉基调）`：移动端控件保持现有 Ocean Blue 密度和非紫色约束。
- `docs/superpowers/specs/2026-05-12-xuanpin-route-layer-design.md#设计`：页面入口和 API 保持 `/xuanpin/mk` 与 `/xuanpin/api/*`。
- `docs/superpowers/specs/2026-05-18-xuanpin-tabs-unification-design.md#设计`：选品中心一级 Tab 保持共享片段和横向滚动。
- `docs/superpowers/specs/2026-05-22-mk-video-material-search-index-design.md#前端行为`：搜索框语义不变，仍支持产品名、product code 和视频文件名。
- `docs/superpowers/specs/2026-06-02-mingkong-material-preselection-design.md#Restricted Operator Access`：素材预选权限和可见控件不改变。

## 目标

1. `/xuanpin/mk` 在手机竖屏宽度下筛选区域完整显示，不因横向工具栏裁掉右侧控件。
2. 搜索框、筛选下拉和搜索/重置按钮在移动端按可读的多行布局展示。
3. 顶部选品中心一级 Tab 和明空二级 Tab 继续可横向滚动，不强行压缩文字。
4. 卡片列表、分页、搜索、重置、快照、入库状态、投放素材、处理状态和排序行为不变。

## 非目标

- 不改 `/xuanpin/api/*`。
- 不改明空素材搜索、素材预选、入库、小语种任务创建流程。
- 不新增新的筛选项、折叠面板或后端状态。
- 不改变桌面端筛选条宽度和桌面卡片布局。

## 设计

只修改 `web/templates/mk_selection.html` 的页面级 CSS 和静态模板测试。

移动端 `max-width: 768px`：

- `.oc-header--actions` 拉满宽度并贴合内容区。
- `.oc-search` 从桌面横排改为 CSS grid，允许自动换行。
- 所有 `.oc-search` 内的 `select`、`input` 和按钮设置 `width: 100%`、`min-width: 0`，覆盖搜索框的 inline `width:480px`。
- 筛选控件在普通手机宽度下采用两列：每个控件 `minmax(0, 1fr)`，搜索框独占整行，搜索和重置按钮各占一列。
- `max-width: 420px` 下保持两列，但用更小 gap 和 padding，避免 iPhone Safari 宽度下出现 body 横向溢出。
- `snapshotSelect[hidden]` 仍由现有 JS 控制，不新增 JS 分支。
- `.mk-video-library-head` 和 `.oc-pager` 在窄屏允许换行，避免状态文本和「卡片放大 2x」按钮把内容撑宽。

本次不做 tab 内容切换时的动态隐藏筛选项。原因是当前筛选条同时服务 `videos`、`yesterday-top300`、`preselection` 和 `products`，动态隐藏会改变操作路径，超出“显示全”的修复范围。

## 验收标准

1. `mk_selection.html` 包含本 spec 的 Docs-anchor 注释。
2. 移动端 CSS 中 `.oc-search` 在 `max-width: 768px` 下使用 `grid-template-columns: repeat(2, minmax(0, 1fr))`。
3. 移动端 CSS 覆盖 `#searchInput` 为 `width: 100% !important`，确保 inline 宽度不会撑破手机布局。
4. 搜索框独占整行，按钮不被裁掉。
5. 顶部一级 Tab 和二级 Tab 仍保留移动端横向滚动。
6. 相关路由/模板测试通过。

## 验证

```bash
pytest tests/test_mk_selection_routes.py tests/test_xuanpin_routes.py -q
python -m compileall appcore web tests -q
git diff --check
```

人工验收：

1. 登录后用手机或 390px 左右移动 viewport 打开 `/xuanpin/mk#videos`。
2. 确认筛选区控件全部可见，搜索和重置按钮不在屏幕右侧外。
3. 切换 `视频素材库`、`昨天消耗前300`、`素材预选`，确认筛选区和分页区不会横向裁切。
