# 广告预警处理状态闭环 + 飞书每日推送设计

## 背景

广告预警模块（含高亏损 tab）上线后存在两个工作流断点：

1. **关停闭环断裂**：研判结论说"建议关停"，但运营在 Meta 后台关停后，该广告仍会在列表里挂约 7 天（活跃窗口未过）。每天打开页面都是同一批条目，无法区分"待处理"和"已处理"，核心目标（揪出新亏损广告）被噪音淹没。
2. **纯被动**：模块要运营自己想起来打开页面；亏损广告每多烧一天都是真实成本。

## 目标

1. 预警条目可标记"已处理 / 忽略"，列表默认隐藏已处理项，可切换显示。
2. 每天业务日数据稳定后，自动向飞书群推送 Top 高亏损广告摘要 + 24h 公开分享链接。

## 非目标

- 不自动操作 Meta 关停广告。
- 不重构现有 `ad_alerts.py`（拆分是独立的 P2 工作）。
- 不新增飞书配置 UI（复用 `feishu_alerts.*` 既有配置与 `/scheduled-tasks` 任务启停面板）。

---

## 功能 1：预警处理状态

### 数据表（新增 migration）

```sql
CREATE TABLE IF NOT EXISTS ad_alert_actions (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  scope VARCHAR(32) NOT NULL COMMENT 'high_loss | language',
  target_key VARCHAR(255) NOT NULL COMMENT 'high_loss: {ad_account_id}:{code}; language: {product_id}:{lang}',
  action ENUM('resolved','ignored') NOT NULL,
  note VARCHAR(500) DEFAULT NULL,
  operator_user_id INT DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_ad_alert_action_target (scope, target_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

一个对象只保留一条最新状态；重复标记走 upsert。

### 核心模块 `appcore/ad_alert_actions.py`（新文件）

```
SCOPE_HIGH_LOSS = "high_loss"
SCOPE_LANGUAGE = "language"
set_action(scope, target_key, action, note, operator_user_id) → dict   # upsert
clear_action(scope, target_key) → bool                                  # 取消标记
get_actions(scope, target_keys) → dict[target_key, dict]                # 批量查
high_loss_target_key(ad_account_id, code) → str
language_target_key(product_id, lang) → str
```

新文件而非塞进 2730 行的 `ad_alerts.py`，与既有"单一职责"演进方向一致。

### 列表接入

- `get_high_loss_ads(..., include_handled=False)`：SQL LIMIT 放大 3 倍取候选，Python 层查 action map 过滤已处理再截断到 limit；`include_handled=True` 时不过滤。两种情况下条目都附带 `action` 信息（HighLossAdItem 新增 `action: dict | None` 字段）。
- 语言级 `get_alerts(..., include_handled=False)`：无 LIMIT，直接 Python 过滤；AlertItem 新增 `action` 字段。

### 路由

```
POST /ad-alerts/api/actions
  body: {scope, target_key, action: "resolved"|"ignored"|"clear", note?}
  resolved/ignored → upsert；clear → 删除标记
  返回 {ok, action}
```

既有列表 API（`/api/list`、`/api/high-loss-ads`）增加 `include_handled=1` 查询参数。

### 前端（ad_alerts.html）

- 高亏损卡片和语言级卡片各加两个小按钮：「已处理」「忽略」（事件委托，阻止冒泡）。
- 标记成功后卡片淡出移除；工具栏加「显示已处理」开关，开启时重新拉取 `include_handled=1`，已处理卡片带状态徽标和「取消标记」按钮。

---

## 功能 2：飞书每日高亏损推送

### 新文件 `appcore/ad_alert_daily_report.py`

```
TASK_CODE = "ad_alert_daily_feishu_report"
build_report_text(business_date, items, share_url) → str   # 推送文本
tick_once() → dict                                          # 定时任务入口
register(scheduler)                                         # cron BJ 17:00
```

`tick_once` 流程：
1. `scheduled_tasks.start_run(TASK_CODE)`
2. `feishu_alerts.load_config()`；未启用 → finish success + summary `{skipped: "feishu_disabled"}`
3. `ad_alerts.get_high_loss_ads(limit=10)`（默认已过滤已处理项）
4. 空列表 → finish success + summary `{skipped: "no_high_loss_ads"}`，不发消息
5. 生成 24h 分享链接：`build_high_loss_share_payload` + `sign_share_token`（SECRET_KEY 从环境变量 `FLASK_SECRET_KEY` 读取，与 web 层同源）；base URL 用 `config.AD_ALERT_PUBLIC_SHARE_BASE_URL`，为空则只给站内路径
6. `feishu_alerts.send_text_message(text)`
7. `finish_run(success, summary={sent, ad_count})`

推送文本格式：

```
【广告预警】MM-DD 高亏损广告 Top N
1. {国家} {广告名} ｜ 7天花费 $X ｜ 7天ROAS Y ｜ 连续亏损 Z 天
...
共 N 条 ｜ 查看明细：{share_url}
```

### 调度注册

`appcore/scheduler.py` 中按既有模式 import + `register(_scheduler)`，cron `hour=17, minute=0`（BJ 业务日 16:00 切换后 1 小时，前一业务日数据已完整）。启停通过 `/scheduled-tasks` 面板控制；飞书总开关 `feishu_alerts.enabled` 不开则任务自动跳过。

---

## 涉及文件

| 文件 | 操作 |
|------|------|
| `db/migrations/2026_06_12_ad_alert_actions.sql` | 新增 |
| `appcore/ad_alert_actions.py` | 新增 |
| `appcore/ad_alert_daily_report.py` | 新增 |
| `appcore/ad_alerts.py` | 修改：两个查询函数加 `include_handled` + action 附加 |
| `appcore/scheduler.py` | 修改：注册每日推送任务 |
| `web/routes/ad_alerts.py` | 修改：actions API + 列表参数透传 |
| `web/templates/ad_alerts.html` | 修改：标记按钮 + 显示已处理开关 |
| `tests/test_ad_alert_actions.py` | 新增 |
| `tests/test_ad_alert_daily_report.py` | 新增 |
| `tests/test_ad_alerts.py` / `test_ad_alert_routes.py` | 修改：补 include_handled 用例 |
