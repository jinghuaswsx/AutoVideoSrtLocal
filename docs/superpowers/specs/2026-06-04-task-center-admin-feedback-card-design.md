# 任务中心管理员反馈高亮卡片设计

- **日期**：2026-06-04
- **上位锚点**：
  - `AGENTS.md`：任务中心主题指引、文档驱动代码、详情页验证要求
  - `web/templates/CLAUDE.md`：详情模板追加内容防呆
  - `web/static/CLAUDE.md`：Ocean Blue 设计系统与弹窗可达性
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-review-process-view-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-step-review-assets-design.md`
  - `docs/superpowers/specs/2026-05-21-task-center-detail-sticky-refresh-route-design.md`
  - `docs/superpowers/specs/2026-05-21-task-center-translation-output-evidence-design.md`

## 背景

管理员打回任务后，任务详情顶部目前只在冻结信息区显示一行 `last_reason` 备注；推送管理打回截图只出现在审核流程时间线里，且缩略图很小、点击后新窗口打开。用户进入 `/tasks/detail/<id>` 时不能第一眼看到完整反馈，也不能在不离开页面的情况下放大查看截图。

## 目标

1. 任务详情打开后，在顶部冻结区下方显示专门的管理员反馈卡片。
2. 卡片必须红边、红色警示底，文案清晰，优先显示最近一次管理员打回/拒绝原因。
3. 如果最近一次管理员反馈事件带截图 URL，卡片内直接大尺寸展示图片；尽量用 `object-fit: contain` 让用户不点开也能看全。
4. 点击反馈卡片内图片时，在当前页面打开全尺寸 modal，不跳新窗口。
5. 审核流程时间线里的打回截图也改为同一个 modal 交互，避免分散行为。

## 数据来源

- 不新增接口，不改数据库，不改任务状态机。
- 前端从现有 `task.last_reason` 与 `events` 中抽取反馈：
  - `push_rework_rejected`：优先使用 payload 的 `reason`、`issue_labels`、`image_urls`。
  - `rejected`：使用 payload 的 `reason`，无截图。
  - `cancelled` 不作为本卡片的管理员打回反馈来源。
- 多条反馈取最后一条，以用户最需要处理的最近反馈为准。
- 如果没有打回事件但 `task.last_reason` 包含 `管理员已拒绝`，仍展示文本卡片。

## 前端行为

- 卡片位置：`tcRenderDetail()` 的顶部冻结区内，状态/负责人之后、操作按钮工具条之前。
- 卡片结构：
  - 标题：`管理员反馈`
  - 副标题：显示反馈来源，例如 `推送管理打回` 或 `任务审核打回`
  - 正文：显示 `last_reason` 或事件 reason。
  - 问题点：有 `issue_labels` 时单独显示。
  - 图片区：大图网格，桌面端单张可占满卡片宽度，多张自动换行；窄屏单列。
- 图片 modal：
  - 点击图片按钮调用 `tcOpenFeedbackImageModal(url, label)`。
  - modal 覆盖当前页面，显示可滚动的大图，支持关闭按钮、遮罩点击关闭、Esc 关闭。
  - URL 必须经过 `tcSafeHref()` 过滤，仅允许站内路径和 `http/https`。

## 不做范围

- 不上传新截图。
- 不改变推送管理打回接口。
- 不新增后端 `admin_feedback` 字段。
- 不改普通产物证据、审核资产或 readiness 计算。

## 验证

1. `pytest tests/test_tasks_routes.py::test_task_detail_admin_feedback_card_highlights_rejection_with_modal -q`
2. `pytest tests/test_tasks_routes.py::test_task_detail_drawer_uses_half_screen_chinese_process_view -q`
3. `python3 -m compileall web/routes/tasks.py`
4. `pytest tests/test_tasks_routes.py -q`
5. 手工打开 `/tasks/detail/<id>`：顶部红边卡片可见；截图大图可看清；点击截图出现当前页全尺寸 modal；Esc 和关闭按钮可退出。
