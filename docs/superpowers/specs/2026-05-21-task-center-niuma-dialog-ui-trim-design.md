# 任务中心牛马去字幕弹窗展示收敛设计

- 日期：2026-05-21
- 上位锚点：
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-review-process-view-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-step-review-assets-design.md`
  - `docs/superpowers/specs/2026-05-21-task-center-niuma-status-link-design.md`
  - `docs/superpowers/specs/2026-05-21-task-center-raw-self-review-design.md`

## 背景

任务中心详情抽屉中，第 2 步“提交牛马去字幕”同时展示归纳状态和双视频对比，导致第 2 步内容过重，也和第 3 步审核查看重复。用户实际审核结果时只需要在后续完成/审核步骤看视频，第 2 步应保留进度、状态和跳转入口。

## 目标

1. `字幕移除任务页` 按钮文字水平居中、垂直居中，并且不显示链接下划线。
2. 第 2 步 `raw_niuma_submitted` 不展示源视频/结果视频对比，只展示进度、状态、提交时间、已过时间、最近更新时间、任务 ID、反馈信息和跳转按钮。
3. 已完成后的第 3 步仍可展示视频素材，优先使用 `raw_niuma_done` 的牛马对比上下文；没有对比上下文时使用步骤审核素材。
4. 字段文案从“错误摘要”改为“结果反馈”，成功和失败都使用同一标签。

## 非目标

- 不改牛马字幕移除详情页。
- 不改任务中心状态机、接口路径、权限或数据库结构。
- 不改变 `review-assets` 只读接口的数据来源。

## 前端设计

`tasks_list.html` 的 `.tc-btn` 同时服务 `<button>` 和 `<a>`，统一设为 `inline-flex`，用 `align-items:center`、`justify-content:center`、`text-align:center` 和 `text-decoration:none` 保证按钮型链接居中且无下划线。

牛马去字幕上下文渲染增加事件类型判断：`raw_niuma_submitted` 始终不渲染 `comparison` 视频区；`raw_niuma_done` 等完成后步骤可以渲染 `comparison`。用于抑制重复 `review-assets` 的判断也只统计实际会展示对比视频的事件，避免第 2 步有上下文但不展示视频时误把第 3 步素材隐藏。

## 验证

1. `pytest tests/test_tasks_routes.py tests/test_task_center_closure_assets.py -q`
2. `python -m compileall web/routes/tasks.py appcore/tasks.py`
3. 手工打开 `/tasks/`：第 2 步只显示状态和反馈字段；第 3 步仍能查看视频；`字幕移除任务页` 按钮无下划线且文字居中。
