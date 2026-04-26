# 产品看板 V1 设计文档

- **日期**：2026-04-26
- **作者**：与 Claude 协作 brainstorm
- **状态**：spec 完成，待写实施计划
- **范围标识**：数据分析模块 — 产品看板（自有数据交叉视图）
- **关联**：现有 `order_analytics` 模块（订单 + 广告 CSV 长期导入），上位规划见 [2026-04-24-data-analysis-ad-analytics-design.md](2026-04-24-data-analysis-ad-analytics-design.md)

---

## 0. 一句话目标

让 admin 每天打开 `/order-analytics`，第一眼就看到"哪些产品在出单、花了多少广告费、ROAS 多少、跟上周期对比涨跌如何"，从而判断**哪些产品该补新素材**。

**本 spec 不做**：素材级归因、趋势 sparkline、CSV 导出、阈值告警、云控选品库素材发掘联动。这些是后续扩展，不在 V1 范围。

---

## 1. 范围与边界

### 1.1 本 spec 做什么

1. 在 `order_analytics` Blueprint 加 1 个新端点 `GET /order-analytics/dashboard`
2. 在 `appcore/order_analytics.py` 加 1 个新服务函数 `get_dashboard(...)`，做产品级聚合 + 环比计算
3. 在 `web/templates/order_analytics.html` 加 1 个新 Tab "产品看板"，**设为默认 Tab**
4. 单元测试 + 路由测试 + 手工验收

### 1.2 本 spec 不做什么

- ❌ 素材级（`media_items`）下钻 — 等 Meta 广告后台确认能否导出素材维度数据后另立项
- ❌ 趋势 sparkline 小图（V2）
- ❌ CSV / Excel 导出（V2，可参考 `productivity_stats` 现有导出实现）
- ❌ 阈值告警 / 自动标红"疲劳产品"（V2，等用户实际使用后再定阈值）
- ❌ 云控选品库素材发掘（独立子项目，应在产品看板上线后另开 brainstorm）
- ❌ 行内展开"产品 × 国家"明细（已有"订单分析" Tab 承担这个职责）
- ❌ 新增数据库表 / migration（**纯查询聚合**）

### 1.3 关键依赖（实施时必须 grep 核对）

| 依赖 | 确认点 |
|---|---|
| `shopify_orders` 表 | `sale_date` / `product_id` / `country` / `quantity` / 收入字段名（可能是 `total_price` / `revenue` / `order_total`，**实施时 grep `appcore/order_analytics.py`** 确认聚合用的字段名） |
| `meta_ad_campaign_metrics` 表 | `report_start_date` / `report_end_date` / `product_id` / `spend` / `purchases` / `purchase_value`（确认字段名） |
| `media_products` 表 | `id` / `name` / `product_code` / `main_image`（V1 行内展示主图缩略，如果该字段未建则 fallback 显示纯文字） |
| `media_items` 表 | `product_id` / `lang` / `deleted_at`（用于"已有素材数"按语种聚合 count） |
| 现有"订单分析" / "广告分析" Tab 的 URL / query string 协议 | 看板"操作列"跳转时要预填参数（实施时 grep 现有 Tab 切换逻辑确认 anchor / query 命名） |

### 1.4 命名约定

- Blueprint：复用 `order_analytics`，不新建
- 服务模块：复用 `appcore/order_analytics.py`，加 `get_dashboard(...)` 函数
- 模板：复用 `web/templates/order_analytics.html`，加 Tab + 子模板（必要时拆 `_dashboard_panel.html`）
- 前端 JS namespace：`oad*`（order-analytics-dashboard 缩写）

---

## 2. 已锁定决定

brainstorm 期间逐条与用户确认：

