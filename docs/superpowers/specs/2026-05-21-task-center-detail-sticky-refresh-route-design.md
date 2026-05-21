# 任务中心详情抽屉冻结与刷新设计

- **日期**：2026-05-21
- **上位锚点**：
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-child-acceptance-design.md`
  - `docs/superpowers/specs/2026-05-21-task-center-subtask-title-details-design.md`

## 背景

任务中心的翻译子任务详情抽屉内容较长。用户滚动查看产物状态、审核流程和素材预览时，顶部的任务识别信息会离开视口；任务执行状态变化后，也需要整页刷新才能拿到最新状态。

## 目标

1. 详情抽屉顶部信息区固定在抽屉顶部，滚动内容时持续可见。
2. 顶部信息区新增醒目的 `刷新最新状态` 按钮。
3. 点击刷新只重新拉取当前抽屉所需数据，不整页刷新：
   - 精确任务记录：`/tasks/api/list?...&task_id=<id>`
   - 审核流程：`/tasks/api/<id>/events`
   - 当前审核素材：`/tasks/api/<id>/review-assets`
   - 产出素材：`/tasks/api/<id>/artifacts`
   - 子任务 readiness：`/tasks/api/child/<id>/readiness`
4. 新增独立页面路由 `/tasks/detail/<task_id>`，渲染任务中心页面，并在加载后自动打开对应抽屉。

## 不做范围

- 不新增单独详情 API。
- 不改变任务状态机、审核动作、素材绑定逻辑或列表筛选语义。
- 不整页自动轮询，只提供用户主动刷新入口。

## 验证

1. `pytest tests/test_tasks_routes.py -q`
2. `python -m compileall web/routes/tasks.py`
3. 手工打开 `/tasks/detail/<id>`：页面进入任务中心并自动滑出详情抽屉；滚动抽屉时顶部区域保持冻结；点击 `刷新最新状态` 后当前抽屉数据更新且页面 URL 不跳转。
