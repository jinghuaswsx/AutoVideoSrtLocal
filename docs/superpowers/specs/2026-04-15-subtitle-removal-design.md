# 字幕移除轻量模块设计

> 日期：2026-04-15
> 状态：已与用户确认，待进入实现计划

## 1. 背景与目标

AutoVideoSrt 现有模块以“视频翻译”“文案创作”“视频评分”为主，还没有一个针对“去字幕”的轻量闭环。用户的目标很明确：

- 上传一个视频
- 在视频首帧上选择去除区域
- 提交第三方去字幕任务
- 由服务端在后台持续轮询第三方状态
- 处理成功后下载结果视频并回传到我们自己的 TOS
- 前端页面只负责展示状态与结果，不承担第三方轮询职责

本次设计选择一个轻量但可靠的方向：新增独立的“字幕移除”模块，只做上传页和单任务详情页，不做历史列表。

## 2. 范围与非目标

### 2.1 本期范围

- 新增左侧导航入口“字幕移除”
- 新增轻量上传页 `/subtitle-removal`
- 新增单任务详情页 `/subtitle-removal/<task_id>`
- 支持两种去除模式：
  - `全屏去除`
  - `框选去除`
- 上传源视频到 TOS
- 服务端提交第三方去字幕任务并后台轮询
- 服务端下载结果视频并上传回我方 TOS
- 详情页展示进度、错误、结果预览、结果下载
- 服务重启后恢复未完成轮询任务

### 2.2 非目标

- 不做历史任务列表
- 不做批量任务提交
- 不做自动识别字幕区域
- 不做多框选区
- 不使用第三方 `notifyUrl`
- 不做去字幕后再次编辑或二次加工

## 3. 方案选型

### 3.1 备选方案

1. 独立轻量模块：上传页 + 详情页，无历史列表
2. 单页即传即跑，不保留详情页
3. 挂到现有“视频翻译”工作台，作为一种新模式

### 3.2 选择结果

采用方案 1。

原因：

- 用户明确要求“先做个轻量级”，但仍需要刷新后可继续查看任务状态与下载结果
- 与“视频翻译”工作台的步骤差异很大，硬塞进现有工作台会带来过多条件分支
- 相比“即传即跑”，详情页可以承接框选确认、处理中状态、错误重试和结果下载，整体更稳

## 4. 信息架构与入口

### 4.1 导航入口

在 [web/templates/layout.html](/abs/path/g:/Code/AutoVideoSrt/web/templates/layout.html) 左侧导航新增菜单：

- 文案：`字幕移除`
- 图标：`🧽`

选择 `🧽` 的原因：

- 现有导航图标统一采用简洁 emoji，风格轻量直接
- “海绵/清理”与“去除字幕、擦掉画面文字”的语义最贴近
- 与现有 `🎬 / ✍️ / 🌐 / 📊 / 📚 / ✨ / ⚙️` 保持同一视觉语言

### 4.2 页面结构

- `GET /subtitle-removal`
  - 轻量上传页
  - 仅负责上传视频并创建任务
- `GET /subtitle-removal/<task_id>`
  - 单任务详情页
  - 负责框选、提交、展示进度、展示结果

本期不新增列表页；用户通过导航进入上传页，通过上传后重定向或详情页直链进入工作页。

## 5. 页面交互设计

### 5.1 上传页

上传页布局保持与现有轻量模块一致：

- 页面标题：`字幕移除`
- 中央上传区：拖拽或点击上传视频
- 说明文案：
  - 上传后将自动提取首帧
  - 支持“全屏去除”与“框选去除”
  - 第三方单任务最长支持 600 秒

上传完成后：

1. 浏览器将源视频直传到 TOS
2. 调用模块自己的 `complete` 接口创建项目
3. 跳转到 `/subtitle-removal/<task_id>`

### 5.2 详情页

详情页只保留四个主区块：

1. 源视频信息
   - 文件名
   - 时长
   - 分辨率
   - 文件大小
   - 视频首帧图

2. 去除模式
   - 单选：`全屏去除`
   - 单选：`框选去除`
   - 如果选择 `框选去除`，在首帧图上拉框

3. 任务进度
   - 本地步骤状态
   - 第三方状态
   - 最近更新时间
   - 错误信息

4. 结果区
   - 成功后显示结果视频预览
   - 显示下载按钮
   - 保留本次去除区域信息，便于核对

### 5.3 框选交互

首帧图区域提供一个简单的矩形框选层：

- 鼠标按下开始拉框
- 拖拽更新矩形
- 鼠标抬起完成
- 可重新拉框覆盖上次结果

框选模式下：

- 未画框前不能提交
- 设定最小框尺寸，避免误点
- 坐标自动裁剪到视频边界范围内

全屏模式下：

