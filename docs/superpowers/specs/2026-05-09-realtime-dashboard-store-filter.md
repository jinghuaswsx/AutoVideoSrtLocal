# 实时大盘：产品搜索框后增加店铺筛选

## 背景

`数据分析 → 实时大盘`（[web/templates/order_analytics.html](../../../web/templates/order_analytics.html) 的 `panelRealtime`）当前展示「Newjoy + Omurio 双店合计」的数据，店铺范围全程硬编码为 `('newjoy', 'omurio')`，分布在 [appcore/order_analytics/realtime.py](../../../appcore/order_analytics/realtime.py) 的 8 处 SQL 与 site_codes 参数，以及 `roi_realtime_daily_snapshots` / `roi_daily_roas_nodes` 的 `store_scope='newjoy,omurio'` 列。用户需求：在产品搜索框右侧加店铺筛选下拉框，可以单独看 Newjoy 或 Omurio 数据。

实时大盘改版 spec：[2026-05-02-realtime-dashboard-redesign.md](2026-05-02-realtime-dashboard-redesign.md)。本 spec 是其延伸，复用同套工具栏、卡片结构与 API。

## 用户已批准的关键决策

1. 店铺枚举来源：[appcore/meta_ad_accounts.AVAILABLE_STORE_CODES](../../../appcore/meta_ad_accounts.py) = `('newjoy', 'omurio')`，与历史硬编码一致。下拉框选项 `全部店铺 / Newjoy / Omurio`。
2. 不传或传完整集合时行为与现状完全一致；传单店时，`scope.stores` 反映真实筛选值（不再硬写双店）。
3. 单店筛选需要绕过 `roi_realtime_daily_snapshots` 的「双店聚合」快照分支，回落到明细表实时算（与 product_id 过滤的现有处理一致）。`roi_daily_roas_nodes` 的 ROAS 走势同理（`store_scope='newjoy,omurio'` 行不再适用）→ 单店筛选时 `roas_points` 全部置空。

## 设计

### UI（前端模板）

在 [order_analytics.html](../../../web/templates/order_analytics.html) `panelRealtime` 内部 `oar-product-search-row` 同一行的产品搜索框右侧追加一个 `<select>`：

```
[搜索产品 ID 或标题…]   [店铺筛选 ▾]
```

- `<select id="realtimeSiteFilter">` 选项：
  - `<option value="">全部店铺</option>` （默认）
  - 由模板渲染时根据 [appcore/meta_ad_accounts.AVAILABLE_STORE_CODES](../../../appcore/meta_ad_accounts.py) 动态展开，例如 `<option value="newjoy">Newjoy</option>`、`<option value="omurio">Omurio</option>`。
- 样式复用 `.oa-controls select` 的 32px 高、海洋蓝 focus 描边。
- 紧贴产品搜索框右侧，flex gap = `--space-3`；移动端 `< 768px` 自然折下到第二行（同 `oar-product-search` 的 wrap 行为）。
- 风格自检：无紫色、无新硬编码颜色、键盘可达（Tab + 原生 select 弹层）。

### JS 行为

- `realtimeState` 增加 `siteCode: ''` 字段，初始化空 = 全部店铺。
- 切换 `<select>` → 更新 `realtimeState.siteCode` → `resetRealtimeProfitPage()` → `loadRealtimeOverview()`。
- `loadRealtimeTopCards` 与 `loadRealtimeSubTabs` 在拼请求 URL 时，若 `realtimeState.siteCode` 非空，附加 `params.set('site_code', realtimeState.siteCode)`。
- 不影响其它 tab，不复用到「订单分析 / 广告分析」等其它子 tab（这些 tab 已有自己的店铺筛选或本身就是聚合视图）。

### API

`GET /order-analytics/realtime-overview` 新增可选参数：

| 参数 | 取值 | 含义 |
|------|------|------|
| `site_code` | `newjoy` / `omurio` | 单店筛选；不传或为空 = 全部 |

参数白名单校验在 [web/routes/order_analytics.py](../../../web/routes/order_analytics.py) 内完成：取值必须在 `meta_ad_accounts.AVAILABLE_STORE_CODES` 内，否则返回 400 `invalid_param`。

### 后端

[appcore/order_analytics/realtime.py](../../../appcore/order_analytics/realtime.py) 改造：

