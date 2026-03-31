# Step Preview Design

日期：2026-03-31

## 背景

当前页面只展示步骤状态，不展示每一步的关键中间产物。用户需要在同一页面里直接查看或试听当前步骤的产出：

- 文本可看
- 音频可听
- 视频可播

目标是在不打断现有流程的前提下，把每个步骤升级为“状态 + 关键预览”的结构。

## 方案

采用“步骤卡片自带预览区”的方案。

- 当前正在执行的步骤自动展开预览区
- 已完成步骤保留关键产出，允许继续回看
- 等待确认步骤继续保留原有编辑/确认面板
- 页面不再只展示纯状态，而是让步骤本身成为主要工作区

## 每步展示内容

1. 音频提取
- 展示提取后的音频播放器
- 附带音频文件名或时长提示

2. 语音识别
- 展示 ASR utterances 列表
- 每段显示时间范围和识别文本

3. 分段确认
- 展示 scene cuts 摘要
- 展示当前 script segments 预览
- 保留 break_after 编辑与确认能力

4. 翻译本土化
- 展示原文和译文
- 保留现有译文编辑 textarea

5. 语音生成
- 展示整条 TTS 音频播放器
- 可选展示分段音频列表和对应译文

6. 字幕生成
- 展示 SRT 文本预览

7. 视频合成
- 展示软字幕视频播放器
- 展示硬字幕视频播放器
- 合成中时显示“等待生成”的占位状态

8. CapCut 导出
- 展示导出 zip 下载入口
- 展示导出 manifest 文本预览

## 后端设计

新增任务预览数据层，不额外引入数据库。

- 在任务状态里新增 `artifacts` 字段，集中记录每个步骤可预览的关键产物
- `GET /api/tasks/{id}` 返回当前任务的 `artifacts`
- 新增只读预览接口：
  - `GET /api/tasks/{id}/artifact/<name>`
- 接口只允许访问白名单产物，不允许任意文件路径读取

建议白名单：

- `source_video`
- `audio_extract`
- `tts_full_audio`
- `tts_segment_<index>`
- `subtitle_srt`
- `soft_video`
- `hard_video`
- `capcut_manifest`

文本类内容优先直接挂在任务 JSON 中，避免额外下载：

- `asr_utterances`
- `alignment_preview`
- `translate_preview`
- `subtitle_preview`
- `capcut_manifest_preview`

媒体类内容走 artifact 路由，供 `<audio>` / `<video>` / 下载按钮直接使用。

## 前端设计

保持现有步骤纵向布局，但每个步骤卡片增加一个 `step-preview` 区域。

- `running` 步骤自动展开
- `done` 步骤如果已有产物则显示预览
- `pending` / `waiting` 没有产物时显示占位文案
- 预览类型按内容自动渲染：
  - text -> `<pre>` / 列表
  - audio -> `<audio controls>`
  - video -> `<video controls playsinline>`
  - download -> 链接按钮

现有单独面板不删除，但职责调整：

- `alignmentReview` 继续承担分段确认
- `translateReview` 继续承担译文确认
- `resultPanel` 缩减为最终下载汇总

## 数据流

1. 后端每完成一个步骤，就更新任务 `artifacts`
2. WebSocket 继续推送步骤状态
3. 前端在收到关键事件后，直接拉取最新任务详情
4. 前端根据 `artifacts` 重绘对应步骤预览区

这样可以避免为每一步新增过多专用事件，降低前后端耦合。

## 错误处理

- 某步骤失败时，保留已生成的预览产物，不清空页面
- artifact 路由遇到文件不存在时返回 404
- 前端预览区显示“产物未就绪”而不是空白
- 文本预览过长时限制高度并允许滚动

## 测试

后端：

- 任务详情接口返回 `artifacts`
- artifact 白名单路由可读媒体文件，非法名称返回 404
- 各步骤执行后会把关键预览信息写入任务状态

前端：

- 关键步骤可根据任务详情渲染文本 / 音频 / 视频预览
- 当前运行步骤会自动展开预览区
- 历史已完成步骤在刷新页面后仍能回看同一轮任务的预览

## 范围控制

本次只做单任务页面内预览，不做：

- 多任务历史列表
- 持久化任务恢复
- 波形编辑器
- 富媒体时间线编辑
