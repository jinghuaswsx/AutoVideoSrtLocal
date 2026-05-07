# 产品盈亏看板 4 Tab 改造设计

- 状态：草案，待用户 review
- 作者：Claude（与 jinghuaswsx 共同设计）
- 日期：2026-05-07
- 路由：`/product-profit`（不变）
- 权限：`product_profit`（不变）

## 1. 背景

`/product-profit` 当前是单产品盈亏看板（[web/templates/product_profit_dashboard.html](web/templates/product_profit_dashboard.html) + [web/routes/product_profit_dashboard.py](web/routes/product_profit_dashboard.py) + [web/routes/product_profit_report.py](web/routes/product_profit_report.py)），结构是：顶部产品下拉 + 日期范围 → 总账卡片 → 站点切片 → 每日盈亏折线 → 国家利润柱状图 → 订单明细表，全部纵向堆叠在一页。

随着业务对"单品盈亏全景"的诉求加深，需要在同一页里同时回答 4 个问题：哪些产品在赚 / 亏（横向比较）、单品的订单逐笔盈亏、单品的国家分布、单品的广告投放效率。当前堆叠式结构无法承载这 4 个视角，本设计把它重构为 **4 Tab 单页**。

## 2. 目标

- **零数据丢失**：现有看板的所有数据视图（总账卡片 / 站点切片 / 每日折线 / 国家柱状图 / 15 列订单明细 / Excel 下载）必须在新版本里全部找得到，不削减。
- **统一视角**：4 个 Tab 共用同一筛选上下文（产品 / 日期 / 国家），一处选择，多处生效。
- **入口友好**：默认 Tab 展示全产品盈亏概览，无需先选产品也能用。
- **可扩展**：广告明细本期到 campaign 级，但数据结构允许后续平滑下钻到 ad_set / ad。

## 3. 非目标

- 不改 `/order-profit`（订单利润核算）和 `/order-analytics`（数据导入 + 通用分析），它们的定位差异在 §10。
- 不改数据库表结构、不改利润计算公式（[appcore/order_analytics/profit_calculation.py](appcore/order_analytics/profit_calculation.py)）。
- 不改权限模型、菜单项、路由 URL。
- 不在本期改造 `tools/roi_hourly_sync.py` 同步逻辑（保持 campaign 级），ad_set / ad 级单独迭代（§11）。

## 4. 信息架构

```
┌──────────────────────────────────────────────────────────────────┐
│ 顶部全局筛选条                                                   │
│  产品: [全部 / 选具体 ▾]   日期: [本月 ▾]   国家: [全部 ▾]  [查询] │
└──────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────┐
│ Tab 切换器（横向 underline）                                      │
│  [ ① 产品列表 ] [ ② 订单明细 ] [ ③ 国家看板 ] [ ④ 广告明细 ]      │
└──────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────┐
│ Tab 内容区（受筛选条约束）                                        │
└──────────────────────────────────────────────────────────────────┘
```

## 5. 顶部全局筛选条

| 字段 | 默认值 | 说明 |
|---|---|---|
| 产品 | 全部 | 选项 = `media_products`（未软删）+ "全部" |
| 日期范围 | 本月 1 号 → 今日 | 复用现有日期组件 |
| 国家 | 全部 | 选项 = 当前数据集出现过的 `buyer_country` 集合 |

- 「产品列表」Tab **忽略**产品下拉（始终展示全部产品）；②③④ Tab 受产品下拉约束。
- 在「产品列表」点某行 → 自动把产品下拉切到该产品 + 跳转到「订单明细」Tab。
- 在「国家看板」点柱状图某国 → 自动把国家筛选切到该国。
- URL 同步 query string（`?product_id=…&start=…&end=…&country=…&tab=…`），可分享、刷新不丢状态。

## 6. Tab ① 产品列表（默认入口，新增）

每行一个 `media_products`，按所选日期 + 国家聚合。

| 列 | 内容 | 备注 |
|---|---|---|
| 产品 | 产品名 | 点击 → 跳转「订单明细」Tab + 锁定产品 |
| 订单数 | 订单笔数 | |
| 收入 | USD | |
| 物流费 | $金额 + 占比 chip | 占比 = 物流 / 收入 |
| 采购费 | $金额 + 占比 chip | |
| 广告费 | $金额 + 占比 chip | 跨账户合并 |
| ROAS | 收入 / 广告费 | 广告费为 0 时显示 "—" |
| 利润 | $金额 + 利润率 chip | 利润率 = 利润 / 收入 |
| 成本完备 | ✅ / ⚠️ | 复用 `cost_completeness.get_completeness_overview` |

