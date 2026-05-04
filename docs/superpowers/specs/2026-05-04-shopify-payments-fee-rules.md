# Shopify Payments 手续费计算业务规则

> 本文档定义 Shopify Payments 收款手续费的完整计算逻辑，用于实现订单费用核算、利润测算、对账、净收入预估等业务功能。

## 1. 适用范围

- **店铺套餐**：Shopify 标准套餐（费率为 2.5% + $0.30 的版本）
- **结算币种**：USD（所有 Fee 与 Net 均以 USD 结算）
- **数据来源**：Shopify 后台 Payments → Transactions 导出的 CSV
- **业务场景**：跨境 DTC 电商，主要市场为欧洲（EUR / GBP），辅以 USD、AUD、CAD、NZD 等

## 2. 核心字段定义

| 字段 | 类型 | 说明 |
|---|---|---|
| `amount` | number (USD) | 客户实际支付金额，已折算为 USD |
| `presentment_currency` | string | 客户结账时使用的币种（如 `EUR`、`GBP`、`USD`） |
| `settlement_currency` | string | 店铺结算币种，固定为 `USD` |
| `card_brand` | string | 卡组织：`visa` / `master` / `american_express` / `discover` |
| `card_country` | string | 发卡行所在国家（CSV 中无此字段，需从其他来源补充或基于费率反推） |
| `fee` | number (USD) | Shopify 收取的手续费 |
| `net` | number (USD) | 商家实际到账金额，等于 `amount - fee` |

## 3. 费率结构（核心规则）

Shopify Payments 的实际费率由 **3 个独立费率组件** 叠加构成，而非套餐宣传的单一 `2.5% + $0.30`。

### 3.1 费率组件

| 组件 | 触发条件 | 费率 |
|---|---|---|
| **基础处理费** | 所有交易 | `2.5% × amount + $0.30` |
| **国际卡费**（International / Cross-border card fee） | 发卡行不在店铺所在国（美国） | `+1.0% × amount` |
| **货币转换费**（Currency conversion fee） | `presentment_currency != settlement_currency` | `+1.5% × amount` |

### 3.2 四档组合矩阵

根据上述两个独立条件（卡是否跨境、是否需要货币转换）组合，实际费率分为 4 档：

| 档位 | 卡来源 | 结账币种 | 百分比费率 | 固定费 | 完整公式 |
|---|---|---|---|---|---|
| **A** | 美国本土卡 | USD | 2.5% | $0.30 | `amount × 0.025 + 0.30` |
| **B** | 国际卡 | USD | 3.5% | $0.30 | `amount × 0.035 + 0.30` |
| **C** | 美国本土卡 | 非 USD | 4.0% | $0.30 | `amount × 0.040 + 0.30` |
| **D** | 国际卡 | 非 USD | 5.0% | $0.30 | `amount × 0.050 + 0.30` |

### 3.3 核心公式

```
fee = amount × (0.025 + cross_border_rate + currency_conversion_rate) + 0.30

其中：
  cross_border_rate         = 0.010  if 卡跨境  else 0.000
  currency_conversion_rate  = 0.015  if 需要转币 else 0.000

net = amount - fee
```

## 4. 卡来源判定（关键难点）

CSV 导出文件**不包含发卡行国家字段**，因此无法直接判断 A/B 档或 C/D 档。需要使用以下策略之一：

### 4.1 策略 A：通过 Shopify Admin API 补全

调用 Order API 获取 `payment_details.credit_card_bin`，再通过 BIN 查询库（如 binlist.net、自建 BIN 库）反查发卡国。

### 4.2 策略 B：通过实际 Fee 反推（推荐用于历史对账）

对于已存在的交易，可通过实际 `fee` 反推卡来源：

```python
def infer_card_origin(amount: float, fee: float, presentment_currency: str, settlement_currency: str = "USD") -> str:
    """根据实际 fee 反推卡来源（domestic / international）。"""
    needs_conversion = presentment_currency != settlement_currency
    base_rate = 0.025 + (0.015 if needs_conversion else 0.0)

    # 不含跨境费时的理论 fee
    expected_domestic_fee = amount * base_rate + 0.30
    # 含跨境费时的理论 fee
    expected_international_fee = amount * (base_rate + 0.01) + 0.30

    # 看实际 fee 更接近哪个
    if abs(fee - expected_domestic_fee) < abs(fee - expected_international_fee):
        return "domestic"
    return "international"
```