| # | 决定 | 备注 |
|---|---|---|
| 1 | 粒度 = **产品级** | 素材级是 future（等 Meta 后台数据导出能力确认后） |
| 2 | 三档时间粒度：**日 / 周 / 月** | |
| 3 | 日视图**只显示订单**（订单数 / 件数 / 收入），不显示广告 / ROAS | Meta 报表无日粒度，强行均摊会失真 |
| 4 | 周 / 月视图含广告 + ROAS | |
| 5 | 看板 = `/order-analytics` 下 "产品看板" Tab，**默认 Tab** | 现有 3 个 Tab 保留作为细看 / 导入工具 |
| 6 | **环比对比**：所有可对比指标带 ↑x% / ↓y% 标记 | 月视图 vs 上月（同长度切片）；周 vs 上周；日 vs 昨日 |
| 7 | 广告周期对齐 = **完全覆盖**：广告报表的 `[start_date, end_date]` 必须**完全包含**所选月 / 周才纳入计算 | 否则该产品行的广告 / ROAS 列显示 "-"，tooltip 提示"该周期无对应广告报表"。**保证 ROAS 数字不被部分覆盖污染** |
| 8 | 国家维度：默认**所有国家聚合到产品**；顶部国家下拉筛选；不做行内展开 | 行内展开 = "订单分析" Tab 已有的能力，不重复 |
| 9 | 默认排序 = **广告花费降序**（周/月视图）；日视图 = **Shopify 收入降序** | 列头点击可切换排序 |
| 10 | 不新增表 / migration，纯查询聚合 | 复用 `shopify_orders` / `meta_ad_campaign_metrics` / `media_products` / `media_items` |
| 11 | 默认进入页面 = **本月**（含截至昨日的累计数据） | 当月不完整 → ROAS 是"截至昨日的部分"，环比对比上月同期同长度切片 |
| 12 | 排除产品：**两边都为 0**（无订单 + 无广告花费）的产品不显示 | 否则 V1 表会爆掉（`media_products` 全量上千行）。筛选发生在 SQL 层 |
| 13 | **ROAS 口径** = `Shopify 收入 ÷ Meta 花费`（不是 Meta `purchase_value ÷ spend`） | Shopify 是真实成交数据；Meta 的 `purchase_value` 受像素归因窗口 / 漏报影响，不作为 ROAS 主口径。`meta_purchases` 仍单独显示作为参考列 |

---

## 3. 数据模型

**不新增表**。本 spec 是纯逻辑扩展。

### 3.1 复用表（实施时 grep 字段名核对）

```
media_products
  id, name, product_code, main_image (?), user_id, archived, deleted_at, created_at

shopify_orders
  id, sale_date, product_id, country, quantity, <revenue field>, ...

meta_ad_campaign_metrics
  id, report_start_date, report_end_date, campaign_name, product_code,
  product_id, spend, purchases, purchase_value, link_clicks, ...

media_items
  id, product_id, lang, filename, object_key, deleted_at, ...
```

### 3.2 新增字段 / 索引

V1 不新增字段。

**性能预警**：以下 SQL 是热路径，实施时如果发现慢需要加索引：
- `shopify_orders(sale_date, product_id)` 复合索引（如未存在）
- `meta_ad_campaign_metrics(report_start_date, report_end_date)` 复合索引（如未存在）

实施时先观察 EXPLAIN，确实慢再加 migration。

---

## 4. Service 层（`appcore/order_analytics.py` 新函数）

### 4.1 主入口

```python
def get_dashboard(
    *,
    period: str,              # 'day' | 'week' | 'month'
    year: int = None,
    month: int = None,        # period='month'
    week: int = None,         # period='week'，ISO week
    date: str = None,         # period='day'，YYYY-MM-DD
    country: str = None,      # 可选，None 表示全国家聚合
    sort_by: str = None,      # 'spend' | 'revenue' | 'orders' | 'roas' | 'units'
    sort_dir: str = 'desc',
    compare: bool = True,
    search: str = None,       # 产品名 / product_code 模糊匹配
) -> dict:
    """返回产品看板数据。
    
    返回结构：
    {
      "period": {"start": "2026-04-01", "end": "2026-04-26", "label": "2026 年 4 月（截至 26 日）"},
      "compare_period": {"start": "2026-03-01", "end": "2026-03-26", "label": "2026 年 3 月（1-26 日）"},
      "country": "DE" | None,
      "products": [
        {
          "product_id": 999,
          "product_code": "ABC-DEF-RJC",
          "product_name": "...",
          "main_image": "...",  // 可空
          "orders": 120, "orders_prev": 100, "orders_pct": 20.0,
          "units": 145, "units_prev": 130, "units_pct": 11.5,
          "revenue": 5400.0, "revenue_prev": 4500.0, "revenue_pct": 20.0,
          "spend": 1200.0, "spend_prev": 1000.0, "spend_pct": 20.0,
          "meta_purchases": 130, "meta_purchases_prev": 120, "meta_purchases_pct": 8.3,
          "roas": 4.5, "roas_prev": 4.5, "roas_pct": 0.0,
          "ad_data_available": true,  // false 时 spend/purchases/roas 显示 "-"
          "media_items_by_lang": {"en": 1, "de": 2, "fr": 0, "ja": 1}
        },
        ...
      ],
      "summary": {
        "total_orders": ..., "total_revenue": ..., "total_spend": ..., "total_roas": ...
        // + 各项 prev / pct
      }
    }
    """
```

