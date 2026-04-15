# 中断任务自动失败恢复设计

**日期：** 2026-04-16

## 背景

当前多个模块把长任务直接跑在 Web 进程内：

- `video_creation` 使用 `eventlet.spawn(...)`
- `video_review` 使用 `eventlet.spawn(...)`
- `copywriting` 使用 `eventlet.spawn(...)`
- 主工作台、德语、法语流水线使用 `threading.Thread(...)`

服务重启后，这些线程或协程会立即消失，但 `projects.status` 和 `state_json.steps.*` 里的 `running` 不会自动回落。结果是前端一直显示“进行中”，按钮被禁用，用户无法重新发起任务。

## 目标

实现一套统一的“任务中断恢复”机制，满足以下结果：

1. 服务重启后，已经失联的 `running` 任务能被自动识别。
2. 识别到的中断任务会被自动标记为内部 `error` 状态，前端继续显示“失败”。
3. 用户可以重新点击“生成”“评估”或“继续执行”，任务不会永久卡死。
4. 已经完成的产物和中间结果保留，不因为恢复逻辑被删除。

## 非目标

- 不引入新的持久化状态枚举，例如 `failed`。
- 不把后台任务迁移到 Celery、Redis 或独立 worker。
- 不改动现有页面的整体交互模型，只修复中断后的状态恢复。

## 方案概览

新增一个公共的“任务中断恢复器”，由两部分组成：

1. **活跃任务注册表**
   - 记录当前进程里仍然存活的后台任务。
   - 每次后台任务启动前登记，结束时在 `finally` 里移除。
   - 服务重启后，该注册表天然为空，因此可以可靠识别“数据库里还在 running，但当前进程已无活任务”的孤儿任务。

2. **任务恢复服务**
   - 统一扫描支持的项目类型和任务状态。
   - 如果任务或步骤仍是 `running`，但注册表里找不到对应活任务，则判定为“已中断”。
   - 将该任务回落为内部 `error`，并写入统一的中断说明。

## 触发时机

恢复服务分两层触发，避免只修详情页：

### 1. 应用启动后全量恢复

应用初始化完成后执行一次全量扫尾：

- 扫描 `projects` 中支持的项目类型
- 找出 `status = 'running'` 或步骤仍为 `running` 的记录
- 将当前进程里不存在活任务的记录统一回落

这样服务重启后，即使用户先打开列表页，也不会看到永久转圈。

### 2. 请求期懒恢复

在关键读取与操作入口再补一次按任务懒恢复：

- 列表页数据读取前
- 详情页渲染前
- 重新生成、开始评估、继续执行、增删素材等会被 `running` 阻塞的接口前

这样即使启动恢复遗漏某条记录，用户也不会再遇到“页面一直卡住但无法操作”的情况。

## 模块覆盖范围

### `video_creation`

命中条件：

- `projects.type = 'video_creation'`
- `state.steps.generate == 'running'`
- 当前进程里不存在对应生成任务

回落规则：

- `state.steps.generate -> 'error'`
- `projects.status -> 'error'`
- 写入统一错误文案到 `state.error`

保留：

- `prompt`
- 素材路径
- `result_video_url`
- `result_video_path`
- `seedance_task_id`

这样详情页会从“生成中”切到“生成失败”，`重新生成` 和素材增删按钮重新开放。

### `video_review`

命中条件：

- `projects.type = 'video_review'`
- `state.steps.review == 'running'`
- 当前进程里不存在对应评估任务

回落规则：

- `state.steps.review -> 'error'`
- `projects.status -> 'error'`
- `state.review_started_at -> None`
- 写入统一错误文案到 `state.error`

现有 5 分钟 stale 判断继续保留，但只作为额外保险，不再承担主恢复逻辑。

### `copywriting`

命中条件：

- `projects.type = 'copywriting'`
- `state.steps` 中存在一个或多个 `running`
- 当前进程里不存在对应文案任务

回落规则：

- 所有 `running` 的步骤统一改为 `error`
- 对应 `step_messages[step]` 改为统一中断说明
- `projects.status -> 'error'`

保留：

- 已抽出的关键帧
- 已生成的文案
- 已生成的音频
- 已合成的视频

这样用户可以继续生成文案、继续 TTS/合成，或者重试当前失败步骤。

