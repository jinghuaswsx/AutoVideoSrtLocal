# 任务取消后小语种任务可重建设计

- **日期**：2026-05-28
- **上位锚点**：
  - `AGENTS.md`
  - `docs/任务中心需求文档-2026-04-26.md`
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-per-language-assignment-design.md`
  - `docs/superpowers/specs/2026-05-28-task-center-cancel-action-and-history-design.md`

## 背景

明空选品中，管理员先把视频素材加入素材库，再为该素材创建小语种翻译任务。创建弹窗会按 `media_item_id` 查询该素材已存在的目标语种任务，并把已有任务的语种置灰，后端创建接口也会用同一套判断阻止重复创建。

2026-05-28 的取消设计改为父任务取消不联动子任务：父任务进入 `cancelled` 后，子任务状态、负责人和时间戳保留在当前节点。这会带来一个新边界：如果父任务已取消，子任务仍停留在 `blocked` / `assigned` / `review`，它们不应继续占用同一素材同一语种，否则管理员无法重新创建该素材的对应小语种任务。

## 目标

1. 同一素材同一语种，只有仍有效的任务阻止再次创建。
2. 已取消任务不阻止再次创建，包括父任务已取消且子任务未完成的场景。
3. 已完成任务继续阻止再次创建，即使它所在父任务后来被取消。
4. 前端明空选品弹窗和后端 `POST /tasks/api/parent` 使用同一套判断，不出现前端可选但后端拒绝，或前端禁用但后端可建的分叉。

## 行为规则

以 `media_item_id + country_code` 为判断维度：

- 子任务 `status='cancelled'`：不阻止再次创建。
- 子任务 `status='done'`：阻止再次创建。
- 子任务为 `blocked` / `assigned` / `review`，且父任务不是 `cancelled`：阻止再次创建。
- 子任务为 `blocked` / `assigned` / `review`，但父任务是 `cancelled`：不阻止再次创建。

当前任务表没有独立的 `archived` 状态；业务口径中的“已归档”按已完成/有效终态处理。若后续新增任务归档状态，应并入阻止重复创建的有效状态集合。

## 实现范围

1. 调整 `appcore.tasks.get_existing_task_languages_for_item()`：
   - 查询子任务自身状态和父任务状态。
   - 只返回会阻止重建的目标语种。
   - 返回值继续保持大写国家/语言码列表。
2. 保持 `appcore.tasks.create_parent_task()` 现有调用点不变，它继续依赖上述函数做重复语种校验。
3. 保持 `/tasks/api/languages?media_item_id=...` 现有契约不变，`existing=true` 只代表该语种当前不可重建。
4. 不改取消动作本身，不引入父子任务联动。

## 不做范围

- 不新增数据库表、字段或迁移。
- 不改明空选品弹窗结构和提交流程。
- 不改任务状态机枚举。
- 不自动清理已取消父任务下的子任务状态。

## 验证

1. 新增服务层单测覆盖：父任务已取消 + 子任务未完成不再返回 existing；父任务已取消 + 子任务已完成仍返回 existing。
2. 聚焦运行相关 pytest。
3. 编译检查 `appcore/tasks.py` 和相关路由文件。
