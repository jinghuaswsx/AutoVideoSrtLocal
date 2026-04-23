# 翻译任务页原始视频文件名展示设计

## 背景

当前商品级“翻译任务管理”页能看到批量任务状态、语言和范围，但看不出该任务实际是从哪个原始视频发起的。视频子任务摘要里也只显示 `原始视频 #id`，对运营排查不直观。

## 目标

在 [medias_translation_tasks.html](/G:/Code/AutoVideoSrtLocal/.worktrees/codex-medias-task-source-filename/web/templates/medias_translation_tasks.html) 对应的任务页中，让用户一眼看到：

- 每个批量翻译父任务关联的原始视频文件名
- 每个视频翻译子任务对应的原始视频文件名

## 方案

- 后端继续以 `bulk_translate` 父任务 `state.raw_source_ids` 作为父任务级来源；若旧任务没有该字段，则回退扫描 `plan[*].ref.source_raw_id/source_raw_ids`
- 原始视频展示名优先取 `media_raw_sources.display_name`，为空时退回 `video_object_key` 的文件名，再退回 `原始视频 #id`
- 父任务新增 `raw_source_display_names` 字段，供任务卡片头部摘要展示
- 视频类子任务摘要从 `原始视频 #id` 改为具体文件名
- 非视频类子任务保持现有摘要不变

## 验证

- 投影层测试覆盖父任务文件名列表和视频子任务摘要
- 前端脚本测试覆盖父任务卡片会渲染 `原始视频:` 元信息
