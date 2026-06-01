# 任务中心视频封面源图绑定修复

- **日期**：2026-06-01
- **上位锚点**：
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-per-language-assignment-design.md`
  - `docs/superpowers/specs/2026-05-29-task-center-translation-task-id-language-guard.md`

## 背景

任务中心子任务由一个英文 `media_items.id` 作为素材源创建。视频翻译入口会把子任务 ID 传给素材管理页，再创建批量翻译任务。

线上任务 323 暴露出一个边界问题：子任务绑定的英文素材封面已经是 `media_items.cover_object_key`，但视频封面翻译子任务仍读取 `media_raw_sources.cover_object_key`。当 raw source 封面和当前英文素材封面不一致时，系统会翻译旧封面，并把结果回填到该子任务目标语素材，看起来像同素材不同任务之间串封面。

## 目标

1. 从任务中心进入视频/视频封面翻译时，后端以子任务绑定英文素材的 `source_raw_id` 为准，不信任前端默认选中的 raw source。
2. 批量翻译创建视频封面图片翻译子任务时，优先使用任务绑定英文素材的 `media_items.cover_object_key`。
3. 如果任务绑定英文素材没有可用封面，才回退到 `media_raw_sources.cover_object_key`，保持历史 raw source 翻译入口可用。
4. 继续保留 2026-05-29 的 `task_center_task_id` 产品、语种、状态和负责人守卫。

## 不做范围

1. 不调整任务中心状态机。
2. 不改变非任务中心的素材管理页批量翻译入口。
3. 不新增数据库字段或迁移。
4. 不自动修复历史已翻译错封面的素材；历史数据由人工重跑或后续修复脚本处理。

## 验证

1. `tests/test_appcore_tasks.py` 覆盖子任务绑定英文素材源信息解析。
2. `tests/test_media_product_translate_service.py` 覆盖任务中心视频翻译强制使用子任务绑定 raw source。
3. `tests/test_bulk_translate_runtime.py` 覆盖视频封面翻译优先使用子任务绑定英文素材封面。
