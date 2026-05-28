# 实时大盘订单明细产品中文名展示设计

日期：2026-05-28
状态：已确认，待实现

## 背景

数据分析模块的「实时大盘」里，订单明细表当前把产品主图和产品英文名拆成相邻两列：主图来自 `product_ids`，产品英文名来自店小秘订单行 `dianxiaomi_order_lines.product_name` 的聚合值。运营在查看订单时还需要同时看到产品中文名，便于和内部产品库、素材任务和选品口径对齐。

本次用户确认的要求是：在产品英文名后面加一行产品中文名；中文名放在产品主图右边，和产品英文名在同一产品信息区域内左对齐。

## 目标

1. 在「数据分析 → 实时大盘 → 订单明细」的产品信息列中显示两行：
   - 第一行：现有产品英文名。
   - 第二行：产品中文名。
2. 中文名位于产品主图右侧，和英文名左边缘对齐。
3. 不改变订单明细分页、筛选、金额、排序和主图取图逻辑。

## 数据来源

- 英文名：继续使用 `dianxiaomi_order_lines.product_name` 聚合为 `product_names`。
- 中文名：通过 `dianxiaomi_order_lines.product_id = media_products.id` 读取 `media_products.name`，聚合为 `product_cn_names`。
- 多产品订单：英文名和中文名都继续用 `GROUP_CONCAT(DISTINCT ... ORDER BY ... SEPARATOR ' / ')` 形式展示。
- 缺失中文名：前端不额外占位，避免把空行误读成产品数据。

## 前端设计

`web/templates/order_analytics.html` 中新增一个小 helper 渲染订单明细产品名单元格：

- 保留现有主图列 `addCoverImageCell(tr, row.product_ids)`。
- 产品名列渲染为两行文本：
  - `.oar-product-name-en`：英文名或 SKU fallback。
  - `.oar-product-name-cn`：中文名，仅在 `product_cn_names` 非空时显示。
- 两行文本在同一 `<td class="oar-product-cell">` 内左对齐；主图列仍在它左侧。

## 后端设计

`appcore/order_analytics/realtime.py` 的订单明细查询补充 `LEFT JOIN media_products mp ON mp.id = d.product_id`，并在单日和日期范围两条订单明细路径中返回 `product_cn_names`。

涉及函数：

- `_get_realtime_order_details`
- `_get_realtime_order_details_for_range`

计数函数不需要变更，因为它只统计分组总数。

## 验证

1. 先写失败测试，覆盖：
   - 实时订单明细 SQL 关联 `media_products` 并选择 `product_cn_names`。
   - 日期范围订单明细 SQL 同样返回 `product_cn_names`。
   - 模板把 `row.product_cn_names` 渲染到产品英文名后的一行。
2. 运行相关测试：

```bash
pytest tests/test_order_analytics_template_layout.py tests/test_order_analytics_realtime_profit_details.py -q
```

当前基线已存在一个无关失败：

```text
tests/test_order_analytics_realtime_profit_details.py::test_build_order_profit_summary_clamps_unallocated_when_total_below_allocated
```

该失败属于实时大盘利润汇总口径，和本次产品中文名展示无直接关系；本次改动不修复该既有失败。
