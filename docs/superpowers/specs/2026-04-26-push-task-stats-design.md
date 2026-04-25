# 推送管理 · 任务统计 Tab 设计

- 创建日期：2026-04-26
- 关联模块：推送管理（`/pushes/`）
- 范围：在推送管理页加一个「任务统计」Tab，展示按产品负责人聚合的素材交付吞吐数据。

## 背景与目标

当前 `/pushes/` 仅展示单条素材的就绪/推送状态。运营管理需要按员工维度看「某段时间内每人交付了多少素材，最终上线多少」，作为周报/月报的数据来源。

需求关键决定（已与用户对齐）：

1. 「员工」 = **产品负责人**（`media_products.user_id`），不是素材上传者，也不是推送操作员。
2. 表格列固定为：员工 / 提交数 / 已推送 / 未推送 / 推送率（信息密度优先，单行表头）。
3. 时间区间切的是 **`media_items.created_at`**（按提交时间切片，关注交付吞吐）。
4. 「任务统计」 Tab **仅 admin 可见**；非 admin 看不到 tab，且直接访问 URL 返回 403。
5. 默认时间范围 = **本月**。
6. Tab 切换走 **路由级分页**（`/pushes/` 与 `/pushes/stats` 两个独立 URL，可分享/收藏）。

## 路由与权限

| 路由 | 方法 | 权限 | 模板 / 响应 |
|---|---|---|---|
| `/pushes/` | GET | 登录 | `pushes_list.html`（现有，无逻辑改动，仅顶部插入 tabs partial） |
| `/pushes/stats` | GET | 登录 + admin | `pushes_stats.html`（新） |
| `/pushes/api/stats` | GET | 登录 + admin | JSON：`{rows, totals, date_from, date_to}` |

非 admin 直接访问 `/pushes/stats` 或 `/pushes/api/stats` 一律 403（复用现有 `admin_required`）。

URL 形态：`/pushes/stats?date_from=2026-04-01&date_to=2026-04-26`。query 缺省时默认本月。

## 数据模型与聚合 SQL

新增 `appcore/pushes.py::aggregate_stats_by_owner(date_from, date_to) -> dict`。

```sql
SELECT
  u.id  AS user_id,
  COALESCE(NULLIF(u.real_name,''), u.username, '未指派') AS owner_name,
  COUNT(*) AS submitted,
  SUM(CASE WHEN i.pushed_at IS NOT NULL THEN 1 ELSE 0 END) AS pushed
FROM media_items i
JOIN media_products p ON p.id = i.product_id
LEFT JOIN users u ON u.id = p.user_id
WHERE i.deleted_at IS NULL
  AND p.deleted_at IS NULL
  AND i.lang <> 'en'
  AND i.created_at >= %s
  AND i.created_at <  %s
GROUP BY u.id, owner_name
ORDER BY submitted DESC, owner_name ASC
```

要点：

- 与列表页一致：排除 `lang='en'`、排除 `deleted_at` 不为空的素材/产品。
- 时间用半开右开区间 `[from_dt, to_dt)`，避免 `23:59:59` 边界 bug。
- `owner_name` 取法对齐现有 `medias._media_product_owner_name_expr()`；产品负责人为空时归到「未指派」一行（保留可见，便于发现脏数据）。
- `LEFT JOIN users`：用户被删除/为空时仍计入「未指派」，不静默丢数据。

### 时间归一化

后端在路由层做：

- `date_from` 缺省 → 当月 1 日。
- `date_to` 缺省 → 今天。
- `date_from` 转 `YYYY-MM-DD 00:00:00`。
- `date_to` 转 `YYYY-MM-DD 00:00:00 + 1 day`（半开右开）。
- `date_from > date_to` 时返回 400。

### 派生字段（后端算）

- `unpushed = submitted - pushed`
- `push_rate = pushed / submitted`，分母 0 时返回 `null`。

### 合计行（后端返回）

JSON 同时返回 `totals = {submitted, pushed, unpushed, push_rate}`，避免前端隐藏行/分页导致算错。

### 返回 JSON 形态

```json
{
  "rows": [
    {"user_id": 7, "name": "张三", "submitted": 12, "pushed": 8, "unpushed": 4, "push_rate": 0.667},
    {"user_id": null, "name": "未指派", "submitted": 3, "pushed": 0, "unpushed": 3, "push_rate": 0.0}
  ],
  "totals": {"submitted": 50, "pushed": 32, "unpushed": 18, "push_rate": 0.64},
  "date_from": "2026-04-01",
  "date_to": "2026-04-26"
}
```

字段映射（路由层做）：

- SQL 的 `owner_name` → JSON 的 `name`
- SQL 的 `submitted` / `pushed` (Decimal) → 路由层 `int(...)` 转成普通整数
- `unpushed`、`push_rate` 由路由层在序列化时算出来注入

