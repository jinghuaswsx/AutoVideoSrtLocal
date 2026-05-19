# 图片翻译页面移动端适配设计

日期：2026-05-19

## 背景

全局移动端壳层由 `docs/superpowers/specs/2026-05-01-mobile-ios-responsive-design.md` 定义，图片翻译功能由 `docs/superpowers/specs/2026-04-16-image-translate-design.md` 定义。当前 `/image-translate` 在手机 Safari 上可以进入，但新建任务卡片内存在移动端可用性问题：产品名输入框和说明文字过大、pill 选项横向挤出、历史任务 tabs 和表格缺少页面级滚动容器，导致内容被裁切或需要整页横向拖动。

## 目标

- `/image-translate` 在 iPhone 宽度下不产生整页横向溢出。
- 新建任务卡片在移动端单列可读，产品名警示仍醒目但不挤出卡片。
- 场景、语言、供应商、模型、处理模式 pill 在移动端使用局部横向滚动，不撑宽页面。
- 历史任务 tabs 和任务表使用局部横向滚动容器，卡片本身不被撑宽。
- `/image-translate/<task_id>` 的任务信息、进度区、图片对比、底部操作在移动端单列堆叠，按钮可触控。

## 非目标

- 不改后端接口、任务状态、上传流程、runner 或权限。
- 不改全局 `mobile.css` 的表格兜底策略。
- 不重做桌面端布局；桌面视觉保持现状。

## 设计

在图片翻译页面专属样式 `_image_translate_styles.html` 内增加页面级移动端覆盖，并在列表模板中补充两个结构类：

- `it-history-tabs`：包住历史任务 tabs，移动端 `overflow-x:auto`。
- `it-history-table-wrap`：包住历史任务表格，移动端局部横向滚动。

样式侧以 `@media (max-width: 767px)` 为边界：

- `.it-shell`、`.card`、`.form-row` 全部设置 `min-width:0`，卡片使用更小 padding 和 radius。
- `.it-pill-group` 在移动端改为 `flex-wrap:nowrap` + `overflow-x:auto`，`.it-pill` 固定为 `flex:0 0 auto`，避免选项撑宽整页。
- 产品名输入框字号和高度下调，`overflow-wrap:anywhere` 兜底长文本。
- 历史 tabs/table wrapper 使用 `max-width:100%`、`overflow-x:auto`、`-webkit-overflow-scrolling:touch`。
- 详情页 `.it-meta-grid` 单列，`.it-item-actions` 换行，操作按钮占满可用宽度。

## 验证

- 静态测试检查列表模板包含 `it-history-tabs` / `it-history-table-wrap`，图片翻译样式包含本 spec 的 docs anchor 和移动端关键选择器。
- 运行 `pytest tests/test_web_routes.py -q`。
- 启动 dev server 后，用 iPhone viewport 访问 `/image-translate`，确认 `document.body.scrollWidth <= window.innerWidth + 1`，新建任务卡片和历史任务区不造成整页横向溢出。