### 4.2 内部 helper

```python
def _resolve_period_range(period, year, month, week, date) -> tuple[date, date]:
    """返回 (start_date, end_date) 闭区间。
    - month: 该月 1 日 ~ 月末（或截至昨日，如果是当月）
    - week: ISO 周一 ~ 周日（或截至昨日）
    - day: 当天 ~ 当天
    """

def _resolve_compare_range(start, end, period) -> tuple[date, date]:
    """计算环比基准期。
    - month: 上月同长度切片（4-1 ~ 4-26 → 3-1 ~ 3-26）
    - week: 上周（同 7 天，但限制到昨日为止）
    - day: 昨日
    """

def _aggregate_orders(start, end, country) -> dict[product_id, dict]:
    """SELECT product_id, COUNT(*) AS orders, SUM(quantity) AS units, SUM(revenue) AS revenue
       FROM shopify_orders
       WHERE sale_date BETWEEN %s AND %s [AND country = %s]
       GROUP BY product_id"""

def _aggregate_ads(start, end, country) -> dict[product_id, dict]:
    """SELECT product_id, SUM(spend), SUM(purchases), SUM(purchase_value)
       FROM meta_ad_campaign_metrics
       WHERE report_start_date >= %s AND report_end_date <= %s
       [AND country filter via某种映射]
       GROUP BY product_id
       
       注意：周期对齐用'完全覆盖'语义（决策 #7）。
       国家筛选：meta 数据是否有 country 字段，实施时确认。
       如无，则当 country 筛选启用时，广告整体不参与（产品看板的 ROAS 列降级为 "-"）。"""

def _count_media_items_by_product() -> dict[product_id, dict[lang, int]]:
    """SELECT product_id, lang, COUNT(*) FROM media_items
       WHERE deleted_at IS NULL
       GROUP BY product_id, lang"""

def _join_and_compute(orders_now, orders_prev, ads_now, ads_prev, items, products) -> list[dict]:
    """合并各数据源，计算 ROAS / 环比百分比；剔除 orders+spend 都为 0 的产品；按排序键排序。"""

def _compute_pct_change(now, prev) -> float | None:
    """(now - prev) / prev * 100；prev=0 且 now>0 → None（前端显示"新增"或"-"）；
       both 0 → 0；prev=0 且 now=0 → 0"""
```

### 4.3 边界与降级

- 当月数据：`period_range.end` = 昨日（不含今天，因为今天数据可能不完整）；环比期切到对应 day-of-month
- 当周数据：同上，end = 昨日，环比上周同 day-of-week 切片
- 选定月/周尚未结束 + 没有任何完全覆盖的广告报表 → `ad_data_available = false` 全量降级
- 选定时间范围 = 未来 → 返回空 `products` 列表 + `period_label = '未来周期'`
- 无 `meta_ad_campaign_metrics` 数据 → 整列降级为 "-"，不报错

---

## 5. API 路由

### 5.1 端点定义

| 方法 | 路径 | 用途 | 权限 |
|---|---|---|---|
| GET | `/order-analytics/dashboard` | 产品看板数据查询 | login_required + admin_required |

### 5.2 Query 参数

| 参数 | 必需 | 默认 | 说明 |
|---|---|---|---|
| `period` | ✓ | `month` | `day` / `week` / `month` |
| `year` | period=month/week 时必需 | 当前年 | |
| `month` | period=month 时必需 | 当前月 | 1-12 |
| `week` | period=week 时必需 | 当前 ISO 周 | 1-53 |
| `date` | period=day 时必需 | 昨日 | YYYY-MM-DD |
| `country` | 否 | (空) | 国家代码，例如 `DE`；空 = 全部 |
| `sort_by` | 否 | `spend`（周/月）/ `revenue`（日） | 字段名 |
| `sort_dir` | 否 | `desc` | `asc` / `desc` |
| `compare` | 否 | `true` | `true` / `false` |
| `search` | 否 | (空) | 产品名 / product_code 模糊匹配 |

### 5.3 响应

200：参考 4.1 主入口 docstring。

400：
```json
{ "error": "invalid_period", "detail": "period must be one of day/week/month" }
```

500：
```json
{ "error": "internal_error", "detail": "..." }
```

`Decimal` / `date` 用现有 `_json_safe(...)` helper 序列化（`order_analytics.py` 已有）。

