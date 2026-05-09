# SKU 实际保本 ROAS 日快照设计

日期：2026-05-10

## 背景

素材管理的 SKU 配对详情弹窗当前已经能展示 SKU 维度的“估算 ROAS”。这个估算值主要来自 SKU 的售价、采购价和小包成本聚合，适合做没有订单样本时的初始参考，但不能反映已经产生订单后的真实成本结构。

运营需要在每个 ERP SKU 上新增一列“实际保本 ROAS”，放在“估算 ROAS”后面。这个值基于实际订单数据计算，覆盖售价、采购成本、实际物流费用和手续费成本；没有足够订单数据时显示横杠。

Shopify Payments 数据存在延迟：业务通常每周或每月导入一次 Payments CSV/Excel。实际手续费到达前，系统应先用 7% 手续费率做估算；导入 Payments 数据后，受影响 SKU 的快照可重算并用真实手续费覆盖。

## 设计锚点

- 现有素材 ROAS 设计：[2026-04-28-material-roas-design.md](2026-04-28-material-roas-design.md)
- Shopify Payments 手续费规则：[2026-05-04-shopify-payments-fee-rules.md](2026-05-04-shopify-payments-fee-rules.md)
- 订单利润详情：[2026-05-07-order-profit-detail-tab-design.md](2026-05-07-order-profit-detail-tab-design.md)
- 定时任务登记规则：[AGENTS.md](../../../AGENTS.md) 的“定时任务归集规则”

## 目标

1. 在素材管理 SKU 配对详情中，于“估算 ROAS”后新增一列“实际保本 ROAS”。
2. 每天北京时间 1:00 计算一次，不在页面打开时实时聚合订单。
3. 每次计算使用滚动 30 个已稳定业务日：运行日为 D 时，统计 `D-32` 到 `D-3`，含首尾。
4. 有窗口内订单数据的 SKU 显示实际保本 ROAS；没有订单数据的 SKU 显示横杠。
5. 手续费来源显示为一个标签：`真实手续费`、`7%估算` 或 `部分真实`。
6. Shopify Payments 数据后到时，允许重算并把手续费来源从 `7%估算` 更新为 `真实手续费` 或 `部分真实`。

## 非目标

- 不改变现有“估算 ROAS”的公式和展示位置。
- 不把广告费扣进“实际保本 ROAS”公式。广告费是实际投放表现数据，不属于保本阈值中的成本扣减项。
- 不在素材列表主表新增宽列；本期只改 SKU 配对详情弹窗。
- 不要求 Payment 导入后立即同步重算所有历史窗口；可以先提供可调用重算入口，再由后续实现决定是否在导入接口里触发。

## 核心口径

“实际保本 ROAS”是保本阈值，不是实际投放 ROAS。

```text
实际保本 ROAS = 总销售额 / 可承受广告费
可承受广告费 = 总销售额 - 总采购成本 - 总物流成本 - 总手续费
```

字段口径：

| 字段 | 口径 |
| --- | --- |
| 总销售额 | 窗口内该 ERP SKU 的订单行商品金额 + 已分摊买家支付运费 |
| 总采购成本 | 优先订单行采购价快照；缺失时用当前 SKU / 产品采购价兜底 |
| 总物流成本 | 优先订单实际 `logistic_fee` 按订单行金额比例分摊 |
| 总手续费 | 优先 Shopify Payments 真实手续费按订单行销售额比例分摊；没有真实 fee 的订单用该订单行销售额 × 7% |
| 可承受广告费 | 若小于等于 0，不输出误导性 ROAS，前端显示横杠 |

广告数据不进入上述公式。它可以作为未来校验“实际投放 ROAS 是否高于保本 ROAS”的比较对象，但本列只负责给出保本阈值。

## 计算窗口

每日 1:00 北京时间触发。

运行日为 `D` 时：

```text
window_start = D - 32 days
window_end   = D - 3 days
```

示例：2026-05-10 01:00 运行，统计 2026-04-08 到 2026-05-07。

原因：

- 店小秘订单和物流费用有同步延迟，排除最近 3 天可降低未完备数据污染。
- 单日样本对 SKU 保本阈值波动太大，滚动 30 天更适合做投放参考。

