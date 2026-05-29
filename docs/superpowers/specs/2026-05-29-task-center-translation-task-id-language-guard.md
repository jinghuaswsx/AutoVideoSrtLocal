# 任务中心翻译任务 ID 语种守卫

- **日期**：2026-05-29
- **上位锚点**：
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-per-language-assignment-design.md`
  - `docs/superpowers/specs/2026-05-21-task-center-subtask-title-details-design.md`

## 背景

产品 532 的任务中心子任务 248 是 FR，但从素材管理翻译入口创建的 FR 批量翻译任务携带了 DE 子任务 247 的 `task_center_task_id`。批量任务完成后，FR 视频素材被写入 `media_items.task_id=247`，导致 `/tasks/detail/248` 的产出素材、审核素材和 readiness 面板都查不到 FR 视频。

## 目标

1. 从任务中心跳转到素材管理创建翻译任务时，`task_center_task_id` 必须绑定到同一个 `product_id` 和同一个目标语种。
2. 后端作为强校验入口：创建产品翻译任务前校验子任务存在、产品匹配、`country_code` 匹配、状态允许接收产出、操作者有权限。
3. 前端桥接模式下只允许选择当前子任务语种，降低误操作概率。
4. 历史错绑数据按最小范围修复：只调整确认属于对应子任务的 `media_items.task_id`。

## 不做范围

- 不改变批量翻译任务计划结构。
- 不改变任务中心状态机和审核规则。
- 不把文案、详情图表新增 `task_id` 字段；它们继续按产品和语种展示。

## 验证

1. `tests/test_media_product_translate_service.py` 覆盖 task center id 与目标语种不匹配时拒绝创建。
2. 模板/静态测试覆盖任务中心桥接模式锁定目标语种。
3. 生产复查产品 532：FR 视频绑定到任务 248，DE 视频仍绑定到任务 247。
