# 明空产品库素材管理入库与投放状态标记设计

日期：2026-05-20

## 背景

`/xuanpin/mk#products` 的「产品库」当前展示店小秘 Listing 快照、产品图、明空消耗、素材库按钮和操作入口。运营在刷产品时，需要直接知道该明空产品是否已经进入本地素材管理产品库，以及已入库产品最近一个月投放表现是否高于素材管理里的保本 ROAS。

现有相关链路：

- `/xuanpin/mk` 的 `产品库 / 视频素材库 / 昨天消耗前100` 子 tab 见 `docs/superpowers/specs/2026-05-18-mingkong-video-material-library-subtabs-design.md`。
- 视频素材库已使用本地明空素材快照，见 `docs/superpowers/specs/2026-05-18-mingkong-video-material-local-index-design.md`。
- 明空入库逻辑按产品 code 去掉 `-rjc` 后匹配 `media_products.product_code`，见 `appcore/mk_import.py`。
- 保本 ROAS 公式来自 `appcore/product_roas.py` 与 `docs/superpowers/specs/2026-05-10-sku-actual-breakeven-roas-design.md`。
- 产品投放 ROAS 口径沿用 `appcore/order_analytics/product_profit_list.py`：产品收入 / 产品广告费。

## 目标

1. 在明空产品库表格新增「是否入库」列，显示 `已入库` 或 `未入库`。
2. 已入库产品按最近 30 个已结束业务日的投放表现给整行标色：
   - 有广告消耗且 `近 30 日 ROAS >= 保本 ROAS`：绿色。
   - 有广告消耗且 `近 30 日 ROAS < 保本 ROAS`：红色。
   - 近 30 日没有对应广告计划，或广告计划无消耗：黄色。
3. 未入库产品保持当前默认样式，不标色。
4. 状态数据由后端 `/xuanpin/api/mk-selection` 返回，前端只渲染，不在浏览器里推断。

## 非目标

- 不改变明空视频素材库、本地明空素材快照和昨天消耗 Top100 的数据同步。
- 不新增定时任务。
- 不新增数据库字段或迁移。
- 不改变素材入库、做小语种、任务中心创建流程。
- 不把当天未收盘实时广告数据纳入本期判断。

## 匹配口径

每个店小秘排名行解析 `product_code`：

1. 优先使用 `dianxiaomi_rankings.product_code` 或 `dianxiaomi_product_assets.product_code`。
2. 缺失时从 `product_url` 的 `/products/<handle>` 解析。
3. 匹配本地 `media_products` 时把两边 code 都小写，并去掉结尾 `-rjc`。
4. 如果排名行已有 `media_product_id` 且对应 `media_products` 未删除，优先使用该产品。
5. 若 `media_product_id` 为空或失效，再按归一化 product code 匹配。

返回字段建议：

```json
{
  "library_status": {
    "in_library": true,
    "status_label": "已入库",
    "media_product_id": 123,
    "matched_by": "product_code",
    "card_status": "green",
    "ad_spend_usd": 58.32,
    "revenue_usd": 168.44,
    "roas": 2.89,
    "breakeven_roas": 2.1,
    "window_start": "2026-04-19",
    "window_end": "2026-05-18"
  }
}
```

`card_status` 取值：

- `none`：未入库或无可展示状态。
- `green`：已入库，有消耗，ROAS 达标。
- `red`：已入库，有消耗，ROAS 低于保本。
- `yellow`：已入库但无广告消耗，或缺少保本 ROAS 数据。

## 投放与保本口径

最近一个月定义为「昨天往前 30 天」，不含当天未收盘数据。运行日为 `D` 时：

```text
window_end = D - 1
window_start = window_end - 29 days
```

广告消耗：

- 从 `meta_ad_daily_campaign_metrics` 按 `COALESCE(meta_business_date, report_date)` 聚合 `spend_usd`。
- 只统计 `product_id IN 当前页已入库产品`。
- `spend_usd > 0` 视为有投放消耗。

收入：

- 从 `order_profit_lines` JOIN `dianxiaomi_order_lines`，按同一业务日期窗口聚合 `revenue_usd`。
- 只统计 `order_profit_lines.product_id IN 当前页已入库产品`。

近 30 日 ROAS：

```text
roas = revenue_usd / ad_spend_usd
```

广告费为 0 时 `roas = null`。

保本 ROAS：

- 使用本地素材管理产品的 `purchase_price`、`packet_cost_estimated`、`packet_cost_actual`、`standalone_price`、`standalone_shipping_fee`。
- 调用 `appcore.product_roas.calculate_break_even_roas()`，取 `effective_roas`。
- 保本 ROAS 算不出时，不判断红绿，落黄色，提示「保本待补」。

## 后端设计

新增服务 helper，放在 `web/services/media_mk_selection.py`，避免路由承担业务逻辑：

- `normalize_library_product_code(value) -> str`
- `build_library_status_index(items, *, db_query_fn, today_fn=None, get_rmb_rate_fn=None) -> dict[str, dict]`

`build_mk_selection_response()` 在组装当前页 `items` 后，批量查询：

1. 当前页 `media_product_id` 对应产品。
2. 当前页归一化 `product_code` 对应产品。
3. 当前页已入库产品的近 30 日收入与广告费。
4. 当前页已入库产品的保本 ROAS 输入字段。

这样避免每行 N+1 查询，也让测试可以通过 fake `db_query_fn` 覆盖。

## 前端设计

`web/templates/mk_selection.html` 调整：

- 在「中文产品名」后新增「是否入库」列。
- 表格 `colspan` 从 12 调整为 13。
- 渲染行时读取 `r.library_status`：
  - badge 显示 `已入库 / 未入库`。
  - `tr` 增加 `mk-library-row--green/red/yellow` 类。
  - 入库 badge title 展示窗口、ROAS、保本 ROAS、消耗。
- 保留原有「素材库」按钮，它仍负责跳到视频素材库。
- 保留原有 `media_product_id` 已关联提示，但改为使用 `library_status.in_library`，避免仅靠 ranking 行的旧字段误判。

颜色使用现有 Ocean Blue token 附近的 success / danger / warning 语义，不引入紫色或大面积重色。

## 验收标准

1. `/xuanpin/api/mk-selection` 每个 item 都包含 `library_status`。
2. 已入库匹配支持 `media_product_id` 和去 `-rjc` 的 `product_code` 两条路径。
3. 未入库产品显示 `未入库`，行不加颜色类。
4. 已入库且有广告消耗，ROAS 高于或等于保本 ROAS 时返回并渲染绿色。
5. 已入库且有广告消耗，ROAS 低于保本 ROAS 时返回并渲染红色。
6. 已入库但广告消耗为 0，或保本 ROAS 缺失时返回并渲染黄色。
7. 明空产品库表格新增「是否入库」列，空数据 colspan 正确。

## 验证

```bash
pytest tests/test_media_mk_selection_service.py tests/test_mk_selection_routes.py tests/test_xuanpin_routes.py -q
python -m compileall appcore web tests -q
git diff --check
```

人工验收：

1. 登录后打开 `/xuanpin/mk#products`。
2. 确认表格有「是否入库」列。
3. 检查已入库行按绿 / 红 / 黄显示；未入库行不标色。
4. 点击「素材库」按钮仍能切到视频素材库并加载对应产品素材。
