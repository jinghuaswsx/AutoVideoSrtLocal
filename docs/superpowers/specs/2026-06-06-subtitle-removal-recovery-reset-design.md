# 字幕移除失败恢复与归零重跑设计

- 日期：2026-06-06
- 状态：已确认，进入实现
- 上位锚点：
  - `AGENTS.md`：文档驱动代码、worktree 隔离、后台路由验证。
  - `web/templates/CLAUDE.md`：模板内可变更请求必须保留 CSRF / 路由守卫约束。
  - `docs/superpowers/specs/2026-04-15-subtitle-removal-design.md`：字幕移除详情页提供 `resume-poll` 与 `resubmit`，失败后需要可恢复入口。
  - `docs/superpowers/specs/2026-05-15-niuma-subtitle-removal-design.md`：牛马复用现有 `aiRemoveSubtitleSubmitTask` / `aiRemoveSubtitleProgress` provider/runtime。
  - `docs/superpowers/specs/2026-05-28-niuma-subtitle-region-submit-design.md`：手工任务支持全屏 / 框选区域，提交时保存 `selection_box` / `position_payload`。
  - `docs/superpowers/specs/2026-05-21-task-center-raw-niuma-force-rerun-design.md`：`tcraw-*` 字幕移除任务需要保留父任务关联，并在完成后回填任务中心。

## 背景

字幕移除任务在第三方提交后，页面或服务端可能因为网络、下载、进程重启等原因进入异常状态。很多时候第三方任务实际仍在处理，或结果已经生成，只需要继续用原来的 `provider_task_id` 调进度接口即可拿到结果。当前页面虽有“继续轮询”入口，但失败态展示和后端状态归并不稳定，用户无法可靠地恢复。

同时，当前“重提”更像是带着旧状态再次提交。用户希望它改成“从零开始”：保留原视频和任务关联关系，其余提交条件、选区、第三方任务和结果全部清空，让用户重新选择全屏或框选后作为纯新任务再跑。

## 目标

1. 任务出问题后，详情页必须给用户可操作入口：`重新轮询结果` 和 `从零重跑`。
2. `重新轮询结果` 只使用当前任务已有的 `provider_task_id` 查询第三方进度，不重新提交第三方任务。
3. 如果第三方进度已经返回成功和结果 URL，服务端继续执行下载 / 本地结果保存，状态自动变为 `done`。
4. `从零重跑` 保留同一个字幕移除任务 ID、源视频、缩略图、媒体信息、处理方式和任务中心父任务关联。
5. `从零重跑` 清空去除范围、provider 信息、旧结果、错误和步骤状态，把任务恢复为 `ready`，允许用户重新选择全屏或框选。
6. `tcraw-*` 任务从零重跑后仍能写入父任务事件、启动 watcher，并在完成后回填父任务素材。

## 非目标

1. 不新增 DB schema。
2. 不删除旧 `tcraw-*` 任务，也不创建新的任务 ID。
3. 不改牛马 / 火山 / 本地 VSR provider 协议。
4. 不改变任务中心父任务的审核状态口径，只修复字幕移除详情页恢复和回填链路。

## 后端契约

### `POST /api/subtitle-removal/<task_id>/resume-poll`

规则：

1. 要求任务存在且未删除。
2. 要求 `provider_task_id` 非空。
3. `status=done` 时返回 409。
4. 如果 runner 已在运行，返回 `{"status":"running"}`。
5. 否则把任务状态归并为 `running`：
   - `submit=done`
   - `poll=running`
   - `download_result` 和 `upload_result` 如果未完成则保持 `pending`
   - 清空 `error`
   - 不清空 `provider_task_id` / `provider_raw` / `provider_result_url`
6. 启动 runner 后，runtime 使用当前任务的 `provider_task_id` 调 `aiRemoveSubtitleProgress`。如果查到结果，继续下载并写入本地结果。

### `POST /api/subtitle-removal/<task_id>/resubmit`

规则：

1. 只允许非 `queued/running/submitted` 状态进入。
2. 删除旧结果文件和旧 TOS 结果对象。
3. 保留：
   - `id`
   - `type`
   - `_user_id`
   - `video_path`
   - `task_dir`
   - `original_filename`
   - `display_name`
   - `thumbnail_path`
   - `source_tos_key`
   - `source_object_info`
   - `subtitle_backend`
   - `erase_text_type` 初始值仅火山保留为 `subtitle`
   - `media_info`
4. 清空：
   - `remove_mode`
   - `selection_box`
   - `position_payload`
   - `local_vsr_options`
   - `provider_task_id`
   - `provider_status`
   - `provider_emsg`
   - `provider_result_url`
   - `provider_raw`
   - `provider_task_submitted_at`
   - `poll_attempts`
   - `last_polled_at`
   - `result_video_path`
   - `result_tos_key`
   - `result_object_info`
   - `error`
5. 步骤恢复为：
   - `prepare=done`
   - `submit=pending`
   - `poll=pending`
   - `download_result=pending`
   - `upload_result=pending`
6. 返回 `{"status":"ready"}`，不立即启动 runner。用户需要在页面重新选择全屏或框选，再点提交。

## 前端行为

1. 结果操作区按钮改为：
   - `重新轮询结果`
   - `从零重跑`
   - `删除`
2. 失败态只要有 `provider_task_id` 就展示 `重新轮询结果`，因为失败可能来自本地下载或进程异常，不代表第三方任务不可查。
3. 点击 `从零重跑` 后先确认；成功后不跳转，页面进入 `ready`：
   - 提交按钮可见。
   - 全屏 / 框选控件可选。
   - 旧结果预览和对比区隐藏。
   - 选区清空，默认回到全屏；用户切换框选时再生成默认字幕带矩形。
4. 点击提交后继续走现有 `submit` 接口。

## 验证

1. Route 测试：`resume-poll` 对 error/interrupted 且有 `provider_task_id` 的任务会恢复 `poll=running` 并启动 runner。
2. Route 测试：`resubmit` 不立即提交，而是把任务从零重置为 `ready`，清空 provider / result / selection。
3. Route 测试：`tcraw-*` 任务从零重跑后重新提交时仍写父任务事件并启动 watcher。
4. UI 测试：详情脚本包含 `从零重跑` 的 ready 回填逻辑，且失败态允许显示 `重新轮询结果`。
5. 回归命令：
   - `pytest tests/test_subtitle_removal_routes.py tests/test_subtitle_removal_runtime.py tests/test_task_raw_video_processing.py tests/test_web_routes.py -q`
   - `python -m compileall web/routes/subtitle_removal.py appcore/subtitle_removal_runtime.py appcore/task_raw_video_processing.py`
