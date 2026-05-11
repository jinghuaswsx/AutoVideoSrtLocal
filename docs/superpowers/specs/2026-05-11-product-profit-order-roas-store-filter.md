# 产品盈亏订单明细 ROAS 与店铺筛选

日期：2026-05-11

## 背景

产品盈亏看板的「订单明细」Tab 目前在汇总卡片区展示订单数、总收入、总利润、广告费和成本合计。运营需要在同一块汇总区直接看到当前产品、日期、国家筛选下的 ROAS，并能在顶部筛选栏按店铺查看单店口径。

现有数据链路：

- 页面入口：`web/templates/product_profit_dashboard.html`
- 数据接口：`/order-analytics/product-profit/report.json`
- Excel 导出：`/order-analytics/product-profit/report.xlsx`
- 聚合逻辑：`appcore/order_analytics/product_profit_report.py::generate_report`

## 设计锚点

- `AGENTS.md` 的「文档驱动代码」「Verification」和隔离开发规则。
- `docs/superpowers/specs/2026-05-07-product-profit-dashboard-tabs-redesign-design.md` 的 Tab ②「聚合卡片（订单数 / 收入 / 物流 / 采购 / 广告 / 利润 / ROAS）」要求。
- `appcore/order_analytics/CLAUDE.md` 的店铺筛选规则：店铺白名单来自 `appcore.meta_ad_accounts.AVAILABLE_STORE_CODES`，广告账户过滤必须走 `meta_ad_accounts.site_account_map(enabled_only=False)`，不能硬编码 `site_code -> ad_account_id`。
- `docs/analytics-data-quality-guardrails.md` 的产品盈亏 JSON 顶层 `data_quality` 要求。

## 目标

1. 在「订单明细」Tab 的顶部汇总卡片区新增 ROAS 展示。
2. ROAS 口径为 `总收入 / 广告费`；广告费为 0 时显示横杠。
3. 顶部筛选栏新增「店铺」筛选，支持 `全部 / newjoyloo / Omurio`。
4. 店铺筛选影响「订单明细」Tab 的汇总卡片、站点切片、每日趋势、国家图、订单表和 Excel 下载。
5. 店铺筛选参数进入 `report.json` 与 `report.xlsx`，刷新或分享 URL 时不丢状态。

## 非目标

- 不改变产品列表、国家看板、广告明细和产品国家分析 Tab 的筛选语义。
- 不新增数据库字段或迁移。
- 不改变利润公式。
- 不把逐行订单 ROAS 当作真实投放 ROAS 新增到订单表；本期只在订单明细 Tab 的汇总区展示整体 ROAS。

## 后端口径

`generate_report(..., site_code=None)` 新增可选店铺参数：

- 空值或 `all`：保持现有全店口径。
- `newjoy` / `omurio`：订单侧追加 `dol.site_code = %s`。
- 广告侧按店铺解析账户：`meta_ad_accounts.site_account_map(enabled_only=False)` 找到该店铺对应的 `ad_account_id`，再给广告 spend 查询追加 `ad_account_id IN (...)`。
- 若店铺值不在白名单内，路由返回 400。
- 若某店铺没有可解析的广告账户，广告费按 0 返回，不回退到全账户，避免单店筛选下混入其他店铺广告费。

返回字段：

- `total.roas = revenue_usd / ad_cost_usd`，广告费为 0 时为 `None`。
- `by_site[].roas = revenue_usd / ad_cost_usd`，广告费为 0 时为 `None`。
- `meta.site_code` 标记当前筛选店铺。

## 前端口径

- 筛选栏在「订单明细」Tab 显示店铺下拉，默认「全部」。
- 查询和 Excel 下载 URL 都附带 `site_code`。
- URL state 增加 `site_code`。
- 汇总卡片区新增「ROAS」卡片，展示 `total.roas`，副文案为 `收入 / 广告费`；广告费为 0 显示 `--`。
- 站点切片补充 ROAS 行，便于单店/全店对账。

## 验收标准

1. `/product-profit?tab=orders&product_id=<id>` 的订单明细汇总卡片区出现 ROAS。
2. 店铺下拉在「订单明细」Tab 可见，其他 Tab 不显示。
3. 选择 newjoyloo 后，`report.json` 请求带 `site_code=newjoy`，订单 SQL 带 `dol.site_code = %s`。
4. 单店广告费查询通过 `site_account_map(enabled_only=False)` 生成 `ad_account_id IN (...)`。
5. ROAS 按 `revenue_usd / ad_cost_usd` 计算；广告费为 0 时为 `None`，前端显示 `--`。
6. Excel 下载链接带 `site_code`，导出文件名包含店铺后缀。
7. 聚焦测试通过。

## 测试计划

```bash
pytest tests/test_product_profit_report.py tests/test_product_profit_dashboard_assets.py -q
python -m compileall appcore web tests -q
git diff --check
```