- 不显示框选层
- 直接使用整个视频尺寸作为去除区域

## 6. 数据模型

### 6.1 projects.type

`projects.type` 新增一种业务值：

- `subtitle_removal`

本期不新增业务表，任务状态继续写入 `projects.state_json`。

### 6.2 state_json 结构

建议新增一个专用创建函数，例如 `task_state.create_subtitle_removal(...)`，初始状态如下：

```json
{
  "id": "uuid",
  "type": "subtitle_removal",
  "status": "uploaded",
  "video_path": "uploads/...",
  "task_dir": "outputs/...",
  "original_filename": "demo.mp4",
  "display_name": "demo",
  "thumbnail_path": "outputs/.../thumbnail.jpg",
  "source_tos_key": "browser-upload/...",
  "source_object_info": {
    "file_size": 2191360,
    "content_type": "video/mp4",
    "uploaded_at": "2026-04-15T21:00:00"
  },
  "media_info": {
    "width": 720,
    "height": 1280,
    "resolution": "720x1280",
    "duration": 10.0,
    "file_size_mb": 2.09
  },
  "steps": {
    "prepare": "pending",
    "submit": "pending",
    "poll": "pending",
    "download_result": "pending",
    "upload_result": "pending"
  },
  "step_messages": {},
  "remove_mode": "",
  "selection_box": null,
  "position_payload": null,
  "provider_task_id": "",
  "provider_status": "",
  "provider_emsg": "",
  "provider_result_url": "",
  "provider_raw": {},
  "poll_attempts": 0,
  "last_polled_at": null,
  "result_video_path": "",
  "result_tos_key": "",
  "result_object_info": {},
  "error": ""
}
```

### 6.3 建议持久化字段

以下字段必须写入状态，保证前端断开、服务重启后仍能恢复：

- `remove_mode`
- `selection_box`
- `position_payload`
- `provider_task_id`
- `provider_status`
- `provider_emsg`
- `provider_result_url`
- `provider_raw`
- `poll_attempts`
- `last_polled_at`
- `result_video_path`
- `result_tos_key`
- `result_object_info`

## 7. 路由与接口设计

建议新增 [web/routes/subtitle_removal.py](/abs/path/g:/Code/AutoVideoSrt/web/routes/subtitle_removal.py)。

### 7.1 页面路由

- `GET /subtitle-removal`
  - 轻量上传页
- `GET /subtitle-removal/<task_id>`
  - 详情页

### 7.2 上传接口

虽然源视频仍然走 TOS 浏览器直传策略，但为了避免复用 `/api/tos-upload/complete` 时误创建“视频翻译”任务，本模块新增同构接口：

- `POST /api/subtitle-removal/upload/bootstrap`
  - 返回 `{ task_id, object_key, upload_url, ... }`
- `POST /api/subtitle-removal/upload/complete`
  - 校验 object 存在
  - 创建 `subtitle_removal` 项目
  - 提取首帧和媒体信息
  - 返回 `{ task_id }`

其内部仍复用：

- `tos_clients.build_source_object_key`
- `tos_clients.generate_signed_upload_url`
- `tos_clients.head_object`

### 7.3 详情页接口

- `GET /api/subtitle-removal/<task_id>`
  - 获取完整任务状态
- `POST /api/subtitle-removal/<task_id>/submit`
  - 保存 `remove_mode` 与坐标
  - 提交第三方任务
  - 启动后台轮询
- `POST /api/subtitle-removal/<task_id>/resubmit`
  - 清理旧的第三方任务信息
  - 允许重新选择模式后再次提交
- `POST /api/subtitle-removal/<task_id>/resume-poll`
  - 基于已有 `provider_task_id` 继续后台轮询
- `GET /api/subtitle-removal/<task_id>/artifact/source`
  - 返回源视频首帧图
- `GET /api/subtitle-removal/<task_id>/artifact/result`
  - 返回结果视频预览流
- `GET /api/subtitle-removal/<task_id>/download/result`
  - 结果下载入口
- `DELETE /api/subtitle-removal/<task_id>`
  - 软删除任务，并清理相关 TOS 对象

### 7.4 SocketIO

页面不参与第三方轮询，但可以像现有模块一样订阅任务更新，减少前端主动刷新频率。

建议新增：

- `join_subtitle_removal_task`
- `sr_step_update`
- `sr_done`
- `sr_error`

前端只订阅我方状态变化，不直接轮询第三方。

## 8. 第三方接口封装

建议新增独立 provider 客户端，例如：

- [appcore/subtitle_removal_provider.py](/abs/path/g:/Code/AutoVideoSrt/appcore/subtitle_removal_provider.py)

职责：

- 读取配置中的第三方地址与授权 token
- 封装“提交任务”请求
- 封装“查询进度”请求
- 做统一错误转换

