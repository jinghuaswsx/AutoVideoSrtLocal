# 字幕移除处理方式隔离设计

> 日期：2026-05-07
> 状态：已确认，进入实现
> 关联前置设计：
> - [2026-04-15-subtitle-removal-design.md](2026-04-15-subtitle-removal-design.md)
> - [2026-04-20-subtitle-removal-erase-text-type-design.md](2026-04-20-subtitle-removal-erase-text-type-design.md)

## 1. 背景与目标

字幕移除模块已经有两种处理方式：

- `火山`：云端字幕移除能力，需要将源视频放到可被云端接口访问的 TOS URL。
- `本地 VSR`：本机 SubtitleRemover/VSR 服务，直接读取本地上传文件。

这两种方式不是同一套流程的参数变体，而是两条独立处理链路。页面需要清楚地区分任务类型、上传方式、可配置项和列表筛选范围。

本次目标：

- 把处理方式筛选放到 `/subtitle-removal` 右侧内容区顶部，与“字幕移除”标题同一行，展示为胶囊按钮。
- 列表可按处理方式筛选：选 `火山` 只看火山任务，选 `本地 VSR` 只看本地 VSR 任务，两个都不选时显示全部。
- 列表每个任务都显示处理方式，明确标出 `火山` 或 `本地 VSR`。
- 新建任务时默认 `火山`，可改选 `本地 VSR`。
- 火山任务使用 TOS 上传源文件；本地 VSR 任务使用本地上传源文件。
- 本地 VSR 不展示、不提交、不保存“擦除类型”设置。

## 2. 范围与非目标

### 2.1 本期范围

- 上传 bootstrap 接口接收 `subtitle_backend`，并根据值返回不同上传 URL：
  - `volc`：返回 TOS signed PUT URL。
  - `local_vsr`：返回本地 PUT URL `/api/subtitle-removal/upload/local/<task_id>`。
- 上传 complete 接口按 `subtitle_backend` 校验源文件：
  - `volc`：源文件来自 TOS，服务端在 complete 阶段下载到本地用于首帧和媒体信息解析，同时保留 `source_tos_key`。
  - `local_vsr`：源文件来自本地 PUT，不写 `source_tos_key`。
- 任务提交时：
  - `volc` 任务确保 `source_tos_key` 存在，并继续通过 TOS 签名 URL 调云端。
  - `local_vsr` 任务不做 TOS staging，runtime 直接读取 `video_path`。
- 上传面板里 `火山` 默认选中；切换到 `本地 VSR` 时隐藏擦除类型。
- 详情页中本地 VSR 任务隐藏擦除类型，只展示处理方式。
- 列表接口支持 `subtitle_backend=volc|local_vsr` 过滤。
- 列表表格新增/展示 `处理方式` 字段。

### 2.2 非目标

- 不新增 DB schema 字段；处理方式继续放在 `projects.state_json.subtitle_backend`。
- 不做管理员默认处理方式配置。
- 不把本地 VSR 接入火山 VOD/TOS 回传链路。
- 不改变本地 VSR 的内部算法参数，仍使用现有 `local_vsr_options` 默认值。
- 不改变字幕区域选择的 `full` / `box` 语义。

## 3. 数据与 API 契约

### 3.1 state_json

`subtitle_backend` 字段继续使用：

| 字段 | 取值 | 默认 | 说明 |
| --- | --- | --- | --- |
| `subtitle_backend` | `volc` / `local_vsr` | `volc` | 处理方式 |
| `erase_text_type` | `subtitle` / `text` / 空 | `subtitle` | 仅火山任务有效；本地 VSR 应为空或忽略 |
| `source_tos_key` | string | `""` | 火山任务必须有值；本地 VSR 为空 |

### 3.2 `POST /api/subtitle-removal/upload/bootstrap`

请求 body 增加可选字段：

```json
{
  "original_filename": "source.mp4",
  "content_type": "video/mp4",
  "subtitle_backend": "volc"
}
```

返回：

```json
{
  "task_id": "...",
  "object_key": "...",
  "upload_url": "...",
  "subtitle_backend": "volc",
  "upload_backend": "tos"
}
```

规则：

- `subtitle_backend` 缺省为 `volc`。
- 非法值返回 `400`，错误信息为 `subtitle_backend must be volc or local_vsr`。
- `volc` 返回 TOS signed PUT URL，`upload_backend="tos"`。
- `local_vsr` 返回本地 PUT URL，`upload_backend="local"`。

### 3.3 `POST /api/subtitle-removal/upload/complete`

请求 body 继续携带 `subtitle_backend`，必须与 bootstrap reservation 一致。

规则：

- `volc`：
  - 校验 TOS object 存在。
  - 将 TOS object 下载到本地 `video_path`，用于 `probe_media_info` 和 `extract_thumbnail`。
  - 创建任务后写入 `source_tos_key=object_key`、`source_object_info.storage_backend="tos"`、`subtitle_backend="volc"`。
  - 保存 `erase_text_type`，默认 `subtitle`。
- `local_vsr`：
  - 校验本地 PUT 生成的 `video_path` 存在。
  - 创建任务后写入 `source_tos_key=""`、`source_object_info.storage_backend="local"`、`subtitle_backend="local_vsr"`。
  - 不保存前端传入的 `erase_text_type`，状态里保持空值。

### 3.4 `GET /api/subtitle-removal/list`

新增 query 参数：

```text
subtitle_backend=volc|local_vsr
```

过滤规则在解析 `state_json` 后执行，避免新增 DB schema。非法值返回 `400`。

每条 item 返回：

```json
{
  "subtitle_backend": "volc",
  "subtitle_backend_label": "火山"
}
```

## 4. 前端行为

### 4.1 列表页

右侧内容区顶部改为：

```text
字幕移除                                      [火山] [本地 VSR]  新建任务
```

- `火山` / `本地 VSR` 是胶囊按钮，不默认选中。
- 选中某个胶囊时给列表 API 增加 `subtitle_backend` 参数。
- 再次点击已选胶囊会取消筛选，显示全部。
- 列表行展示 `处理方式`，取值为 `火山` 或 `本地 VSR`。

### 4.2 上传面板

- 新建任务默认选中 `火山`。
- 选择 `火山` 时展示擦除类型胶囊，默认 `仅字幕`。
- 选择 `本地 VSR` 时隐藏擦除类型，上传和 complete 不提交有效擦除类型。

### 4.3 详情页

- `处理方式` 状态项继续显示。
- 如果任务是 `local_vsr`，隐藏擦除类型选择和擦除类型状态项。
- 如果任务是 `volc`，保留原擦除类型行为。

## 5. 测试要点

- bootstrap 默认 `volc`，返回 TOS signed URL。
- bootstrap `local_vsr` 返回本地 upload URL。
- bootstrap 非法 `subtitle_backend` 返回 400。
- complete `volc` 从 TOS 下载源文件到本地，创建任务时保留 `source_tos_key`。
- complete `local_vsr` 只使用本地文件，`source_tos_key` 为空。
- complete 拒绝与 reservation 不一致的 `subtitle_backend`。
- list 支持按 `subtitle_backend` 筛选，并返回中文 label。
- 本地 VSR submit 不做 public source staging。
- 火山 submit 使用已有 `source_tos_key`，缺失时才按需 staging。
