# 明空视频数据弹窗任务状态展示设计

Date: 2026-06-08

## Anchors

- `AGENTS.md`：文档驱动代码、worktree 隔离、素材管理路由验证和 focused pytest 规则。
- `docs/superpowers/specs/2026-06-08-video-card-data-modal-dual-entry.md`：明空视频卡片数据弹窗读取素材工作台同一份 `video-workbench` payload。
- `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`：任务中心支持 `/tasks/?task_id=N` 深链自动打开任务详情。
- `docs/superpowers/specs/2026-05-20-mk-selection-task-contract-alignment.md`：任务流转 UI 必须在上下文内展示任务 ID、下一步入口和失败原因。
- `docs/superpowers/specs/2026-06-08-medias-workbench-mk-card-flow-alignment.md`：小语种创建弹窗展示已有任务、成功任务 ID 和跳转入口。

## Context

`/xuanpin/mk` 视频卡片的数据图标弹窗已经复用素材工作台的翻译版本、投放消耗 / ROAS、订单量和 AI 评估数据。运营查看某条原视频素材时，还需要同时确认它在任务中心的编排情况：是否已经为该原视频创建去字幕父任务、各国家小语种翻译子任务，以及能否直接跳转到对应任务详情。

当前弹窗仅显示翻译素材和广告数据。截图中需要补充的两个位置是：

1. “翻译版本”每个国家卡片标题附近。
2. “投放消耗 / ROAS”表格的版本列。

## Goals

1. `/medias/api/product/<pid>/video-workbench` 在每个工作台 card 内返回任务编排信息，前端不额外逐行请求任务接口。
2. 任务信息以素材绑定的英文源 `media_item_id` 为主键批量查询：
   - 父任务：`tasks.parent_task_id IS NULL` 且 `tasks.media_item_id = <英文源素材 id>`。
   - 子任务：`tasks.parent_task_id IS NOT NULL` 且 `tasks.media_item_id = <英文源素材 id>`，按 `country_code` 映射。
3. 翻译版本国家卡片显示该国家对应子任务；没有子任务时显示低权重“无任务”。
4. 投放消耗 / ROAS 表格的每个国家行显示同一个国家子任务；汇总行显示父任务。
5. 有任务时显示蓝色加粗 `任务#<id>`，点击后在新标签打开 `/tasks/?task_id=<id>`。
6. 无任务时显示白底、低权重、不抢视觉的“无任务”标注。

## Non-Goals

1. 不新增数据库表或迁移。
2. 不改变任务中心创建、状态机、归档或权限契约。
3. 不在弹窗里直接执行任务操作；这里只做状态可视化和跳转。
4. 不把取消或归档任务当成“无任务”静默隐藏；payload 可带回当前最新任务，避免运营误判历史编排。

## Payload Contract

每个 card 新增：

```json
{
  "task_summary": {
    "has_task": true,
    "parent_task": {"id": 1001, "status": "raw_in_progress", "archived": false},
    "child_tasks_by_country": {
      "DE": {"id": 1002, "country_code": "DE", "status": "blocked", "archived": false}
    }
  }
}
```

每个 `target_country_versions[]` 行同步附加 `task`，每个 `translated_versions[]` 国家行同步附加 `task`。汇总行的 `task` 指向 `parent_task`。

任务负责人显示名从 `users.xingming` / `users.username` 解析；生产表没有 `users.display_name`，任务状态查询不得引用该字段。

## UX Contract

- `task` 存在且有正整数 `id`：
  - 显示 `任务#<id>`。
  - 样式为蓝色、加粗、可点击链接。
  - `href="/tasks/?task_id=<id>"`，`target="_blank"`，`rel="noopener noreferrer"`。
- `task` 不存在：
  - 显示 `无任务`。
  - 白底、浅边框、灰色小字，低视觉权重。
- 任务标注只占用标题附近的小空间，不挤压视频文件名；长文件名仍用现有省略规则。

## Verification

1. `python3 scripts/pytest_related.py --base origin/master --run`
2. 若 selector 无法覆盖，运行：
   - `pytest tests/test_medias_product_video_workbench.py tests/test_mk_selection_routes.py -q`
   - `python3 -m compileall web/routes/medias -q`
   - `git diff --check`
3. 按 `2026-06-08-targeted-pytest-verification.md` 跳过全量 `pytest -q`：本次只改明空视频卡片数据 payload、模板和局部测试，不涉及 schema/auth/deploy/scheduler/LLM/storage/billing。