### 8.1 提交接口

- URL：`https://goodline.simplemokey.com/api/openAi`
- Method：`POST`
- Header：`authorization: GOLDEN_xxx`
- Body：

```json
{
  "biz": "aiRemoveSubtitleSubmitTask",
  "fileSize": 15.2,
  "duration": 10,
  "resolution": "720x1280",
  "videoName": "sr_<task_id>_0_0_720_1280",
  "coverUrl": "",
  "url": "https://tos-signed-url-or-public-url",
  "notifyUrl": ""
}
```

### 8.2 查询进度接口

- URL：`https://goodline.simplemokey.com/api/openAi`
- Method：`POST`
- Body：

```json
{
  "biz": "aiRemoveSubtitleProgress",
  "taskId": "<provider_task_id>"
}
```

### 8.3 状态映射

第三方状态与本地状态映射：

- `waiting` -> 本地 `poll: running`
- `doing` -> 本地 `poll: running`
- `success` -> 进入 `download_result`
- `failed` -> 本地 `error`

## 9. 坐标与提交参数设计

### 9.1 坐标换算规则

前端框选坐标必须基于“图片实际渲染尺寸”而不是外层容器尺寸。

换算公式：

- `x_original = round(x_display / image_display_width * source_width)`
- `y_original = round(y_display / image_display_height * source_height)`

最终得到：

- `x1`
- `y1`
- `x2`
- `y2`

并同时保存结构化区域：

```json
{
  "l": 0,
  "t": 0,
  "w": 720,
  "h": 1280
}
```

### 9.2 两种模式

- `全屏去除`
  - `x1=0`
  - `y1=0`
  - `x2=source_width`
  - `y2=source_height`
- `框选去除`
  - 使用用户框选后的真实坐标

### 9.3 videoName 生成规则

第三方文档中真正关键的是尾部区域串 `x1_y1_x2_y2`。

本地统一生成：

- `sr_<local_task_id>_<x1>_<y1>_<x2>_<y2>`

这样做的目的：

- 便于从第三方记录反查本地任务
- 尾部坐标仍完全符合第三方要求

## 10. 文件流与 TOS 设计

### 10.1 源视频

源视频始终先上传到我方 TOS，不走应用服务器中转。

任务创建后：

- 写入 `source_tos_key`
- 写入 `source_object_info`
- 在本地任务目录保留一份下载后的源视频路径，供提取首帧和必要分析

### 10.2 首帧提取

复用 [pipeline/ffutil.py](/abs/path/g:/Code/AutoVideoSrt/pipeline/ffutil.py) 中的 `extract_thumbnail(...)`：

- 上传完成后立即从源视频提取第一帧
- 存为 `thumbnail.jpg`
- 详情页直接展示该图

第一版不支持按时间轴选帧，只使用首帧。

### 10.3 结果视频

轮询成功后：

1. 服务端下载第三方 `resultUrl` 到任务目录，例如 `result.cleaned.mp4`
2. 上传到我方 TOS 成品路径
3. 保存 `result_tos_key`
4. 详情页预览和下载优先走我方稳定路径

建议复用：

- `tos_clients.build_artifact_object_key(user_id, task_id, "subtitle_removal", "result.cleaned.mp4")`

### 10.4 结果下载策略

参考现有 artifact 下载策略：

- 如果本地结果文件存在，则本地直出并支持 Range
- 如果本地文件缺失但 `result_tos_key` 存在，则跳转到 TOS 签名 URL

这样即使本地缓存被清理，任务详情页仍能恢复下载能力。

## 11. 后台运行时设计

建议新增：

- [appcore/subtitle_removal_runtime.py](/abs/path/g:/Code/AutoVideoSrt/appcore/subtitle_removal_runtime.py)

该 runner 负责完整执行链路：

1. `prepare`
2. `submit`
3. `poll`
4. `download_result`
5. `upload_result`

### 11.1 状态流转

本地步骤状态机：

- `prepare`
- `ready`
- `submit`
- `poll`
- `download_result`
- `upload_result`
- `done`
- `error`

说明：

- `ready` 是一个等待用户确认的业务状态，不必作为耗时执行步骤单独跑线程
- 真正的后台线程从 `submit` 开始

### 11.2 轮询策略

- 提交成功后立即开始轮询
- 前 1 分钟每 8 秒轮询一次
- 之后每 15 秒轮询一次
- 网络异常时不立刻失败，而是继续重试
- 只有第三方明确返回 `failed` 才判定为业务失败

### 11.3 重试接口

失败后提供两类动作：

- `重新提交`
  - 适用于提交失败或区域想重选
  - 清理旧 provider 信息后重新走提交流程
- `继续轮询`
  - 适用于已经有 `provider_task_id`，但本地轮询中断
  - 不重新提交第三方，只恢复轮询

