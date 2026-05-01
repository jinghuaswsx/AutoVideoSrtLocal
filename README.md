# AutoVideoSrtLocal

AutoVideoSrtLocal 是一个带货短视频本土化与素材运营平台，基于 Flask、Socket.IO、MySQL 和一组视频/图片/LLM 处理流水线构建。系统覆盖从素材入库、多语言翻译、配音、字幕、合成，到素材推送、订单回流和 ROAS 分析的完整链路。

> 默认开发仓库：`jinghuaswsx/AutoVideoSrtLocal`。旧服务器版仓库 `jinghuaswsx/AutoVideoSrt` 仅作迁移参考。
>
> 本轮系统审视的问题清单见 [docs/project-audit-2026-05-01.md](docs/project-audit-2026-05-01.md)。

## 运行环境

| 环境 | 地址 | 目录 | 服务 |
| --- | --- | --- | --- |
| 线上 | `http://172.30.254.14/` | `/opt/autovideosrt` | `autovideosrt.service` |
| 测试 | `http://172.30.254.14:8080/` | `/opt/autovideosrt-test` | `autovideosrt-test.service` |
| 本地开发 | `http://127.0.0.1:5000` | 当前工作区 | `python main.py` |

关键约束：

- 数据库验证默认走测试服务器 MySQL，不在 Windows 开发机本地安装、启动或依赖 MySQL。
- 线上发布含义固定为：提交代码，合并主干，发布到线上环境；默认先在测试环境验证。
- 大改动必须走 worktree 隔离，`master` 只做用户明确要求的紧急 hotfix。
- 新增或调整任何调度任务，必须同步登记到 Web 后台“定时任务”模块，即 `appcore/scheduled_tasks.py`。

## 系统架构设计

```text
浏览器 / 管理后台
  -> web/routes/*.py            HTTP、JSON API、Socket.IO 房间入口
  -> web/services/*.py          后台 runner 与 Socket.IO 的薄适配层
  -> appcore/runtime*.py        任务编排、状态流转、业务服务
  -> pipeline/*.py              具体媒体处理能力：ASR、翻译、TTS、字幕、合成
  -> appcore/db.py              MySQL 连接池
  -> db/schema.sql + migrations 数据模型与增量迁移

外部能力：
  MySQL、火山 TOS/VOD、OpenRouter、Google Gemini/Vertex、豆包、ElevenLabs、
  Seedream、Seedance、APIMART、店小秘、Meta、Shopify、ffmpeg、剪映/CapCut。
```

### 分层职责

| 层 | 目录 | 职责 |
| --- | --- | --- |
| Web 层 | `web/` | Flask 应用、模板、前端静态资源、登录权限、Socket.IO、业务路由 |
| 业务编排层 | `appcore/` | 任务状态、运行时编排、素材库、推送、账单、调度、配置、存储 |
| 媒体处理层 | `pipeline/` | 音频提取、ASR、翻译封装、TTS、字幕、视频合成、CapCut 导出 |
| 数据层 | `db/` | 初始化 schema、增量迁移、管理员初始化 |
| 工具与部署 | `tools/`、`scripts/`、`deploy/` | 桌面工具、运维脚本、systemd/gunicorn 配置 |
| 测试与设计 | `tests/`、`docs/` | pytest 覆盖、设计稿、实施计划、运行文档 |

当前部署是**单进程多线程**模型：Gunicorn `gthread` 单 worker，Socket.IO 使用 `threading` async mode，后台任务主要在 Web 进程内线程和 APScheduler 中执行。这使得系统部署简单，但也意味着内存任务状态、Socket.IO 房间和后台任务都绑定到单个进程。

## 核心数据与状态

核心任务以 `projects` 表为入口，关键字段包括：

- `id`：任务/项目 ID
- `user_id`：资源归属用户
- `type`：业务类型，如 `translation`、`multi_translate`、`image_translate`
- `status`：项目级状态
- `task_dir`：本地工作目录
- `state_json`：任务运行态、步骤状态、产物索引和前端所需上下文
- `expires_at` / `deleted_at`：保留期和软删除

任务运行时主要通过 `appcore.task_state` 读写进程内状态，并同步回 `projects.state_json`。冷启动时可以从数据库回读状态；启动恢复逻辑默认只做状态标记，少数异步上游任务会尝试继续轮询。

素材运营链路的长期数据主要落在 `media_products`、`media_items`、`media_copywritings`、`media_raw_sources`、`tasks`、`pushes` 相关迁移表中。`tasks` 表承担“任务中心”的父子任务模型，`projects` 表继续承担媒体处理型项目的运行态容器。