## 前端 UI

### 公共 Tab 头（partial：`web/templates/_pushes_tabs.html`）

- 两个页面顶部 include 同一个 partial。
- partial 接收 `active`（`'list' | 'stats'`）和 `is_admin`。
- 「任务统计」tab 用 `{% if is_admin %}` 包住，非 admin 完全看不到。
- 视觉走 ocean-blue token：active tab 底部 2px `--accent` 实心条 + `--fg`；inactive `--fg-muted` + hover `--bg-muted`。
- 点击 = 普通 `<a>` 跳转。

### `/pushes/stats` 页面布局

```
┌── tab 头 ──────────────────────────────────────────┐
│ [推送管理]  [任务统计 ◾]                           │
├────────────────────────────────────────────────────┤
│ 时间范围: [年/月/日] 至 [年/月/日]                  │
│ 快捷:    [今天][昨天][本周][上周][本月][上月]       │
│          [筛选]  [重置]                             │
├────────────────────────────────────────────────────┤
│ 员工        提交数  已推送  未推送  推送率          │
│ ──────────────────────────────────────────────      │
│ 张三           12       8       4    66.7%          │
│ 李四            8       8       0   100.0%          │
│ 未指派          3       0       3       0%          │
│ ──────────────────────────────────────────────      │
│ 合计          23      16       7    69.6%   ←粗体   │
└────────────────────────────────────────────────────┘
```

- 表格行高 40-44px，与 `pushes_list` 风格一致。
- `推送率` 列在分母为 0 时显示 `—`。
- 合计行底色 `--bg-subtle` + 字重 600。

### 快捷按钮日期换算（前端 JS，按本地时区）

| 按钮 | from | to |
|---|---|---|
| 今天 | 今天 | 今天 |
| 昨天 | 昨天 | 昨天 |
| 本周 | 本周一 | 今天 |
| 上周 | 上周一 | 上周日 |
| 本月 | 本月 1 日 | 今天 |
| 上月 | 上月 1 日 | 上月最后一天 |

- 点快捷按钮 = 填好两个日期框 + 自动重新拉数据（不再需要点筛选）。
- 点「筛选」 = 用当前日期框值拉数据。
- 点「重置」 = 恢复默认「本月」并重新拉数据。

### 三态

- **加载中**：表格区域显示 `加载中…`（与现有列表风格一致）。
- **空数据**：`该区间内暂无提交记录` + 提示「试试其他时间范围」。
- **错误**：行内 inline error 条 `--danger-bg`。

## 文件清单

**新增：**
- `web/templates/pushes_stats.html`
- `web/templates/_pushes_tabs.html`
- `web/static/pushes_stats.js`
- `web/static/pushes_stats.css`
- `tests/test_pushes_stats.py`

**改动：**
- `web/templates/pushes_list.html` — 顶部插入 `_pushes_tabs.html` partial。
- `web/routes/pushes.py` — 新增 `/stats` 与 `/api/stats` 两个路由（admin gate）。
- `appcore/pushes.py` — 新增 `aggregate_stats_by_owner(date_from, date_to)`。

## 测试

`tests/test_pushes_stats.py`：

- `aggregate_stats_by_owner`：
  - 跨日 / 跨月 / 空区间 / 全员均无提交
  - 分母为 0 时 `push_rate` 返回 `null`
  - 半开区间边界（`23:59:59` 那条记录归到当天，不漏算也不重算）
  - 产品 `user_id` 为 NULL → 归到「未指派」行
  - 已 `deleted_at` 的素材/产品不计入
  - `lang='en'` 不计入
- 路由层：
  - admin 访问 `/pushes/stats` → 200，`/pushes/api/stats` → 200
  - 非 admin 访问两个 URL → 403
  - `date_from > date_to` → 400
  - 缺省 date_from / date_to → 默认本月

## 非目标 / YAGNI

- 不做按员工筛选下拉（行数 = 员工数，本身就是完整列表）。
- 不做导出 CSV（首版先看页面，后续按需求加）。
- 不做语种维度展开（用户选了简洁版 A）。
- 不做按周/月趋势折线（需求只到「时间范围内的总数」）。
- 不影响推送操作与 `/pushes/` 现有功能。

## 风险与回退

- 风险点：聚合 SQL 在大数据量（>10w 素材）下可能慢。`media_items.created_at` 已有索引（推送管理迁移已加 `idx_pushed_at`，但没有 `created_at` 单列索引）。如果性能不达标，加 `KEY idx_created_at (created_at)`。首版先观察。
- 回退：纯前端 + 一个新表/列都不引入，回退就是删除新增文件 + 撤回 `pushes_list.html` / `pushes.py` / `appcore/pushes.py` 三处改动。
