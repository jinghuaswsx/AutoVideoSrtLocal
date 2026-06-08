# 素材管理稳定分级筛选与移动端列对齐

- 状态：已确认
- 日期：2026-06-08
- 页面：`/medias/` 产品管理 tab
- 接口：`GET /medias/api/products`

## 文档锚点

- [AGENTS.md](../../../AGENTS.md)：素材管理位于 `web/`，改动前先落文档，改动后按项目验证顺序执行。
- [web/static/CLAUDE.md](../../../web/static/CLAUDE.md)：`/medias` 前端路径、Ocean Blue 设计系统和移动端单列约束。
- [2026-06-07 素材管理移动端适配](2026-06-07-medias-mobile-adaptation-design.md)：移动端产品管理表保留 `<thead>` / `<tbody>`，通过 `.oc-list` 横向滑动查看完整表格。
- [2026-06-07 每周 AI 分析报告产品稳定分级](2026-06-07-weekly-ai-analysis-report-design.md)：稳定分级缓存、分级状态和素材管理稳定分级列的口径来源。

## 背景

素材管理产品列表已经展示 `稳定分级` 列，但筛选区缺少对应入口，运营无法快速只看稳定品、二级稳定品、测试品或未投放产品。移动端横向查看产品管理表时，表格 CSS 宽度与前端 `colgroup` 列宽总和不一致，容易造成表头列名和数据列视觉错位。

## 目标

1. 产品管理筛选区新增 `稳定分级` 下拉筛选。
2. 筛选参数使用 `stability_status`，后端基于 `media_product_stability_snapshots` 过滤产品。
3. 支持筛选：全部、稳定品、7 天稳定、30 天稳定、二级稳定品、测试品、已停投、未投放、投放未满 7 天。
4. 移动端产品管理表继续保留表格结构和横向滑动，不卡片化，不隐藏表头。
5. 产品管理表的 CSS 宽度与 `colgroup` 总列宽保持一致，让列名和数据列对齐。

## 实现

- `appcore/media_product_stability.py`：集中定义稳定分级筛选值和归一化 helper。
- `appcore/medias.py`：`list_products()` 接收 `stability_status`；按稳定分级快照表做 `EXISTS` 过滤。`stable_7d` / `stable_30d` 使用对应布尔列，其余按 `status` 过滤。
- `web/services/media_products_listing.py`：读取并归一化 `stability_status`，透传给 DAO。
- `web/templates/medias_list.html`：产品管理筛选区新增下拉；产品表移动端固定宽度改为与 `colgroup` 一致。
- `web/static/medias.js`：列表请求带上 `stability_status`，筛选变化时重载第一页。

## 移动端 Safari 表头对齐修订

2026-06-08 线上反馈：iPhone Safari 中产品管理表横向滑动后，sticky 表头的列名会与真实数据列错位。Chrome 移动模拟下 `<th>` / `<td>` 坐标一致，但 Safari 对横向滚动容器里的 `position: sticky` table cell 存在绘制偏移。

修订规则：

- `max-width: 640px` 下产品管理表和视频素材表禁用 table header sticky，`thead th` 回到 `position: static`。
- `max-width: 640px` 下产品管理表和视频素材表自身必须保持 `display: table`；不能让 table 变成 `block`，否则 `thead` 和 `tbody` 会各自按内容重新计算列宽。
- 产品管理表的 16 列必须同时给 `th` 和 `td` 设置同一套 `width` / `min-width` / `max-width` 兜底，避免浏览器忽略 `colgroup` 时表头与数据按内容宽度错位。
- 移动端优先保证列名和数据在同一个 table 流里横向同步；不使用克隆表头或独立浮动表头。
- 桌面端继续保留现有 sticky 表头。

## 非目标

- 不新增数据库表或迁移。
- 不改变稳定分级计算规则、定时任务或周报 AI 逻辑。
- 不改变视频素材管理 tab 的筛选项。
- 不把二级稳定品、测试品等状态改成产品列表默认显眼标签；当前列表展示口径继续按已有 spec 只突出稳定品。

## 验证

```bash
pytest tests/test_medias_list_filters.py tests/test_medias_pages_routes.py -q
node --check web/static/medias.js
```