交互细节：
- 表格头排序（默认按"利润"降序）
- 亏损行（利润 < 0）整行底色 `--danger-bg`
- 占比 chip 用 `--bg-muted` 底 + `--fg-muted` 字（"$1,200 (24%)"）
- 底部分页（默认 50 行）
- 顶部小字摘要："共 N 个产品 / 总收入 $X / 总利润 $Y / 整体 ROAS Z"
- "国家"维度由顶部全局筛选器承担（选越南 → 表格只展示越南维度的聚合），不另设列

## 7. Tab ② 订单明细（迁移现有 + 增强）

未选具体产品时显示空状态："请在顶部选择具体产品后查看订单明细。"

选了产品后展示（**完整保留现有页面所有数据视图**）：

```
┌─ 聚合卡片（订单数 / 收入 / 物流 / 采购 / 广告 / 利润 / ROAS）
├─ [左] 每日盈亏折线        [右] 站点切片（newjoyloo / Omurio）
└─ 订单明细表（15 列：订单号 / SKU / 国家 / 单价 / 数量 / 金额 / 采购 / 物流 / 广告分摊 / 利润 / …）
   底部按钮: [下载 Excel]
```

跟现状的差别：
- 受顶部"国家筛选"约束（选越南就只显示越南订单）
- 站点切片仍展示 newjoyloo / Omurio 双栏对比
- 折线图、卡片、表格的数据计算逻辑完全复用 `generate_report()`，无需改动

## 8. Tab ③ 国家看板（增强）

未选具体产品时显示空状态。选了产品后：

```
┌─ 国家分布柱状图（继承现有，按收入或利润切换）
│   点击柱条 → 自动设顶部国家筛选 + 锁到下方明细
├─ 国家选定后:
│   聚合卡片（该国订单数 / 收入 / 各项费用 / 利润 / ROAS）
│   该国每日盈亏折线
│   该国订单明细（迷你表，前 100 行 + Excel 下载）
└─ 国家筛选 = 全部 时只显示柱状图
```

数据来自 `generate_report()` 现有的 `by_country` 字段，前端在国家筛选变化时切换显示模式。

## 9. Tab ④ 广告明细（新增）

未选具体产品时显示空状态。选了产品后：

```
┌─ 账户分组小计（newjoyloo / Omurio 两栏卡片：花费 / 展示 / 点击 / ROAS）
├─ Campaign 列表（按账户分组）
│   列：[账户] [Campaign] [花费] [展示] [点击] [结果] [CTR] [CPC] [归属订单] [归属收入] [ROAS] [利润贡献]
│   排序默认按花费降序
├─ 日趋势子图（折叠区，默认收起）
│   每日花费 vs 当日归属收入 双线
└─ 未匹配 campaign 折叠区
   列出当前日期范围内 campaign_overrides.product_id IS NULL 的项
   每条提供 [手工配对到当前产品] 按钮 → 调 manual_match_meta_ad_campaign()
```

数据来源：
- `meta_ad_realtime_daily_campaign_metrics` 按日期范围聚合
- `resolve_ad_product_match()` + `meta_ad_campaign_overrides` 把 campaign 关联到 product
- 归属订单 / 归属收入 = 同日同产品的订单聚合（口径与现有 `allocate_ad_cost_to_line()` 一致）
- 利润贡献 = 归属收入 - 该 campaign 花费 - 同日同产品的采购 + 物流分摊

## 10. 三个看板的分工（不变）

| 路由 | 定位 | 主要使用者 |
|---|---|---|
| `/order-analytics` | 数据导入 + 通用分析（CSV 上传 / 周 ROAS / 全局国家汇总） | 运营 |
| `/order-profit` | 订单时段利润核算（亏损告警 / 成本完备性 / 订单行级利润） | 财务 |
| `/product-profit` | **产品维度盈亏全景看板（本设计的 4 Tab）** | 产品负责人 / 经理 |

## 11. 数据 / API

### 复用（不动）

| 端点 | 用途 |
|---|---|
| `GET /order-analytics/product-profit/products` | 产品下拉数据源 |
| `GET /order-analytics/product-profit/report.json` | Tab ② ③ 数据源 |
| `GET /order-analytics/product-profit/report.xlsx` | Tab ② Excel 下载 |

### 新增

| 端点 | 入参 | 出参 | 用途 |
|---|---|---|---|
| `GET /order-analytics/product-profit/list.json` | `start`, `end`, `country` | `{ rows: [...], summary: {...} }` 含每个产品的 9 列聚合 + 顶部摘要 | Tab ① |
| `GET /order-analytics/product-profit/ads.json` | `product_id`, `start`, `end`, `country` | `{ accounts: [...], campaigns: [...], daily: [...], unmatched: [...] }` | Tab ④ |
| `POST /order-analytics/product-profit/ads/manual-match` | `campaign_code`, `product_id` | 200 OK | Tab ④ 未匹配区按钮 |
| `GET /order-analytics/product-profit/list.xlsx` | 同 list.json | xlsx | Tab ① Excel 下载 |

