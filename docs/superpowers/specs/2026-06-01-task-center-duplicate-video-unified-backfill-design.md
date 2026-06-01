# 任务中心重复视频任务统一回填设计

- **日期**：2026-06-01
- **上位锚点**：
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-05-29-task-center-translation-task-id-language-guard.md`
  - `docs/superpowers/specs/2026-05-29-task-output-reset-push-state-design.md`

## 背景

任务中心允许管理员刻意创建同一原始素材、同一目标语种的多个翻译子任务，用于测试、重跑或并行验收。视频回填链路应保持统一最终产出口径：同一 `product_id + lang + source_raw_id` 的视频结果回填到同一个对象和同一条当前 `media_items` 行，最后完成的任务覆盖最终产出。

问题不在于重复任务覆盖同一个最终产出，而在于任务详情页只按 `media_items.task_id = 当前任务 id` 查找目标语种素材。最后完成的任务把统一产出行的 `task_id` 改成自己之后，先前任务详情页就找不到同一源素材同一语种的最终产出。

## 目标

1. 不禁止重复任务；重复任务是否多余由操作者决定。
2. 回填数据保持统一逻辑：最后完成的任务覆盖同一个最终产出。
3. 任务详情/readiness 不再只依赖 `media_items.task_id`，还要能按当前任务源素材的 `source_raw_id + product_id + lang` 找到统一产出。
4. 普通批量翻译和任务中心视频回填继续使用同一套 `sync_video_result()` 复用逻辑。

## 行为规则

- `_materialize_multi_translate_video()` 保持稳定对象 key：`user/medias/<product>/<lang>_<source>.mp4`。
- `sync_video_result(task_center_task_id=...)` 保持原有复用逻辑：同 object key 的当前有效 `media_items` 行会被更新，`task_id` 指向最后完成的任务。
- `_child_readiness_payload_for_row()` 查目标语种素材时，先按当前 `task_id` 查；查不到时按当前任务源素材的 `source_raw_id + product_id + lang` 查统一产出。
- 手工提交视频已经按 `find_current_item_by_source()` 覆盖同源同语种当前素材，保持同一最终产出口径。

## 验证

1. 单测覆盖：同 object key 已存在且绑定任务 A 时，任务 B 回填会复用同一行并把 `task_id` 更新为 B。
2. 单测覆盖：视频 materialize 对象 key 保持统一稳定，不因不同视频子任务拆分。
3. 单测覆盖：任务详情 readiness 对旧任务可通过 `source_raw_id` 找到最后统一产出。
4. 聚焦运行 `tests/test_bulk_translate_backfill.py`、`tests/test_bulk_translate_runtime.py`、`tests/test_appcore_tasks_supporting_data.py` 相关用例。
