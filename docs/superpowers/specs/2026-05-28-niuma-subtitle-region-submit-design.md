# 牛马去字幕手工选区提交设计

## 锚点

- `AGENTS.md`：文档驱动代码、隔离 worktree、后台路由验证要求。
- `docs/superpowers/specs/2026-04-15-subtitle-removal-design.md`：字幕移除详情页支持 `全屏去除` 与 `框选去除`，选区写入任务状态。
- `docs/superpowers/specs/2026-05-07-subtitle-removal-backend-type-design.md`：牛马是字幕移除的处理方式之一，默认入口仍选牛马；牛马不展示火山的擦除类型。
- `docs/superpowers/specs/2026-05-15-niuma-subtitle-removal-design.md`：牛马复用现有 `aiRemoveSubtitleSubmitTask` / `aiRemoveSubtitleProgress` provider/runtime，坐标已通过 `videoName` 兼容提交。

## 目标

在后台字幕移除详情页中，允许用户手工选择牛马去字幕范围。默认流程不变：新建任务进入详情页后仍默认全屏去除；用户只有主动切换到框选并在首帧上拉框时，才提交局部区域。

## 范围

1. 只影响手工进入 `/subtitle-removal/<task_id>` 的字幕移除任务。
2. 详情页恢复并展示 `全屏去除` / `框选去除` 控件、坐标提示和当前处理范围。
3. `POST /api/subtitle-removal/<id>/submit` 继续接收现有 `remove_mode` 与 `selection_box`，并把规范化选区写入 `selection_box` / `position_payload`。
4. 牛马 provider 提交时，在保留现有 `videoName` 坐标兼容的同时，对“用户手工选择的框选区域”新增 `position` 字段，内容为 `{"l":x1,"t":y1,"w":width,"h":height}` 的 JSON 字符串。
5. 火山和本地 VSR 的既有行为不变；火山继续只通过原有 payload 和擦除类型工作。

## 非目标

1. 不影响任务中心自动提交的牛马去字幕任务。自动任务仍由 `appcore/task_raw_video_processing.py` 固定生成全帧 `selection_box`，不新增人工选区、不改变任务中心状态机、不改变自动提交触发条件，也不额外下发手工 `position` 参数。
2. 不做多选区、自动识别字幕区域或默认字幕带估算。
3. 不改 DB schema；新增区域信息继续存在 `projects.state_json`。
4. 不改牛马凭据、轮询、结果回填、任务中心 watcher 与重跑逻辑。

## 数据契约

手工框选提交示例：

```json
{
  "remove_mode": "box",
  "selection_box": {"x1": 0, "y1": 1000, "x2": 720, "y2": 1180}
}
```

后端保存：

```json
{
  "remove_mode": "box",
  "selection_box": {"x1": 0, "y1": 1000, "x2": 720, "y2": 1180},
  "position_payload": {"l": 0, "t": 1000, "w": 720, "h": 180}
}
```

仅当手工任务使用 `remove_mode="box"` 时，牛马提交 payload 增加：

```json
{
  "biz": "aiRemoveSubtitleSubmitTask",
  "videoName": "task_0_0_0_1000_720_1180",
  "position": "{\"l\":0,\"t\":1000,\"w\":720,\"h\":180}"
}
```

`position` 只在 `credential_code == "niuma_main"` 且当前任务是用户手工框选时发送。默认全屏提交和任务中心自动牛马都不带这个新增字段。这样保留已验证可用的 `videoName` 坐标通道，同时接入原始资料接口支持的显式去除区域。

## 前端行为

1. 详情页默认选中 `全屏去除`，提交 payload 为 `{"remove_mode":"full"}`。
2. 用户切换 `框选去除` 后，详情页使用可播放的视频预览框，字幕是动态内容，用户需要能在播放过程中核对并调整区域。
3. 框选模式不再要求用户从空白状态手动画框。若当前任务没有保存过选区，前端自动创建一个默认矩形：按原视频坐标，矩形顶边位于视频高度约 70% 处，高度 100px，宽度默认覆盖视频宽度，并自动限制在视频边界内。
4. 默认矩形在视频预览层上可整体拖动，也可以拖动四个角缩放；视频播放过程中仍可调整该矩形。提交时继续使用同一个 `selection_box` 数据契约。
5. 手工框选预览框按竖屏视频工作流放大到 450x800 级别，窄屏下按容器自适应缩小，避免溢出。
6. 页面显示当前坐标，如 `l:0 t:1000 w:720 h:180`，便于提交前核对。
7. 任务进入 queued/running/done/error 后，页面仍展示本次保存的处理范围。
8. 牛马任务不显示火山的“擦除类型”，只显示“处理方式”和“处理范围”。

## 验证

1. Provider 测试：牛马提交在显式传入手工区域时包含 `position` JSON 字符串；火山提交不包含 `position`。
2. Runtime 测试：牛马 `_submit()` 只在 `remove_mode="box"` 时从 `selection_box` / `position_payload` 生成 `remove_region` 并传给 provider；`remove_mode="full"` 不传。
3. Route 测试：手工框选提交保存 `selection_box` 与 `position_payload`；任务中心自动牛马测试仍断言全帧 selection 且不写手工选区标记。
4. UI 测试：详情页包含全屏/框选控件、可播放视频节点、默认 70%/100px 矩形逻辑、四角缩放节点、坐标展示节点和提交按钮。
5. 回归命令：
   - `pytest tests/test_subtitle_removal_provider.py tests/test_subtitle_removal_runtime.py tests/test_subtitle_removal_routes.py tests/test_task_raw_video_processing.py tests/test_web_routes.py -q`
   - `python3 -m compileall appcore/subtitle_removal_provider.py appcore/subtitle_removal_runtime.py web/routes/subtitle_removal.py`
