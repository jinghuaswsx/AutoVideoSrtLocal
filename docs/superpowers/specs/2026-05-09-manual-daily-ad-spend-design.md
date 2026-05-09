# Meta 广告费人工录入兜底（2026-05-09）

## 背景

Meta Ads Manager 的浏览器导出（`autovideosrt-roi-realtime-sync.timer` + `autovideosrt-meta-daily-final-sync.timer`）依赖 9222 CDP chrome 在 `DXM01-Meta` 环境下点「导出」按钮，受 Meta UI 改版、登录态过期、按钮 30s click timeout 等问题影响，已数次出现整账户 sync 全失败的情况（事故记录见 [docs/superpowers/specs/2026-05-07-meta-ads-multi-account-design.md](2026-05-07-meta-ads-multi-account-design.md) 与 2026-05-08 17:24 / 2026-05-09 00:12 的失败样本）。

sync 失败时，`order_profit_aggregation.get_order_profit_status_summary` 计算的「总利润」KPI 会因 unallocated 广告费缺失而虚高，给运营决策造成误导。

需要一个**人工兜底入口**：当 sync 不可用时，运营/管理员可在「广告分析」tab 内手动录入每天每个广告账户的总花费，作为产品看板顶部「总利润」KPI 的兜底数据。

## 目标与非目标

### 目标

- 在「广告分析」tab 下提供独立 sub-tab「人工录入」，admin 可按 `(业务日, 广告账户)` 录入/修改总花费金额，列表查看历史录入并标识 sync 状态。
- 当某 `(业务日, 广告账户)` 的 sync ad spend sum 为 0（或行不存在）时，把人工值加进「总利润」公式的 `unallocated` 桶。
- sync 任意金额 > 0 时，**完全不**使用人工值，让 sync 自动接管。

### 非目标

- 不下沉到 per-product 分摊：产品看板的「已分摊广告费」per-product 列保持原样（来自 sync 表）。
- 不提供录入原因/note 字段。
- 不接 `weekly_roas_report_snapshots` 历史快照重算。
- 不开放给非 admin 用户。
- 不替代 `meta_ad_daily_*` / `meta_ad_realtime_*` sync 链路本身，仅作为兜底。

## 数据模型

### 新增表 `meta_ad_manual_daily_spend`

```sql
CREATE TABLE meta_ad_manual_daily_spend (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  business_date DATE         NOT NULL,
  account_code  VARCHAR(64)  NOT NULL,
  ad_account_id VARCHAR(32)  NOT NULL,
  spend_usd     DECIMAL(14,4) NOT NULL,
  updated_by    INT NULL,
  updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uniq_date_account (business_date, account_code),
  KEY idx_date (business_date)
);
```

字段语义：

- `business_date`：与 `meta_ad_realtime_daily_campaign_metrics.business_date` 同口径（Meta 收盘日的「业务日」）。
- `account_code`：来自 `system_settings.meta_ad_accounts[*].code`（如 `newjoyloo` / `Omurio` / `newjoyloo_old`）。disabled 的历史账户也允许录入，便于补抓历史。
- `ad_account_id`：冗余存广告账户 ID，便于聚合层查询无需再 join。
- `spend_usd`：USD 金额，`DECIMAL(14,4)`，校验范围 `[0, 1e8]`。
- `updated_by`：admin 用户 ID（`web.auth.current_user.id`），用于审计；NULL 兼容脚本/迁移导入。
- 唯一键 `(business_date, account_code)`，所有写入走 `INSERT ... ON DUPLICATE KEY UPDATE`。

### Migration

新增 `migrations/2026-05-09-add-meta-ad-manual-daily-spend.sql`，由 `appcore.db` 启动器在服务重启时自动 apply 并登记到 `schema_migrations`（不要手动 `mysql` 执行；详见 CLAUDE.md「发布流程」）。

## 兜底语义

### 改动点：`appcore/order_analytics/order_profit_aggregation.py`

`get_order_profit_status_summary(*, date_from, date_to)` 当前公式：

```
total_profit = confirmed_profit + estimated_profit - unallocated
unallocated  = sum(meta_ad_daily_campaign_metrics.spend WHERE product_id IS NULL)
             + (fallback) sum(meta_ad_realtime_daily_campaign_metrics.spend WHERE product_id IS NULL)
```

新增叠加项 `manual_unallocated_supplement`：

