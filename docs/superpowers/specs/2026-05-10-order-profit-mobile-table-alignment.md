# 订单利润核算移动端表格对齐修复

## 背景

移动端访问「订单利润核算」时，表头和数据列没有对齐。页面内多张 `.op-table` 都是普通 table，没有独立横滚容器；全局 `web/static/css/mobile.css` 在 `< 768px` 下会把 `.main-content table:not(.mobile-no-scroll)` 改成 `display: block`，再把直接子级 `thead` / `tbody` / `tfoot` 分别设成独立 `display: table; width: max-content`。

这个全局兜底会让表头和表体各自按内容计算列宽。订单 ID、Campaign Code、产品名等数据列通常比表头长，移动端就会出现表头列和数据列错位。

## 目标

- 订单利润核算页所有 `.op-table` 在移动端保持一个完整 table layout，表头和数据共享同一套列宽。
- 横向滚动由表格所在 `.op-section` 承担，不再由 table 自身拆分表头和表体承担。
- 长文本列允许在稳定宽度内换行，避免把数字列推到不可预期位置。
- 不改数据接口、排序、金额口径、桌面端展示和权限逻辑。

## 设计

在 `web/templates/order_profit_dashboard.html` 的页面级 CSS 增加 `< 768px` 覆盖：

- `.op-section` 开启 `overflow-x: auto` 与触屏惯性滚动。
- `.op-section table.op-table:not(.mobile-no-scroll)` 恢复 `display: table`，使用 `width: max-content` 与 `min-width: 100%`，让宽表仍可横向滚动。
- 其直接子级 `thead` / `tbody` / `tfoot` 分别恢复为 `table-header-group` / `table-row-group` / `table-footer-group`，并清掉全局移动端兜底写入的独立宽度。
- 第一列限定稳定宽度并允许换行，数字列保持 `nowrap` 和右对齐，保证读数扫描稳定。

## 验证

- 静态回归：`tests/test_order_profit_dashboard_assets.py` 检查订单利润模板包含移动端 table layout 覆盖。
- 针对性测试：`pytest tests/test_order_profit_dashboard_assets.py -q`。
