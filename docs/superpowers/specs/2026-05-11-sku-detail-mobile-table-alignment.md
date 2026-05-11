# SKU 详情弹窗移动端表头对齐修复

- 日期：2026-05-11
- 范围：素材管理 `/medias` 的 SKU 配对详情弹窗
- 文档锚点：`AGENTS.md` 文档驱动代码、`web/static/CLAUDE.md` Ocean Blue 控件约束、`docs/superpowers/specs/2026-05-07-sku-pairing-editor-fullscreen-design.md`、`docs/superpowers/specs/2026-05-01-mobile-ios-responsive-design.md`

## 背景

移动端打开「素材管理 → ERP SKU 管理 / SKU 配对详情」时，SKU 表格的列名抬头和数据列没有对齐。截图中可见表头从中间列开始显示，数据输入框却落在不同的横向位置，运营无法可靠判断每个值对应的列。

## 根因

全局 `web/static/css/mobile.css` 在小屏下把 `.main-content table:not(.mobile-no-scroll)` 改成 `display: block`，并把直接子级 `thead`、`tbody`、`tfoot` 分别设为独立的 `display: table; width: max-content`。这个兜底适合没有滚动容器的普通表格，但 SKU 详情弹窗已经有 `.oc-sku-detail-table-wrap` 作为横向滚动容器。

当全局兜底作用到 `.oc-sku-detail-table` 后，表头和表体会各自按 label 与真实数据计算列宽，导致列名和输入框错位。

## 目标

1. SKU 详情弹窗内的表格在移动端保持完整 table layout，表头和表体共享同一套列宽。
2. 横向滚动继续由 `.oc-sku-detail-table-wrap` 承担。
3. 保留桌面端全屏主区弹窗、sticky 表头、已有列宽和接口行为。

## 不做

- 不改 SKU 刷新、保存、新增手动变体接口。
- 不改 SKU 数据字段、列顺序或列宽。
- 不重写全局 `mobile.css` 的表格兜底。

## 设计

在 `web/templates/medias_list.html` 的 SKU 详情弹窗样式中增加移动端页面级覆盖：

- `.oc-sku-detail-table-wrap table.oc-sku-detail-table:not(.mobile-no-scroll)` 恢复 `display: table`，保留 `min-width: 1940px`，让现有列宽继续生效。
- 其直接子级 `thead`、`tbody`、`tfoot` 分别恢复为 `table-header-group`、`table-row-group`、`table-footer-group`，并清理全局兜底写入的独立宽度。
- `.oc-sku-detail-table-wrap` 明确保留 `overflow-x: auto` 与触屏惯性滚动。

## 验证

- 静态回归：检查 `medias_list.html` 包含 SKU 详情表格的移动端 table layout 覆盖。
- 针对性测试：`pytest tests/test_material_roas_frontend.py::test_sku_detail_mobile_table_keeps_shared_header_and_body_layout -q`。
- 相关测试：`pytest tests/test_material_roas_frontend.py -q`。