```python
# 1. 计算每个 (business_date, ad_account_id) 的 sync sum
#    包含 product 级行 + product_id IS NULL 的 unallocated 行
sync_account_total[(date, ad_account_id)]
    = sum(spend) over (date, ad_account_id) from meta_ad_daily_campaign_metrics
                                                 ∪ meta_ad_realtime_daily_campaign_metrics fallback

# 2. 从 manual table 拉同区间的人工值
manual_map = manual_ad_spend.load_supplement_map(date_from, date_to)
    -> {(business_date, ad_account_id): spend_usd}

# 3. 对每个 manual_map 里的 (date, account)：
#    if sync_account_total.get((date, ad_account_id), 0) == 0:
#        manual_unallocated_supplement += manual_map[(date, ad_account_id)]

unallocated_final = unallocated_sync + manual_unallocated_supplement
total_profit      = confirmed_profit + estimated_profit - unallocated_final
```

行为约束：

- sync sum 严格 == 0 才触发兜底。`> 0` 即使是 `$0.01` 也信 sync。
- 触发时**不影响**任何 per-product 行（产品看板 per-product 表格不变）。
- 返回 summary dict 新增字段 `manual_unallocated_supplement_usd`（USD 总额，前端可选显示，便于运营理解为何 unallocated 比 sync 大）。
- 实时大盘的「未分摊广告费」/ 总利润读取也通过同一函数，自动获益。

### 不改动的地方

- `_load_realtime_ad_snapshot_fallback` 内部按 `(business_date, ad_account_id)` 取 latest snapshot 的逻辑保持不变（CLAUDE.md 已强调过的反复事故规则）。
- `meta_ad_daily_*` / `meta_ad_realtime_*` 表本身不写入；人工值只存在 `meta_ad_manual_daily_spend` 表。
- `weekly_roas_report_snapshots` 不重算。

## API

新增 3 个路由，挂在已有 `web/routes/order_analytics.py` blueprint（`/order-analytics/` 前缀）下。所有路由 `@login_required + @permission_required("data_analytics")`（与现有 `meta_ad_accounts_save` 等同模块路由保持一致），CSRF 通过 `layout.html` 的 meta 注入自动加 `X-CSRFToken` 头。

### GET `/order-analytics/manual-ad-spend/list`

参数：`from=YYYY-MM-DD`、`to=YYYY-MM-DD`（必填，区间最大 90 天，避免大 range）。

响应：

```json
{
  "accounts": [
    {"code": "newjoyloo", "label": "Newjoyloo", "ad_account_id": "1861285821213497", "enabled": true},
    {"code": "Omurio", "label": "Omurio", "ad_account_id": "1253003326160754", "enabled": true}
  ],
  "rows": [
    {
      "business_date": "2026-05-08",
      "entries": {
        "newjoyloo": {
          "manual_spend_usd": 300.0,
          "sync_spend_usd": 0.0,
          "effective": "manual",
          "updated_by": 7,
          "updated_at": "2026-05-09T00:30:00"
        },
        "Omurio": {
          "manual_spend_usd": null,
          "sync_spend_usd": 204.12,
          "effective": "sync"
        }
      },
      "sync_status": "partial"
    }
  ]
}
```

`effective` 三态：`sync` / `manual` / `none`（sync=0 且无手动值）。
`sync_status` 三态：`sync` / `partial` / `manual`，决定表格里那行的状态点颜色。

### POST `/order-analytics/manual-ad-spend`

请求 body：

```json
{
  "business_date": "2026-05-08",
  "entries": [
    {"account_code": "newjoyloo", "spend_usd": 300.0},
    {"account_code": "Omurio",    "spend_usd": 200.0}
  ]
}
```

行为：批量 upsert。`entries` 数组里只 upsert 显式列出的 `account_code`，未列出的保持原状（不会清空已有手动值）。

校验：

- `business_date`：合法 ISO 日期，且 ≤ 今天（按 `Asia/Shanghai` 时区取 today，拒绝未来日期；今天本身允许）。
- `account_code`：必须存在于 `system_settings.meta_ad_accounts`；disabled 账户允许录入。
- `spend_usd`：`0 ≤ x ≤ 1e8`，最多 4 位小数。
- entries 非空、单次最多 20 条。

写入：`updated_by = current_user.id`，`updated_at` 由 `ON UPDATE CURRENT_TIMESTAMP` 自动刷新。

审计：调用 `_audit_order_analytics_action("order_analytics_manual_ad_spend_upserted", target_type="manual_ad_spend", detail={"business_date": ..., "entries": [...]})`。

### DELETE `/order-analytics/manual-ad-spend`

参数：`business_date=YYYY-MM-DD&account_code=...`（query 或 body 均可）。

行为：删除一行。Idempotent（行不存在时返回 200 而非 404，简化前端）。