---

## 6. 前端改造（`web/templates/order_analytics.html`）

### 6.1 Tab 结构调整

现有 3 个 Tab：订单导入 / 订单分析 / 广告分析。

新结构（4 个 Tab，**产品看板默认**）：
1. **产品看板**（新增，默认）
2. 订单导入
3. 订单分析
4. 广告分析

Tab 切换走现有机制（实施时 grep 当前用 `data-tab` / `aria-controls` / hash 哪种，沿用）。

### 6.2 产品看板 Tab 布局

```
┌──────────────────────────────────────────────────────────────────────────┐
│ [日][周][月]  时间选择: [2026 年 4 月 ▼]   国家: [全部 ▼]  搜索: [____]   │  ← 工具栏
│                                                              [ 刷新 ]     │
├──────────────────────────────────────────────────────────────────────────┤
│ 时段: 2026-04-01 ~ 2026-04-26  对比: 2026-03-01 ~ 2026-03-26              │  ← 期间标签
├──────────────────────────────────────────────────────────────────────────┤
│ 总览: 订单 1,234 ↑12%  收入 $54,000 ↑8%  花费 $12,000 ↑15%  ROAS 4.5 ↓6% │  ← 顶部 summary
├──────────────────────────────────────────────────────────────────────────┤
│ 产品           订单     件数    收入       花费      Meta购买  ROAS  素材  操作
│ ─────────────────────────────────────────────────────────────────────────
│ [📷] ABC-DEF   120 ↑20% 145 ↑12% $5,400↑20% $1,200↑20%  130     4.5↑0  EN1 DE2  [订单][广告][素材]
│ [📷] XYZ-GHI    80 ↓5%   90 ↓3%  $3,200↓2%   $900 ↑10%   95     3.6↓11 EN1 DE0  [订单][广告][素材]
│ ...
└──────────────────────────────────────────────────────────────────────────┘
```

### 6.3 关键交互

- **粒度切换**（日/周/月）：切换时重新请求 `dashboard` endpoint
- **时间选择器**：
  - 月：年月双下拉，从已有数据中拉可选月份（复用 `available-months` endpoint）
  - 周：年 + 周次下拉
  - 日：日历输入框
- **国家下拉**：从已有数据中拉国家清单（V1 用一个简单的 `/order-analytics/countries` endpoint，**实施时 grep 看现有逻辑里有没有，没有就加**）
- **搜索框**：输入产品名 / product_code，回车触发查询
- **列头排序**：点击切换 asc/desc，同列再点反转
- **环比箭头**：
  - `pct > 0` → 绿色 ↑ + 百分比
  - `pct < 0` → 红色 ↓ + 百分比
  - `pct = 0` 或 None → 灰色 "-"
  - **特殊**：ROAS 的好坏方向跟其他相反，但 V1 不做特殊处理（用户能自己读懂）；**未来 V2** 可以让 ROAS 下降标红、上升标绿
- **空态**：
  - 当周期无数据 → 显示"该时段暂无数据"图（参考现有 `productivity_stats` 空态实现）
  - 国家筛选后无产品 → 显示"该国家无对应数据"
- **加载态**：表格上方进度条 + 行级 skeleton
- **错误态**：顶部红色 banner + "重试"按钮

### 6.4 操作列按钮（每行 3 个）

| 按钮 | 行为 |
|---|---|
| 订单 | 切到"订单分析" Tab，**预填**产品 ID + 时间范围（实施时确认订单分析 Tab 用什么 query 锁参数；如果它只支持月度筛选则只能传月份） |
| 广告 | 切到"广告分析" Tab，**预填**产品过滤 + 报表周期 |
| 素材 | 跳到 `/medias?product_id=999`（实施时确认 medias 列表是否支持该 query；如不支持，跳到 `/medias` 主页即可） |

实施时 grep 现有 Tab 切换 + URL hash + query string 协议。如果跳转目标不支持预填，**V1 退化为不预填**，跳过去用户自己筛——不要为这个功能新增 medias / order-analytics 的接口。

### 6.5 设计 token

走根 CLAUDE.md 的 Ocean Blue 设计系统：
- 行高 40-44px（卡片密度）
- 卡片 `--radius-lg` + `1px solid --border`
- 环比箭头：↑ = `--success`，↓ = `--danger`，平 = `--fg-subtle`
- 排序按钮 hover = `--bg-muted`
- Loading skeleton = `--bg-subtle`

---

