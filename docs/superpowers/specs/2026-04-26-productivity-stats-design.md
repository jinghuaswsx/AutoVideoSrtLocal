# F 子系统：员工产能报表（productivity-stats）设计文档

- **日期**：2026-04-26
- **范围**：F — 基于 `task_events` 的员工日产 / 通过率 / 返工率报表
- **上位**：[docs/任务中心需求文档-2026-04-26.md](../../任务中心需求文档-2026-04-26.md)

---

## 0. 一句话目标

admin 进入"员工产能报表"菜单后，看到三个表：每位员工的**日产数**、**通过率**、**返工率**，按时间窗口（7/30/60 天 或自定义）切换。

不做：导出、阈值告警、可视化图表、普通员工自查。

---

## 1. 范围

### 1.1 做什么

1. 新 service `appcore/productivity_stats.py`：3 个聚合函数
2. 新 blueprint `web/routes/productivity_stats.py`，前缀 `/productivity-stats`，2 端点
3. 新模板 `web/templates/productivity_stats.html`：admin-only 报表页
4. layout.html 加菜单
5. permissions.py 加 `productivity_stats` 权限码（admin true / user false）

### 1.2 不做

- 导出 CSV / PDF
- 阈值告警 / 邮件通知
- 图表库（Chart.js 等）
- 普通员工的"我的产能"自查页
- 跨产品 / 跨国家 维度细分（先按人聚合）

---

## 2. 数据源

`task_events` 表（C 已建）。所有 event_type：
- `created` / `claimed` / `raw_uploaded` / `submitted` / `approved` / `rejected` / `cancelled` / `completed` / `assignee_changed` / `unblocked`

### 2.1 指标定义

- **日产数（per user）** = COUNT(event_type IN ('approved', 'completed'))
- **通过率（per user）** = approved / (approved + rejected)（reject 后修复重提的情况：分母累加，分子也累加；admin 多次审核都计入）
- **返工率（per user）** = rejected / submitted（同一任务多次 submit 都计入）
- **actor_user_id IS NULL** 的事件（系统触发的 unblock / cascade）不计入员工统计

### 2.2 时间窗口

- 默认 30 天
- 支持 query: `?days=7|30|60` 或 `?from=YYYY-MM-DD&to=YYYY-MM-DD`

---

## 3. 服务层（`appcore/productivity_stats.py`）

```python
def get_daily_throughput(*, from_dt, to_dt) -> list[dict]:
    """日产汇总：返回 [{user_id, username, day, count}]，按人 + 天分组。"""
    return query_all("""
        SELECT te.actor_user_id AS user_id, u.username,
               DATE(te.created_at) AS day,
               COUNT(*) AS count
        FROM task_events te
        JOIN users u ON u.id = te.actor_user_id
        WHERE te.event_type IN ('approved', 'completed')
          AND te.created_at >= %s AND te.created_at < %s
          AND te.actor_user_id IS NOT NULL
        GROUP BY te.actor_user_id, DATE(te.created_at)
        ORDER BY u.username, day
    """, (from_dt, to_dt))


def get_pass_rate(*, from_dt, to_dt) -> list[dict]:
    """通过率：approved / (approved + rejected) per user."""
    return query_all("""
        SELECT te.actor_user_id AS user_id, u.username,
               SUM(CASE WHEN te.event_type='approved' THEN 1 ELSE 0 END) AS approved,
               SUM(CASE WHEN te.event_type='rejected' THEN 1 ELSE 0 END) AS rejected
        FROM task_events te
        JOIN users u ON u.id = te.actor_user_id
        WHERE te.event_type IN ('approved', 'rejected')
          AND te.created_at >= %s AND te.created_at < %s
          AND te.actor_user_id IS NOT NULL
        GROUP BY te.actor_user_id
        HAVING approved + rejected > 0
        ORDER BY (approved / (approved + rejected)) DESC
    """, (from_dt, to_dt))


def get_rework_rate(*, from_dt, to_dt) -> list[dict]:
    """返工率：rejected / submitted per user."""
    return query_all("""
        SELECT u.id AS user_id, u.username,
               SUM(CASE WHEN te.event_type='rejected' AND ev_t.assignee_id=u.id THEN 1 ELSE 0 END) AS rejected,
               SUM(CASE WHEN te.event_type='submitted' AND te.actor_user_id=u.id THEN 1 ELSE 0 END) AS submitted
        FROM users u
        JOIN task_events te ON 1=1
        LEFT JOIN tasks ev_t ON ev_t.id = te.task_id
        WHERE te.created_at >= %s AND te.created_at < %s
        GROUP BY u.id
        HAVING submitted > 0
        ORDER BY (rejected / submitted) DESC
    """, (from_dt, to_dt))
```

⚠️ 实施时把 SQL 简化或调整以匹配实际索引情况。Rework rate 那个 join 可能慢，必要时改成两段查询。

---

## 4. API 路由

| 方法 | 路径 | 用途 | 权限 |
|---|---|---|---|
| GET | `/productivity-stats/` | 主页（render） | admin |
| GET | `/productivity-stats/api/summary?days=30` 或 `?from=&to=` | JSON 三段数据 | admin |

---

## 5. 前端

`productivity_stats.html`：
- 顶部：时间窗口切换（7d / 30d / 60d / 自定义）+ 刷新按钮
- 3 个 collapsible 卡片：
  - 日产汇总 = 员工 × 日期透视表（colored heat cells）
  - 通过率排行 = 表格 sorted by pass_rate DESC
  - 返工率排行 = 表格 sorted by rework_rate DESC

Ocean Blue tokens (`--ps-*` namespace).

---

## 6. 测试

- service 层：3 个 aggregate 函数 smoke test（plant fake events, verify counts）
- routes：authed_client_no_db 检查 admin only + 端点 registered

---

## 7. 实施顺序

6 任务（permissions / service / API / frontend / menu / deploy）。