审计：`order_analytics_manual_ad_spend_deleted`。

## DAO 层

新增 `appcore/order_analytics/manual_ad_spend.py`，与 `meta_ad_accounts.py` 同层。导出函数：

| 函数 | 入参 | 出参 | 用途 |
|------|------|------|------|
| `upsert_entries(business_date, entries, updated_by)` | date, list[dict], int\|None | int (写入条数) | API POST 调用 |
| `list_range(date_from, date_to)` | date, date | list[dict] (按日期降序的 raw rows) | API GET 调用 |
| `delete_entry(business_date, account_code)` | date, str | bool (是否真的删了) | API DELETE 调用 |
| `load_supplement_map(date_from, date_to)` | date, date | `dict[(date, ad_account_id), Decimal]` | 聚合层 `order_profit_aggregation` 调用 |

DAO 层只做 SQL，不做权限/审计/校验（那些在路由层）。所有 SQL 走 `appcore.db.get_conn()`。

## UI

### 广告分析 tab 新 sub-tab「人工录入」

`web/templates/order_analytics.html` 内现有 sub-tab 列表 `[概览 | Campaign | Ad Set | Ad]` 之后追加 `[人工录入]`，`data-tab="ads-manual-input"`。

布局（参考 ASCII mockup）：

```
日期范围: [2026-04-26] 至 [2026-05-09]   [刷新]                       [ + 新增/编辑 ]

┌─────────────┬──────────────┬──────────────┬─────────────┬──────────┬──────────┐
│ 业务日       │ newjoyloo    │ Omurio       │ Sync 状态   │ 更新时间  │ 操作      │
├─────────────┼──────────────┼──────────────┼─────────────┼──────────┼──────────┤
│ 2026-05-08  │ $0.00 (sync) │ $204.12(sync)│ ●部分sync   │ —        │ 编辑      │
│ 2026-05-07  │ $300.00 ✏    │ $180.00 ✏    │ ○手动兜底   │ admin    │ 编辑/删除 │
│ 2026-05-06  │ $156.78(sync)│ $98.40(sync) │ ●sync       │ —        │ —        │
└─────────────┴──────────────┴──────────────┴─────────────┴──────────┴──────────┘
```

字段渲染：

- 列：业务日 + 每个 enabled 账户列（动态从 `system_settings.meta_ad_accounts` 读，新加账户自动出列；disabled 账户也显示但加灰底）。
- 单元格金额：现行有效值。括号 `(sync)` 或 `(手动)` 标识来源。金额后 `✏` 表示该 (date, account) 有手动行存在。
- Sync 状态点：
  - 绿 `●sync`：当天所有列出账户的 sync sum > 0
  - 黄 `●部分sync`：当天部分账户 sync > 0、部分 = 0（即使 = 0 那个账户没录手动值也归为这一态）
  - 红 `○手动兜底`：当天至少一个账户 sync = 0 且有手动值生效
- 更新时间：取该行内任一手动值的 `updated_by` username（多账户取 latest），无手动值则 `—`。
- 操作列：「编辑」总是可点；「删除」仅在该天有手动行时显示。

#### 编辑 modal

「+ 新增/编辑」按钮 + 行内「编辑」共用一个 modal：

```
┌──────────── 录入广告费 ────────────┐
│ 业务日: [2026-05-08]              │
│                                   │
│ newjoyloo (1861285821213497):     │
│   $ [    300.00    ]              │
│                                   │
│ Omurio (1253003326160754):        │
│   $ [    200.00    ]              │
│                                   │
│ newjoyloo_old (已停用):            │
│   $ [           ] (留空=不录)      │
│                                   │
│           [取消]  [保存]           │
└────────────────────────────────────┘
```

- 日期默认是被点击行的日期；「+ 新增」打开则默认今天。
- 每个账户一个金额输入（`type=number step=0.01 min=0`）。
- 金额留空 = 该账户不写入（保留 sync 兜底/原状）。
- 金额填 `0` = 显式录入"那天该账户 $0"（与 sync=0 数学上等价但语义上明确"我看过了，确实 0"）。
- 保存调 POST，刷新表格行；删除调 DELETE，删完刷新。

### 现有页面无 UI 变化

产品看板「总利润」数字会因聚合层兜底自动变化，模板和 JS 不动。

## 文档与认知锚点

新增/更新：

