# AutoVideoSrtLocal

## 当前正式契约

- 正式入口：`http://172.30.254.14/`
- 运行方式：无 nginx，`gunicorn` 直接监听 `80` 端口
- 运行目录：`/opt/autovideosrt`
- 数据目录：`/data/autovideosrt/uploads` 和 `/data/autovideosrt/output`
- MySQL 库名：`auto_video`

> 仓库说明：当前默认开发仓库是 `jinghuaswsx/AutoVideoSrtLocal`。旧服务器版仓库 `jinghuaswsx/AutoVideoSrt` 暂时保留不动，仅作为迁移参考；本地工作区默认 `origin` 应指向本仓库，旧仓库建议保留为单独备用远程（如 `server-origin`）。

> 产品名称说明：仓库已切换为本地版，但应用内产品名、页面标题和历史设计文档里仍大量使用 `AutoVideoSrt`，这些文案默认视为产品名，不等同于 GitHub 仓库名。

面向多模块短视频生产的 Flask 平台。主线能力是“上传视频 -> 识别/本土化/配音/字幕/成片/CapCut 导出”，同时还扩展了文案创作、文案翻译、视频评测、视频生成等子模块。

这份 README 的目标不是介绍产品，而是让新的开发 Agent 快速知道：

- 项目分几层
- 每类功能入口在哪
- 改一个功能应该先看哪些文件
- 哪些文档/目录是当前实现，哪些只是历史参考

## 1. 现在有哪些能力

`projects.type` 目前支持这些项目类型：

- `translation`：中文视频 -> 英文本土化视频
- `de_translate`：中文/英文视频 -> 德语视频
- `fr_translate`：中文/英文视频 -> 法语视频
- `copywriting`：基于视频关键帧和商品信息生成带货文案，再配音合成
- `text_translate`：纯文本/分段文本翻译
- `video_review`：用 Gemini/OpenRouter 对视频做质量评估
- `video_creation`：基于参考视频/关键帧 + Prompt 调用 Seedance 生成视频

主线输出通常包括：

- 中间 JSON 产物
- 预览音频 / 视频 / SRT
- 软字幕视频
- 硬字幕视频
- CapCut / 剪映工程包

## 2. 架构总览

```text
Browser / Template
  -> web/routes/*.py
  -> web/services/*.py         # 仅做 SocketIO / 线程适配
  -> appcore/runtime*.py       # 任务编排，决定步骤顺序和状态流转
  -> pipeline/*.py             # 纯处理逻辑，调用 ffmpeg / LLM / TTS / TOS / CapCut
  -> appcore/task_state.py     # 任务状态内存层 + DB 回写 / 冷启动回读
  -> appcore/db.py + db/schema.sql
```

可以把仓库理解成 4 层：

1. `web/`
   负责 HTTP 路由、页面模板、SocketIO 房间和前端工作台。
2. `appcore/`
   负责任务状态、运行时编排、数据库封装、密钥/设置/清理等应用核心逻辑。
3. `pipeline/`
   负责具体处理能力：音频提取、ASR、翻译、TTS、字幕、合成、CapCut 导出、视频评测、视频生成。
4. `db/`
   负责 schema、迁移脚本和初始化管理员。

一个重要约定：

- `web/services/*.py` 很薄，只是把 `EventBus` 事件桥接到 SocketIO，不要把业务逻辑堆进去。
- 真正的步骤编排在 `appcore/runtime.py`、`appcore/runtime_de.py`、`appcore/runtime_fr.py`、`appcore/copywriting_runtime.py`。

## 3. 目录地图

