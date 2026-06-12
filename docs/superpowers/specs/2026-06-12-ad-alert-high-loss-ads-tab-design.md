# 2026-06-12 — 广告预警默认「高额亏损广告」子 Tab

## 背景

`/ad-alerts` 已有按商品聚合的「广告预警」和按 Campaign / Ad Set / Ad 展示今天 0 成效的「问题广告」。运营当前更需要默认打开一张 AD 级止损清单：把所有具体广告过一遍，优先找出仍在消耗、没有成效或 ROAS 低于 1，且消耗金额最高的广告。

## 目标

1. `/ad-alerts/` 默认加载「高额亏损广告」子 Tab。
2. 新增子 Tab「高额亏损广告」，展示 AD 级 Top 30。
3. 保留既有「广告预警」和「问题广告」子 Tab，不改变它们原有筛选和 API 语义。
4. 新 Tab 以“帮助运营快速止损”为目标：先看具体 AD，再补充商品和亏损信息。

## 口径

### 数据源

- 历史日终：`meta_ad_daily_ad_metrics`
- 今天实时：`meta_ad_realtime_daily_ad_metrics`
- 今天实时只取每个 `(business_date, ad_account_id)` 最新 `snapshot_at`，不能使用全局 `MAX(snapshot_at)`。
- 实时表没有 `matched_product_code` 时，先依赖同一 AD 的历史日终 `matched_product_code/product_id`；仍缺失时允许以广告 code 作为兜底展示，但不得导致接口失败。

### 入选条件

以 AD 维度 `(normalized_ad_code, ad_account_id)` 聚合：

1. 最近 7 天有消耗：`last_7d.spend_usd > 0`。
2. 在最近 7 天内满足任一亏损信号：
   - `last_7d.result_count = 0`
   - 或 `last_7d.purchase_value_usd / last_7d.spend_usd < 1`

### 排序和数量

默认取前 30 条：

1. 先按最近 7 天消耗从高到低。
2. 再按今天消耗从高到低。
3. 再按最近 30 天消耗从高到低。

这个排序比单纯按 ROAS 更符合止损目标：ROAS 极低但只花了几美元的广告，不应该排在持续高消耗广告前面。

### 连续亏损天数

连续亏损天数按 AD 最近 30 天的每日聚合数据计算：

- 从当前 Meta 业务日往前看。
- 某天 `spend_usd > 0` 且 `result_count = 0` 或当日 `ROAS < 1`，记为亏损日。
- 遇到有消耗且 `ROAS >= 1` 的日期中断。
- 遇到无消耗日期也中断，避免把已停投广告误认为持续亏损。

## 展示

卡片展示字段：

- 商品名称
- 产品 Code
- 商品主图
- 广告信息：AD 名称、AD code、账户、国家/市场、广告分析详情链接
- 亏损信息：今天 / 最近 7 天 / 最近 30 天 / 整体的消耗、成效、ROAS、广告口径净盈亏
- 活跃天数：展示为“连续亏损 X 天”，同时保留首投日期

空状态文案必须说明当前没有命中最近 7 天高额亏损规则的 AD。加载失败不得影响其它 Tab。

## API

新增：

```text
GET /ad-alerts/api/high-loss-ads?q=&limit=30
```

返回：

```jsonc
{
  "business_date": "2026-06-12",
  "items": [
    {
      "code": "ad-code",
      "name": "Ad Name",
      "ad_account_id": "123",
      "ad_account_name": "newjoyloo",
      "country": "DE",
      "product_id": 10,
      "product_code": "demo-rjc",
      "product_name": "商品名",
      "product_main_image": "/medias/obj/...",
      "first_active_date": "2026-05-01",
      "last_active_date": "2026-06-12",
      "active_days": 12,
      "consecutive_loss_days": 4,
      "detail_url": "/order-analytics?...",
      "metrics": {
        "today": {"spend_usd": 12.0, "purchase_value_usd": 0.0, "result_count": 0, "roas": 0.0, "estimated_loss": -12.0},
        "last_7d": {"spend_usd": 120.0, "purchase_value_usd": 40.0, "result_count": 1, "roas": 0.3333, "estimated_loss": -80.0},
        "last_30d": {"spend_usd": 300.0, "purchase_value_usd": 100.0, "result_count": 3, "roas": 0.3333, "estimated_loss": -200.0},
        "overall": {"spend_usd": 500.0, "purchase_value_usd": 180.0, "result_count": 6, "roas": 0.36, "estimated_loss": -320.0}
      }
    }
  ],
  "total": 1
}
```

## 非目标

- 不新增数据库表。
- 不自动关停广告。
- 不改变「广告预警」商品聚合逻辑。
- 不改变「问题广告」今天 0 成效定义。
- 不将 Campaign / Ad Set 纳入本 Tab；本 Tab 只做具体 AD。

## 验证

Focused tests：

```bash
python3 scripts/pytest_related.py --base origin/master --run
```

若选择器未覆盖，至少运行：

```bash
pytest tests/test_ad_alerts.py tests/test_ad_alert_routes.py tests/test_ad_alert_template.py -q
```

全量 pytest 仅在发布、合并、用户明确要求或改动升级为跨模块基础设施时运行。
