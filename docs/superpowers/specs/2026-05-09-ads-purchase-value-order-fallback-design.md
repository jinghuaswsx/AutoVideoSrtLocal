# 广告分析「购买金额」校正方案：账户级 column_preset + 订单口径兜底

- 状态：active
- 起因：[AUT-14](mention://issue/cf7ad548-5b9e-4f67-aceb-5c408865fe3e) — `/order-analytics` 广告分析「Ad」tab 显示 Omurio / newjoyloo_bak 账户的购买金额为 0
- 关联：[2026-05-07-meta-ads-multi-account-design.md](2026-05-07-meta-ads-multi-account-design.md)、[2026-05-08-ads-analytics-tabs-design.md](2026-05-08-ads-analytics-tabs-design.md)
- 上次：[2026-05-08-ads-analytics-tabs-design.md](2026-05-08-ads-analytics-tabs-design.md) 落地了三层 tab；本 spec 修复其中购买金额数据完整性

## 1. 背景与现象

Meta Ads Manager 浏览器端导出 CSV 时，URL 必须带 `column_preset=<id>` 才能拿到一组完整的列（`购物转化价值 / 广告花费回报 (ROAS) - 购物 / 加入购物车次数 / 结账发起次数 / 平均购物转化价值 / 视频平均播放时长 / CPM / 单次链接点击费用` 等）。

旧户 `2110407576446225` 在 Meta UI 内手工存了一个 column preset，ID 是 `1658418688523178`，并被硬编码进：

- [scripts/run_meta_ads_backfill_range.py:44](../../../scripts/run_meta_ads_backfill_range.py#L44)
- [tools/meta_realtime_local_sync.py:75](../../../tools/meta_realtime_local_sync.py#L75)

**Meta 列模板的可见性是按账户绑定的**——同一个 preset ID 在另一个账户下不存在。当 Omurio (`1253003326160754`) 与 newjoyloo_bak (`1861285821213497`) 走这套 URL 时，Meta 后台找不到对应 preset 就回退到一组裸列（成效 / 展示次数 / 已花费金额 / 单次成效费用 / 广告组预算 / 广告投放 / 竞价 / 归因设置），里面**没有购买相关字段**。

DB 现状（2026-04-25 ~ 05-07）：

| 账户 | 状态 | 消耗 | 购买金额 |
|---|---|---|---|
| 2110407576446225（newjoyloo_old）| disabled | $90,617 | $124,577 ✓ |
| 1253003326160754（Omurio）| enabled | $4,223 | $0 ❌ |
| 1861285821213497（newjoyloo_bak）| enabled | $1,195 | $0 ❌ |

订单侧两个站都正常同步：`dianxiaomi_order_lines` 里 omurio 站 4-25 起 90 单 $5,502，newjoy 站 5,476 单 $107K。

## 2. 目标与非目标

### 目标
1. 让广告分析（Campaign / Ad Set / Ad）三个 tab 在 Omurio / newjoyloo_bak 这种「Meta CSV 缺购买列」的场景下也能显示有意义的购买金额，而不是一律 $0。
2. 给每个广告账户独立配 `column_preset`，**当 Meta 同步恢复后**，新导出的 CSV 列齐全，DB 不再缺数据。
3. 改动**不动同步链路的执行节奏 / 锁机制 / 失败兜底**——只追加配置面与查询时计算。
4. 接入现有 `data_quality` 体系，让前端能识别哪些值是按订单兜底算出来的。

### 非目标
- 历史数据回填：用户明确说 "现在 Meta 的数据同步已经出问题了，没有办法正常同步数据"，本 spec 不动 `meta_ad_daily_*_metrics` / `meta_ad_realtime_*_metrics` 历史行的 `purchase_value_usd=0`；查询时按订单兜底覆盖即可。
- 不改 `meta_ad_campaign_metrics`（手工上传周期表）路径；它走 `import_meta_ad_rows`，CSV 是用户手工上传的，列由用户决定。
- 不引入新的 dashboard 页面；只在现有「广告分析」三 tab + `data_quality` 字段补强。

## 3. 修复方案（A + C）

### A. 账户级 `column_preset` 配置

**数据模型**：在 `appcore/meta_ad_accounts.py` 的 `MetaAdAccount` 上加一个可选字段 `column_preset: str | None`。

`system_settings.meta_ad_accounts` JSON 每条记录新增可选字段 `column_preset`，缺失时回退到老 preset `1658418688523178`，保持向后兼容。

```json
[
  {
    "code": "newjoyloo_bak",
    "account_id": "1861285821213497",
    "business_id": "476723373113063",
    "csv_prefix": "newjoyloo_bak",
    "store_codes": ["newjoy"],
    "enabled": true,
    "column_preset": "<TO_BE_FILLED>",
    "note": "..."
  },
  {
    "code": "Omurio",
    "account_id": "1253003326160754",
    "business_id": "909367947900474",
    "csv_prefix": "Omurio",
    "store_codes": ["omurio"],
    "enabled": true,
    "column_preset": "<TO_BE_FILLED>",
    "note": ""
  }
]
```

**URL 构造**：[scripts/run_meta_ads_backfill_range.py](../../../scripts/run_meta_ads_backfill_range.py) `build_url` 接受 `column_preset` 参数；CLI 加 `--column-preset`，默认仍为 `1658418688523178`（兼容老脚本）。

**调用端**：[tools/meta_daily_final_sync.py](../../../tools/meta_daily_final_sync.py) 的 `_run_meta_ads_export` 把 `account.column_preset` 拼成 `--column-preset <id>` 传给 subprocess。同样地 [tools/roi_hourly_sync.py](../../../tools/roi_hourly_sync.py) 的实时同步链路也走相同 wiring。

**手动同步入口**：[appcore/meta_ad_manual_sync.py](../../../appcore/meta_ad_manual_sync.py) 已通过 `run_final_sync(..., account_codes=[code])` 间接复用同条链路，自动跟着 column_preset 走，不需要单独改。

**运维步骤**（admin 一次性）：
1. 在 Omurio / newjoyloo_bak 各自 Meta Ads Manager UI 里创建一个 column preset，包含与老户一致的列集合（购物转化价值、ROAS - 购物、加入购物车次数、结账发起次数、平均购物转化价值、视频平均播放时长、CPM、单次链接点击费用、链接点击量、展示次数）。
2. 复制 preset ID（`https://adsmanager.facebook.com/adsmanager/...&column_preset=<复制这个>...`）。
3. `/order-analytics?tab=ad-accounts` 编辑账户，填入 `column_preset`，保存。
4. 等下次自动同步或点手动同步。

### C. 查询时按订单口径兜底（立即生效）

新增 `appcore/order_analytics/meta_ads_purchase_fallback.py`（或在 `meta_ads.py` 内独立段落）：

```python
def fill_purchase_value_from_orders(
    rows: list[dict],
    *,
    level: str,                      # "campaign" / "adset" / "ad"
    start_date: date,
    end_date: date,
) -> tuple[list[dict], dict]:
    """
    对 spend>0 但 purchase_value=0 的行，按 (matched_product_code, ad_account_id) 分组，
    用 dianxiaomi_order_lines 里 (product_code, site_code∈account.store_codes, paid_at∈[start,end+1)) 
    的总营收，按当行 spend 占组内总 spend 的比例分摊回 purchase_value_usd。

    返回 (augmented_rows, fallback_stats)：
      augmented_rows: 每行增加 'purchase_value_source': 'meta' | 'order_fallback'
      fallback_stats: { 'fallback_row_count', 'fallback_revenue_total_usd' }
    """
```

**触发条件**（保守）：只对一个分组（matched_product_code, ad_account_id）内**所有行**都 `purchase_value_usd == 0` 且 `SUM(spend) > 0` 的整组应用兜底。这样不会污染老户里"个别广告真没转化"的合理 0 值。

**未匹配产品的兜底**：当 `matched_product_code IS NULL` 时无法兜底，保留原值（0）。前端可显示 unknown 状态。

**集成点**：
- `get_ads_level_list(level, start_date, end_date)` — 在拼装 `out` 之前，把 SQL `SELECT` 加上 `MAX(matched_product_code) AS matched_product_code`，结果交给 `fill_purchase_value_from_orders`，再做 ROAS / 总额计算。
- `get_ads_level_detail(level, code, start_date, end_date)` — 类似，但要把 daily rows 内每一天的 `(matched_product_code, ad_account_id)` 拿出来分别兜底。

### `data_quality` 集成

接入 [appcore/order_analytics/data_quality.py](../../../appcore/order_analytics/data_quality.py) 的现有体系，给广告分析三个 API 顶层加：

```json
{
  "data_quality": {
    "status": "ok" | "fallback_used",
    "purchase_value": {
      "fallback_row_count": 24,
      "fallback_revenue_total_usd": 5418.76,
      "note": "Meta CSV 缺购买列，按 dianxiaomi_order_lines 站内同产品营收按 spend 比例分摊"
    }
  }
}
```

前端缺 `data_quality` 时按 `unknown` 处理（沿用 [docs/analytics-data-quality-guardrails.md](../../analytics-data-quality-guardrails.md) 约定）。

## 4. 测试矩阵（TDD）

### 4.1 `appcore/meta_ad_accounts`
- `MetaAdAccount.column_preset` 默认是 `"1658418688523178"`；JSON 里不写时回退；写了非空 string 时优先用配置值。
- `to_dict()` round-trip 包含 `column_preset` 字段。
- `_coerce_account` 接 `column_preset` 为空字符串时回落到默认。

### 4.2 `scripts/run_meta_ads_backfill_range.build_url`
- `build_url(level, day, account_id=..., business_id=..., column_preset="abc123")` 生成的 URL 含 `column_preset=abc123`。
- 不传 `column_preset` 时使用默认值。

### 4.3 `tools/meta_daily_final_sync._run_meta_ads_export`
- subprocess.run 的 cmd 列表里包含 `--column-preset <account.column_preset>`。
- 缺省（账户没填）时仍然传默认值（兼容老配置）。

### 4.4 `appcore/order_analytics/meta_ads.fill_purchase_value_from_orders`
- 只对全 0 分组兜底；老户里 spend>0 + purchase>0 + 个别行 0 的不动。
- `matched_product_code IS NULL` 的行保留原值。
- `account.store_codes` 多店铺时按全部站点 SUM。
- 兜底后 `purchase_value_source = "order_fallback"`，老值的 source = `"meta"`。
- `roas_purchase` 用兜底后的 purchase 重算。

### 4.5 `get_ads_level_list` 集成
- 仅 Omurio 数据时，purchase_value 来自订单兜底。
- 顶层 `data_quality.status == "fallback_used"`，`fallback_row_count` 与 `fallback_revenue_total_usd` 与兜底命中一致。
- 老户数据正常（purchase_value 非 0）时 `data_quality.status == "ok"`，无 fallback。

### 4.6 `get_ads_level_detail` 集成
- 详情页按日逐天兜底；包含 realtime today 时仍然走 Meta 路径不兜底（实时表无 ad-level 数据）。
- 历史天数里若某天 spend>0 / purchase=0 + matched_product_code 在订单表里有数据，该天 purchase_value 显示兜底值。

### 4.7 现有回归
- `tests/test_order_analytics_ads.py` 既有 21 个用例必须不退化（兜底默认 off=老户数据全部走原路径）。
- `tests/test_roi_hourly_sync_meta_multi_account.py` 多账户 spec 测试不退化。

## 5. 文档锚点 / 修改清单

- 新建 [docs/superpowers/specs/2026-05-09-ads-purchase-value-order-fallback-design.md](.) — 本 spec
- 更新 [CLAUDE.md](../../../CLAUDE.md) Meta 多账户段落：账户字段加 `column_preset`，运维 SOP 加「新增账户时必须在 Meta UI 建对应 column preset」
- 更新 [AGENTS.md](../../../AGENTS.md) 同款补充
- 更新 [appcore/meta_ad_accounts.py](../../../appcore/meta_ad_accounts.py)：MetaAdAccount + `_coerce_account`
- 更新 [scripts/run_meta_ads_backfill_range.py](../../../scripts/run_meta_ads_backfill_range.py)：CLI 加 `--column-preset`，URL 用参数
- 更新 [tools/meta_realtime_local_sync.py](../../../tools/meta_realtime_local_sync.py)：URL 用 env / 默认
- 更新 [tools/meta_daily_final_sync.py](../../../tools/meta_daily_final_sync.py)：`_run_meta_ads_export` 拼 `--column-preset`
- 新增/更新 [appcore/order_analytics/meta_ads.py](../../../appcore/order_analytics/meta_ads.py)：兜底 helper + 三处集成 + `data_quality` 输出
- 新增 / 更新对应测试：`tests/test_roi_hourly_sync_meta_multi_account.py`（column_preset 字段）、`tests/test_meta_server_sync_tools.py`（subprocess cmd）、`tests/test_order_analytics_ads.py`（fallback 路径）

## 6. 风险与已知坑

1. **兜底语义**：`order_fallback` 是按 spend 比例分摊到产品下所有 ad，不是真实归因。一个产品下跑多条 ad 时，每条 ad 的购买金额是估计值；适合看「整体趋势」，不适合微观对比。前端必须能区分（通过 `purchase_value_source`）。
2. **`matched_product_code` 缺失**：未匹配产品的 ad 仍然显示 0；这部分需要靠 Meta CSV 列恢复后才能修。
3. **多账户共享 store**：如果未来一个 store 被多个 ad account 共享（spec 当前 store_codes 里允许），订单兜底要按每个账户的 store_codes 集合各自查询，**不要**做整 store 全局求和后跨账户重复分摊；本 spec 实现按 `(matched_product_code, ad_account_id)` 分组保证不重复。
4. **column_preset 在 Meta 改名/删除**：账户里建的 preset 后面被人删了，URL 又会落回裸列。这是数据质量问题，不是代码 bug；`data_quality.status="fallback_used"` 会持续告警，提醒 admin 去 Meta UI 重建。
5. **同步链路当前坏的状态**：用户明确说现在 Meta 同步本身有问题。本 spec 的 A 部分（per-account preset 配置）只有当 Meta 同步恢复后才真正起作用；C 部分立刻让看板可读。
