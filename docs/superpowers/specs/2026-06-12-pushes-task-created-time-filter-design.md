# 推送管理任务创建时间筛选设计

- **日期**：2026-06-12
- **范围**：推送管理 `/pushes` 列表的时间展示与筛选
- **相关锚点**：
  - `docs/superpowers/specs/2026-04-18-push-management-design.md`
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-04-26-task-center-skeleton-design.md`

## 背景

推送管理列表当前“创建时间”来自 `media_items.created_at`，表示素材记录创建时间，不是产品创建时间，也不是任务创建时间。实际运营筛选时，需要区分：

- **素材创建时间**：当前素材记录的 `media_items.created_at`。
- **任务创建时间**：该素材关联任务的 `tasks.created_at`，用于按任务生成批次筛选。

## 目标

1. 将推送列表现有“创建时间”文案明确为“素材创建时间”。
2. 新增“任务创建时间”列，展示该素材对应任务的创建时间。
3. 新增任务创建时间起止筛选，独立于素材创建时间筛选。
4. 保持现有素材创建时间排序与 `date_from` / `date_to` 查询参数兼容。

## 字段来源

- 素材创建时间：`media_items.created_at`，API 字段继续为 `created_at`。
- 任务创建时间：优先使用 `media_items.task_id` 对应的 `tasks.created_at`；若素材未直接绑定任务，则按当前推送页负责人/任务推导口径，匹配同产品、同语种的子任务创建时间。无法匹配时返回空。

## 接口

`GET /pushes/api/items` 新增参数：

- `task_created_from`: 任务创建时间开始日期或日期时间。
- `task_created_to`: 任务创建时间结束日期或日期时间。

返回 item 新增字段：

- `task_created_at`: ISO 字符串或 `null`。

## 前端

- 筛选区保留素材创建时间范围，并将标签改为“素材创建时间”。
- 新增“任务创建时间”范围输入。
- 表格在“素材创建时间”旁新增“任务创建时间”列。
- 无任务创建时间时显示空占位，避免误解为素材或产品时间。

## 验证

- 相关 pytest：`tests/test_appcore_pushes.py`、`tests/test_pushes_routes.py`、`tests/test_pushes_ui_assets.py`。
- 按 `docs/superpowers/specs/2026-06-08-targeted-pytest-verification.md` 运行聚焦测试，不默认跑全量。
