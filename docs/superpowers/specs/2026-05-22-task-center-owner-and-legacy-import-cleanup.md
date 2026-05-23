# 任务中心负责人隔离与旧导入建任务接口清理

- **日期**：2026-05-22
- **状态**：用户确认，立即修复
- **上位锚点**：
  - `docs/superpowers/specs/2026-05-20-task-center-assignment-and-niuma-automation-fix.md`
  - `docs/superpowers/specs/2026-05-20-task-center-per-language-assignment-design.md`
  - `docs/superpowers/specs/2026-05-20-task-language-label-and-mk-modal-guard-design.md`

## 背景

任务中心已经切到“原视频处理人 + 按语种独立翻译负责人”的创建模型。旧逻辑仍有两个风险：

1. 素材管理修改产品负责人时，会级联覆盖未完成子任务负责人，可能冲掉已经按语种分配好的翻译负责人。
2. 旧接口 `POST /tasks/api/import-and-create` 仍可被历史入口调用；它会用旧链路导入并创建任务，且不会传 `raw_processor_id`，从而绕过当前自动牛马主链路。

## 目标

1. 产品负责人只代表素材归属，不再自动改派任务中心的父任务或子任务负责人。
2. 子任务负责人以创建任务时的按语种指派为准；后续换人必须走任务级显式改派能力。
3. 删除旧 `POST /tasks/api/import-and-create` 路由和前端遗留调用，所有明空小语种任务创建都必须基于已入库素材走 `POST /tasks/api/parent`。
4. 保留历史 `assignee_changed` 事件展示能力，但文案不再暗示它来自产品负责人变更。

## 验收

1. `medias.update_product_owner()` 只更新素材管理相关负责人字段，不调用任务中心负责人同步。
2. `tasks.on_product_owner_changed()` 不再修改任务负责人；保留兼容时只能是 no-op。
3. `/tasks/api/import-and-create` 不再注册，访问返回 404，且前端模板不包含该接口调用。
4. 明空选品页“创建小语种翻译任务”入口继续调用 `/tasks/api/parent`，必须携带 `raw_processor_id`。