### 主工作台 / 德语 / 法语流水线

命中条件：

- `projects.type` 属于工作台流水线类型
- `task_state` / `state_json.steps` 中存在 `running`
- 当前进程里不存在对应流水线任务

回落规则：

- 所有 `running` 的步骤统一改为 `error`
- 对应 `step_messages[step]` 改为统一中断说明
- `status -> 'error'`
- `current_review_step -> ''`

保留：

- 已完成步骤的所有中间结果
- 译文、字幕、导出结果、分析结果等产物

这样用户可以直接从失败步骤 `resume`，而不是整条链路重做。

## 状态与文案策略

内部继续使用现有 `error`，不新增 `failed` 枚举，避免扩散到模板、路由和数据库判断。

统一中断文案建议为：

`任务因服务重启或后台执行中断，已自动标记为失败，请重新发起。`

模块也可以在此前缀上增加上下文，例如“重新生成”“重新评估”“从该步骤继续执行”。

## 代码结构设计

新增一个公共服务模块，例如：

- `appcore/task_recovery.py`

职责：

- 管理活跃任务注册表
- 暴露后台任务登记/清理接口
- 提供全量恢复与单任务恢复入口
- 提供不同项目类型的恢复规则适配

现有模块接入方式：

- `web/routes/video_creation.py`
- `web/routes/video_review.py`
- `web/routes/copywriting.py`
- `web/services/pipeline_runner.py`
- `web/services/de_pipeline_runner.py`
- `web/services/fr_pipeline_runner.py`
- `web/routes/task.py`
- `web/routes/de_translate.py`
- `web/routes/fr_translate.py`
- `web/app.py`

接入原则：

- 后台任务启动前调用注册接口
- 后台任务结束时在 `finally` 中调用清理接口
- 页面/API 入口在读取状态前调用单任务恢复接口
- 应用启动后调用一次全量恢复接口

## 数据恢复原则

恢复器只修正“活着的执行状态”，不修正业务内容。

允许修改的字段：

- `projects.status`
- `state.steps.*`
- `state.step_messages.*`
- `state.error`
- `state.review_started_at`
- `state.current_review_step`

不允许删除或重置的字段：

- 上传视频、图片、音频路径
- 已生成文案、译文、字幕、音频、视频
- 导出结果
- 分析结果
- 任何历史产物路径

## 测试策略

测试覆盖分为三层：

### 1. 公共恢复服务单元测试

验证：

- 活跃任务存在时，不误判为中断
- 活跃任务不存在时，正确把 `running` 步骤回落为 `error`
- 只修改 `running` 步骤，不影响 `done`、`pending`、`waiting`
- 已有产物字段被完整保留

### 2. 路由层回归测试

验证：

- `video_creation` 详情页打开时会自动修复僵尸 `running`
- `video_creation` 恢复后可重新调用 `regenerate`
- `video_review` 打开详情页后会回落为失败
- `copywriting` 详情页打开后中断步骤变为失败
- 主工作台相关接口在恢复后允许 `resume`

### 3. 启动恢复测试

验证：

- 应用启动恢复逻辑会扫描并修正孤儿 `running` 任务
- 同一任务反复恢复不会产生额外副作用

## 风险与取舍

### 不采用纯前端超时兜底

原因：

- 页面按钮看起来恢复了，但数据库仍是 `running`
- 换页或刷新后仍会卡死
- 无法从根上修复列表页与接口阻塞问题

### 不采用单模块各自修补

原因：

- 代码会继续分散在多个路由里
- 容易漏掉列表页、操作接口、后续新模块
- `video_review` 现有 stale 逻辑已经说明这种做法难以复用

### 不引入新状态 `failed`

原因：

- 当前模板和逻辑大多已经把 `error` 渲染为“失败”
- 新增枚举会带来额外联动改动和兼容风险

## 验收标准

满足以下条件即视为完成：

1. 服务重启后，原本卡在 `running` 的任务会自动显示为“失败”。
2. `video_creation`、`video_review`、`copywriting`、主工作台、德语、法语模块都覆盖到。
3. 用户能重新点击“生成”“评估”或“继续执行”。
4. 已完成产物保留，不因恢复逻辑丢失。
5. 回归测试覆盖中断恢复与按钮重新开放场景。