## 各模块功能

| 模块 | 主要文件 | 功能 |
| --- | --- | --- |
| 启动与应用工厂 | `main.py`、`web/app.py` | 校验配置、执行启动迁移、创建 Flask app、注册蓝图、启动调度器 |
| 用户与权限 | `web/auth.py`、`appcore/users.py`、`web/routes/admin.py` | 登录、角色、权限、后台用户管理 |
| 视频翻译主线 | `appcore/runtime.py`、`web/routes/task.py`、`web/services/pipeline_runner.py` | 中文/多源视频到目标语言的提取、ASR、翻译、配音、字幕、合成、导出 |
| 多语种翻译 | `appcore/runtime_multi.py`、`runtime_omni.py`、`runtime_ja.py`、对应 routes | 多语言、全能翻译、日语等分支流程 |
| 批量翻译 | `appcore/bulk_translate_*.py`、`web/routes/bulk_translate.py` | 批量计划、调度、进度投影、失败重试、补偿 |
| 素材库 | `appcore/medias.py`、`web/routes/medias.py` | 商品素材、主图、详情图、视频、文案、小语种资产、AI 评估结果 |
| 推送管理 | `appcore/pushes.py`、`web/routes/pushes.py` | 推送就绪度检查、素材推送状态、外部站点同步 |
| 任务中心 | `appcore/tasks.py`、`web/routes/tasks.py` | 父任务/子任务、认领、审核、打回、完成状态机 |
| 图片翻译 | `appcore/image_translate_runtime.py`、`web/routes/image_translate.py` | 商详图本土化、并行/串行处理、失败重试、ZIP 下载 |
| 字幕擦除 | `appcore/subtitle_removal_runtime*.py`、`web/routes/subtitle_removal.py` | goodline 或火山 VOD 字幕擦除、轮询、结果回填 |
| 链接对照 | `appcore/link_check_*.py`、`web/routes/link_check.py`、`link_check_desktop/` | 落地页图片与素材一致性检查，桌面端辅助采集 |
| 文案与标题 | `appcore/copywriting_runtime.py`、`pipeline/copywriting.py`、`web/routes/copywriting*.py`、`title_translate.py` | 商品文案、广告文案、标题翻译和改写 |
| 视频评测与生成 | `appcore/material_evaluation.py`、`pipeline/video_review.py`、`pipeline/seedance.py`、`web/routes/video_*.py` | AI 素材评估、视频打分、Seedance 生成 |
| 订单与数据分析 | `appcore/order_analytics.py`、`web/routes/order_analytics.py`、`tools/roi_hourly_sync.py` | 店小秘订单、Meta 广告、真实 ROAS、日/周/月分析 |
| LLM/ASR/TTS 配置 | `appcore/llm_client.py`、`llm_use_cases.py`、`llm_bindings.py`、`llm_provider_configs.py`、`asr_router.py` | UseCase 到 provider/model 的绑定、供应商凭据、调用量与计费 |
| 定时任务 | `appcore/scheduler.py`、`appcore/scheduled_tasks.py` | APScheduler、systemd、cron、Windows 任务的统一登记和控制视图 |
| 存储与备份 | `appcore/tos_clients.py`、`tos_backup_storage.py`、`tos_backup_job.py` | 火山 TOS、签名 URL、本地文件灾备、数据库 dump |
| 独立工具 | `tools/shopify_image_localizer/`、`link_check_desktop/`、`AutoPush/` | Shopify 图片替换、链接检查桌面端、推送辅助工具 |

## 核心业务流程

### 视频翻译

```text
上传源视频
  -> 提取音频与元数据
  -> ASR 识别或同语言规整
  -> 分段/对齐/人工确认
  -> LLM 本土化翻译
  -> 音色匹配与 TTS 配音
  -> 字幕生成与二次校正
  -> 视频合成
  -> CapCut/剪映项目导出
  -> 可选 AI 质量评估
```

主要产物写入任务工作目录，同时通过 `state_json.artifacts`、`preview_files`、`exports` 暴露给前端工作台。前端预览协议由 `web/preview_artifacts.py` 维护。

### 素材运营

素材库以商品为中心管理英文源素材和多语言本土化素材。产品素材可以进入图片翻译、视频翻译、文案生成、链接对照、推送管理和任务中心。推送前会检查封面、视频、文案、商品链接、AI 评估等就绪条件。

### LLM 统一调用

