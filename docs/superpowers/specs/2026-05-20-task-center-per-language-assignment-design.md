# 任务中心按语言独立翻译指派设计

- **日期**：2026-05-20
- **上位锚点**：
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-child-acceptance-design.md`
  - `docs/superpowers/specs/2026-05-20-user-work-scope-translation-design.md`
- **下游依赖**：`master` 已合入第二步 `db3991a79`（父任务直指派 + 自动牛马）

## 背景

当前第二步已经支持：

1. 创建父任务时指定 `raw_processor_id`，父任务直接进入 `raw_in_progress`。
2. 创建后立即触发自动牛马去字幕链路。
3. 子任务仍沿用单个 `translator_id`，同一素材下所有语言写入同一个 `assignee_id`。

这与最新业务要求不符。管理员需要对同一素材的不同语种分别分配给不同翻译负责人，例如 DE 给 A，FR 给 B。产品原负责人只应作为提示，不能成为固定默认值或禁选条件。

## 目标

1. 任务中心创建翻译任务时，支持按目标语言分别指定翻译负责人。
2. `tasks` 子任务写入时，每个 `country_code` 使用对应语言自己的 `assignee_id`。
3. 兼容旧调用方：只传 `translator_id` 时，仍按旧逻辑把所有语言指给同一人。
4. 第二步能力不回退：继续保留 `raw_processor_id`、父任务直指派、自动牛马入口、以及主流程中去掉“认领”按钮的行为。
5. 所有翻译负责人都必须通过 `ensure_translation_work_user()` 校验。

## 请求契约

### 新字段

任务中心创建接口新增 `language_assignments`：

```json
{
  "countries": ["DE", "FR"],
  "language_assignments": {
    "DE": 101,
    "FR": 202
  }
}
```

规则：

1. key 使用大写国家/语言码，与 `country_code` 一致。
2. value 为翻译负责人 user id。
3. `language_assignments` 存在时：
   - 每个 `countries` 中的目标语种都必须有 assignment。
   - 不允许空值、非整数、缺失语种。
4. `language_assignments` 不存在时：
   - 必须继续接受旧 `translator_id`。
   - 所有子任务沿用该 `translator_id`。

### 适用接口

1. `POST /tasks/api/parent`
   - 继续要求 `raw_processor_id`
   - 新 UI 使用 `language_assignments`
   - 旧调用兼容 `translator_id`
2. `POST /tasks/api/import-and-create`
   - 保持向后兼容 `translator_id`
   - 若新调用传 `language_assignments`，同样按语种写子任务负责人

## 服务层设计

### `appcore.tasks.create_parent_task`

签名扩展为：

```python
def create_parent_task(..., translator_id: int | None = None, language_assignments: dict[str, int] | None = None, raw_processor_id: int | None = None, ...)
```

内部规则：

1. 先标准化 `countries` 为大写。
2. 若提供 `language_assignments`：
   - 标准化 key 为大写。
   - 校验覆盖所有目标语种。
   - 逐语种取对应 assignee。
3. 若未提供 `language_assignments`：
   - 仍要求 `translator_id`
   - 构造 `{country: translator_id}`
4. 子任务创建循环不再使用统一 `translator_id`，改为逐语种 `assignee_id=assignment_map[country]`。
5. `task_events.created.payload_json` 增加 `language_assignments`，保留 `translator_id` 兼容字段。

### `appcore.tasks.import_and_create_task`

同步透传 `language_assignments` 到 `create_parent_task()`；未传时仍走旧 `translator_id`。

## 路由与校验

### `web/routes/tasks.py`

新增 helper 语义：

1. 解析 `language_assignments` JSON object。
2. 去重后对 map 中每个 user id 调 `ensure_translation_work_user()`。
3. 当请求未带 `language_assignments` 时，退回校验单个 `translator_id`。

错误口径：

- assignment 缺语种：400
- assignment 非整数 / 空：400
- assignment 用户不在翻译工作范围：400

## 前端设计

`web/templates/tasks_list.html` 的创建弹窗调整为：

1. 保留 `raw_processor` 下拉。
2. 每个目标语言显示：
   - 勾选框
   - 对应翻译负责人下拉
3. 产品原负责人仅显示为提示文案：
   - 示例：`原负责人：顾倩（仅提示，不会自动带入）`
4. 不再：
   - 自动选中产品原负责人
   - 因老品而禁用翻译员选择
   - 提交单个统一 `translator_id` 作为新 UI 主路径
5. 提交体改为：

```json
{
  "media_product_id": 1,
  "media_item_id": 2,
  "raw_processor_id": 9,
  "countries": ["DE", "FR"],
  "language_assignments": {
    "DE": 101,
    "FR": 202
  }
}
```

## 不做范围

1. 不改第一步牛马素材路径修复。
2. 不改第二步 raw_processor 自动牛马状态机。
3. 不新增数据库迁移。
4. 不改“我的任务”查询 SQL；现有按子任务 `assignee_id` 过滤逻辑已满足目标。

## 测试计划

1. `tests/test_appcore_tasks.py`
   - 新增按语种 assignment 写入不同 `assignee_id`
   - 新增旧 `translator_id` fallback 兼容
2. `tests/test_tasks_routes.py`
   - 新增 route 接受 `language_assignments`
   - 新增 route 缺 assignment / 非法 assignment 拒绝
   - 新增 import-and-create 对 assignment map 的透传
3. `tests/test_appcore_tasks_notifications.py`
   - 子任务 blocked 通知按各语言 assignee 发出
4. 模板测试
   - 创建弹窗包含逐语言负责人控件
   - 原负责人只显示提示，不再自动锁定或默认选中

## 验证

1. 先跑新增红测，确认当前实现不支持按语言独立指派。
2. 再实现最小修复并重跑聚焦 pytest。
3. 最后执行 `python3 -m compileall appcore/tasks.py web/routes/tasks.py`。
