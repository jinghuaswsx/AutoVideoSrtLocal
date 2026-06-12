# 产品级保本 ROAS 全量补齐设计

日期：2026-06-12

## 背景

素材管理的 ROAS 弹窗已经支持维护产品级独立站售价、采购价、小包成本、尺寸和用户支付运费，并即时计算预估/实际/当前采用保本 ROAS。运营要求所有产品都必须有一个可用的产品级保本 ROAS 数据，不能因为缺少实际采购、实际物流或历史订单而停留在空值。

## 事实来源

- `docs/superpowers/specs/2026-04-28-material-roas-design.md`：产品级 ROAS 字段和公式。
- `docs/superpowers/specs/2026-05-10-sku-actual-breakeven-roas-design.md`：SKU 订单实算 ROAS 继续独立存在，不替代产品级兜底。
- `docs/superpowers/specs/2026-05-04-order-level-shipping-cost-design.md`：物流真实值优先、产品级成本兜底。
- `docs/superpowers/specs/2026-06-05-dianxiaomi-sku-purchase-sync-design.md`：采购价优先来自店小秘云仓 SKU。
- `AGENTS.md`：文档驱动、worktree 隔离和 focused pytest 门禁。

## 目标

1. 所有未删除 `media_products` 都补齐产品级 ROAS 必要输入，保证 `product_roas.calculate_break_even_roas()` 能产出当前采用保本 ROAS。
2. 每个输入字段记录来源：实际、估算或默认值，避免估算数据伪装成实算。
3. 独立站售价优先从产品英文链接对应 Shopify 公共商品 JSON 的第一个 SKU/variant 价格获取。
4. 有实际采购价、实际物流价和历史用户支付运费时优先使用实际数据；缺失时按固定规则估算。
5. 回填脚本支持 dry-run、默认只填空值、必要时 force 覆盖。

## 非目标

- 不改变现有 ROAS 公式和手续费率；仍按独立站售价 + 用户支付运费的 7% 手续费计算。
- 不把广告费写入保本 ROAS 公式。
- 不替代 SKU 订单实算 ROAS 快照；产品级补齐用于素材列表和 ROAS 弹窗兜底。
- 不在本次新增定时任务；先提供可重复执行的回填脚本。

## 补齐规则

### 独立站售价

优先级：

1. 已有 `media_products.standalone_price`，且未使用 `--force`。
2. 英文 `media_products.product_link` 的 Shopify 公共 JSON，取 `variants[0].price`。
3. 产品链接域配置和多语言链接能解析出的 Shopify 公共 JSON，按候选 URL 逐个尝试，取第一个可用 `variants[0].price`。
4. `media_product_skus.shopify_price` 中该产品第一条有价格的 variant。
5. `shopify_orders` 历史订单中单件订单价格众数。
6. 估算：当前处理范围内已有售价、SKU 价、订单价的全局中位数；如果全局样本也为空，使用默认 `$19.99`。

第 6 项必须标注 `estimated`。运营要求所有产品都必须产出保本 ROAS，因此售价缺失不能阻断整批回填，但来源必须明确为估算。

### 采购价

优先级：

1. 已有 `media_products.purchase_price`，且未使用 `--force`。
2. 产品关联 `media_product_skus.dianxiaomi_sku` 命中 `dianxiaomi_yuncang_skus.unit_price` 的中位数。
3. 订单行 `dianxiaomi_order_lines.purchase_price_cny` 的中位数。
4. 估算：`standalone_price_usd * rmb_per_usd * 10%`。

第 4 项必须标注 `estimated`。

### 物流成本

字段语义保持：

- `packet_cost_actual`：只写真实物流样本中位数。
- `packet_cost_estimated`：可写真实中位数，也可写估算兜底。

优先级：

1. `dianxiaomi_order_lines.logistic_fee` 的中位数，写入 `packet_cost_actual` 和缺失的 `packet_cost_estimated`。
2. 估算：`standalone_price_usd * rmb_per_usd * 20%`，只写 `packet_cost_estimated`，不伪造 `packet_cost_actual`。

### 产品尺寸

缺失时统一填：

```text
package_length_cm = 10
package_width_cm  = 5
package_height_cm = 5
```

标注为 `default`。

### 用户支付运费

优先级：

1. `shopify_orders.shipping` 的产品历史平均值。
2. `dianxiaomi_order_lines.ship_amount` 的产品历史平均值。
3. 默认 `$6.99`。

第 3 项标注为 `estimated`。

## 来源标注

新增 `media_products.roas_inputs_source_json`，记录字段级来源和最近一次补齐计算结果：

```json
{
  "standalone_price": {"basis": "actual", "source": "shopify_product_js", "url": "..."},
  "purchase_price": {"basis": "estimated", "source": "standalone_price_10pct"},
  "packet_cost_estimated": {"basis": "estimated", "source": "standalone_price_20pct"},
  "packet_cost_actual": {"basis": "actual", "source": "dianxiaomi_logistic_fee_median"},
  "standalone_shipping_fee": {"basis": "estimated", "source": "default_6_99"},
  "package_length_cm": {"basis": "default", "source": "default_10x5x5"},
  "calculation": {"effective_roas": 2.31, "effective_basis": "estimated"}
}
```

前端序列化字段名为 `roas_inputs_source`。ROAS 弹窗显示字段级 `实算`、`估算` 或 `默认` 标记。

## 回填脚本

扩展 `tools/roas_fields_backfill.py`：

```bash
python tools/roas_fields_backfill.py --kind complete --dry-run
python tools/roas_fields_backfill.py --kind complete
```

返回统计至少包含：

- `products_total`
- `completed`
- `missing_price`
- `updated`
- `estimated_price`
- `estimated_purchase`
- `estimated_packet`
- `estimated_shipping`
- `default_dimensions`

默认只填空值；`--force` 允许重算覆盖。

## 验收

Focused tests：

```bash
python scripts/pytest_related.py --base origin/master --run
python -m pytest tests/test_roas_backfill.py tests/test_product_roas.py tests/test_material_roas_frontend.py -q
```

运行回填前先 dry-run，确认 `missing_price=0`；实际回填后抽查：

```sql
SELECT COUNT(*) AS missing
FROM media_products
WHERE deleted_at IS NULL
  AND (
    standalone_price IS NULL
    OR purchase_price IS NULL
    OR packet_cost_estimated IS NULL
    OR standalone_shipping_fee IS NULL
    OR package_length_cm IS NULL
    OR package_width_cm IS NULL
    OR package_height_cm IS NULL
    OR roas_inputs_source_json IS NULL
    OR JSON_EXTRACT(roas_inputs_source_json, '$.calculation.effective_roas') IS NULL
  );
```

`missing=0` 才能认为产品级 ROAS 输入补齐完成。
