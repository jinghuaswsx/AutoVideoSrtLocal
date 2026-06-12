# 2026-06-12 — 广告预警增加「问题广告」子 Tab

## 背景

`/ad-alerts` 已有「广告预警」模块，按商品 × 语言维度展示低 ROAS 且仍有活跃消耗的预警。运营还需要一张更直接的排查表：先看到今天仍在消耗但没有成效的广告，再从 Campaign / Ad Set / Ad 三个维度查看这些广告的历史表现，并能跳到广告分析页看完整明细。

## 目标

1. `/ad-alerts` 页面增加两个一级子 Tab：
   - **广告预警**：保留现有列表、阈值、详情弹窗和筛选逻辑。
   - **问题广告**：新增表格视图，专门展示今天不出单的广告。
2. 「问题广告」下方增加 `Campaign / Ad Set / Ad` 三个维度 Tab。
3. 每个维度都以表格展示今天没有成效的广告，并按时间窗口展示成效数据。
4. 点击具体广告，跳转到 `/order-analytics` 的广告分析详情页。

## 数据范围

问题广告的准入条件：

- 今天广告有消耗：`today.spend_usd > 0`。
- 今天成效为 0：`today.result_count = 0`。
- 今天数据优先使用实时表最新快照：
  - Campaign：`meta_ad_realtime_daily_campaign_metrics`
  - Ad Set：`meta_ad_realtime_daily_adset_metrics`
  - Ad：`meta_ad_realtime_daily_ad_metrics`
- 实时表取数必须按 `(business_date, ad_account_id)` 取各账户最新 `snapshot_at`，不得用全局 `MAX(snapshot_at)`。
- 历史窗口使用对应 daily 表：
  - Campaign：`meta_ad_daily_campaign_metrics`
  - Ad Set：`meta_ad_daily_adset_metrics`
  - Ad：`meta_ad_daily_ad_metrics`

## 时间窗口与指标

每行必须包含以下 5 个时间窗口，每个窗口包含三组指标：

| 时间窗口 | 消耗 | 成效 | ROAS |
|---|---:|---:|---:|
| 今天 | `spend_usd` | `result_count` | `purchase_value_usd / spend_usd` |
| 昨天 | 同上 | 同上 | 同上 |
| 最近 7 天 | 同上，包含今天 | 同上 | 同上 |
| 最近 30 天 | 同上，包含今天 | 同上 | 同上 |
| 整体 | 自上线以来累计，包含今天实时值 | 同上 | 同上 |

「整体」的起点来自对应 daily 表的最早 `meta_business_date/report_date`；如果只有今天实时数据，则起点为今天。

## 跳转

每行点击后打开广告分析详情：

```text
/order-analytics?tab=ads&ads_level=<campaign|adset|ad>&ads_code=<code>&ads_name=<name>&ad_account_id=<account_id>&start_date=<first_active_date>&end_date=<today>
```

广告分析页面的深链逻辑必须优先使用 URL 中的 `start_date/end_date`。如果没有传日期，保留原有最近一个月默认。

## API

新增：

```text
GET /ad-alerts/api/problem-ads?level=campaign|adset|ad&q=&limit=200
```

返回：

```jsonc
{
  "level": "ad",
  "business_date": "2026-06-12",
  "items": [
    {
      "level": "ad",
      "code": "...",
      "name": "...",
      "ad_account_id": "...",
      "ad_account_name": "...",
      "first_active_date": "2026-05-01",
      "last_active_date": "2026-06-12",
      "detail_url": "/order-analytics?...",
      "metrics": {
        "today": {"spend_usd": 1.23, "result_count": 0, "roas": 0.0},
        "yesterday": {"spend_usd": 0, "result_count": 0, "roas": null},
        "last_7d": {"spend_usd": 12.34, "result_count": 1, "roas": 0.8},
        "last_30d": {"spend_usd": 45.67, "result_count": 3, "roas": 1.1},
        "overall": {"spend_usd": 100.0, "result_count": 6, "roas": 1.2}
      }
    }
  ],
  "total": 1
}
```

## 非目标

- 不新增数据库表。
- 不改变现有「广告预警」触发规则。
- 不在 `/ad-alerts` 内重做广告分析详情页；详情仍复用 `/order-analytics`。
- 不自动关停广告。

## 验证

Focused tests：

```bash
pytest tests/test_ad_alerts.py \
       tests/test_ad_alert_routes.py \
       tests/test_ad_alert_template.py \
       tests/test_order_analytics_ads.py -q
```

全量 pytest 仅在发布 / 合并 / 用户明确要求时运行。
