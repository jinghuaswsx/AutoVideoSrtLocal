# 任务中心指派与牛马自动化修订设计

- **日期**：2026-05-20
- **状态**：用户确认，分步实施
- **上位锚点**：
  - `docs/superpowers/specs/2026-04-26-task-center-skeleton-design.md`
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-review-process-view-design.md`
  - `docs/superpowers/specs/2026-05-20-user-work-scope-translation-design.md`
  - `docs/superpowers/specs/2026-05-07-subtitle-removal-backend-type-design.md`

## 背景

任务中心当前仍带有旧的“认领原始视频任务”和“产品负责人决定子任务负责人”假设。实际业务已经变为纯指派：管理员把任务分配给谁，谁就是该任务负责人并直接执行，不再存在任务负责人主动认领的过程。

同时，父任务被指派后应自动把绑定的英文视频素材提交给牛马去字幕，并持续同步进度。截图中的失败 `source media file not found: <object_key>` 不是视频不存在，而是自动提交逻辑只按旧 `UPLOAD_DIR/object_key` 查找文件；明空入库与素材管理新链路把 `media_items.object_key` 写入 `local_media_storage`，实际文件位于 `OUTPUT_DIR/media_store/<object_key>`。

## 目标

1. 管理员指派原始视频任务后，系统自动提交牛马去字幕并轮询结果。
2. 任务负责人在任务中心和原始素材任务库都能看到牛马提交、轮询、完成、失败或超时状态。
3. 翻译子任务不再被产品负责人固定绑定；每个素材的每种语言都可以单独指定翻译工作范围内的用户。
4. 若需要指派给原产品负责人，界面只提示原负责人是谁，由管理员手工选择，不自动锁定。
5. 修复牛马自动提交的本地素材定位：优先使用 `local_media_storage` 解析 `media_items.object_key`，保留旧 `UPLOAD_DIR` 兜底。

## 阶段 1：牛马自动提交素材定位修复

### 行为

- `appcore/task_raw_video_processing.py` 解析父任务素材路径时：
  1. 优先调用 `appcore.local_media_storage.exists(object_key)`。
  2. 命中后使用 `local_media_storage.safe_local_path_for(object_key)` 作为源文件。
  3. 未命中或 object key 非法时，再回退到旧的 `UPLOAD_DIR/object_key`。
- 牛马处理完成写回父任务素材时使用同一解析逻辑，确保新链路和旧链路都可覆盖原视频文件。
- 若两个位置都不存在，仍记录 `raw_niuma_failed`，错误继续展示在过程时间线。

### 测试

- 新增回归测试：当素材只存在于 `local_media_storage`，`start_niuma_processing_for_parent_task()` 必须能继续创建字幕移除任务、上传公共源文件并启动 runner。
- 保留现有 runner 启动失败、结果写回、watcher 失败事件测试。

## 阶段 2：任务创建即指派并自动提交

后续实现时将替换旧 `claim_parent()` 主路径：

- 创建父任务时必须指定原始视频处理负责人，负责人必须具备原始视频处理能力。
- 父任务创建后直接进入 `raw_in_progress`，`assignee_id` 为指定负责人，并写入新事件 `assigned` 或兼容事件 `claimed`。
- 创建成功后立即调用牛马自动提交流程。
- 旧 `/tasks/api/parent/<id>/claim` 可保留为历史兼容或隐藏，不再作为主流程入口。

## 阶段 3：按语言单独指派翻译员

后续实现时调整创建任务 payload：

- 支持 `assignments=[{"country_code":"DE","translator_id":1}, ...]`。
- 每个 `translator_id` 都必须通过 `ensure_translation_work_user()`。
- UI 可以多次创建任务，将不同语言分配给不同人。
- 创建弹窗展示原产品负责人提示，但不禁用翻译员下拉、不自动强制沿用。
- 产品负责人变更不再级联覆盖未完成子任务负责人；子任务负责人以创建时指派为准，后续换人应走任务级显式改派。

## 验证

阶段 1：

1. `pytest tests/test_task_raw_video_processing.py -q`
2. `python3 -m compileall appcore/task_raw_video_processing.py`

阶段 2 / 3 后续实施时再补充任务中心路由、模板和状态机测试。
