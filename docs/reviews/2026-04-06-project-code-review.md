# AutoVideoSrt 代码审查结论与建议

审查日期：2026-04-06

审查范围：
- 系统架构
- 模块结构设计
- 代码安全
- 运行环境与依赖冲突

审查方式：
- 通读项目主干代码与关键路由、运行时、配置、数据库、部署文件
- 聚焦上传下载、鉴权授权、路径处理、后台任务、第三方服务调用
- 抽样运行安全相关测试
- 对部分高风险点做了本地行为验证

## 总体结论

当前项目已经具备可运行的产品雏形，也能看出作者在“Web 层 / 运行时 / Pipeline 层”上有分层意识，并且补了一部分安全和归属校验测试。

但从代码审查角度看，项目仍存在几类需要尽快处理的问题：
- 架构边界不够稳定，存在“名义解耦、实际耦合”的情况
- 多条任务子系统并存，状态源和配置协议不统一
- 安全短板偏实质性，不只是代码风格问题
- 运行时并发模型和部署模型存在混搭，后期容易出现难复现问题

综合评价：
- 系统架构：`C+`
- 模块结构设计：`C`
- 代码安全：`D`
- 运行环境冲突：`C-`

## 分维度评估

### 1. 系统架构

优点：
- 已经有 `web/`、`appcore/`、`pipeline/` 这样的分层意图
- `PipelineRunner`、`EventBus`、`task_state` 这些概念说明作者在尝试做职责拆分
- 流程主干比较清晰，视频处理链路可读性尚可

主要问题：
- `appcore/runtime.py` 头部注明“不依赖 Flask/web”，但实际直接依赖 `web.preview_artifacts`
- 状态管理不是单一事实来源，内存态、数据库 `state_json`、路由内局部更新逻辑同时存在
- 多个业务子系统各自管理任务流转，项目逐渐变成多个“小框架”拼在一起
- 当前架构更像“单进程 MVP 累加”，而不是可持续扩展的服务边界

影响：
- 边界会逐步继续被打穿
- 多进程、多 worker、部署切换时容易出现状态不一致
- 后续新增功能时，维护者很难判断改动应该落在哪一层

### 2. 模块结构设计

优点：
- 主流程中的音频提取、ASR、翻译、TTS、字幕、合成等模块拆分较明确
- 关键安全点已有部分测试覆盖，例如归属校验、扩展名校验、配置安全校验

主要问题：
- `task`、`copywriting`、`video_creation`、`video_review` 四套任务体系实现风格不一致
- 一部分模块通过 `task_state` 管状态，另一部分直接读写数据库 `state_json`
- 配置 service 名称不统一，已经出现“保存一套 key，运行时读取另一套 key”的漂移
- 文档、测试、模板、实现之间存在命名协议分叉

影响：
- 同一类问题会在多个模块重复出现
- 修一个问题可能只能覆盖部分子系统
- 系统会越来越依赖“作者脑内约定”而不是代码约定

### 3. 代码安全

本轮最需要关注的维度。

主要问题：
- 仓库中存在生产服务器接入信息文档，且包含敏感连接信息
- 服务以 `root` 身份运行，并且公网直接暴露应用端口
- 设置页会将真实 API Key 回填到 HTML 页面
- 数据库中 API Key 以明文方式存储
- TOS 下载接口允许用户传入任意 `tos_key` 申请签名链接，未绑定项目制品清单
- TOS 直传完成接口未复用原有的视频扩展名校验
- 文件系统删除与复制操作缺少受控路径边界校验
- `FLASK_SECRET_KEY` 仅校验“非空”，占位值也能被接受
- 缺少 HTTPS、Cookie 安全属性、反向代理层保护等生产硬化配置

影响：
- 仓库泄露时会直接放大为生产入侵风险
- 用户密钥泄露风险高
- 对象存储中的非本项目对象可能被签名下载
- 高权限进程下的路径问题会放大破坏面

### 4. 运行环境与冲突

主要问题：
- Flask-SocketIO 使用 `threading` 模式，但部署却采用 Gunicorn `eventlet` worker
- 项目中同时存在 `eventlet.spawn`、`threading.Thread`、`BackgroundScheduler`
- 还叠加了同步 `requests`、子进程 `ffmpeg/ffprobe`、线程锁、数据库连接池
- 测试运行时已出现 Eventlet 弃用告警
- Node 侧只看到 Playwright 依赖，但 `package.json/package-lock.json` 被 `.gitignore` 忽略，环境难以复现
- 依赖版本多数是范围约束，不是锁定版本，线上线下行为可能漂移

影响：
- 容易出现卡住、阻塞、僵任务、线程竞争类问题
- 本地可运行不代表线上稳定
- 问题定位会越来越依赖具体机器、具体版本、具体部署方式

## 重点问题清单

按严重性排序如下。

### P0

1. 仓库包含生产接入敏感信息
- 证据：`server.md`
- 风险：一旦仓库泄露，可能直接危及生产机
- 建议：立刻轮换密码、SSH key、API key、访问令牌；把该文件从仓库与 Git 历史中清除

2. 生产服务以 `root` 运行且公网明文暴露
- 证据：`deploy/autovideosrt.service`
- 风险：任何应用层漏洞都会被高权限放大
- 建议：切换到专用低权限用户，前置 Nginx，启用 HTTPS，仅暴露反向代理端口

