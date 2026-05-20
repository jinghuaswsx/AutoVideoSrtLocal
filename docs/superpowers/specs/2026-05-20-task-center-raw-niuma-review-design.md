# 任务中心牛马结果审验设计

最后更新：2026-05-20

## 背景

原始素材任务库中，处理人认领父任务后会自动提交牛马去字幕并轮询结果。现有实现中，牛马完成后会直接覆盖父任务视频并把任务推进到 `raw_review`，处理人无法先查看结果是否可用。

## 目标

- 认领/分配后自动提交牛马去字幕并轮询。
- 任务人能在原始素材任务库看到提交时间、阶段、失败原因和结果状态。
- 牛马完成后结果先进入“待审验”，任务仍停留在“我已认领”。
- 任务人可下载牛马结果；确认可用后点击“采用牛马结果”，系统把结果作为对应产品的原始素材入库，并进入“已上传待审”。
- 如果牛马结果不可用，任务人仍可手动上传替换视频。

## 状态与事件

复用 `task_events` 作为状态源，新增语义：

- `raw_niuma_submitted`：已提交牛马，payload 包含 `subtitle_task_id`、`timeout_seconds`。
- `raw_niuma_result_ready`：牛马结果已下载到本地，等待处理人审验，payload 包含 `subtitle_task_id`、`result_video_path`、`result_size`。
- `raw_niuma_result_accepted`：处理人采用牛马结果，payload 包含 `subtitle_task_id`、`new_size`。
- `raw_niuma_failed`：提交、轮询或结果保存失败，payload 包含 `stage` 和 `error`。
- `raw_niuma_timeout`：轮询超时。
- `raw_manual_uploaded`：处理人手动上传替换版本。

列表页以父任务最后一条相关事件投影状态：

- `not_started`
- `niuma_running`
- `niuma_result_ready`
- `niuma_accepted`
- `niuma_failed`
- `niuma_timeout`
- `manual_uploaded`

## 服务端行为

`watch_niuma_processing()` 轮询到牛马任务 `done` 时，不再覆盖父任务视频，也不调用 `tasks.mark_uploaded()`。它只校验 `result_video_path` 可读，写入 `raw_niuma_result_ready` 事件，并返回 `ready`。

新增采用动作：

`accept_niuma_result_for_parent_task(parent_task_id, actor_user_id)`：

1. 校验当前用户是任务处理人或管理员可见范围内的合法处理人。
2. 校验父任务仍为 `raw_in_progress`。
3. 查找最新 `raw_niuma_result_ready` 事件。
4. 校验结果文件存在。
5. 使用父任务绑定的英文素材文件名作为 `media_raw_sources.display_name`。
6. 将牛马结果复制到对应产品的 `raw_sources/{英文素材文件名}` 对象键。
7. 创建或更新同名 `media_raw_sources`，不覆盖 `media_items.object_key` 对应的英文素材文件。
8. 写入 `raw_niuma_result_accepted`，payload 带 `raw_source_id`、`result_video_path` 和 `new_size`。
9. 调用 `tasks.mark_uploaded()`，任务进入 `raw_review`。

后续管理员审核通过时，`task_raw_source_bridge.ensure_raw_source_for_parent_task()` 必须优先使用最新 `raw_niuma_result_accepted` 事件中的结果文件，避免把带字幕英文素材重新覆盖进原始素材库。手动上传流程仍按现有逻辑：手动上传先替换父任务媒体文件，审核通过时再同步到原始素材库。

结果下载动作只允许管理员或任务处理人访问，并且只允许下载最新 `raw_niuma_result_ready` 指向的本地结果文件。

## 页面行为

“我已认领”列表展示：

- 处理进度标签。
- 提交时间或结果时间。
- 失败原因（如有）。
- 牛马结果待审验时显示：
  - 下载牛马结果
  - 采用牛马结果
  - 上传替换视频
- 牛马处理中时显示：
  - 下载原始
  - 上传替换视频
- 牛马失败/超时时显示：
  - 下载原始
  - 上传替换视频

页面保持轻量表格，不引入新的任务详情页。

## 验证

- 单元测试覆盖结果 ready 不自动 mark_uploaded。
- 单元测试覆盖采用结果后写入产品原始素材库并 mark_uploaded，且不覆盖英文素材文件。
- 单元测试覆盖列表投影提交时间、失败原因、结果可用状态。
- 路由测试覆盖下载牛马结果、采用牛马结果的成功与权限/状态错误响应。
