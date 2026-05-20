# 任务中心第二步：父任务直指派与自动牛马提交设计

- **日期**：2026-05-20
- **上位**：[任务中心端到端流程补全设计](2026-05-16-task-center-e2e-flow-design.md)
- **相关**：[原始素材任务库设计](2026-04-26-raw-video-pool-design.md) / [明空流水线主 spec](2026-04-26-mingkong-pipeline-master.md)

## 目标

把父任务主流程从“创建后待认领”改为“创建时直接指定原视频处理负责人并进入 `raw_in_progress`”，同时在创建成功后自动提交牛马去字幕轮询。旧 `claim` 接口保留兼容，但 UI 不再把它作为主入口。

## 范围

1. `create_parent_task()` 支持单独传入原视频处理负责人。
2. 父任务创建即写入：
   - `assignee_id = raw_processor_id`
   - `status = raw_in_progress`
   - `claimed_at = NOW()`，作为 `assigned_at` 的兼容时间戳
3. 创建父任务成功后立即触发 `task_raw_video_processing.start_niuma_processing_for_parent_task()`。
4. 任务中心创建弹窗补充“原视频处理人”选择；详情页不再把“认领”作为主操作。
5. `mk_selection` 文案同步为“指派后自动提交牛马”，但本步不做第三步“按语言独立指派不同人”。

## 不做

- 不改子任务仍由单一翻译员负责的模型。
- 不删除 `claim` 路由；仅降级为兼容入口。
- 不改部署、定时任务定义、轮询协议或结果挂载逻辑。

## 服务层设计

### `appcore.tasks.create_parent_task`

- 新参数：`raw_processor_id`
- 创建父任务时直接插入 `assignee_id`、`claimed_at`、`status='raw_in_progress'`
- 仍写 `created` 事件，payload 追加 `raw_processor_id`
- 不再发“待认领”广播，改为只通知被指派的原视频处理人
- 子任务逻辑不变：仍按 `translator_id` 物化 `blocked` 子任务

### 自动牛马提交

- 创建路由在 service 成功后调用 `start_niuma_processing_for_parent_task()`
- 若启动失败：
  - 不回滚已创建任务
  - 记录 `raw_niuma_failed`
  - API 响应带 `raw_processing={"status":"start_failed","error":...}`
- 若启动成功：
  - API 响应带 `raw_processing={"status":"submitted","subtitle_task_id":...}`

## 路由与前端

### `POST /tasks/api/parent`

- 新增必填参数：`raw_processor_id`
- 校验该用户具备 `can_process_raw_video`

### `tasks_list.html`

- 创建弹窗新增“原视频处理人”下拉
- 成功创建后父任务已在“我的任务”可见，不再依赖处理人点击“认领”
- 父任务详情中：
  - `pending` 不再是主流程常态
  - 不主动渲染“认领”按钮作为默认操作

### `mk_selection.html`

- 只改说明文案，把“认领后自动提交牛马”改成“指派后自动提交牛马”

## 测试

1. 服务层：父任务创建后的 `status / assignee_id / claimed_at / event payload`
2. 路由：`/tasks/api/parent` 要求 `raw_processor_id` 并触发牛马启动
3. 模板：创建弹窗出现原视频处理人字段；任务中心与明空页不再显示“认领后自动提交牛马”主流程文案
