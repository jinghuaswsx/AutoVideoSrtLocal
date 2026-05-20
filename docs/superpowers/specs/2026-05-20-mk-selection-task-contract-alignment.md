# 明空小语种创建任务接口契约对齐

日期：2026-05-20

## 锚点

- `AGENTS.md`：文档驱动代码与任务中心端到端流程门禁。
- `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`：明空选品到任务中心的端到端主线。
- `docs/superpowers/specs/2026-05-20-task-center-direct-assignment-design.md`：`POST /tasks/api/parent` 必须接收 `raw_processor_id`，父任务创建后直接进入原视频处理。
- `docs/superpowers/specs/2026-05-20-task-center-per-language-assignment-design.md`：新任务创建主路径使用 `language_assignments` 指定各语种负责人。
- `docs/superpowers/specs/2026-05-20-mk-import-progress-modal-design.md`：加入素材库成功后，进度弹窗继续调用 `/tasks/api/parent` 创建小语种任务。

## 背景

生产环境中，明空选品「加入素材库」可以成功创建 `media_products` 与英文 `media_items`，但随后点击「下一步：创建小语种任务」时，前端仍按旧契约提交：

```json
{
  "media_product_id": 594,
  "media_item_id": 1354,
  "translator_id": 33,
  "countries": ["DE", "FR"]
}
```

当前后端已经要求 `raw_processor_id`，并支持 `language_assignments`。旧 payload 会在 `/tasks/api/parent` 参数解析阶段返回 400，导致产品留在待派单池，任务详情页打开旧 task id 时显示「任务未找到」。

## 目标

1. 明空小语种创建任务入口与任务中心当前后端契约一致。
2. 加入素材库进度弹窗、已入库素材卡片创建任务都提交 `raw_processor_id`。
3. 现有单翻译员小弹窗不扩展为完整按语言独立分配 UI；勾选的所有语种先映射到同一个翻译负责人，生成 `language_assignments`。
4. `translator_id` 继续保留在 payload 中，作为旧兼容字段和事件展示辅助。
5. 原视频处理人从 `/tasks/api/raw-processors` 加载，创建前必须选择。
6. 创建任务确认后小语种弹窗不立即关闭，必须在弹窗内展示创建中、成功任务 ID/跳转入口，或失败请求与后端错误原因。

## 不做

- 不改变任务中心完整创建弹窗，它已经支持按语言独立分配。
- 不新增数据库字段或迁移。
- 不自动补建本次失败的生产任务；数据修复与功能修复分开处理。
- 不放宽后端对 `raw_processor_id` 的校验。

## 实现

`web/templates/mk_selection.html`：

1. 小语种弹窗加载两个用户列表：
   - `/tasks/api/translation-work-users` 用于翻译负责人。
   - `/tasks/api/raw-processors` 用于原视频处理人。
2. 弹窗返回：
   - `translatorId`
   - `rawProcessorId`
   - `countries`
3. 新增 helper 把 `{translatorId, countries}` 转为：

```json
{
  "DE": 33,
  "FR": 33
}
```

4. 两个直接调用 `/tasks/api/parent` 的入口都提交：

```json
{
  "media_product_id": 594,
  "media_item_id": 1354,
  "countries": ["DE", "FR"],
  "translator_id": 33,
  "raw_processor_id": 33,
  "language_assignments": {
    "DE": 33,
    "FR": 33
  }
}
```

5. 任务创建确认后：
   - 弹窗保持打开，确认按钮进入 loading 状态。
   - 状态区显示正在请求 `/tasks/api/parent`。
   - 成功时显示父任务 ID，并提供 `/tasks/?task_id=<id>` 的直接入口。
   - 失败时显示请求路径、HTTP 状态和后端 `error/detail/statusText`，方便定位。

## 流程闭环原则

任务中心和上下游入口属于任务流转界面。每一个动作都必须明确衔接上一步和下一步：用户提交后要看到处理中状态；成功后要知道创建了哪个任务、下一步去哪处理；失败后要知道哪个请求失败、后端返回了什么原因。禁止让任务流按钮只关闭弹窗或只 toast 一句，导致用户无法判断当前业务状态。

## 验证

```bash
pytest tests/test_mk_selection_routes.py tests/test_tasks_routes.py -q
python -m compileall web/routes/tasks.py appcore/tasks.py -q
git diff --check
```