### 后端模块新增

- [appcore/order_analytics/product_profit_list.py](appcore/order_analytics/product_profit_list.py) — `generate_list()` / `generate_list_xlsx()`
- [appcore/order_analytics/product_profit_ads.py](appcore/order_analytics/product_profit_ads.py) — `generate_ads_report()`，组合 campaign + match + 订单归属
- 复用 `appcore/order_analytics/profit_calculation.py` 的单行利润函数，禁止新写一份重复实现

## 12. 视觉规范

严格沿用 Ocean Blue Admin tokens（[CLAUDE.md](CLAUDE.md) §Frontend Design System）：

- Tab 切换器：横向 underline 风格，激活态 `--accent` 文字 + 2px 下划线 `--accent`，非激活 `--fg-muted`
- 占比 chip：`--bg-muted` 底 + `--fg-muted` 字 + `--radius-md`
- 亏损行：`--danger-bg` 整行底色（不加边框）
- 卡片：白底 + `1px solid --border` + `--radius-lg`
- 表格行高 40-44px，间距 `--space-4` ~ `--space-5`
- hue 严格 200-240，禁止任何紫色 / 靛蓝
- 桌面优先；< 1024 折叠侧栏；< 768 主区单列、Tab 切换器横向滚动

## 13. 不变的部分

- 路由 URL：`/product-profit`
- 权限：`product_profit`（[appcore/permissions.py](appcore/permissions.py)）
- 菜单项位置：「📈 产品盈亏看板」（[web/templates/layout.html](web/templates/layout.html) §540-548）
- 数据库表结构、所有现有 SQL migration
- 利润计算公式
- 现有 Excel 下载文件名 / 字段顺序

## 14. 验收标准

- [ ] `/product-profit` 默认进入「产品列表」Tab，显示当月全产品聚合
- [ ] 顶部产品下拉切换为具体产品时，②③④ Tab 全部解锁
- [ ] 顶部产品下拉为"全部"时，②③④ Tab 显示"请先选择产品"空状态
- [ ] 「产品列表」点击产品行 → 自动锁定该产品 + 切到「订单明细」
- [ ] 「订单明细」Tab 包含旧版的所有 5 个区块（聚合卡片 / 每日折线 / 站点切片 / 15 列订单表 / Excel 下载），数值与旧版逐项对账一致
- [ ] 「国家看板」Tab 含柱状图 + 选国家后的下钻明细
- [ ] 「广告明细」Tab 列出 campaign 级聚合、按账户分组、有日趋势 + 未匹配区
- [ ] 未匹配区的"手工配对"按钮调用现有 `manual_match_meta_ad_campaign()`
- [ ] URL query string 同步 4 个筛选项 + 当前 Tab，刷新页面状态保留
- [ ] 视觉零紫色（hue ≤ 240），所有颜色 / 间距走 token，无硬编码
- [ ] 路由有 `@login_required` + `@admin_required`，未登录访问跳 302 而非 500
- [ ] 部署到测试环境（172.30.254.14:8080）端到端验证
- [ ] pytest 新增 / 修改的测试全部通过

## 15. 未来迭代（不在本期）

- **ad_set / ad 级粒度**：扩展 `tools/roi_hourly_sync.py` 抓 Meta `/insights?level=adset` 和 `level=ad`，新增 `meta_ad_realtime_daily_adset_metrics` / `meta_ad_realtime_daily_ad_metrics` 两表，Tab ④ 增加「展开 → ad_set / ad」二级行。预计独立 spec / plan，不影响本期 campaign 级 schema。
- **产品复购 / 首单拆分**：「产品列表」可能加入"首单 / 复购订单数"两列，需新增基于 `buyer_email` 或 `customer_id` 的复购判定。
- **跨产品对比**：「订单明细」Tab 顶部加多产品多选，做横向对比看板，本期不做。

## 16. 风险与回滚

- **风险 1**：Tab ④ 广告归属逻辑（campaign → product → 订单）涉及 `resolve_ad_product_match()` + `campaign_overrides`，若有 campaign 跨多产品匹配，归属订单 / 收入会重复计入。本期默认按 `manual_match` 优先 + 自动匹配兜底，归属计算严格按"campaign 唯一归一个产品"假设。多产品归属作为已知限制写入 README，未来再做精细化分摊。
- **风险 2**：「产品列表」一次拉全产品 + 全订单聚合，数据量大时慢。先用 SQL 单次聚合 + 前端分页，加索引（如缺）；P95 > 2s 时再上缓存（5 分钟 TTL）。
- **回滚**：所有改动集中在 `web/routes/product_profit_*` + `web/templates/product_profit_dashboard.html` + `appcore/order_analytics/product_profit_*`。回滚单点：`git revert` 本 feature 分支的 merge commit。