新代码应优先通过 `appcore.llm_client.invoke_chat()` 或 `invoke_generate()` 调用模型。调用路径分三层：

| 层 | 职责 | 文件 |
| --- | --- | --- |
| UseCase | 定义业务能力、默认 provider/model、计费单位 | `appcore/llm_use_cases.py` |
| Binding | 管理员可覆盖 use_case 到 provider/model 的绑定 | `appcore/llm_bindings.py`、`llm_use_case_bindings` |
| Adapter | 适配 OpenRouter、豆包、Gemini AI Studio、Vertex、Vertex ADC | `appcore/llm_providers/` |

供应商 API key、base_url、model_id 存储在 `llm_provider_configs`，由 `/settings?tab=providers` 管理。`.env` 只保留数据库、TOS/VOD、服务地址、文件路径和少量运行参数。

### 调度任务

Web 进程内 APScheduler 会注册清理、素材评估、推送质量检查、封面回填、TOS 备份、VOD 字幕擦除轮询等任务。服务器上还存在 systemd timer、cron 和 Windows 计划任务；这些必须同步登记在 `appcore/scheduled_tasks.py`，以便后台统一展示。

## 核心设计思路

1. **项目即运行容器**：`projects` 保存项目级元数据，`state_json` 保存步骤状态与产物索引，任务目录保存大文件。
2. **Web 薄入口，编排下沉**：路由只处理请求、鉴权和响应，流程编排集中在 `appcore/runtime*.py`。
3. **处理能力尽量纯函数化**：`pipeline/` 中的模块以文件路径和结构化数据为输入输出，避免依赖 Flask。
4. **模型选择数据化**：业务功能不硬编码模型，先解析 use_case，再由 binding 和 provider config 决定运行时模型。
5. **人工确认可插拔**：翻译、分段、音色、失败重试等环节允许前端暂停、确认和从指定步骤恢复。
6. **单进程有状态优先**：当前系统偏向快速交付和低运维成本，暂未引入 Redis、队列或独立 worker。
7. **素材库作为中枢**：新能力优先沉淀到素材库和任务中心，减少一次性页面和隐形调度任务。

## 常用命令

```bash
pip install -r requirements.txt
python main.py
pytest tests -q
```

项目规则要求数据库相关验证默认使用测试服务器 MySQL。不要在 Windows 开发机本地启动 MySQL 来兜底。

常用聚焦测试：

```bash
pytest tests/test_project_docs.py -q
pytest tests/test_llm_client_invoke.py tests/test_llm_use_cases_registry.py -q
pytest tests/test_appcore_scheduled_tasks.py tests/test_scheduled_tasks_ui.py -q
pytest tests/test_shopify_image_localizer_batch_cdp.py tests/test_shopify_image_localizer_gui.py -q
```

桌面工具：

```bash
python -m link_check_desktop.main
python -m tools.shopify_image_localizer.main
python -m tools.shopify_image_localizer.build_exe --version 1.0
```

## 接手阅读顺序

1. `main.py`、`web/app.py`
2. `db/schema.sql`、`db/migrations/`
3. `appcore/task_state.py`、`appcore/task_recovery.py`
4. `appcore/runtime.py`、`appcore/runtime_multi.py`、`appcore/runtime_omni.py`
5. `web/routes/task.py`、`web/services/pipeline_runner.py`
6. `pipeline/extract.py`、`pipeline/translate.py`、`pipeline/tts.py`、`pipeline/subtitle.py`、`pipeline/compose.py`
7. `appcore/llm_client.py`、`appcore/llm_use_cases.py`、`appcore/llm_bindings.py`
8. `appcore/medias.py`、`web/routes/medias.py`
9. `appcore/scheduled_tasks.py`、`appcore/scheduler.py`
10. [docs/project-audit-2026-05-01.md](docs/project-audit-2026-05-01.md)

## 维护原则

- 代码真相优先级：`runtime / routes / pipeline / tests` 高于历史 `docs/superpowers/*` 和旧计划文档。
- 新增路由必须继续按 `current_user.id` 或管理员权限做资源归属校验。
- 新增模型调用优先走 `appcore.llm_client`；确需保留旧调用路径时要写明兼容理由。
- 后端产物结构变化时，同步更新 `web/preview_artifacts.py`、前端模板/静态脚本和对应测试。
- 新增定时任务必须登记到 `appcore/scheduled_tasks.py`，并说明调度来源、入口、部署位置和日志归属。
- 前端遵循 Ocean Blue Admin 设计系统，禁止引入紫色/靛蓝色调。
