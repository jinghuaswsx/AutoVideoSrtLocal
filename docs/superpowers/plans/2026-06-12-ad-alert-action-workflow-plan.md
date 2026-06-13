# 广告预警处理状态 + 飞书每日推送 Implementation Plan

**Goal:** Mark-as-handled workflow for ad alerts + daily Feishu push of top losing ads.
**Spec:** docs/superpowers/specs/2026-06-12-ad-alert-action-workflow-design.md
**执行方式:** 本会话 TDD 直接实现（非 Codex 交接）。

### Task 1: ad_alert_actions 核心模块（TDD）
- [ ] migration `db/migrations/2026_06_12_ad_alert_actions.sql`
- [ ] 测试 `tests/test_ad_alert_actions.py`：target_key 构造、set/clear/get 的 SQL 形态
- [ ] 实现 `appcore/ad_alert_actions.py`
- [ ] commit

### Task 2: 列表接入 include_handled（TDD）
- [ ] 测试：`get_high_loss_ads` 过滤已处理 + 附加 action；`get_alerts` 同理
- [ ] 实现：`HighLossAdItem.action` / `AlertItem.action` 字段 + 过滤逻辑
- [ ] commit

### Task 3: actions API + 列表参数（TDD）
- [ ] 测试：POST /api/actions（resolved/ignored/clear/参数校验）；列表 API include_handled 透传
- [ ] 实现路由
- [ ] commit

### Task 4: 飞书每日推送（TDD）
- [ ] 测试 `tests/test_ad_alert_daily_report.py`：文本格式、disabled 跳过、空列表跳过、正常发送
- [ ] 实现 `appcore/ad_alert_daily_report.py` + `scheduler.py` 注册
- [ ] commit

### Task 5: 前端
- [ ] 卡片标记按钮（高亏损 + 语言级）、显示已处理开关、淡出交互
- [ ] commit

### Task 6: 验证收尾
- [ ] 运行新增/相关 pytest（环境装 pymysql 等最小依赖）
- [ ] python 语法检查 + 模板契约测试
- [ ] push 分支，汇报