3. TOS 下载接口存在任意对象签名风险
- 证据：`web/routes/projects.py`
- 现象：只校验项目归属，不校验 `tos_key` 是否属于该项目
- 建议：前端只传受控 artifact id，服务端从项目状态中查允许对象，不允许用户直传 bucket key

4. API Key 明文存储且回显到前端页面
- 证据：`db/schema.sql`、`appcore/api_keys.py`、`web/templates/settings.html`
- 风险：数据库泄露、页面源码泄露、误共享屏幕均可能暴露凭据
- 建议：库内加密或外部密钥托管；页面仅显示掩码；提交表单时仅在非空时更新

### P1

5. 运行时层依赖 web 层，架构边界被打穿
- 证据：`appcore/runtime.py`
- 建议：把 preview artifact builder 下沉到中性模块，补“禁止 import web.*”的边界测试

6. 任务状态有多个事实来源
- 证据：`appcore/task_state.py`、`web/routes/video_creation.py`、`web/routes/video_review.py`
- 建议：统一任务状态存储接口，明确唯一真相源，逐步移除散落的 `_update_state()` 风格写法

7. 上传链路校验不一致
- 证据：`web/routes/tos_upload.py`
- 现象：TOS 直传完成接口接受 `.exe` 作为源文件完成建任务
- 建议：`bootstrap`、`complete`、本地直传三条链路统一复用同一套文件类型校验器

8. 递归删除/复制缺少路径白名单保护
- 证据：`appcore/cleanup.py`、`pipeline/capcut.py`
- 建议：所有递归文件操作前，先对目标路径做 `resolve()` 并校验是否位于允许根目录内

### P2

9. 并发模型混搭
- 证据：`web/extensions.py`、`web/services/pipeline_runner.py`、`web/routes/video_creation.py`、`appcore/scheduler.py`
- 建议：统一到单一并发模型；优先移除 Eventlet；任务执行与 Web 进程进一步解耦

10. 配置 service 名称已经漂移
- 证据：`web/routes/settings.py`、`appcore/runtime.py`、`web/routes/copywriting.py`、`appcore/copywriting_runtime.py`
- 现象：
- 设置页保存 `doubao_asr`，运行时读取 `volc`
- 翻译偏好保存 `translate_pref`，另一处却读取 `translate_preference`
- 建议：抽统一常量定义，并增加配置协议一致性测试

11. 秘钥与生产安全配置校验不充分
- 证据：`web/app.py`、`.env.example`
- 现象：`FLASK_SECRET_KEY=change-me-in-production` 这种占位值也能启动
- 建议：拒绝已知弱值/占位值，补 Cookie 安全属性和反向代理信任配置

### P3

12. 文档、依赖和运行约定还不够可复现
- 证据：`requirements.txt`、`.gitignore`、`package.json`
- 建议：补充更明确的运行矩阵，决定是否正式维护 Node 测试依赖，并将必要清单纳入版本控制

## 本轮已验证现象

以下问题不是纯静态推断，而是本轮已做过本地验证：

1. 安全相关测试子集可通过
- 命令：`pytest -q tests/test_security_config.py tests/test_security_ownership.py tests/test_security_upload_validation.py`
- 结果：`55 passed`

2. 全量测试未在当前超时时间内完成
- 命令：`pytest -q`
- 结果：在本轮 2 分钟窗口内未收完，不宜把“全量测试通过”作为当前结论

3. Eventlet 已出现弃用告警
- 说明：运行测试时出现 Eventlet deprecation warning

4. 设置页会把真实 API Key 渲染回页面
- 本地模拟后确认页面 HTML 中可见伪造 key

5. TOS 直传完成接口接受非视频扩展名
- 本地模拟 `demo.exe` 可成功完成任务创建

6. TOS 下载接口可为任意对象 key 生成签名链接
- 本地模拟传入任意对象路径后，接口返回跳转签名 URL

7. 占位 `FLASK_SECRET_KEY` 会被接受
- 本地设置 `change-me-in-production` 后应用仍可正常创建

## 整改建议

建议分三步推进。

### 第一阶段：先止血

- 移除并清理 `server.md` 及历史敏感信息
- 轮换所有生产敏感凭据
- 停止使用 `root` 运行服务
- 修复任意 `tos_key` 签名问题
- 停止在前端回显真实 API Key

### 第二阶段：补安全边界

- 统一上传文件校验
- 为文件系统危险操作增加目录边界校验
- 拒绝弱 secret / 占位 secret
- 增加 HTTPS、Cookie 安全配置、反向代理层
- 梳理对象存储访问权限边界

### 第三阶段：做结构收敛

- 统一任务状态管理与持久化策略
- 统一 service 名称、配置键、用户偏好协议
- 去掉 Eventlet，统一后台任务执行模型
- 明确 `web / appcore / pipeline` 的依赖方向
- 把新增测试补到“边界约束”和“配置一致性”层面

## 最终判断

这个项目不是“不能用”，但目前更接近“功能堆叠中的可运行版本”，还没有到“可放心长期演进”的状态。

如果只做一件事，优先处理安全问题。

如果要做两件事，第二件就处理架构收敛。

在当前阶段，最不建议继续无约束地往上叠新模块，否则后面每新增一个功能，治理成本都会更高。