```text
main.py                         # 启动入口；创建 app、校验配置、启动定时清理
config.py                       # 环境变量、目录、外部服务配置

appcore/
  api_keys.py                   # 用户级 API Key / 额外配置读取
  cleanup.py                    # 过期项目、孤儿上传、TOS 对象清理
  copywriting_runtime.py        # 文案创作任务编排
  db.py                         # MySQL 连接池
  events.py                     # EventBus，供 runtime -> web/socketio 解耦
  runtime.py                    # 英文视频翻译主流程编排
  runtime_de.py                 # 德语翻译编排（覆盖 translate/tts/subtitle）
  runtime_fr.py                 # 法语翻译编排（覆盖 translate/tts/subtitle）
  settings.py                   # 系统设置，当前主要是保留周期
  task_state.py                 # 任务状态内存层 + DB state_json 同步
  tos_clients.py                # TOS 客户端、签名 URL、对象 key 规则
  usage_log.py                  # 调用量记录
  users.py                      # 用户 CRUD / 密码哈希

pipeline/
  extract.py                    # ffmpeg 提取音频、探测时长
  asr.py                        # 豆包 ASR 提交 / 轮询 / 解析
  alignment.py                  # 基于停顿、标点、镜头切点做分段建议
  localization.py               # 英文本土化 Prompt、TTS script 校验、字幕块重建
  localization_de.py            # 德语本土化 Prompt / 常量
  localization_fr.py            # 法语本土化 Prompt / 常量
  translate.py                  # OpenRouter / 豆包 LLM 调用封装
  tts.py                        # ElevenLabs TTS 和音频拼接
  subtitle_alignment.py         # 用目标语 ASR 结果校正字幕时间
  subtitle.py                   # 生成 SRT、断行、法语标点处理
  timeline.py                   # 生成统一时间线 manifest
  compose.py                    # 合成软字幕/硬字幕视频
  capcut.py                     # 导出 CapCut 工程、改写路径、打包 zip
  keyframe.py                   # 关键帧抽取
  copywriting.py                # 多模态文案生成 / 重写
  video_review.py               # 视频评测
  seedance.py                   # Seedance 视频生成
  voice_library.py              # 用户音色库
  elevenlabs_voices.py          # ElevenLabs Voice Library 导入

web/
  app.py                        # Flask 工厂，注册蓝图和 SocketIO 事件
  auth.py                       # Flask-Login 用户对象和 admin 装饰器
  preview_artifacts.py          # “中间产物预览协议”，前端工作台高度依赖它
  store.py                      # 对 appcore.task_state 的兼容 facade
  upload_util.py                # 上传文件扩展名校验、安全文件名
  services/
    pipeline_runner.py          # 英文翻译 runtime -> SocketIO 适配
    de_pipeline_runner.py       # 德语翻译适配
    fr_pipeline_runner.py       # 法语翻译适配
  routes/
    task.py                     # 英文翻译主 API
    de_translate.py             # 德语翻译模块
    fr_translate.py             # 法语翻译模块
    copywriting.py              # 文案创作模块
    text_translate.py           # 文案翻译模块
    video_creation.py           # 视频生成模块
    video_review.py             # 视频评测模块
    voice.py                    # 音色库 CRUD / 导入
    prompt.py                   # 用户 Prompt CRUD
    settings.py                 # 用户 API 配置 / 剪映目录
    projects.py                 # 翻译项目列表和详情页
    admin.py                    # 用户管理 / 保留周期设置
    admin_usage.py              # 调用量页面
    tos_upload.py               # TOS 直传引导接口
    auth.py                     # 登录/登出
  templates/
    _task_workbench*.html       # 翻译工作台核心 UI 片段
    *.html                      # 各模块列表页 / 详情页

db/
  schema.sql                    # 表结构
  migrate.py                    # 执行 schema.sql
  create_admin.py               # 初始化管理员
  migrations/                   # 增量 SQL

tests/                          # 以子系统划分的 pytest 用例
deploy/                         # systemd / 部署脚本
docs/superpowers/               # 设计稿、历史方案、实现计划
backup/GUI/                     # 旧桌面版原型，当前不是主实现
```

## 4. 主线翻译流程

英文翻译主线由 `appcore/runtime.py` 编排，步骤顺序固定为：

1. `extract`
2. `asr`
3. `alignment`
4. `translate`
5. `tts`
6. `subtitle`
7. `compose`
8. `export`

各步骤的大致职责：

- `extract`
  `pipeline/extract.py` 用 ffmpeg 提取 16k 单声道 wav，并生成 mp3 预览。
- `asr`
  `pipeline/asr.py` 把音频传到 TOS，再调用豆包 ASR v3。
- `alignment`
  `pipeline/alignment.py` 基于停顿、标点、镜头切点生成建议分段；可人工确认。
- `translate`
  `pipeline/localization.py` + `pipeline/translate.py` 生成目标语本土化文本。
- `tts`
  `pipeline/tts.py` 调 ElevenLabs 生成每段语音并拼接整轨。
- `subtitle`
  先对目标语音频再做一次 ASR，再用 `pipeline/subtitle_alignment.py` 校正字幕块时间。
- `compose`
  `pipeline/timeline.py` 生成时间线；`pipeline/compose.py` 合成软字幕/硬字幕视频。
- `export`
  `pipeline/capcut.py` 导出 CapCut 工程目录和 zip 包。

德语和法语模块不是独立重写一套流程，而是：

- 复用 `PipelineRunner`
- 在 `appcore/runtime_de.py` / `appcore/runtime_fr.py` 只覆盖语言相关步骤
- 语言规则放在 `pipeline/localization_de.py` / `pipeline/localization_fr.py`

## 5. Web 工作台怎么组织

翻译模块的前端不是每一步各写一套页面，而是共用一套“任务工作台”：

- 页面模板入口：`web/templates/index.html`、`web/templates/project_detail.html`
- 共享片段：`web/templates/_task_workbench.html`
- 样式与脚本：`web/templates/_task_workbench_styles.html`、`web/templates/_task_workbench_scripts.html`

前端中间产物渲染依赖 `web/preview_artifacts.py` 返回的结构化 artifact，例如：

- `utterances`
- `segments`
- `sentences`
- `tts_blocks`
- `subtitle_chunks`
- `side_by_side`
- `action`
- `download`

如果你改了 runtime 某一步的输出，通常也要同步检查：

- `web/preview_artifacts.py`
- `_task_workbench_scripts.html`
- 对应测试里的页面断言