## 12. 服务重启恢复

服务端必须保证“页面关闭不影响后台处理”。

实现方式：

- 在 [web/app.py](/abs/path/g:/Code/AutoVideoSrt/web/app.py) 应用启动阶段增加一个恢复入口
- 启动后扫描 `projects` 表中 `type='subtitle_removal'` 且状态仍在以下集合内的任务：
  - `submit`
  - `poll`
  - `download_result`
  - `upload_result`
- 根据当前状态重新挂到后台线程继续执行

恢复规则：

- 如果还没有 `provider_task_id`，不自动继续，保留错误或等待人工重提
- 如果已有 `provider_task_id` 且状态处于 `poll`，则直接恢复轮询
- 如果已轮询成功但结果尚未下载，则从 `download_result` 继续
- 如果结果已下载但未上传回我方 TOS，则从 `upload_result` 继续

## 13. 前端状态展示

详情页对用户可见的状态只保留少量高信号信息：

- 当前步骤
- 第三方状态
- 最近更新时间
- 错误信息

成功状态：

- 展示处理后视频
- 提供下载按钮
- 展示本次去除模式和区域信息

失败状态：

- 展示失败步骤
- 展示第三方 `status`
- 展示第三方 `emsg`
- 展示是否已拿到 `provider_task_id`
- 提供“重新提交”和“继续轮询”两个按钮

## 14. 代码组织建议

建议新增或调整如下文件：

- `appcore/subtitle_removal_provider.py`
- `appcore/subtitle_removal_runtime.py`
- `web/routes/subtitle_removal.py`
- `web/templates/subtitle_removal_upload.html`
- `web/templates/subtitle_removal_detail.html`
- `web/templates/_subtitle_removal_styles.html`
- `web/templates/_subtitle_removal_scripts.html`

建议调整如下文件：

- [web/app.py](/abs/path/g:/Code/AutoVideoSrt/web/app.py)
  - 注册蓝图
  - 注册 websocket join 事件
  - 调用未完成任务恢复逻辑
- [web/templates/layout.html](/abs/path/g:/Code/AutoVideoSrt/web/templates/layout.html)
  - 新增导航入口与图标
- [appcore/settings.py](/abs/path/g:/Code/AutoVideoSrt/appcore/settings.py)
  - `PROJECT_TYPE_LABELS` 增加 `subtitle_removal`
- [appcore/task_state.py](/abs/path/g:/Code/AutoVideoSrt/appcore/task_state.py)
  - 增加 `create_subtitle_removal(...)`
  - 增加与本模块匹配的步骤默认值

## 15. 错误处理

### 15.1 提交前校验

- 上传文件必须是支持的视频格式
- 视频时长超过 600 秒直接拦截
- `框选去除` 模式下未框选时禁止提交
- 框选区域小于最小阈值时禁止提交

### 15.2 提交阶段错误

- 第三方接口 4xx/5xx
- token 缺失
- `url / resolution / duration / videoName` 参数非法

处理方式：

- `submit` 置为 `error`
- 将错误写入 `error`、`provider_emsg`
- 前端显示“重新提交”

### 15.3 轮询阶段错误

- 网络超时
- 第三方短时不可用
- 返回数据格式不完整

处理方式：

- 保持在 `poll`，记录尝试次数与最近错误
- 达到内部最大容错阈值后再进入 `error`

### 15.4 结果处理错误

- `resultUrl` 下载失败
- 上传 TOS 失败

处理方式：

- 明确区分失败阶段
- 支持从 `download_result` 或 `upload_result` 继续恢复

## 16. 测试设计

### 16.1 路由与页面

- 上传页可正常渲染
- 详情页可正常渲染
- 导航中出现 `字幕移除` 菜单和 `🧽` 图标

### 16.2 提交参数

- `全屏去除` 模式生成正确的 `videoName`
- `框选去除` 模式生成正确的 `videoName`
- 坐标换算结果正确

### 16.3 状态机

- `waiting / doing / success / failed` 能正确映射到本地状态
- 网络异常不会立即导致业务失败

### 16.4 结果处理

- `success` 后能下载结果视频
- 结果视频能回传到 TOS
- 下载接口能在“本地文件存在”和“仅剩 TOS key”两种情况下工作

### 16.5 恢复能力

- 模拟任务停在 `poll`
- 重启服务后能自动继续轮询

## 17. 验收标准

- 用户可从新菜单 `字幕移除` 进入上传页
- 上传后能在详情页看到首帧图
- 可在详情页选择 `全屏去除` 或 `框选去除`
- 提交后关闭页面不影响后台继续处理
- 第三方成功后，详情页可稳定预览并下载结果
- 服务重启后，未完成任务会自动恢复
- 失败任务可区分“重新提交”和“继续轮询”