## 数据模型

新增 SKU ROAS 快照表，避免把时间窗口指标塞进 `xmyc_storage_skus` 基础资料表。

建议表名：`sku_actual_breakeven_roas_snapshots`。

核心字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 主键 |
| `sku` | ERP SKU / `xmyc_storage_skus.sku` |
| `window_start` | 统计窗口开始日期 |
| `window_end` | 统计窗口结束日期 |
| `orders_count` | 窗口内订单数 |
| `units` | 窗口内销售件数 |
| `revenue_usd` | 总销售额 |
| `purchase_cost_usd` | 总采购成本 |
| `shipping_cost_usd` | 总物流成本 |
| `shopify_fee_usd` | 总手续费 |
| `fee_source` | `real` / `estimated_7pct` / `mixed` |
| `actual_breakeven_roas` | 实际保本 ROAS；不可保本时为 NULL |
| `computed_at` | 计算时间 |
| `source_run_id` | 定时任务运行记录 ID，可为空 |
| `summary_json` | 预留样本、缺失字段、真实手续费覆盖率等诊断信息 |

唯一键建议：`(sku, window_start, window_end)`。素材管理默认读取每个 SKU 最新 `computed_at` 或最新窗口的一行。

## 手续费真实优先与延迟覆盖

手续费计算按订单维度处理：

1. 先用 `dianxiaomi_order_lines.extended_order_id` 或可匹配的 Shopify order name 查 `shopify_payments_transactions`。
2. 命中真实 Payment charge 时，取该订单真实 `fee_usd`。
3. 将订单真实 fee 按订单内参与计算行的销售额比例分摊到 SKU 行。
4. 未命中真实 Payment 时，该行手续费使用 `line_revenue_usd * 0.07`。

窗口内如果所有订单都命中真实手续费，`fee_source='real'`。
如果所有订单都用 7% 估算，`fee_source='estimated_7pct'`。
如果两者都有，`fee_source='mixed'`，前端标签显示 `部分真实`。

Payment CSV/Excel 导入后，重算同一窗口即可覆盖旧快照。这样不会因为 Payment 延迟导致 SKU 长期停留在 7% 估算。

## 后端流程

新增一个聚合模块，建议放在 `appcore/sku_actual_roas.py` 或扩展 `appcore/sku_aggregates.py`，但实现上应保持和基础 SKU 聚合解耦。

核心接口：

```python
compute_sku_actual_breakeven_roas(window_start, window_end) -> dict
get_latest_sku_actual_roas(skus: list[str]) -> dict[str, dict]
```

计算步骤：

1. 从 `dianxiaomi_order_lines` 读取窗口内有 `product_display_sku` 的订单行。
2. 按 `dxm_package_id` 聚合订单行总金额和买家支付运费，用现有 `allocate_shipping_to_line` 逻辑分摊收入侧运费。
3. 采购成本优先使用 `purchase_price_cny` 快照，缺失时 fallback 到 `xmyc_storage_skus.unit_price` 或产品级采购价。
4. 物流成本优先使用订单 `logistic_fee` 按订单行金额比例分摊。
5. 手续费按“真实 Payment 优先，7% 兜底”分摊。
6. 按 SKU 汇总，并写入快照表。

## 定时任务

新增 systemd timer/service：

- `deploy/server_browser/autovideosrt-sku-actual-roas.service`
- `deploy/server_browser/autovideosrt-sku-actual-roas.timer`

触发时间：每天北京时间 01:00。

新增任务定义：

- `appcore/scheduled_tasks.py` 登记 `sku_actual_breakeven_roas`
- 日志表使用 `scheduled_task_runs`
- runner 建议为 `tools/sku_actual_roas_snapshot.py`

任务可支持参数：

```text
--date YYYY-MM-DD              # 指定运行日 D，默认北京时间今天
--window-days 30               # 默认 30
--settlement-delay-days 3      # 默认 3
--dry-run
```

## 前端展示

位置：素材管理 SKU 配对详情弹窗。

在当前“估算 ROAS”列后新增：

```text
实际保本 ROAS
```

展示规则：

