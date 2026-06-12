# 实时大盘产品销量订单详情入口

## 背景

数据分析模块「实时大盘」的「产品销量」子 tab 已经展示产品名称、product code、主图、销量、销售额和 ROAS。运营在移动端查看产品销量后，需要从同一行直接进入该产品的订单曲线详情，减少再切到「订单分析」后搜索 product code 的步骤。

「订单分析」模块的「商品销量统计」表格末尾已有同类入口：每行最后一列用 `btn btn-default btn-xs` 链接到 `/order-analytics/dxm-orders-view/order-trend/<product_code>`。本次沿用该目标路由和按钮风格。

## 锚点

- `AGENTS.md`：文档驱动代码、worktree 隔离和 targeted pytest 规则。
- `appcore/order_analytics/CLAUDE.md`：实时大盘、订单分析模块规则。
- `web/templates/CLAUDE.md`：Jinja 模板修改规则。
- `docs/superpowers/specs/2026-06-07-realtime-dashboard-product-sales-copy-search.md`：实时大盘产品销量表现有产品列操作设计。
- `docs/superpowers/specs/2026-06-09-data-analysis-realtime-roas-default-subtab.md`：实时大盘子 tab 路由和刷新边界。

## 要求

1. 仅调整「数据分析 -> 实时大盘 -> 产品销量」表格。
2. 在表格最末尾新增一列，表头为「订单情况」。
3. 每个有 `product_code` 的产品行在该列显示「订单详情」链接按钮。
4. 链接目标为 `/order-analytics/dxm-orders-view/order-trend/<product_code>`，与「订单分析 -> 商品销量统计」末尾入口一致。
5. 没有 `product_code` 的行显示 `-`，不生成不可用链接。
6. 保持实时大盘现有接口、统计口径、日期/店铺/新品筛选逻辑不变。

## 设计

- 后端不改。`product_sales_stats` 已包含 `product_code`，前端直接用现有字段拼接链接。
- 模板表头在 `#realtimeProductStatsTable` 最后一列增加「订单情况」。
- `renderRealtimeProductSales` 在现有 9 个单元格之后追加一个操作单元格：
  - `row.product_code` 存在时生成 `<a class="btn btn-default btn-xs">订单详情</a>`。
  - 使用 `encodeURIComponent(row.product_code)` 构造路径。
  - `row.product_code` 缺失时沿用 `addTextCell(tr, '-')`。
- 空态 `colspan` 从 `9` 调整为 `10`，避免移动端表格空态宽度不匹配。

## 不做

- 不修改「订单分析 -> 商品销量统计」现有按钮文案。
- 不修改「新品投放分析」产品销量表。
- 不新增接口、权限、数据库字段或统计口径。
- 不改 `order_trend_detail.html` 页面。
- 仓库无根级 `CHANGELOG*` 文件，本次不更新 changelog。

## 验证

- TDD 静态回归：`tests/test_order_analytics_template_layout.py` 覆盖实时大盘产品销量表头、渲染函数中的订单详情链接、缺失 product code 时的 `-` 兜底、空态 `colspan="10"`。
- 运行：`pytest tests/test_order_analytics_template_layout.py -q`。
- 按项目规则再运行：`python scripts/pytest_related.py --base origin/master --run`。
- 不默认跑全量 `pytest -q`，因为本次只改模板展示和对应静态测试，不涉及接口、schema、鉴权、调度、LLM、存储或跨模块重构。