| 文件 | 改动 |
|------|------|
| `docs/superpowers/specs/2026-05-09-manual-daily-ad-spend-design.md` | 本设计稿（即此文件） |
| `CLAUDE.md` | 新增小节「Meta 广告费人工录入兜底」，说明：表名、何时触发（sync sum=0）、不下沉到 per-product、唯一约束。避免后续改 `order_profit_aggregation` 时误删兜底 |
| `docs/superpowers/specs/2026-05-07-meta-ads-multi-account-design.md` | 末尾「相关文档」加链接到本 spec |

## 测试

### `tests/test_manual_ad_spend.py`（新增）

- `upsert_entries` happy path：写入新行 → list 能查到 → 再 upsert 同 (date, account) → spend 更新、`updated_at` 变化、`created_at` 不变
- `upsert_entries` 部分写入：entries 含两条，只列出一条 → 已存在的另一条不动
- `list_range` 按日期降序、字段齐全
- `delete_entry` 存在/不存在两条路径
- `load_supplement_map` 区间过滤、返回 `(date, ad_account_id)` 键
- 路由层：
  - 无 `data_analytics` 权限的用户调 POST/DELETE → 403
  - 缺 CSRF token → 400
  - `business_date > today` → 400
  - `spend_usd < 0` 或 `> 1e8` → 400
  - 未知 `account_code` → 400
  - 单次 entries > 20 → 400
  - 审计日志写入正确

### `tests/test_order_profit_aggregation.py`（追加）

- sync sum > 0：手动值不生效，`manual_unallocated_supplement_usd == 0`
- sync sum = 0 且有手动值：`unallocated` 按手动值叠加，`total_profit` 减少；per-product 行不变
- 多账户混合（一个 sync 有数据、一个 sync 为 0）：只为 sync=0 的账户叠加手动值
- 跨日期边界：手动值仅在 `[date_from, date_to]` 区间内的命中

### 端到端 dev server 自检

按 CLAUDE.md「自己验收完再交付」要求：

1. 起 dev server，admin 登录
2. 进 `/order-analytics`，切到广告分析 → 人工录入 sub-tab
3. 录入今天 newjoyloo=$500、Omurio=$300，保存
4. 列表行刷新出来，sync 状态点正确
5. 进 `/medias` 产品看板，顶部「总利润」KPI 比录入前减少 $800（如果今天 sync sum=0）；如果今天 sync 部分有数据，KPI 减少 = 仅 sync=0 那部分账户的手动值
6. 编辑该行金额，KPI 跟着变
7. 删除一行，`effective` 切回 `sync`/`none`，KPI 回弹

## 实施顺序（按 CLAUDE.md 文档驱动）

1. 改认知文档：`CLAUDE.md` 加「Meta 广告费人工录入兜底」小节
2. 落规范文档：本设计稿提交
3. 写代码：migration → DAO → 路由 → 模板 + JS → 测试
4. 改使用文档：在已有运维 doc / `docs/superpowers/specs/2026-05-07-meta-ads-multi-account-design.md` 末尾加 related 链接
5. 端到端 dev server 自检
6. commit 含本 spec + 代码 + 测试 + CLAUDE.md 改动一起提交
7. 部署到线上（按 CLAUDE.md 路径 A）

## 不在范围

- per-product 拆分（手动值仅作 unallocated 兜底）
- 录入原因/note 字段（YAGNI）
- 历史 `weekly_roas_report_snapshots` 重算
- 非 admin 用户自助录入
- 多币种（仅 USD）
- 跨平台广告（仅 Meta）

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| sync 真的就是 $0（账户当天没投）但有人录了手动值 → 错误地把不该有的费用加进 unallocated | UI 单元格明显标注 `(手动)` + 红状态点；表格 sync 列显示 0 让用户能直观对账；接受语义上 sync=$0 时手动值优先 |
| 录入金额单位混淆（CNY/USD） | 字段名、占位符、Modal 标题统一显示 `USD`；后端只接 USD |
| sync 部分恢复（少量 spend > 0 但远小于实际）→ 手动值不再触发，KPI 偏低 | 这是 B 优先级语义的明确取舍；运营若需要可手动改 sync 行（不在本 spec 范围）或暂停手动录入并等 sync 完整恢复 |
| disabled 历史账户（newjoyloo_old）仍可录入 → 历史 unallocated 暴涨 | 需要的功能（CLAUDE.md 已要求 disabled 账户保留以便补抓历史）；接受 |
| 表行数无限增长 | 一年 ~ 365 × N 行，10 年万级别；不需要分表 |

## 相关文档

- [Meta 广告实时同步 多账户改造（2026-05-07）](2026-05-07-meta-ads-multi-account-design.md)
- [数据分析「订单分析」业务日对齐修复（2026-05-08）](2026-05-08-analytics-business-date-alignment-fix.md)