| 数据状态 | 展示 |
| --- | --- |
| 无窗口订单 | `—` |
| 可承受广告费 <= 0 | `—`，tooltip 说明“销售额不足以覆盖采购、物流和手续费” |
| 全部真实手续费 | `2.34 真实手续费` |
| 全部 7% 估算 | `2.34 7%估算` |
| 部分真实部分估算 | `2.34 部分真实` |

颜色延续现有 ROAS 风格：低阈值更好，高阈值风险更高；不要引入新的紫色或靛蓝色。

## API 与序列化

素材列表接口已批量返回产品 SKU。新增批量读取最新 SKU 实际 ROAS：

1. `build_products_list_response` 收集当前页所有 `dianxiaomi_sku`。
2. 调用 `get_latest_sku_actual_roas(skus)`。
3. `_serialize_product_skus` 给每个 SKU 增加：

```json
{
  "actual_breakeven_roas": {
    "value": 2.34,
    "fee_source": "real",
    "window_start": "2026-04-09",
    "window_end": "2026-05-08",
    "orders_count": 12,
    "computed_at": "2026-05-10T01:00:08"
  }
}
```

无数据时该字段为 `null`。

## 测试计划

后端测试：

- `tests/test_sku_actual_roas.py`
  - 滚动窗口计算边界：D-32 到 D-3。
  - 真实 Payment 命中时使用真实 fee。
  - Payment 缺失时使用 7% 估算。
  - 混合窗口标记 `mixed`。
  - 没有订单的 SKU 不写误导性 ROAS。
  - 可承受广告费小于等于 0 时 ROAS 为 NULL。
- `tests/test_sku_aggregates.py`
  - 最新快照批量读取并挂到 SKU 行。
- `tests/test_media_products_listing_service.py`
  - 列表序列化时批量加载 SKU 实际 ROAS，避免 N+1。
- `tests/test_material_roas_frontend.py`
  - SKU 表头包含“估算 ROAS”后紧跟“实际保本 ROAS”。
  - 来源标签文本存在。
- `tests/test_appcore_scheduled_tasks.py`
  - `sku_actual_breakeven_roas` 已登记。

建议聚焦命令：

```bash
pytest tests/test_sku_actual_roas.py tests/test_sku_aggregates.py tests/test_media_products_listing_service.py tests/test_material_roas_frontend.py tests/test_appcore_scheduled_tasks.py -q
```

## 迁移与回填

上线后先允许任务跑最新窗口。历史窗口不需要全量回填，除非运营需要查看历史趋势。

如果 Payment CSV 导入覆盖了最近 30 天内订单，应重跑当前最新窗口；如果导入的是更久以前的 Payment，仅在未来需要历史趋势时再回填对应窗口。

## 风险与约束

1. **订单名匹配风险**：`shopify_payments_transactions.order_name` 与店小秘订单字段可能不完全一致。实现时必须先复用产品盈亏报表已有匹配逻辑，必要时把未匹配比例写入 `summary_json`。
2. **真实手续费覆盖率滞后**：Payment 一周或一个月才导入一次，前端必须明确标出 `7%估算` 或 `部分真实`。
3. **SKU 样本偏少**：滚动 30 天仍可能只有 1 单。前端保留订单数 tooltip，避免把小样本误读为稳定结论。
4. **本地 MySQL 禁止规则**：Windows 开发机不得连接本地 MySQL；数据库验证使用测试环境配置。
5. **定时任务归集**：新增 timer 必须同步登记到 Web 后台“定时任务”模块。

## 验收标准

1. 素材管理 SKU 配对详情中，“估算 ROAS”后出现“实际保本 ROAS”列。
2. 有最近 30 个已稳定业务日订单的 SKU 显示数值和手续费来源标签。
3. 无订单数据或不可保本的 SKU 显示横杠。
4. 每天 1:00 北京时间可由 systemd timer 触发快照计算。
5. Payment 导入后重算窗口时，同 SKU 快照可从 `7%估算` 更新为 `真实手续费` 或 `部分真实`。
6. `appcore/scheduled_tasks.py` 中能看到该任务定义。
7. 聚焦测试全部通过。
