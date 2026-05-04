# 订单级实际小包费用核算 Design

## 目标

将订单利润核算中的小包成本从"产品级估算 × 数量"切换到"订单级真实 `logistic_fee`"，缺失时三级降级兜底。同时把 `parcel_cost_suggest.py` 的 CDP 实现替换为本地 SQL，新增定时任务自动写 `packet_cost_actual` / `packet_cost_estimated`，UI 去"采纳"按钮。

## 1. 利润核算——三级降级链

### 公式变更

```
旧: shipping_cost_usd = product_packet_cost_cny × quantity / rmb_per_usd
    (packet_cost_cny 来自 check_sku_cost_completeness: actual > estimated)

新: shipping_cost_usd = resolved_shipping_cny / rmb_per_usd
    其中 resolved_shipping_cny:
      1. allocated_logistic_fee              (订单级真实值, 按 line_amount 比例分摊)
      2. packet_cost_actual × quantity       (产品均值, 自动写库)
      3. packet_cost_estimated × quantity    (产品中位数, 自动写库)
      4. None → incomplete
```

### 关键差异

`logistic_fee` 是订单级总运费（同一 order 下所有 line 值相同），需要分摊到行：

```python
allocated_logistic_fee = logistic_fee × (line_amount / order_total_line_amount)
```

分摊后**不再 × quantity**（已经是该行应摊的总额）。产品级兜底仍 `× quantity`（因为产品字段是 per-unit 语义）。

### cost_basis 记录

`order_profit_lines.cost_basis` JSON 新增 `shipping_cost_source`:
- `"order_logistic_fee"` — 用了订单真实运费
- `"product_actual"` — 兜底到产品均值
- `"product_estimated"` — 兜底到产品中位数

### 完备性 gate 调整

原来"packet_cost 缺失 → incomplete"改为：有 `logistic_fee` 就算完备（不管产品字段有没有）。

## 2. parcel_cost_suggest —— CDP → 本地 SQL

### 接口不变

`GET /medias/api/products/<pid>/parcel-cost-suggest` 返回格式不变。

### 实现重写

`appcore/parcel_cost_suggest.py`:
- 去掉 Playwright、CDP、`browser_automation_lock`
- `pick_primary_sku_and_shop()` 保留（仍用 SQL）
- `suggest_parcel_cost()` 改查 `dianxiaomi_order_lines.logistic_fee`：
  ```sql
  SELECT logistic_fee FROM dianxiaomi_order_lines
  WHERE product_id = %s AND product_sku = %s
    AND logistic_fee IS NOT NULL AND logistic_fee > 0
    AND paid_at BETWEEN %s AND %s
  ```
- `compute_suggestion()` 保留不变（中位数/均值/min/max）
- `SETTLEMENT_DELAY_DAYS = 2` 保留

### 删除的代码

- `open_dxm_page()` context manager
- `build_order_payload()` / `post_form_via_page()` / `fetch_orders_in_window()`
- `filter_logistic_fees()` (不再需要按 SKU 从 productList 过滤，SQL 直接筛选)
- `DEFAULT_DXM_CDP_URL` / `ORDER_LIST_URL` / `ORDER_PAGE_URL` 常量
- Playwright import

## 3. 自动写库定时任务

### 新文件: `tools/auto_update_packet_costs.py`

每天跑一次（建议凌晨 3:07）：

```python
# 对每个有 ≥5 条有效 logistic_fee 的产品:
#   packet_cost_actual   = ROUND(AVG(logistic_fee), 2)    — 均值
#   packet_cost_estimated = ROUND(median of logistic_fee, 2) — 中位数
# 样本 < 5 → 跳过（不覆盖现有值）
```

中位数在 SQL 层不好直接算（MySQL 无原生 median），策略：
- 先查到 Python 内存
- 按 `product_id` 分组
- 每组的 `logistic_fee` 列表 sort → Python `statistics.median()`
- 批量 UPDATE

### 注册到 scheduled_tasks

`code = "auto_update_packet_costs"`，`runner = "tools/auto_update_packet_costs.py"`，每天一次。

## 4. UI 变更

### `_roas_form.html` + `roas_form.js`

- **去**：`#roasParcelSuggestAdopt` 按钮及其 JS handler
- **改**：`_renderParcelSuggestResult()` 不再渲染采纳按钮，改为展示统计信息 + 提示文字
- **改**：`_fetchParcelCostSuggestion()` 不再需要 30~60s 等待；提示文字改为"正在查询…"
- **加**：结果面板底部加一行灰色小字"实际小包成本和预估小包成本由系统每日自动更新"
- **保留**：`#roasParcelSuggestBtn` 按钮（但现在走 SQL，秒级返回）
- **保留**：`packet_cost_actual` / `packet_cost_estimated` 输入框可编辑（允许手动覆盖自动值）

### `web/routes/medias/products.py`

`api_parcel_cost_suggest` 端点不变，但去掉 browser automation lock 错误处理（不再需要）。

## 5. 回填脚本变更

`tools/order_profit_backfill.py`:
- `_LINE_QUERY` 增加 `d.logistic_fee`
- `_process_line()` 实现三级降级解析
- `calculate_line_profit()` 入参调整

## 6. 文件清单

### 新建
- `tools/auto_update_packet_costs.py` — 定时任务
- `tests/test_auto_update_packet_costs.py`

### 修改
- `appcore/order_analytics/profit_calculation.py` — `calculate_line_profit` 入参
- `tools/order_profit_backfill.py` — 三级降级链
- `appcore/parcel_cost_suggest.py` — CDP → SQL
- `web/static/roas_form.js` — 去采纳按钮
- `appcore/scheduled_tasks.py` — 注册新任务

### 不改（保持兼容）
- `web/routes/medias/products.py` — 端点签名不变
- `web/templates/medias/_roas_form.html` — HTML 结构不变（JS 动态渲染按钮区域）
- `appcore/order_analytics/cost_completeness.py` — 逻辑不变（product-level gate 单独保留）

## 7. 无回滚风险

- 旧 `logistic_fee` 列的 NULL 比例不变，降级链保证覆盖率不低于现状
- `parcel_cost_suggest` 接口格式不变
- DB schema 无变更