## 6. 数据与状态模型

数据库主表是 `projects`，关键字段：

- `id`
- `user_id`
- `type`
- `display_name`
- `status`
- `task_dir`
- `state_json`
- `expires_at`
- `deleted_at`

当前状态模型是“双写”：

- 运行时优先写 `appcore.task_state` 里的进程内字典
- 每次更新再同步回 `projects.state_json`
- 冷启动时 `task_state.get()` 会回退到 DB 读取

这意味着：

- 路由层拿任务时优先走 `store.get()` / `task_state.get()`
- 任务字段变化要考虑内存态和 DB 回读兼容
- 清理逻辑依赖 `task_dir`、`video_path`、`source_tos_key`、`tos_uploads`

其他重要表：

- `users`：登录用户
- `api_keys`：用户级服务密钥和额外配置
- `user_voices`：用户音色库
- `user_prompts`：用户 Prompt
- `system_settings`：全局配置，当前主要是保留周期
- `usage_logs`：调用量统计
- `copywriting_inputs`：文案创作的商品信息

## 7. 功能改动时先看哪里

### 改翻译流程 / Prompt / 目标文案结构

- `appcore/runtime.py`
- `pipeline/localization.py`
- `pipeline/translate.py`
- `web/routes/task.py`
- `web/preview_artifacts.py`

### 改德语 / 法语规则

- `appcore/runtime_de.py` / `appcore/runtime_fr.py`
- `pipeline/localization_de.py` / `pipeline/localization_fr.py`
- `web/routes/de_translate.py` / `web/routes/fr_translate.py`

### 改 TTS / 音色推荐 / 音色管理

- `pipeline/tts.py`
- `pipeline/voice_library.py`
- `pipeline/elevenlabs_voices.py`
- `web/routes/voice.py`

### 改字幕显示或时间校正

- `pipeline/subtitle_alignment.py`
- `pipeline/subtitle.py`
- `pipeline/timeline.py`
- `pipeline/compose.py`

### 改 CapCut / 剪映导出

- `pipeline/capcut.py`
- `web/routes/task.py` 的下载和部署接口
- `appcore/api_keys.py` / `web/routes/settings.py` 里的剪映目录配置

### 改上传、任务列表、项目权限

- `web/routes/task.py`
- `web/routes/tos_upload.py`
- `web/routes/projects.py`
- `web/auth.py`
- `tests/test_security_*`、`tests/test_web_routes.py`

### 改文案创作模块

- `web/routes/copywriting.py`
- `appcore/copywriting_runtime.py`
- `pipeline/copywriting.py`
- `pipeline/keyframe.py`

### 改视频评测 / 视频生成

- `web/routes/video_review.py` + `pipeline/video_review.py`
- `web/routes/video_creation.py` + `pipeline/seedance.py`

## 8. 运行与初始化

```bash
pip install -r requirements.txt
copy .env.example .env
python db/migrate.py
python db/create_admin.py
python main.py
```

默认访问：

- 本地开发：`http://127.0.0.1:5000`

测试：

```bash
pytest tests -q
```

## 9. 测试分布

`tests/` 基本按子系统分组，常见入口：

- `test_appcore_*`：状态、事件、DB、runtime
- `test_pipeline_runner.py`：翻译主流程编排
- `test_web_routes.py`：主路由和页面工作台
- `test_capcut_export.py`：CapCut 导出
- `test_localization.py`：翻译 / TTS script 结构和规则
- `test_cleanup.py`：清理逻辑
- `test_tos_upload_routes.py`：TOS 直传

排查 bug 时优先先找同名子系统测试文件。

## 10. 当前实现的几个关键事实

- 当前翻译主流程实际只跑 `variants.normal`。
  `pipeline/localization.py` 和部分历史测试/设计稿里还能看到 `hook_cta` 痕迹，但不是当前主流程事实。
- `backup/GUI/` 是旧桌面版原型，不是当前主线产品。
- `docs/superpowers/specs/` 和 `docs/superpowers/plans/` 很有参考价值，但不少文件描述的是某个阶段方案，不一定等于当前代码。
- 代码真相优先级：`runtime / route / pipeline / tests` > `docs/superpowers/*` > `PLAN.md` / `readme_codex.md`
- 多用户隔离是硬约束。新增路由时要延续 `current_user.id` 过滤和资源归属校验。
- 翻译工作台严重依赖 artifact 协议；后端数据结构一变，前端预览通常也要一起改。

## 11. 建议的阅读顺序

如果你是第一次接手这个项目，推荐按这个顺序读：

1. `main.py`
2. `web/app.py`
3. `web/routes/task.py`
4. `appcore/runtime.py`
5. `appcore/task_state.py`
6. `pipeline/localization.py`
7. `pipeline/translate.py`
8. `pipeline/tts.py`
9. `pipeline/subtitle.py`
10. `pipeline/compose.py`
11. `pipeline/capcut.py`

看完这 11 个文件，基本就能继续开发翻译主线功能了。