1. 新增模块级常量 `_DEFAULT_SITE_CODES = ("newjoy", "omurio")` 与白名单 `_ALLOWED_SITE_CODES`，以及辅助函数：
   ```python
   def _normalize_site_codes(site_codes) -> tuple[str, ...]: ...
   def _site_codes_in_sql(site_codes, column="site_code") -> str: ...
   ```
   `_site_codes_in_sql` 使用预校验后的字面量拼出 `site_code IN ('newjoy', 'omurio')` 形式，保留与历史 SQL 字符串一致（已通过白名单防注入），不引入参数化 placeholder（避免动辄改动多个 query 调用点）。

2. 给以下函数加 `site_codes: tuple[str, ...] | None = None` 参数，默认走 `_DEFAULT_SITE_CODES`：
   - `_get_realtime_order_details`
   - `_get_realtime_order_details_for_range`
   - `_get_realtime_order_profit_details`
   - `_get_realtime_order_profit_details_for_range`
   - `_get_realtime_order_summary`
   - `_get_realtime_product_sales_stats`（透传到 `get_dianxiaomi_product_sales_stats`，该函数已支持 site_codes）
   - `_get_realtime_order_updated_at`
   - `_build_realtime_overview_for_range`

3. `get_realtime_roas_overview` 入口加 `site_codes: list[str] | None = None`，归一化后向下透传。

4. **绕过快照表的条件**：在 `get_realtime_roas_overview` 中，当 `normalized_site_codes != _DEFAULT_SITE_CODES` 时：
   - 跳过 `roi_realtime_daily_snapshots` 查询（`should_try_snapshot` 改造或在外层短路）；
   - `roi_daily_roas_nodes` 走 `store_scope='newjoy,omurio'` 的查询替换为返回空 `roas_points`，避免单店看到双店 ROAS 节点的错误数据。

5. 单店筛选时仍走「明细路径」：用 `_get_realtime_order_summary(... site_codes=...)` 等直接读 `dianxiaomi_order_lines`、`meta_ad_realtime_daily_campaign_metrics` 等明细表实时聚合。

6. 广告 / campaign 数据按 store→ad_account_id 映射过滤：通过 [appcore/meta_ad_accounts.site_account_map](../../../appcore/meta_ad_accounts.py) 拿到 `site_code → (ad_account_id, ...)`；
   - `_get_realtime_campaign_details` 与 `_get_today_realtime_meta_totals` 在拿到 `site_codes` 时，把 `meta_ad_realtime_daily_campaign_metrics` 查询限定到 `ad_account_id IN (...)`。
   - `_get_daily_campaigns` 与 `_build_realtime_overview_for_range` 的 daily ad rows 同款。
   - 全部店铺时 `ad_account_id IN` 限定取消，等价历史行为。

7. `scope.stores` 始终反映真实筛选值：返回 `list(normalized_site_codes)`。

### 不做

- 不修改 `roi_realtime_daily_snapshots` / `roi_daily_roas_nodes` 表结构（不新增按 store 拆分的快照行；这是后续可选的优化）。
- 不修改其它 tab（订单分析 / 广告分析 / 国家看板 / 真实 ROAS / 周报 / 订单导入 / 产品看板）。
- 不修改 `roas_points` 在「全部店铺」时的渲染逻辑。

## 验证

1. **后端单测**：新建 [tests/test_order_analytics_realtime_site_filter.py](../../../tests/test_order_analytics_realtime_site_filter.py)：
   - 不传 site_code（默认全部）：`scope.stores == ["newjoy", "omurio"]`，SQL 中包含 `site_code IN ('newjoy', 'omurio')`，行为与现状一致。
   - 传 `site_codes=["newjoy"]`：`scope.stores == ["newjoy"]`，SQL 中包含 `site_code IN ('newjoy')`，**不**包含 `omurio` 字面量；不查询 `roi_realtime_daily_snapshots`；不查询 `roi_daily_roas_nodes`。
   - 传 `site_codes=["omurio"]`：同款，针对 omurio。
   - 传非法 site_code（路由层）：返回 400。
2. **既有测试不破**：
   - `pytest tests/test_order_analytics*.py tests/characterization/test_order_analytics_baseline.py -q` 全过。
3. **手动**：登录测试环境 `http://172.16.254.106:8080/order-analytics`，分别选「全部 / Newjoy / Omurio」三档比对订单数 / 广告费 / ROAS。

## 已知非目标 / 注意事项

- 单店筛选下，「Meta ROAS」按对应店铺关联的 ad_account_id 过滤再分子分母重算；与「全部」时口径不同但物理意义一致。
- `roi_daily_roas_nodes` 是为双店聚合预先计算好的快照，单店筛选时 ROAS 走势子 tab 暂时没有数据 → 前端 `realtimeRoasChart` 空状态走「暂无数据」（已有空状态分支，无需改）。
