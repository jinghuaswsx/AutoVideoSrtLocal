# 2026-06-10 Shopify 未结算货款项目存档

## Anchors

- `AGENTS.md`：数据分析模块改动必须文档先行，非 hotfix 在 worktree 中开发。
- `web/templates/CLAUDE.md`：新 POST / fetch 必须带 CSRF；新页面和 API 需要登录、管理员和权限守卫。
- `web/static/CLAUDE.md`：Ocean Blue 后台视觉、三态、移动端适配。
- `appcore/order_analytics/CLAUDE.md`：数据分析模块边界；本工具不得污染现有分析数据源。

## Goal

在 `/order-analytics` 顶栏新增 `未结算货款` TAB。该 TAB 是 Shopify Payments 导出文件的项目化核算与存档工具：

1. 首屏为项目模式，支持新建项目，展示项目卡片。
2. 新建项目时选择店铺并上传 Shopify Payments Transactions 导出文件。
3. 按导入文件内 `Payout Status` 核算 `pending`、`paid`、`scheduled` 三类订单。
4. 项目详情顶部展示核心核算结果，并保留历史项目后续可查。
5. 本功能只做工具与存档，不写入 `shopify_orders`、`dianxiaomi_order_lines`、`order_profit_lines`、`shopify_payments_transactions` 等分析数据源表。

## Data Source

文件来自 Shopify 后台 Payments / Payouts 的 Transactions 明细导出。支持 `.csv`、`.xls`、`.xlsx`。

必需列：

- `Payout Status`
- `Amount`
- `Fee`
- `Net`

建议列：

- `Transaction Date`
- `Type`
- `Order`
- `Payout Date`
- `Payout ID`
- `Available On`
- `Currency`

列名大小写保持 Shopify 原始命名；后端解析时按 trim 后精确匹配，缺必需列返回 400。

## Status Buckets

只聚合以下三个 `Payout Status`，大小写不敏感：

| 状态 | 中文展示 | 销售额 | 手续费 | 打款总额 |
| --- | --- | --- | --- | --- |
| `pending` | 未结算订单 | `SUM(Amount)` | `SUM(Fee)` | 预计打款总额 `SUM(Net)` |
| `paid` | 已结算订单 | `SUM(Amount)` | `SUM(Fee)` | 已打款总额 `SUM(Net)` |
| `scheduled` | 已排期订单 | `SUM(Amount)` | `SUM(Fee)` | 已排期打款总额 `SUM(Net)` |

所有金额按文件里的结算币种原值核算；页面展示币种取文件 `Currency` 列的主币种，混合币种时标记 `MIXED` 并保留各行原始 JSON。

`Type` 不作为过滤条件。Shopify 导出里退款等负数行会自然抵减对应状态金额，保证与 Payments 明细合计一致。

## Storage

新增两张独立存档表：

- `shopify_unsettled_payout_projects`
  - 保存项目、店铺、文件名、币种、三类状态汇总、导入行数、创建人和时间。
- `shopify_unsettled_payout_rows`
  - 保存项目内明细行、状态、订单号、金额、手续费、打款金额和原始行 JSON。

这些表不被订单分析、产品盈亏、真实 ROAS、实时大盘读取。后续如需导出或复核，只从本工具自己的接口读取。

## Routes

页面：

- `GET /order-analytics/unsettled-payouts`
  - `@login_required`
  - `@admin_required`
  - `@permission_required("data_analytics")`

API：

- `GET /order-analytics/unsettled-payouts/projects`
  - 返回项目卡片列表和全局汇总。
- `GET /order-analytics/unsettled-payouts/projects/<project_id>`
  - 返回项目详情、状态分区汇总和分页明细。
- `POST /order-analytics/unsettled-payouts/projects`
  - multipart form：`store_code`、`project_name`、`file`。
  - CSRF 走 `X-CSRFToken` header。

所有 API 都使用登录、管理员和 `data_analytics` 权限守卫。

## UI

`未结算货款` TAB：

1. 顶部是操作栏：新建项目、刷新。
2. 下方是三个状态核心卡：未结算、已结算、已排期。
3. 项目列表使用卡片：店铺、文件、导入行数、币种、三类打款总额和创建时间。
4. 点击项目卡片进入详情视图：
   - 顶部保留返回按钮和项目元信息。
   - 核心核算区按 `pending` / `paid` / `scheduled` 三个分区对齐展示。
   - 明细表展示状态、交易时间、订单号、销售额、手续费、打款额、Payout Date / Payout ID。
5. empty / loading / error 三态齐全；移动端卡片单列。

## Verification

按 `docs/superpowers/specs/2026-06-08-targeted-pytest-verification.md` 跑 focused tests：

```bash
python3 scripts/pytest_related.py --base origin/master --run
```

至少覆盖：

- 服务层解析和三状态汇总。
- 路由上传文件、文件名清洗、CSRF header 路径。
- 迁移文件包含两张独立表和索引。
- 模板包含新 TAB、项目面板和 API 调用。

不跑全量 `pytest -q`，除非本次改动升级为广影响 schema / auth / 发布门禁。