## 7. 错误处理与 UX

| 场景 | 后端响应 | 前端展示 |
|---|---|---|
| 参数缺失（如 period=month 但缺 year） | 400 `{error: 'missing_param', detail: 'year required'}` | 红 banner |
| period 无效 | 400 `{error: 'invalid_period'}` | 同上 |
| 查询时间为未来 | 200 + 空 products | "该时段暂无数据" 空态 |
| 当月广告报表未上传 | 200 + 各行 `ad_data_available=false` | 行内 ROAS / 花费列 "-"，hover tooltip |
| DB 查询失败 | 500 `{error: 'internal_error'}` | 红 banner + 重试按钮 |
| 全产品都 0 数据（被决策 #12 过滤完） | 200 + 空 products + summary 全 0 | "该时段暂无产品有订单或投放" 空态 |

---

## 8. 测试策略

### 8.1 单元测试 `tests/test_order_analytics_dashboard.py`

- `test_resolve_period_range_month` — 月范围切片，含当月截至昨日
- `test_resolve_period_range_week` — ISO 周
- `test_resolve_compare_range_*` — 环比期切片
- `test_aggregate_orders_by_country` — 国家筛选 SQL
- `test_aggregate_ads_full_coverage_only` — 决策 #7：仅完全覆盖的广告报表纳入
- `test_join_and_filter_zero_rows` — 决策 #12：两边都 0 的产品被过滤
- `test_compute_pct_change_edge_cases` — prev=0 / both=0 / now=0
- `test_dashboard_happy_path_month` — 端到端 service 调用
- `test_dashboard_day_view_no_ads` — 决策 #3：日视图 ad 列降级
- `test_dashboard_search_filter` — 产品名模糊匹配

### 8.2 路由测试 `tests/test_order_analytics_routes.py`（在现有文件里加）

- `test_dashboard_admin_only` — 非 admin 403
- `test_dashboard_default_params` — 不带参数 → 默认本月
- `test_dashboard_invalid_period_returns_400`
- `test_dashboard_country_filter`
- `test_dashboard_compare_off`
- `test_dashboard_response_shape` — 验证返回结构包含 period / compare_period / products / summary

### 8.3 手工验收

测试服务器（`172.30.254.14`）跑通：
1. admin 登录 → 进入 `/order-analytics` → 看板自动加载
2. 切换日/周/月，每种粒度都能渲染
3. 切换月份，环比数字正确变化
4. 国家筛选生效
5. 列头排序生效
6. 操作列 3 个按钮正确跳转
7. 广告报表未上传的月份，ROAS/花费列显示 "-"
8. 搜索 product_code 关键字，结果正确

---

## 9. 接驳点

- **现有 order_analytics**：本看板复用其 service / DB / Blueprint，不破坏 / 不重命名现有端点
- **未来 V2 — 素材级下钻**：等 Meta 后台数据导出能力确认后，扩展 `meta_ad_campaign_metrics` 加 `media_item_id` 字段，看板加"展开看素材"行内交互
- **未来 V2 — 云控选品库素材发掘**：操作列再加一个"找新素材"按钮，跳到云控选品库（mk_selection）预填产品 product_code 搜索；选品库要支持"按投放数据筛选 + 标记未入库"——独立子项目
- **未来 V2 — 趋势 / 告警**：基于现有数据加 sparkline 列 + 阈值标红
- **productivity_stats 模块**：本看板的设计参考其 UI 密度和环比模式（如有）

---

## 10. 决策日志

12 条决定，brainstorm 期间逐条与用户确认。详见第 2 节"已锁定决定"表。

---

## 11. 实施前必做的核对清单

实施第一步**先 grep 这些字段 / 文件**，再写代码：

- [ ] `shopify_orders` 收入字段名（`total_price` / `revenue` / 其他）
- [ ] `meta_ad_campaign_metrics` 是否有 country 字段；如无，国家筛选启用时广告整体降级
- [ ] `media_products.main_image` 字段是否存在；如无，行内显示纯文字
- [ ] 现有 `order_analytics.html` Tab 切换机制（hash / data-tab / aria）
- [ ] 现有"订单分析" / "广告分析" Tab 接受什么 query 锁参数（用于操作列跳转）
- [ ] `/medias` 列表是否支持 `?product_id=` 筛选
- [ ] 现有 `available-months` endpoint 是否能复用为时间选择器数据源
- [ ] `productivity_stats` 现有 UI 实现（环比 / 空态 / Skeleton）—— 复用其样式 / 组件
