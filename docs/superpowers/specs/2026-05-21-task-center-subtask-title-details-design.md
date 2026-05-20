# 任务中心子任务标题详情补充设计

- **日期**：2026-05-21
- **上位锚点**：
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-child-acceptance-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-review-process-view-design.md`

## 背景

任务中心详情抽屉顶部当前只显示产品名、状态、任务类型和负责人。翻译子任务处理时，用户还需要快速确认当前任务对应的是哪条英文素材，以及该产品的 `product_code`，否则需要再跳到素材管理或流程记录里查找，影响处理和复制。

## 目标

1. 子任务详情顶部按三行展示关键识别信息：
   - 第一行：产品名，作为主标题。
   - 第二行：对应素材文件名，后跟复制按钮。
   - 第三行：产品 `product_code`，后跟复制按钮。
2. 复制按钮直接复制对应文本，不复制标签。
3. 父任务保持现有信息结构，但可安全接收同一批返回字段。
4. 数据由任务中心列表接口返回，详情抽屉不依赖产出素材异步面板加载后再补齐标题。

## 数据来源

`appcore.tasks.list_task_center_items()` 在现有 `tasks -> media_products` 查询上补充 `tasks.media_item_id -> media_items.filename`：

- `source_media_filename`：任务绑定英文素材的 `media_items.filename`。
- 查询使用 `LEFT JOIN media_items source_mi ON source_mi.id=t.media_item_id`，兼容历史任务未绑定素材的情况。
- 若文件名为空，前端显示 `—`，复制按钮禁用。

`product_code` 继续沿用现有 `media_products.product_code` 字段。

## 前端行为

`web/templates/tasks_list.html` 的 `tcRenderDetail()` 顶部改为结构化标题区：

1. `<h3>` 只展示 `task.product_name`。
2. 子任务标题下方新增两行 `.tc-detail-ident-row`：
   - 标签 `素材文件名`
   - 标签 `Product code`
3. 每行值旁边放一个小号复制按钮：
   - 有值时调用统一 `tcCopyText(value)`。
   - 无值时按钮 disabled。
4. 复制成功后按钮短暂显示 `已复制`；失败时回退到 `prompt`，让用户仍可手动复制。

## 不做范围

- 不新增单任务详情接口。
- 不改任务状态机、审核流程、产出素材面板或素材管理跳转规则。
- 不新增数据库迁移。
- 不改变任务列表表格列。

## 验证

1. `pytest tests/test_appcore_tasks_supporting_data.py tests/test_tasks_routes.py -q`
2. `python3 -m compileall appcore/tasks.py web/routes/tasks.py`
3. 手工打开 `/tasks/`：进入子任务详情后，顶部三行依次是产品名、素材文件名和 product code，后两行复制按钮可用。
