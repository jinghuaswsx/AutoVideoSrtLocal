# 产品盈亏看板移动端表格对齐修复

## 背景

移动端访问「产品盈亏看板」时，产品列表等宽表存在表头和数据列错位。页面内 `.ppd-table-wrap` 已经是横向滚动容器，但全局 `web/static/css/mobile.css` 在 `< 768px` 下会把 `.main-content table:not(.mobile-no-scroll)` 改成 `display: block`，并把直接子级 `thead` / `tbody` / `tfoot` 分别设成独立 `display: table; width: max-content`。

这个全局兜底会让表头和表体各自按内容计算列宽。产品名、product_code、Campaign 等数据列通常比表头长，移动端就会出现表头列和数据列错位。

## 目标

- 产品盈亏看板所有 `.ppd-table-wrap .ppd-table` 在移动端保持一个完整 table layout，表头和数据共享同一套列宽。
- 横向滚动由 `.ppd-table-wrap` 承担，不再由 table 自身拆分表头和表体承担。
- 第一列长文本允许在稳定宽度内换行，数字列保持不换行，保证读数扫描稳定。
- 不改数据接口、排序、金额口径、桌面端展示和权限逻辑。

## 设计

在 `web/templates/product_profit_dashboard.html` 的页面级 CSS 增加 `< 768px` 覆盖：

- `.ppd-table-wrap` 保持横向滚动与触屏惯性滚动。
- `.ppd-table-wrap table.ppd-table:not(.mobile-no-scroll)` 恢复 `display: table`，使用 `width: max-content` 与 `min-width: 100%`，让宽表仍可横向滚动。
- 其直接子级 `thead` / `tbody` / `tfoot` 分别恢复为 `table-header-group` / `table-row-group` / `table-footer-group`，并清掉全局移动端兜底写入的独立宽度。
- 第一列限定稳定宽度并允许换行，数字列保持 `nowrap`。

## 验证

- 静态回归：`tests/test_product_profit_dashboard_assets.py` 检查产品盈亏模板包含移动端 table layout 覆盖。
- 针对性测试：`pytest tests/test_product_profit_dashboard_assets.py -q`。
