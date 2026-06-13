# 产品盈亏未分摊广告费新品占比（2026-06-13）

## 背景

运营每天会上新产品和素材，需要在 `/product-profit` 的“产品列表”中点击“未分摊广告”后，快速判断未分摊广告费主要来自新品投放还是非新品投放。

现有“未分摊 campaign”列表已经能展示未分摊广告计划及原因，但没有标注新品/非新品，也没有给出两类广告费占比。

## 锚点

- `AGENTS.md`：文档驱动代码、worktree 隔离、focused pytest 规则。
- `appcore/order_analytics/CLAUDE.md`：未分摊 campaign、广告费分摊、数据质量护栏。
- `docs/superpowers/specs/2026-05-10-realtime-unallocated-campaign-navigation.md`：未分摊广告费原因包含 `unmatched_product` 和 `matched_no_units`。
- `docs/superpowers/specs/2026-05-27-new-product-launch-analysis-design.md`：新品/老品按上广告时间判断，默认窗口 7 天。
- `docs/superpowers/specs/2026-06-12-product-profit-unallocated-daily-consistency.md`：产品盈亏未分摊广告费的每日一致口径。
- `docs/superpowers/specs/2026-06-08-targeted-pytest-verification.md`：本需求使用 focused tests，不默认跑全量 pytest。

## 已确认口径

1. “新品”沿用现有“新品投放分析”口径，不新增另一套定义。
2. 新品判断字段为 `product_ad_launch_dates.ad_launch_date`。
3. 默认窗口为 7 天，语义为“上广告时间近 7 天内”；后续如果产品盈亏看板增加窗口选择，可复用 3 / 7 / 15 / 30 / 60 天参数。
4. 新品判断使用北京时间自然日，不跟随 `/product-profit` 查询的历史日期范围变化。
5. 没有匹配到产品的 `unmatched_product` campaign 不算新品，统计归入“非新品”，但行内保留“未匹配产品”原因，避免误读为老产品。
6. 占比按未分摊广告费金额计算，不按 campaign 数量计算。

## 范围

做：

- 在产品盈亏未分摊 campaign API 响应中，为每行增加新品分类字段。
- 在同一响应中增加新品/非新品金额统计和占比。
- 在 `/product-profit` 的未分摊 campaign 折叠区展示：
  - 行级“新品 / 非新品”标签；
  - 汇总：新品占比、非新品占比、各自金额和 campaign 数。
- 保留现有“分摊原因”和手动配对操作。

不做：

- 不改变广告费分摊公式。
- 不新增数据库表或 migration。
- 不改变 `product_ad_launch_dates` 的生成规则。
- 不把未匹配广告强行归因到新品。
- 不新增独立页面；继续使用现有“未分摊广告”点击入口和广告明细 Tab。

## 后端设计

### 行级字段

扩展 `appcore/order_analytics/product_profit_ads.py::generate_unmatched_ads_report()` 输出的 `unmatched[]` 行：

```json
{
  "launch_segment": "new_product",
  "launch_segment_label": "新品",
  "is_new_product": true,
  "ad_launch_date": "2026-06-10",
  "product_launch_window_days": 7
}
```

字段语义：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `launch_segment` | string | `new_product` 或 `non_new_product` |
| `launch_segment_label` | string | `新品` 或 `非新品` |
| `is_new_product` | bool | 是否新品 |
| `ad_launch_date` | string/null | 匹配产品的上广告日期；未匹配产品为 `null` |
| `product_launch_window_days` | int | 本次判断使用的新品窗口，默认 7 |

`matched_no_units` 行有 `matched_product_id`，按该产品的 `product_ad_launch_dates.ad_launch_date` 判断。`unmatched_product` 行没有产品 ID，固定为：

```json
{
  "launch_segment": "non_new_product",
  "launch_segment_label": "非新品",
  "is_new_product": false,
  "ad_launch_date": null
}
```

### 汇总字段

在 `generate_unmatched_ads_report()` 顶层增加：

```json
{
  "unallocated_launch_segment_summary": {
    "window_days": 7,
    "total_spend_usd": 1000.0,
    "new_product": {
      "label": "新品",
      "spend_usd": 700.0,
      "share_pct": 70.0,
      "campaign_count": 12
    },
    "non_new_product": {
      "label": "非新品",
      "spend_usd": 300.0,
      "share_pct": 30.0,
      "campaign_count": 8
    }
  }
}
```

占比公式：

```text
share_pct = segment_spend_usd / total_unallocated_spend_usd * 100
```

当总未分摊金额为 0 时，两类 `share_pct` 返回 `0.0`。

### 分类 helper

新增内部 helper，集中完成：

1. 从 `unmatched[]` 聚合结果收集 `matched_product_id`。
2. 调用 `product_ad_launch.seed_missing_fallback_launch_dates()`，保证缺失产品有 fallback 上广告日期。
3. 批量查询 `product_ad_launch_dates`。
4. 使用 `product_ad_launch.classify_launch_date(..., window_days=7)` 判断新品。
5. 给行附加字段，并计算汇总。

helper 不应逐行查询数据库。

## 前端设计

`web/templates/product_profit_dashboard.html` 中的“未分摊 campaign”折叠区增加一个紧凑统计条，位置在折叠区标题下方、表格上方：

```text
新品 $700.00 / 70.00% / 12 个
非新品 $300.00 / 30.00% / 8 个
```

表格增加一列“新品标签”，显示：

- `新品`
- `非新品`
- 对 `unmatched_product` 行，可显示 `非新品 · 未匹配产品`

行级“分摊原因”列保持现有展示，不合并到新品标签里。

## 错误处理

- `product_ad_launch_dates` 缺记录时，helper 先执行 fallback seed；仍查不到时按非新品处理，并保持 `ad_launch_date = null`。
- 单行缺 `matched_product_id` 时按非新品处理。
- 前端缺少 `unallocated_launch_segment_summary` 时隐藏统计条，表格仍能展示旧数据。

## 测试计划

后端 focused tests：

- `tests/test_product_profit_ads.py`
  - `matched_no_units` 且上广告时间在窗口内，标为新品并计入新品金额。
  - `matched_no_units` 且上广告时间早于窗口，标为非新品并计入非新品金额。
  - `unmatched_product` 固定标为非新品。
  - 汇总占比按金额计算，campaign count 单独统计。

前端静态测试：

- `tests/test_product_profit_dashboard_assets.py`
  - 未分摊 campaign 表存在“新品标签”列。
  - 模板包含渲染 `unallocated_launch_segment_summary` 的函数。
  - 未匹配产品行显示“非新品 · 未匹配产品”的逻辑存在。

验证命令：

```bash
python3 scripts/pytest_related.py --base origin/master --run
pytest tests/test_product_profit_ads.py tests/test_product_profit_dashboard_assets.py -q
```

全量 `pytest -q` 默认跳过，除非本需求升级为发布/合并验证或用户明确要求。

## 修改顺序

1. 新增本 spec。
2. 后端给 `generate_unmatched_ads_report()` 增加行级新品分类和汇总。
3. 前端展示统计条和行级标签。
4. 更新 focused tests。
5. 运行 focused tests 和必要 route smoke。

## related

- [2026-05-10-realtime-unallocated-campaign-navigation.md](2026-05-10-realtime-unallocated-campaign-navigation.md)
- [2026-05-27-new-product-launch-analysis-design.md](2026-05-27-new-product-launch-analysis-design.md)
- [2026-06-12-product-profit-unallocated-daily-consistency.md](2026-06-12-product-profit-unallocated-daily-consistency.md)
