# 移动端数据分析表格表头对齐修复

## 背景

移动端访问「数据分析」内的宽表时，表头和数据列可能错位：表头按短 label 计算列宽，tbody 则按长产品名、广告系列名、账户名等真实数据计算列宽，导致数据看起来落在错误列下。已知影响包括「实时大盘 → 广告计划」和「广告分析 → 概览 / Campaign / Ad Set / Ad / 人工录入」。

根因是全局 `web/static/css/mobile.css` 在 `< 768px` 下把 `.main-content table:not(.mobile-no-scroll)` 改成 `display: block`，并把 `thead` / `tbody` 分别设为独立 `display: table; width: max-content`。这能给部分普通表格兜底横滚，但实时大盘的 `.oa-table-scroll` 已经是横滚容器；再次拆开 `thead` 和 `tbody` 会让二者各自计算列宽。

## 目标

- 数据分析页内所有 `.oa-table-scroll .oa-table` 在移动端仍保持一个完整 table layout，由外层 `.oa-table-scroll` 负责横向滚动。
- 广告计划表的第一列在移动端有稳定宽度并允许长 campaign 名换行，避免长文本把后续列推到不可预期位置。
- 不改数据、接口、排序和桌面端样式。

## 设计

在 `web/templates/order_analytics.html` 的页面级 CSS 中增加移动端覆盖：

- `.oa-table-scroll table.oa-table:not(.mobile-no-scroll)` 恢复 `display: table`，`width: max-content`，`min-width: 100%`，`max-width: none`，覆盖整个数据分析页而不是只覆盖 `#panelRealtime`。
- 其直接子级 `thead` / `tbody` / `tfoot` 分别恢复为 `table-header-group` / `table-row-group` / `table-footer-group`，取消全局移动端补丁写入的独立宽度。
- 给广告计划实时数据表添加 `oar-campaign-table` class，并限制第一列宽度，表头和数据同列共享同一宽度。

## 验证

- 静态回归：`tests/test_order_analytics_template_layout.py` 检查页面级 CSS 覆盖、`oar-campaign-table` class 存在，并确认广告分析表格也落在同一移动端覆盖范围内。
- 相关测试：`pytest tests/test_order_analytics_template_layout.py -q`。
