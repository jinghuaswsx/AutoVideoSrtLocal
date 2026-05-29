# 明空入库提示归属与创建紧急任务设计

- **日期**：2026-05-29
- **上位锚点**：
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-per-language-assignment-design.md`
  - `docs/superpowers/specs/2026-05-20-mk-import-domain-selection-design.md`
  - `docs/superpowers/specs/2026-05-22-mk-import-progress-card-contained-actions-design.md`
  - `docs/superpowers/specs/2026-05-22-task-center-owner-and-legacy-import-cleanup.md`
  - `docs/superpowers/specs/2026-05-28-task-center-urgent-priority-design.md`

## 背景

明空“加入素材库”进度弹窗已经把产品负责人、发布域名和后续跳转操作放回各自步骤卡片。但入库完成说明仍以独立结果块显示在弹窗底部，和“对应卡片的信息放到对应卡片里”的交互原则不一致。该说明描述的是产品和英文素材已写入素材库，以及下一步创建任务的前置状态，因此应该归属到“写入素材库”卡片末尾。

同时，任务中心已支持管理员对单个任务标记紧急；但创建小语种任务时还不能一次性把整个产品任务链标为紧急。管理员需要在创建弹窗确认前勾选“紧急任务”，默认不勾选；勾选后，本次创建的去字幕父任务和所有小语种翻译子任务都应写为紧急任务。

## 目标

1. “入库完成”说明不再作为弹窗底部独立块展示。
2. “入库完成”说明渲染到“写入素材库”卡片末尾。
3. 创建小语种翻译任务弹窗在确认按钮左侧显示“紧急任务”checkbox。
4. checkbox 默认不勾选。
5. 勾选后提交 `is_urgent=true` 到 `POST /tasks/api/parent`。
6. 后端创建父任务和所有子任务时使用同一个 `is_urgent` 值。

## 交互设计

### 加入素材库进度弹窗

现有底部独立结果块：

```text
入库完成
产品 #...，素材 #... 已入库，请确认该产品的发布域名。
下一步创建小语种任务；原视频处理人指派后会自动提交牛马去字幕，并进入人工审核。
```

改为“写入素材库”步骤卡片内的末尾提示。提示样式沿用步骤卡片内部信息块，不放在弹窗底部，不影响“选择发布域名”和“后续任务入口”卡片的操作归属。

### 创建小语种翻译任务弹窗

在弹窗底部操作区、确认按钮左侧加入：

```text
[ ] 紧急任务
```

规则：

- 默认未勾选。
- 只影响本次创建动作。
- 勾选后提示语义为“创建后，去字幕任务和所有小语种翻译任务都按紧急任务排序”。
- 已创建后仍可在任务中心按单任务继续标记或取消紧急。

## 后端设计

`POST /tasks/api/parent` 新增可选 JSON 字段：

```json
{"is_urgent": true}
```

规则：

1. 缺省或非 true 值按 `false` 处理。
2. `appcore.tasks.create_parent_task()` 增加 `is_urgent: bool = False`。
3. 创建父任务时写入 `tasks.is_urgent`。
4. 创建所有子任务时写入同一 `tasks.is_urgent`。
5. `task_events.created.payload_json` 记录 `is_urgent`，便于排查创建来源。
6. 不改变后续 `POST /tasks/api/<id>/urgency` 单任务切换能力。

## 不做范围

1. 不新增产品级持久表或产品级紧急字段；本次只在创建任务时把同一紧急值写入任务链。
2. 不做已存在任务的批量紧急回填。
3. 不改变任务中心列表筛选、排序和单任务紧急切换接口。
4. 不改变入库流程的域名确认、产品负责人确认和后续跳转顺序。
5. 不改变 Meta 热帖页的同名弹窗，除非后续明确要求。

## 验证

1. `tests/test_xuanpin_routes.py`
   - 模板不再包含底部独立 `mkiProgressResult` 结果块。
   - 模板包含写入素材库卡片内结果渲染函数或锚点。
   - 模板包含 `mkiXiaoUrgent` checkbox、默认未勾选、提交体包含 `is_urgent`。
2. `tests/test_tasks_routes.py`
   - `/tasks/api/parent` 将 `is_urgent=true` 透传给 `create_parent_task()`。
   - 缺省请求透传 `is_urgent=false`。
3. `tests/test_appcore_tasks.py`
   - `create_parent_task(is_urgent=True)` 创建父任务和子任务时都写入 `is_urgent=1`。
4. `python3 -m compileall appcore/tasks.py web/routes/tasks.py`。