### 4.3 策略 C：用于前向预估（无 BIN 信息时）

如果场景是 **预估** 而非 **核对**（例如做利润测算或显示给客户预估到账金额），建议直接按市场假设：
- USD 订单：默认按 B 档（3.5% + $0.30）保守估算
- 非 USD 订单：默认按 D 档（5.0% + $0.30）保守估算

## 5. 参考实现（伪代码）

### 5.1 计算手续费

```python
def calculate_shopify_fee(
    amount: float,
    presentment_currency: str,
    card_country: str | None = None,
    settlement_currency: str = "USD",
    store_country: str = "US",
) -> dict:
    """
    计算 Shopify Payments 单笔交易手续费。

    Args:
        amount: 交易金额（已折算为店铺结算币种）
        presentment_currency: 客户支付时的币种
        card_country: 发卡行国家（ISO 2-letter code），None 表示未知
        settlement_currency: 店铺结算币种，默认 USD
        store_country: 店铺所在国家，默认 US

    Returns:
        {
            "amount": float,
            "fee": float,
            "net": float,
            "tier": str,                # "A" | "B" | "C" | "D" | "UNKNOWN"
            "rate_breakdown": dict,
        }
    """
    BASE_RATE = 0.025
    FIXED_FEE = 0.30
    CROSS_BORDER_RATE = 0.010
    CURRENCY_CONVERSION_RATE = 0.015

    needs_conversion = presentment_currency != settlement_currency

    if card_country is None:
        # 未知卡来源，用保守估算（按国际卡处理）
        is_cross_border = True
        tier_suffix = "_estimated"
    else:
        is_cross_border = card_country.upper() != store_country.upper()
        tier_suffix = ""

    percentage_rate = BASE_RATE
    if is_cross_border:
        percentage_rate += CROSS_BORDER_RATE
    if needs_conversion:
        percentage_rate += CURRENCY_CONVERSION_RATE

    fee = round(amount * percentage_rate + FIXED_FEE, 2)
    net = round(amount - fee, 2)

    # 判定档位
    if not is_cross_border and not needs_conversion:
        tier = "A"
    elif is_cross_border and not needs_conversion:
        tier = "B"
    elif not is_cross_border and needs_conversion:
        tier = "C"
    else:
        tier = "D"

    return {
        "amount": amount,
        "fee": fee,
        "net": net,
        "tier": tier + tier_suffix,
        "rate_breakdown": {
            "base_rate": BASE_RATE,
            "cross_border_rate": CROSS_BORDER_RATE if is_cross_border else 0.0,
            "currency_conversion_rate": CURRENCY_CONVERSION_RATE if needs_conversion else 0.0,
            "total_percentage_rate": percentage_rate,
            "fixed_fee": FIXED_FEE,
        },
    }
```

### 5.2 反推 + 校验

```python
def verify_fee(amount: float, actual_fee: float, presentment_currency: str) -> dict:
    """
    给定实际 fee，反推卡来源并校验是否符合标准费率。

    用于对账：判断某笔交易是否被多扣或少扣。
    """
    needs_conversion = presentment_currency != "USD"
    base_rate = 0.025 + (0.015 if needs_conversion else 0.0)

    expected_domestic = round(amount * base_rate + 0.30, 2)
    expected_international = round(amount * (base_rate + 0.01) + 0.30, 2)

    # 容差 $0.02（处理舍入差异）
    TOLERANCE = 0.02

    if abs(actual_fee - expected_domestic) <= TOLERANCE:
        return {"card_origin": "domestic", "matches_standard": True, "diff": actual_fee - expected_domestic}
    if abs(actual_fee - expected_international) <= TOLERANCE:
        return {"card_origin": "international", "matches_standard": True, "diff": actual_fee - expected_international}

    # 都不匹配，可能是异常订单
    return {
        "card_origin": "unknown",
        "matches_standard": False,
        "expected_domestic": expected_domestic,
        "expected_international": expected_international,
        "actual": actual_fee,
    }
```

## 6. 验证用例（基于真实数据）

实现后应通过以下用例（数据来自真实 Shopify 导出）：

