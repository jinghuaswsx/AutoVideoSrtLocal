# 推送管理 — 标记不推送 / 恢复推送

日期：2026-05-07
分支：`feature/pushes-skip-push`
入口：[/pushes/](http://172.16.254.106/pushes/)

## 背景

推送管理列表的素材在被产品负责人看过之后，有些会判断为「确实不打算推」（产品下架预备、素材质量太差、等等）。当前 UI 没办法把这种「主动放弃推送」的素材跟「未就绪」「待推送」「推送失败」区分开，导致每次进入「待推送」筛选时同一批素材一直挂在那里干扰判断。

## 需求

在每行操作列加一个「标记不推送」按钮：

- 点击后该素材进入「标记不推送」状态。
- 该状态下推送按钮变灰禁用；同位置的按钮文案变为「恢复推送」。
- 点「恢复推送」回到原状态（按底层就绪情况自动算回 `pending` / `not_ready` / `failed`）。
- 状态筛选下拉新增一项「标记不推送」。
- 仅 admin 可操作；操作写 system audit。

## 设计

### 状态机

新增第 5 个枚举 `STATUS_SKIPPED = "skipped"`，作为**互斥的顶层状态**：一旦 `skip_push=1`，[appcore/pushes.py](appcore/pushes.py) `compute_status()` 直接返回 `skipped`，不再继续算 readiness 和 latest_push 状态。

| 当前状态 | 显示 | 操作列 |
|---------|------|-------|
| `not_ready` / `pending` / `failed` | 原样 | 「推送」+「标记不推送」 |
| `skipped` | 中性灰 chip「不推送」 | 「推送」灰底禁用 +「恢复推送」 |
| `pushed` | 已推送 | 不显示「标记不推送」（已推过的没意义） |

「待推送」筛选 = 就绪 ∧ 未推送 ∧ 未被标记。

### Schema

新增 migration `2026_05_07_media_items_skip_push.sql`：

```sql
ALTER TABLE media_items
  ADD COLUMN skip_push TINYINT(1) NOT NULL DEFAULT 0,
  ADD COLUMN skip_push_at DATETIME DEFAULT NULL,
  ADD COLUMN skip_push_by INT DEFAULT NULL,
  ADD KEY idx_skip_push (skip_push);
```

字段说明：

- `skip_push`：0/1 标记位
- `skip_push_at`：标记时间，便于审计
- `skip_push_by`：操作人 user_id

### 后端

**[appcore/pushes.py](appcore/pushes.py)**

- `STATUS_SKIPPED = "skipped"` 常量
- `compute_status(item, product)` 顶部加：`if item.get("skip_push"): return STATUS_SKIPPED`
- 新增 `mark_skip_push(item_id, operator_user_id)` / `unmark_skip_push(item_id)` 两个 helper：写 SQL + 不改 `pushed_at` / `latest_push_id`
- `list_items_for_push(...)` 已经返回 `media_items.*`，新加的字段会自动带出来；不需要改 SQL

**[web/routes/pushes.py](web/routes/pushes.py)**

- `POST /pushes/api/items/<id>/skip` → admin only，调 `mark_skip_push`，audit `push_skipped`
- `POST /pushes/api/items/<id>/unskip` → admin only，调 `unmark_skip_push`，audit `push_skip_cleared`
- 已推送（`pushed_at` 不为空）的素材调 skip 返回 409
- `_serialize_row(row)` 在返回 dict 里加 `skip_push: bool`，前端用来切按钮状态

**API 列表过滤**

`api_list` 状态过滤白名单加 `"skipped"`。其它过滤（`pending` 等）保持现状——因为 `compute_status` 已经把 skipped 优先级提到最高，被标记的素材自动从 `pending` / `not_ready` / `failed` 列表里消失，不需要额外 SQL。

### 前端

**[web/templates/pushes_list.html](web/templates/pushes_list.html)**

状态下拉加一项：

```html
<option value="skipped">标记不推送</option>
```

**[web/static/pushes.js](web/static/pushes.js)**

- 操作列渲染：在「推送」按钮后追加按钮，`item.skip_push` 决定文案 / class
- 标记：`POST /pushes/api/items/<id>/skip`，成功后 reload 当前行（或重拉列表）
- 恢复：`POST /pushes/api/items/<id>/unskip`，同上
- `skip_push=1` 时给「推送」按钮 `disabled` + class `btn-disabled`
- 状态 chip 加分支 `skipped` → 中性灰

**[web/static/pushes.css](web/static/pushes.css)**

- 沿用 token：`--bg-muted` 灰底，`--fg-muted` 文字色
- 不引入新色。`.status-chip.skipped` 灰底 + 灰字；`.btn.btn-disabled` 灰背 + cursor 禁用

## 测试

- pytest：扩 `tests/test_pushes_routes.py`，加
  - `test_skip_marks_item_and_returns_status`
  - `test_skip_blocked_for_pushed_item`（409）
  - `test_unskip_clears_flag_and_recomputes_status`
  - `test_status_filter_skipped_returns_only_marked`
  - `test_pending_filter_excludes_skipped`
- 跑 `tests/test_pushes_stats.py` 确认 stats 计算路径（统计是否要把 skipped 排除—**先按"不排除"**实现，因为统计聚焦 push 行为，stats 路径没动）
- Playwright 端到端：admin 登录 → 进入 [/pushes/](http://172.16.254.106/pushes/) → 标记某条素材 → 验证按钮切换 + 筛选「标记不推送」能查到 → 恢复 → 验证回到 pending

## 部署

测试环境（172.16.254.106:8080）→ 验证 → 用户确认 → 上线（172.16.254.106:80）。
启动器自动 apply migration + 登记 schema_migrations，不需要手动跑 SQL。

## 不做的事

- 不动 stats 页（`/pushes/stats`）的统计口径
- 不动小语种文案推送 / 产品链接推送（这两条与「标记不推送」无关）
- 不加备注字段（标记原因），如有需要后续按需追加列
- 不做批量「标记不推送」（每行单独操作即可，列表本身不长）
