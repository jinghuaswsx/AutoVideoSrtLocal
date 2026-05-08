# 2026-05-08 — 数据分析「广告分析」面板：Campaign / Ad Set / Ad 三个 Tab + 实时搜索 + 按日期详情视图

- 触发 issue：[AUT-11](mention://issue/15b87b71-5b09-4e3b-8b9c-03d1e3d83831)
- 工作分支：`agent/server-opus4-7/de389b8f`
- 关联文档：
  - [AGENTS.md](../../../AGENTS.md) — 服务器环境、定时任务、Meta 多账户同步规则
  - [CLAUDE.md](../../../CLAUDE.md) — 发布流程、路由守卫规范
  - [docs/analytics-data-quality-guardrails.md](../../analytics-data-quality-guardrails.md) — `data_quality` envelope 约束

## 1. 目标

数据分析模块「广告分析」面板（`#panelAds`）当前只展示按产品聚合的 Campaign 级汇总。本次新增：

1. 在 `#panelAds` 内增加三个二级 tab：**Campaign / Ad Set / Ad**，把数据库中已抓取的对应级别数据全部展示。
2. 每个 tab 顶部增加一个**实时搜索框**：仅搜本级（Campaign tab 搜 Campaign 标题，Ad Set tab 搜 Ad Set 标题，Ad tab 搜 Ad 标题）；输入即匹配，下拉里点击即跳转到该条目的详情。
3. 选中条目后跳到**详情视图**：可选日期范围，按日期表格展示该条目的 ROAS / 消耗 / CPC / eCPM / 预算（占位）以及其他可从已有数据派生的指标。

## 2. 已对齐的需求决策

需求确认在 [AUT-11](mention://issue/15b87b71-5b09-4e3b-8b9c-03d1e3d83831) 评论流里逐条收口。

| # | 决策 | 选项 | 用户选择 | 落地含义 |
|---|------|------|---------|---------|
| Q1 | 二级 tab 与现有「按产品汇总」的关系 | A 替换 / B 共存 / C 概览置顶 | **B 平级共存** | `#panelAds` 内一组二级 tab：「概览（现有产品汇总）/ Campaign / Ad Set / Ad」，默认进「概览」 |
| Q2 | 「预算」字段处理（DB 里没抓） | A 跳过 / B 占位 / C 同步入库 | **B 占位** | 详情页保留「预算」列，单元格显示 `—`，hover 提示「未采集」；不动 sync / schema |
| Q3 | 详情页位置 | A 同 panel / B 新路由 / C Modal | **A 同 panel 子页** | 详情视图替换当前 tab 内容区，顶部「← 返回」按钮；URL 不变 |
| Q4 | 搜索框作用域 | A 全局跨级混搜 / B 每 tab 独立 / C 全局单框跟随 tab 过滤 | **B 每 tab 独立** | Campaign tab 一个搜索框只搜 Campaign，Ad Set / Ad tab 同理；选中后跳本级详情 |
| Q5 | 详情页默认日期范围 | — | **最近 14 天**（agent 决定） | 详情页打开默认请求 `today-13 .. today`；用户可改 |
| Q6 | 是否包含当日实时数据 | — | **Campaign 级包含，AdSet/Ad 级不包含**（agent 决定） | Campaign 详情页今日行带「实时」badge 取自 `meta_ad_realtime_daily_campaign_metrics`；AdSet/Ad 没有 realtime 表，今日列直接不出现 |
| Q7 | 图表 / 排序 / 分页默认值 | — | **暂不加图表；列表按 `spend_usd DESC`；page_size 50；详情按日期 DESC**（agent 决定） | 模板内目前没有 chart.js / echarts，加图表需要单独加资源；本期纯表格更稳。后续可单独 issue 加趋势图。 |

## 3. 数据来源

| 级别 | List / Detail 主表 | Realtime 增量 | 标识列（用于搜索 + 详情查询） | 名称列 | 关键指标列 |
|------|-------------------|--------------|----------------------------|-------|----------|
| Campaign | `meta_ad_daily_campaign_metrics` | `meta_ad_realtime_daily_campaign_metrics`（仅 Q6 启用） | `normalized_campaign_code` | `campaign_name` | `spend_usd`, `purchase_value_usd`, `roas_purchase`, `result_count`, `raw_json` |
| Ad Set | `meta_ad_daily_adset_metrics` | （无 realtime 表） | `normalized_adset_code` | `adset_name` | 同上 |
| Ad | `meta_ad_daily_ad_metrics` | （无 realtime 表） | `normalized_ad_code` | `ad_name` | 同上 |

**已抓字段 vs 用户列出指标差距**（详情页展示策略）：

| 指标 | 来源 | 详情页处理 |
|------|------|-----------|
| ROAS | `roas_purchase`（直存） | 直接展示 |
| 消耗 | `spend_usd` | 直接展示 |
| 预算 | **三张表都没存** | 占位「—」+ hover 提示「未采集」（Q2=B） |
| CPC | Campaign 周期表有 `unique_link_click_cost_usd`；日表只在 `raw_json["link_click_cost"]` | 详情页从 `raw_json` 解析；缺则 `—` |
| eCPM | Campaign 周期表有 `cpm_usd`；日表只在 `raw_json["cpm"]` | 详情页从 `raw_json` 解析；缺则 `—` |
| 其他（展示量 / 点击 / 加购 / 发起结账 / 视频均播） | 周期表直存；日表从 `raw_json` 解析 | 详情页二行附加列；缺则 `—` |

**预算 follow-up**：Q2=B 的代价是「预算」永远显示占位。本 issue 不处理同步链路。需另开 issue「Meta 广告同步链路补 budget 字段」处理 GraphQL 字段集 + schema migration + 历史回填。

## 4. 后端设计

### 4.1 路由（新增 3 条，挂在现有 `web/routes/order_analytics.py` 里）

所有路由都加 `@login_required` + `@admin_required`（CLAUDE.md 路由守卫规范）。

#### `GET /order-analytics/ads/list`

参数：
- `level`: `campaign` / `adset` / `ad`（必填）
- `start_date`, `end_date`: ISO 日期，默认 `today-13 .. today`
- `page`, `page_size`: 默认 `1`, `50`，`page_size` 上限 `200`
- `sort_by`: `spend_usd` / `roas_purchase` / `purchase_value_usd` / `result_count`，默认 `spend_usd`
- `sort_dir`: `desc` / `asc`，默认 `desc`

返回（JSON）：
```jsonc
{
  "level": "campaign",
  "period": { "start_date": "...", "end_date": "..." },
  "rows": [
    {
      "code": "...",          // normalized_*_code
      "name": "...",          // campaign_name / adset_name / ad_name
      "ad_account_id": "...",
      "ad_account_name": "...",
      "spend_usd": 0.0,
      "purchase_value_usd": 0.0,
      "roas_purchase": 0.0,
      "result_count": 0,
      "day_count": 0
    }
  ],
  "page": 1, "page_size": 50, "total": 0, "has_more": false
}
```

聚合规则：在所选日期范围内 `GROUP BY normalized_*_code`，`SUM` 数值列，`MAX` 名称列。

#### `GET /order-analytics/ads/search`

参数：
- `level`: `campaign` / `adset` / `ad`（必填）
- `q`: 搜索关键字（必填，至少 1 字符；后端做 `%xxx%` LIKE 匹配 `name` 列；TRIM + 大小写不敏感）
- `limit`: 默认 `20`，上限 `50`

返回：
```jsonc
{
  "level": "campaign",
  "rows": [
    {
      "code": "...",
      "name": "...",
      "last_active_date": "2026-05-07",
      "total_spend_usd_30d": 0.0
    }
  ]
}
```

排序：按 `last_active_date DESC, total_spend_usd_30d DESC`（最近活跃且消耗高的优先）。

#### `GET /order-analytics/ads/detail`

参数：
- `level`: `campaign` / `adset` / `ad`（必填）
- `code`: 必填（即 `normalized_*_code`）
- `start_date`, `end_date`: 默认 `today-13 .. today`

返回：
```jsonc
{
  "level": "campaign",
  "code": "...",
  "name": "...",
  "ad_account_id": "...",
  "ad_account_name": "...",
  "period": { "start_date": "...", "end_date": "..." },
  "rows": [
    {
      "date": "2026-05-08",
      "is_realtime": true,            // Campaign 级当日才有；其他都 false
      "spend_usd": 0.0,
      "purchase_value_usd": 0.0,
      "roas_purchase": 0.0,
      "result_count": 0,
      "budget_usd": null,             // 永远 null（Q2=B 占位）
      "cpc_usd": 0.0,                 // 来自 raw_json
      "ecpm_usd": 0.0,                // 来自 raw_json
      "impressions": 0,               // 来自 raw_json
      "link_clicks": 0,               // 来自 raw_json
      "add_to_cart_count": 0,
      "initiate_checkout_count": 0,
      "video_avg_play_time": null
    }
  ],
  "totals": {                          // 聚合用，给页头用
    "spend_usd": 0.0,
    "purchase_value_usd": 0.0,
    "roas_purchase": 0.0,
    "result_count": 0,
    "day_count": 0
  }
}
```

`raw_json` 解析容错：所有 `KeyError / TypeError / ValueError` 一律 fallback 到 `null`。

Realtime 注入（仅 Campaign 级）：
- 当 `end_date >= today`，detail 返回里 `today` 一行的数据来源是
  `meta_ad_realtime_daily_campaign_metrics`（按 `(business_date, ad_account_id)` 取最新 snapshot，参见 CLAUDE.md "Meta 广告多账户同步" 段对该 fallback 的硬性要求）。
- AdSet / Ad 级永远只查 `meta_ad_daily_*_metrics`，今日数据若未收盘则那行不出现。

### 4.2 数据层（在 `appcore/order_analytics/meta_ads.py` 末尾追加）

新增 6 个内部函数（每个 level 一组 list / detail，search 三 level 共用 dispatch）：

```python
_LEVEL_CONFIG = {
    "campaign": {
        "table": "meta_ad_daily_campaign_metrics",
        "code_col": "normalized_campaign_code",
        "name_col": "campaign_name",
        "supports_realtime": True,
    },
    "adset":    { "table": "meta_ad_daily_adset_metrics",    "code_col": "normalized_adset_code", "name_col": "adset_name",    "supports_realtime": False },
    "ad":       { "table": "meta_ad_daily_ad_metrics",       "code_col": "normalized_ad_code",    "name_col": "ad_name",       "supports_realtime": False },
}

def get_ads_level_list(level, start_date, end_date, page, page_size, sort_by, sort_dir): ...
def search_ads_by_level(level, q, limit): ...
def get_ads_level_detail(level, code, start_date, end_date): ...
```

每个函数内部：
1. 先 `_LEVEL_CONFIG[level]` 拿到表名 / 列名（无效 level 抛 `ValueError`）
2. 走现有 `query()` / `query_one()` facade（保持 monkeypatch 透传）
3. 数值字段统一走现有 `_money()` / `_safe_int()` / `_safe_float_default()` helpers

### 4.3 路由 → 数据层的胶水

`web/routes/order_analytics.py` 顶部已有 `from appcore import order_analytics as oa`。
新加路由直接 `oa.get_ads_level_list(...)` 等三个函数。返回值走现有 `_json_response(_json_safe(...))` 序列化。

错误返回模式：
- 缺参 / level 非法 → 400 `{"error": "invalid_param", "detail": "..."}`
- 数据库失败 → 500 `{"error": "internal_error"}`，日志 `log.exception(...)`

## 5. 前端设计

### 5.1 `#panelAds` DOM 结构（替换 `web/templates/order_analytics.html` 第 2501–2562 行）

```html
<section id="panelAds" class="oa-tab-panel" data-panel="ads">
  <!-- 二级 tab 条 -->
  <nav class="oa-subtabs" id="adsSubtabs" role="tablist">
    <button data-subtab="overview" class="active">概览</button>
    <button data-subtab="campaign">Campaign</button>
    <button data-subtab="adset">Ad Set</button>
    <button data-subtab="ad">Ad</button>
  </nav>

  <!-- 概览：保留现有「按产品汇总」DOM 原样（已有逻辑不动） -->
  <div data-subpanel="overview" class="oa-subpanel active">
    <!-- 现有 #adStats / #adContent / #adUnmatchedWrap 等节点维持原样 -->
  </div>

  <!-- Campaign / Ad Set / Ad 三个 list 视图（结构相同，levelhint 不同） -->
  <div data-subpanel="campaign" class="oa-subpanel" data-level="campaign">
    <!-- 复用 .oa-search-box + .oa-search-results -->
    <div class="oa-toolbar">
      <div class="oa-search-box ads-level-search">
        <input type="text" placeholder="搜索 Campaign 标题..." data-search-level="campaign">
        <div class="oa-search-results" data-search-results-for="campaign"></div>
      </div>
      <div class="oad-range-presets" data-presets-for="campaign"><!-- 复用现有按钮组 --></div>
      <input type="date" data-range-start-for="campaign">
      <input type="date" data-range-end-for="campaign">
      <button class="btn-secondary" data-refresh-for="campaign">刷新</button>
    </div>
    <table class="oa-table" data-list-table="campaign">
      <thead>...</thead>
      <tbody></tbody>
    </table>
    <div class="oa-pagination" data-pagination-for="campaign"></div>
  </div>
  <div data-subpanel="adset" class="oa-subpanel" data-level="adset">...</div>
  <div data-subpanel="ad"    class="oa-subpanel" data-level="ad">...</div>

  <!-- 详情视图（覆盖在所属 sub-panel 上方） -->
  <div data-subdetail="campaign" class="oa-subdetail" hidden>
    <div class="oa-breadcrumb">
      <button data-back-to="campaign">← 返回 Campaign 列表</button>
      <span class="oa-detail-title">[Campaign] <strong></strong></span>
    </div>
    <header class="oa-detail-header">
      <span data-detail-summary="spend_usd"></span>
      <span data-detail-summary="purchase_value_usd"></span>
      <span data-detail-summary="roas_purchase"></span>
      <span data-detail-summary="result_count"></span>
    </header>
    <div class="oa-toolbar">
      <input type="date" data-detail-range-start-for="campaign">
      <input type="date" data-detail-range-end-for="campaign">
      <button class="btn-secondary" data-detail-refresh-for="campaign">刷新</button>
    </div>
    <table class="oa-table" data-detail-table="campaign">
      <thead><tr>
        <th>日期</th><th>消耗</th><th>预算</th><th>购买金额</th><th>ROAS</th>
        <th>购买数</th><th>CPC</th><th>eCPM</th><th>展示量</th><th>点击</th>
        <th>加购</th><th>发起结账</th><th>视频均播</th>
      </tr></thead>
      <tbody></tbody>
    </table>
  </div>
  <div data-subdetail="adset" class="oa-subdetail" hidden>...</div>
  <div data-subdetail="ad"    class="oa-subdetail" hidden>...</div>
</section>
```

样式走 Ocean Blue Admin token（`--accent` / `--border` / `--bg-muted` 等），不引入新色值。Sub-tab 激活态用 `--accent-subtle` 底 + `--accent-fg` / `--accent` 文字；其他依现有 `.oad-range-presets` / `.oa-search-box` / `.oa-table` 样式。

### 5.2 JS 模块（新增 `web/static/order_analytics_ads_levels.js` 或追加到现有内联 `<script>`）

为了减少新文件，**追加到现有 `order_analytics.html` 内联 `<script>` 段尾部**，包成 IIFE：

```js
(function() {
  // 状态机（每个 level 一份）
  const state = {
    campaign: { mode: "list", code: null, listParams: {...}, detailParams: {...} },
    adset:    { mode: "list", ... },
    ad:       { mode: "list", ... },
  };

  // 子 tab 切换：监听 #adsSubtabs 上的 click
  document.querySelector("#adsSubtabs").addEventListener("click", evt => {...});

  // 每个 level 的 search input 上挂 debounce(150ms) → /ads/search
  // 下拉项点击 → 切到详情模式 + 拉 /ads/detail
  // 列表行点击 → 切到详情模式 + 拉 /ads/detail
  // 详情面包屑「← 返回」→ 切回 list 模式

  // ...
})();
```

关键决策：
- **debounce 150ms**：用户停止输入 150ms 后才触发请求；避免连击。
- **键盘**：搜索下拉支持 ↑/↓/Enter/Esc（沿用现有 `.oar-product-search` 模式）。
- **空态**：每个列表 / 详情都有「暂无数据」占位（Ocean Blue 设计系统）。
- **错误态**：网络/HTTP 错误 → `.oa-banner-error` 提示「加载失败，请刷新或换日期范围」。
- **响应式**：`< 768px` 把表格列折叠（沿用现有 `.oa-table.is-compact`）。

### 5.3 Tab 切换钩子

- 顶层 tab 已有 `setActiveTab()`（`order_analytics.html` 2620–2644）。
- 子 tab 切换在 `#panelAds` 内自管，**不改 `setActiveTab()` 行为**。
- 进入「广告分析」顶层 tab 时仍然首先调用 `initAds()`（保留现有逻辑）；新增的子 tab 是延迟初始化的（首次切到才发请求）。

## 6. 测试

### 6.1 新增 pytest 用例（`tests/test_order_analytics_ads.py` 或新建 `tests/test_order_analytics_ads_levels.py`）

- `test_ads_list_campaign_returns_rows_within_date_range`
- `test_ads_list_returns_400_for_invalid_level`
- `test_ads_list_paginates_and_sorts`
- `test_ads_search_campaign_returns_matches_and_excludes_other_levels`
- `test_ads_search_returns_400_for_missing_query`
- `test_ads_detail_campaign_includes_realtime_today_when_in_range`
- `test_ads_detail_adset_excludes_today_when_unfinalized`
- `test_ads_detail_returns_404_when_code_not_found`
- `test_ads_detail_budget_field_is_null_placeholder`

测试客户端：复用现有 `tests/conftest.py` 的 `logged_in_client` fixture（live DB），直接调三个新路由，断言结构 + 关键字段。

### 6.2 既有回归

修改 `web/routes/order_analytics.py` 后，至少跑一次 AGENTS.md 已登记的回归集：

```
pytest tests/test_order_analytics_data_quality.py \
       tests/test_order_profit_routes.py \
       tests/test_product_profit_routes.py \
       tests/test_order_analytics_true_roas.py \
       tests/test_data_quality_frontend_assets.py \
       tests/test_order_analytics_ads.py -q
```

加上本次新建的 ads-levels 用例。

## 7. 风险 / 边界

| 风险 | 影响 | 缓解 |
|------|------|------|
| Campaign 级 realtime 表查询条件不严，导致今日多账户某账户失败时数据偏小 | Campaign 详情今日 ROAS / 消耗失真 | 严格按 CLAUDE.md "Meta 广告多账户同步" 段：按 `(business_date, ad_account_id)` 分组取 `MAX(snapshot_at)`，再合并各账户结果；**不允许**全局 `MAX(snapshot_at)` |
| `raw_json` 字段名不一致 / 缺失 | CPC / eCPM 显示 `—` | 解析容错：单字段 try/except → null；前端用 `—` 占位 |
| AdSet / Ad 名称里出现特殊字符（emoji、空格、unicode 引号），LIKE 搜索不命中 | 用户搜索看似无结果 | 搜索做 `LOWER(name) LIKE LOWER(%xxx%)`；TRIM 输入；保留原始 name 做返回 |
| 大日期范围（30+ 天）+ 海量 Ad 行（万级）一次拉太多数据 | 浏览器卡顿 / 后端慢 | list 端点强制分页（page_size 上限 200），detail 单条目即使 90 天也只 90 行；前端默认日期 14 天 |
| 新加路由没加 `@login_required` / `@admin_required` 守卫 | 未登录访问触发 layout.html `current_user.username` UndefinedError → HTTP 500 / 安全问题 | 三条路由必须加双重守卫，与 `dashboard / realtime_overview` 同款 |
| 修改大模板 `order_analytics.html`（5723 行）时误碰其他 tab 的 DOM | 整个数据分析页崩 | 只在 #panelAds 内部改；保留现有「按产品汇总」节点原样作为 overview sub-panel |

## 8. 实施分步（由 [writing-plans](../../../) skill 后续展开）

1. spec 落盘 + commit（本 commit）
2. 后端：新增 `_LEVEL_CONFIG` + 3 个数据函数 + 3 条路由
3. 后端测试：~9 个 pytest 用例
4. 前端：`#panelAds` DOM + 内联 JS（subtab 切换 / search debounce / 列表 / 详情）
5. 全量 pytest 回归 + 浏览器手测
6. commit / push origin master
7. 线上发布（`sudo bash -c '...'`）+ HTTP 验证
8. 回 issue 给入口链接

## 9. Follow-up（不在本 issue 范围）

- **Meta 广告同步链路补 budget 字段**：让 Q2 占位列变成真实数据。涉及 GraphQL 字段集 + 三张 daily 表 schema migration + 历史回填，预计 1–2 天，单独排期。
- **详情页趋势图**：本期纯表格。后续可加 ECharts CDN 引入 + 折线图（ROAS / 消耗 / 购买金额三轴）。
- **跨级搜索**（即 Q4 的 A 方案）：如果用户后续反馈「找不到 Ad 属于哪个 Campaign」很常见，可以再加一个全局搜索框做跨级混搜。

---

**约定**：本 spec 落盘 commit 后，按上面"实施分步"在同一个 worktree 分支推进。每个分步会单独 commit，commit message 都引用本 spec 路径作 `Docs-anchor:`。