| # | amount | currency | card_country | 期望 fee | 期望档位 |
|---|---|---|---|---|---|
| 1 | 19.94 | USD | US | 0.80 | A |
| 2 | 30.94 | USD | GB | 1.38 | B |
| 3 | 22.13 | EUR | US | 1.19 | C |
| 4 | 22.13 | EUR | DE | 1.41 | D |
| 5 | 20.94 | EUR | DE | 1.35 | D |
| 6 | 47.79 | EUR | US | 2.21 | C |

注：实际 CSV 中存在 ±$0.02 的舍入差异，校验时使用 `TOLERANCE = 0.02`。

## 7. 业务侧建议参数

供利润测算、定价模型、看板展示直接使用：

```python
# 历史实测平均费率（基于 8500+ 笔真实订单）
HISTORICAL_AVG_FEE_RATE = 0.0485        # 整体平均 4.85%
HISTORICAL_AVG_FEE_PER_ORDER = 1.38     # 平均每笔 $1.38
HISTORICAL_AVG_ORDER_VALUE = 28.50      # 平均客单价 $28.50

# 分档实测平均费率（含 $0.30 固定费在客单价下的折算）
TIER_A_EFFECTIVE_RATE = 0.0380   # 美元本土卡
TIER_B_EFFECTIVE_RATE = 0.0430   # 美元国际卡
TIER_C_EFFECTIVE_RATE = 0.0420   # 非美元本土卡
TIER_D_EFFECTIVE_RATE = 0.0600   # 非美元国际卡

# 利润测算保守值（推荐）
PROFIT_MODEL_FEE_RATE = 0.05     # 跨境电商场景，按 5% 预留手续费成本
```

## 8. 边界情况与注意事项

1. **退款（refund）**：CSV 中 `Type = refund` 的记录，`amount` 为负值，`fee` 通常为 0（Shopify 不退手续费）。计算 GMV 净收入时需将 refund 行的 `net`（负值）累加进去。
2. **拒付（chargeback）**：`Type = chargeback` 会产生额外 `$15` 的拒付费（dispute fee），且 `amount` 与 `fee` 均为负值，需单独处理。
3. **小数舍入**：Shopify 实际计算时按 banker's rounding（四舍六入五成双），与普通 `round()` 在 `.5` 边界可能差 $0.01。容差按 $0.02 处理足够覆盖。
4. **American Express**：在某些套餐下 Amex 有额外费率（如 +0.5%），但本店铺数据未观察到该规则生效，按通用规则处理即可。
5. **Shop Pay / Apple Pay / Google Pay**：以上钱包仍走底层信用卡，费率与底层卡的 4 档判定一致，不需特殊处理。
6. **套餐切换前的历史数据**：本文档费率仅适用于当前套餐切换后的订单。切换前的订单（如有）需使用旧套餐费率（如 Basic 套餐为 2.9% + $0.30）单独处理。

## 9. 必须实现的对外接口

任何使用本规则的代码模块，至少需暴露以下接口：

```python
calculate_shopify_fee(amount, presentment_currency, card_country=None) -> dict
verify_fee(amount, actual_fee, presentment_currency) -> dict
classify_tier(presentment_currency, card_country) -> str  # "A" | "B" | "C" | "D"
estimate_net_income(amount, presentment_currency, card_country=None) -> float
```

## 10. 在订单利润核算中的实施约束

参见 `docs/superpowers/plans/2026-05-04-order-profit-calculation.md`：

- **本店首版采用策略 C（前向预估）**：因店小秘 `raw_order_json` 不含 `card_country` / `bin` / `gateway` 字段，且 Shopify Admin API 路线已放弃；用 `buyerCountry`（收货国）作为 `card_country` 代理。
- **`presentment_currency` 推断**：店小秘 raw_json 没有该字段，按 `buyerCountry` 映射推断（欧元区→EUR / GB→GBP / US→USD / AU→AUD / CA→CAD / NZ→NZD / 其他→USD）。
- **校验回路（策略 B）**：业务方按月/周从 Shopify 后台导出 Payments CSV，导入数据库 `shopify_payments_transactions` 表，跑反推 + 与策略 C 估算值对比，输出偏差报告，用于调整 `material_roas_rmb_per_usd` 等参数。
