# AutoVideoSrtLocal

AutoVideoSrtLocal 是一个内部一体化工具，覆盖带货短视频本土化、电商素材运营、任务中心、Shopify / 明空 / 店小秘 / Meta 自动化、订单利润与 ROAS 分析。Web 端基于 Flask + Flask-SocketIO，后端以 MySQL 为主存储，媒体处理走本地文件 + 火山 TOS/VOD，LLM 与多模态能力统一通过 `appcore.llm_client` 和 provider 配置编排。

本 README 面向所有后续接手本仓库的 agents。它不是营销页，也不是用户手册，而是“快速建立项目心智模型 + 避免踩生产红线 + 找到正确代码入口”的操作手册。执行任何改动前仍必须先读 [AGENTS.md](AGENTS.md)，进入特定模块后再读对应模块级 `CLAUDE.md` 或 spec。

## 读前结论

- 这是生产项目，不是纯本地 demo。本机服务器同时承载测试环境和生产环境。
- 常规改动必须在隔离 worktree 中完成；`master` 只接受用户明确要求的 hotfix 或已验证后的合并。
- Web 服务是单进程多线程模型：gunicorn `gthread`、`workers=1`，后台任务、Socket.IO 房间和内存状态都绑定同一 Python 进程。
- 数据库实际使用 MySQL，代码入口是 [appcore/db.py](appcore/db.py) 与 [db/migrate.py](db/migrate.py)。不要在 Windows 开发机上临时启动本地 MySQL 来“兜底验证”。
- LLM/API provider 不应在业务代码里硬编码。新调用优先注册 use case，并通过 [appcore/llm_client.py](appcore/llm_client.py) 调用。
- 前端 POST/PUT/PATCH/DELETE 必须处理 CSRF。模板和静态资源约束见 [web/templates/CLAUDE.md](web/templates/CLAUDE.md) 与 [web/static/CLAUDE.md](web/static/CLAUDE.md)。
- 新增定时任务必须同步登记到 [appcore/scheduled_tasks.py](appcore/scheduled_tasks.py)，否则后台“定时任务”模块无法统一控制和审计。
- 数据分析相关接口必须显式返回 `data_quality`，不要让页面静默展示无法证明正确的数据。
- 发布、重启、线上验证必须按 [AGENTS.md](AGENTS.md) 与 [docs/server-environments.md](docs/server-environments.md) 执行；用户已明确“上线”时才允许重启生产服务。

## 权威文档顺序

当文档之间有冲突，按下面顺序判断：

1. 用户当条明确指令。
2. [AGENTS.md](AGENTS.md) 的硬红线、发布、验证和主题指引。
3. 当前任务相关的 `docs/superpowers/specs/*.md`。
4. 进入目录后的模块级 `CLAUDE.md`。
5. 代码、测试和数据库迁移。
6. 历史 `docs/superpowers/plans/*.md`、handoff、notes。

历史 plan 只作为背景，不自动代表当前事实。代码行为、迁移和测试更可信。

## 项目定位

系统把三条业务线放在同一个后台里：

| 业务线 | 目标 | 主要入口 |
| --- | --- | --- |
| 视频本土化 | 将源视频转成多语种成片，含 ASR、翻译、TTS、字幕、合成、审核和导出 | `web/routes/*translate*.py`、`appcore/runtime*.py`、`pipeline/` |
| 素材运营 | 管理商品素材、详情图、视频、文案、链接、推送就绪度和任务协作 | `web/routes/medias/`、`appcore/medias.py`、`appcore/tasks.py` |
| 数据分析 | 回流订单和广告数据，计算订单利润、产品盈亏、真实 ROAS、SKU 保本 ROAS | `web/routes/order_analytics.py`、`appcore/order_analytics/`、`tools/*sync*.py` |

此外还有几个独立但强依赖服务端 OpenAPI 的桌面/自动化工具：

- `tools/shopify_image_localizer/`：Windows EXE，Playwright/CDP 自动替换 Shopify 图片，并逐步扩展到 AI 上品。
- `link_check_desktop/`：Windows 链接巡检客户端，抓取落地页图片并与素材库参考图比对。
- `tools/meta_*`、`tools/dianxiaomi_*`、`tools/sku_*`：广告、订单、SKU、素材同步脚本。
- `tools/audio_separator/`、`tools/subtitle/`：本地服务或辅助工具。

## 运行环境

服务器环境以 [docs/server-environments.md](docs/server-environments.md) 为准。当前主文档记录如下：

| 环境 | 地址 | 目录 | systemd 服务 | 说明 |
| --- | --- | --- | --- | --- |
| 测试 | `http://172.16.254.106:8080/` | `/opt/autovideosrt-test` | `autovideosrt-test.service` | 默认联调和页面验证环境 |
| 生产 | `http://172.16.254.106/` | `/opt/autovideosrt` | `autovideosrt.service` | 用户明确上线后才操作 |
| 本地 worktree | 当前工作区 | `~/.paseo/worktrees/...` 或其他隔离目录 | 无 | 写代码、跑单测、临时 dev server |

服务器可能同时存在多个网卡地址；对外协作文档统一以 `docs/server-environments.md` 和 `AGENTS.md` 当前版本为准。不要把旧 IP 硬编码到新代码里。

## 技术栈

| 分类 | 组件 |
| --- | --- |
| 语言/框架 | Python 3.12、Flask、Flask-SocketIO、Flask-Login、Flask-WTF |
| 数据库 | MySQL、PyMySQL、DBUtils 连接池 |
| 后台任务 | APScheduler、systemd timer、Windows 计划任务、部分后台线程 |
| 媒体处理 | ffmpeg、ffmpeg-python、srt、scenedetect、librosa、resemblyzer、Pillow、scikit-image |
| 自动化 | Playwright sync API、CDP 接管浏览器、部分 PyAutoGUI/Wine 打包链路 |
| 存储 | 本地文件、火山 TOS、火山 VOD、灾备 TOS 桶 |
| LLM/AI | OpenRouter、豆包、Gemini AI Studio、Vertex、Vertex ADC、ElevenLabs、APIMART、Seedream/Seedance 等 |
| 部署 | gunicorn `gthread` 单 worker、systemd、服务器本机 venv |

## 安装与本地运行

普通开发只需要仓库依赖和能连到正确数据库/基础设施配置。完整能力依赖服务器凭据和外部 provider，不要期待纯离线环境跑完整业务链路。

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
python main.py
```

常用命令：

```bash
pytest -q
python main.py
python -m link_check_desktop.main
python -m tools.shopify_image_localizer.main
```

注意：

- `main.py` 会先执行迁移、同步基础设施凭据、校验必填项，再创建 Flask app 和启动 APScheduler。
- `python -m web.app` 在部分历史文档中出现过，但当前可靠入口仍是 `python main.py` 或生产 gunicorn `main:app`。
- `.env` 只放基础设施兜底配置和运行参数；正式 provider key、model、base_url 优先走数据库和后台设置页。
- dev server 验证未登录路由时，未登录应该返回 302，而不是 500。

## 启动链路

主入口在 [main.py](main.py)：

1. 初始化日志。
2. 创建 `UPLOAD_DIR`、`OUTPUT_DIR`。
3. 调用 `appcore.db_migrations.ensure_up_to_date()` 自动应用迁移。
4. 调用 `appcore.infra_credentials.sync_to_runtime()`，把 DB 中的基础设施凭据同步到 runtime。
5. 调用 `config.validate_runtime_config()` 校验 TOS 等启动必填项。
6. 调用 `web.app.create_app()` 创建 Flask app。
7. 调用 `appcore.scheduler.start_scheduler_if_enabled()` 启动 APScheduler。
8. 作为脚本运行时，用 `socketio.run(..., port=5000)` 起本地服务。

Flask app 工厂在 [web/app.py](web/app.py)，主要做：

- 配置 session、cookie、CSRF。
- 初始化 `login_manager`、`socketio`。
- 注册所有蓝图。
- 注册 cookie API CSRF guard。
- 注入权限、定时任务告警、运行环境徽标等 Jinja context。
- 启动时做“状态标记型恢复”，不自动重启大批 runner。
- 注册 Socket.IO 房间订阅事件。

## 部署模型

生产服务由 [deploy/autovideosrt.service](deploy/autovideosrt.service) 启动：

```text
/opt/autovideosrt/venv/bin/gunicorn --config /opt/autovideosrt/deploy/gunicorn.conf.py main:app
```

gunicorn 配置在 [deploy/gunicorn.conf.py](deploy/gunicorn.conf.py)：

- `worker_class = "gthread"`
- `workers = 1`
- 默认线上 `threads = 32`
- 测试环境通常覆盖为 16 线程
- `worker_exit` 和信号处理会触发 shutdown coordinator，并尽量 drain 活跃任务

单 worker 是设计约束，不是偶然配置。多个 worker 会让以下内容需要重新设计：

- Socket.IO 房间和进度推送。
- 进程内任务状态。
- APScheduler 单例。
- 后台线程和取消语义。
- 内存缓存与运行时锁。

## 目录总览

| 路径 | 角色 | agent 关注点 |
| --- | --- | --- |
| `appcore/` | 业务核心层 | 优先放业务逻辑、服务、DAO、调度、LLM 编排 |
| `pipeline/` | 媒体处理层 | 尽量保持 Flask 无关，以文件路径和结构化数据为输入输出 |
| `web/` | Web 层 | 路由、模板、静态资源、Socket.IO 适配，不应塞大量业务算法 |
| `web/services/` | 路由服务层 | 路由和 appcore 的薄适配、响应构造、runner 桥接 |
| `web/routes/` | Flask 蓝图 | 鉴权、参数解析、调用 service/appcore、返回响应 |
| `web/templates/` | Jinja 页面 | 必读模块级 CLAUDE，避免继承和 raw HTML 事故 |
| `web/static/` | JS/CSS | 必读 Ocean Blue、CSRF、medias 前缀规则 |
| `db/` | schema/迁移 | schema 初始化和增量迁移，禁止绕过 DB_NAME 乱切库 |
| `tools/` | 脚本/桌面工具 | 通常连接生产/测试服务或 CDP 浏览器，风险较高 |
| `link_check_desktop/` | 桌面链接巡检 | Windows 客户端，依赖服务端 OpenAPI |
| `docs/superpowers/specs/` | 规格事实来源 | 新功能和关键修复必须写或引用 spec |
| `docs/superpowers/plans/` | 历史计划 | 背景材料，不能替代当前代码事实 |
| `tests/` | 单元/回归/特征测试 | 改动后按风险选择聚焦集 |

## 核心数据模型

以下是接手时最常遇到的表和概念：

| 表/概念 | 用途 |
| --- | --- |
| `users` | 用户、角色、权限基础 |
| `projects` | 媒体处理型任务容器，保存 `type`、`status`、`task_dir`、`state_json` |
| `state_json` | 任务运行态、步骤状态、产物索引、前端上下文 |
| `media_products` | 商品素材主表 |
| `media_items` | 商品视频/图片等素材项 |
| `media_raw_sources` | 原始素材池和小语种素材来源 |
| `tasks` | 任务中心父子任务模型 |
| `pushes` 相关表 | 素材推送、推送状态、推送历史 |
| `llm_provider_configs` | provider key、base_url、model 等配置 |
| `llm_use_case_bindings` | use case 到 provider/model 的管理员覆盖 |
| `usage_logs` / `usage_log_payloads` | AI 调用计费和调试载荷 |
| `scheduled_task_runs` / controls | 定时任务运行记录和控制 |
| `meta_ad_*` | Meta 广告日终、实时、账户、手工补录数据 |
| `order_profit_*` | 订单利润、利润行、分摊和汇总 |
| `sku_actual_breakeven_roas_snapshots` | SKU 实际保本 ROAS 快照 |

数据库迁移由 `appcore/db_migrations.py` 和 `db/migrations/` 管理。需要修改数据结构时：

1. 新增迁移 SQL，不要只改 `db/schema.sql`。
2. 迁移必须幂等或有存在性保护。
3. 涉及生产数据修复时优先写可回放脚本，并保留 dry-run 或只读验证。
4. 不要直接在生产库手工 `UPDATE` 绕过服务层，尤其是 Meta 账户、广告分摊和任务状态。

## Web 层规则

新增或修改路由时：

- 页面路由必须 `@login_required`。
- 管理员功能必须加 `@admin_required`、`@superadmin_required` 或 `@permission_required(...)`。
- API 要明确资源归属：普通用户只能访问自己的项目/素材，管理员按规则放开。
- mutating 请求必须处理 CSRF；前端从 `layout.html` meta 读取 token，header 名用 `X-CSRFToken`。
- 对 JSON API，失败响应应包含可定位的错误原因，不要吞异常后返回空成功。
- 新蓝图要确认 `url_prefix`，特别是 `medias` 的真实前缀是 `/medias`。
- 未登录访问受保护页面应该是 302 到登录页，不能因为模板访问 `current_user` 直接 500。

常见代码路径：

| 功能 | 路由 | 服务/核心 |
| --- | --- | --- |
| 项目列表/详情 | `web/routes/projects.py`、`task.py` | `web/store.py`、`appcore/task_state.py` |
| 视频翻译 | `task.py`、`multi_translate.py`、`omni_translate.py`、`ja_translate.py` | `appcore/runtime*.py`、`pipeline/` |
| 素材库 | `web/routes/medias/` | `appcore/medias.py`、`web/services/media_*` |
| 任务中心 | `web/routes/tasks.py` | `appcore/tasks.py` |
| 推送管理 | `web/routes/pushes.py` | `appcore/pushes.py` |
| 图片翻译 | `web/routes/image_translate.py` | `appcore/image_translate_runtime.py` |
| 字幕擦除 | `web/routes/subtitle_removal.py` | `appcore/subtitle_removal_runtime*.py` |
| 数据分析 | `web/routes/order_analytics.py`、`order_profit.py`、`product_profit_report.py` | `appcore/order_analytics/` |
| OpenAPI | `web/routes/openapi_materials.py` | `appcore/openapi_auth.py`、`appcore/openapi_materials.py` |
| 定时任务后台 | `web/routes/scheduled_tasks.py` | `appcore/scheduled_tasks.py` |

## 前端和模板规则

进入 `web/templates/` 或 `web/static/` 前必须读模块级文档：

- [web/templates/CLAUDE.md](web/templates/CLAUDE.md)：Jinja 继承、detail shell、防止 include 后追加 raw HTML。
- [web/static/CLAUDE.md](web/static/CLAUDE.md)：Ocean Blue 设计系统、CSRF、medias url prefix。

关键约束：

- 禁止在 `{% include base_with_extends %}` 后追加 raw HTML、script 或 style。
- 翻译详情页追加卡片必须通过 `_translate_detail_shell.html` 的 `detail_extra` block。
- 前端 mutating fetch 必须带 CSRF。
- 新 UI 应与后台现有密度一致，避免营销站式 hero 和装饰性大渐变。
- Ocean Blue 约束：管理后台主色在 cyan-blue 范围，禁止紫色/靛蓝/粉色方向。
- 表格、按钮、筛选、弹窗要有 loading、empty、error 三态。

## LLM 和 provider 编排

统一入口是 [appcore/llm_client.py](appcore/llm_client.py)：

- `invoke_chat(...)`
- `invoke_generate(...)`

use case 注册在 [appcore/llm_use_cases.py](appcore/llm_use_cases.py)。provider adapter 在 `appcore/llm_providers/`。管理员覆盖绑定走 `appcore/llm_bindings.py` 和 DB。

新业务接入 LLM 时：

1. 在 `llm_use_cases.py` 注册 use case，写清模块、label、默认 provider/model、计费单位。
2. 如需迁移默认绑定，补 DB migration。
3. 业务代码调用 `llm_client.invoke_chat` 或 `invoke_generate`，不要直接 import OpenAI/Gemini SDK。
4. 传 `user_id`、`project_id`，让 usage/billing 能追踪。
5. 大 payload 要利用现有 sanitizer/媒体优化逻辑，避免把 base64 原文写入日志。
6. provider key、base_url、model 不写 `.env` 或代码，走后台设置和 `llm_provider_configs`。

## 媒体翻译流水线

视频本土化大致流程：

```text
上传/选取源视频
  -> 提取音频、视频元数据、预览资源
  -> ASR 或同语言规整
  -> 分段、对齐、必要时人工确认
  -> LLM 本土化翻译/改写
  -> TTS 脚本生成
  -> 音色匹配与 TTS 生成
  -> 字幕生成、分行、安全切分
  -> 音画同步、背景保留/音量处理
  -> 视频合成
  -> 剪映/CapCut 项目导出
  -> 可选 AI 质量评估
```

主要文件：

| 层 | 文件 |
| --- | --- |
| 任务状态 | `appcore/task_state.py`、`appcore/project_state.py` |
| 通用 runtime | `appcore/runtime.py`、`appcore/runtime/_pipeline_runner.py` |
| 多语种/Omni/日语/英语重配 | `appcore/runtime_multi.py`、`runtime_omni.py`、`runtime_omni_v2.py`、`runtime_ja.py`、`runtime_english_redub.py` |
| pipeline 能力 | `pipeline/extract.py`、`translate.py`、`tts.py`、`subtitle.py`、`compose.py` |
| 详情页协议 | `web/services/translate_detail_protocol.py`、`web/preview_artifacts.py` |
| Socket.IO runner 适配 | `web/services/*pipeline_runner.py` |

改动时要特别注意：

- 不要在 asyncio loop 内直接调用 Playwright sync API。
- 长任务要接入取消点和 shutdown coordinator，避免 systemd restart 时硬杀造成坏状态。
- 任务恢复默认应是状态标记，不要启动时自动重跑大量 runner。
- 后端产物结构变化时同步更新预览协议、模板/JS 和测试。
- TTS/音频相关变更要先读 AGENTS 中列出的 TTS/audio specs。

## 素材库和任务中心

素材库以商品为中心：

- 商品主记录：`media_products`
- 素材项：`media_items`
- 详情图、多语言图、视频封面、视频素材、raw source
- AI 评估、链接检测、ROAS、SKU、店铺链接、推送状态

任务中心用于把选品、翻译、审核、素材回填、推送串成流程。相关规则：

- 所有任务流 UI 必须展示处理中状态、成功后的下一步入口、失败请求与失败原因。
- 按语言指派和任务 ID 语种守卫有专门 specs，改任务中心前先查 AGENTS 主题指引。
- 任务中心不是简单 CRUD，涉及父子任务、认领、打回、完成、素材回填和推送闭环。

常见入口：

| 功能 | 文件 |
| --- | --- |
| 素材列表/编辑 | `web/routes/medias/`、`web/templates/medias_list.html`、`web/static/medias.js` |
| 素材服务 | `appcore/medias.py`、`web/services/media_*` |
| 明空导入 | `appcore/mk_import.py`、`web/routes/mk_import.py` |
| 原始视频池 | `appcore/raw_video_pool.py`、`web/routes/raw_video_pool.py` |
| 任务中心 | `appcore/tasks.py`、`web/routes/tasks.py`、`web/templates/tasks_list.html` |
| 推送 | `appcore/pushes.py`、`web/routes/pushes.py` |

## 数据分析和广告订单体系

进入数据分析前必读 [appcore/order_analytics/CLAUDE.md](appcore/order_analytics/CLAUDE.md) 与 [docs/analytics-data-quality-guardrails.md](docs/analytics-data-quality-guardrails.md)。

模块职责：

| 文件/目录 | 职责 |
| --- | --- |
| `appcore/order_analytics/realtime.py` | 实时大盘、业务日、实时广告兜底 |
| `order_profit_aggregation.py` | 订单利润列表、广告费分摊、实时补偿 |
| `data_quality.py` | 数据质量水位、对账、状态对象 |
| `meta_ads.py` | Meta 广告聚合、purchase value fallback |
| `product_profit_*` | 产品盈亏、广告明细、国家看板 |
| `manual_ad_spend.py` | 广告费人工录入兜底 |
| `tools/roi_hourly_sync.py` | ROI 小时级同步 |
| `tools/meta_daily_final_sync.py` | Meta 日终同步 |
| `tools/dianxiaomi_*` | 店小秘订单、SKU、排名等同步 |

硬规则：

- Meta 实时 fallback 必须按 `(business_date, ad_account_id)` 取最新 snapshot，再合并账户。
- 不允许用 `GROUP BY business_date` 取全局 `MAX(snapshot_at)`。
- 店铺筛选必须走 `meta_ad_accounts` 服务层和白名单，不硬编码 `site_code -> ad_account_id`。
- 产品盈亏广告费分摊要考虑所有已配置账户，包括禁用但有历史数据的账户。
- `/order-profit/*`、`/order-analytics/realtime-overview`、`/order-analytics/product-profit/*` 顶层必须带 `data_quality`。
- 前端缺少 `data_quality` 时应按 unknown 处理，不要默认 ok。

## 定时任务

APScheduler 入口在 [appcore/scheduler.py](appcore/scheduler.py)。统一登记和控制在 [appcore/scheduled_tasks.py](appcore/scheduled_tasks.py)。

定时任务来源包括：

- Web 进程内 APScheduler。
- systemd timer。
- crontab。
- Windows 计划任务。
- 部分后台轮询/daemon。

新增任务时必须登记：

1. `code` 稳定唯一。
2. 写清 `source_type`、`source_label`、`runner`、部署位置、日志归属。
3. 如需后台控制，接入控制策略。
4. 记录运行结果或失败告警。
5. 补测试，例如 `tests/test_appcore_scheduled_tasks.py`、`tests/test_scheduled_tasks_ui.py`。

## 存储、TOS 和灾备

本地文件和 TOS 同时存在：

- `UPLOAD_DIR`：上传文件。
- `OUTPUT_DIR`：任务输出和素材文件。
- 火山 TOS：ASR、素材、公共访问、备份。
- 火山 VOD：字幕擦除/视频上传。
- TOS 灾备桶：按本地绝对路径映射 object key，并保存 MySQL dump。

关键文件：

- [config.py](config.py)：基础设施与运行参数。
- `appcore/tos_clients.py`：TOS client。
- `appcore/tos_backup_storage.py`：文件灾备读写。
- `appcore/tos_backup_job.py`：备份任务。
- `appcore/tos_backup_restore.py`：恢复。
- `scripts/tos_backup_sync.py`、`scripts/tos_backup_restore.py`：手动工具。

规则：

- TOS/VOD 正式凭据优先从 `infra_credentials` 管理，不要只改 `.env`。
- 服务器启用代理/TUN 时，TOS 域名必须直连。
- 文件路径进入数据库后不能随意移动，否则灾备恢复和前端签名 URL 会断。

## 桌面工具和自动化

### Shopify Image Localizer

目录：[tools/shopify_image_localizer/](tools/shopify_image_localizer/)

它是高风险 Windows EXE 工具，发布事故成本高。改动前必须读：

- [tools/shopify_image_localizer/CLAUDE.md](tools/shopify_image_localizer/CLAUDE.md)
- `docs/superpowers/specs/2026-05-24-shopify-image-localizer-release-standard-fix.md`
- [docs/shopify-image-localizer-exe-release-standard.md](docs/shopify-image-localizer-exe-release-standard.md)

关键规则：

- Playwright sync API 要在线程隔离中使用。
- CDP 复用前必须确认 Chrome profile。
- 不要把等待替换为 `time.sleep`。
- 打包发布只能按 release standard，不能把占位、空值或示例 key 打进包。
- 验证鉴权要打受保护 bootstrap 接口，公开 languages/domains 接口不能证明 key 正确。

### Link Check Desktop

目录：[link_check_desktop/](link_check_desktop/)

能力：

- 输入产品页链接。
- 调服务端 OpenAPI 拉素材参考图。
- 可见浏览器抓目标页面图片。
- 做同图/语言/质量分析。
- 生成本地静态报告。

开发运行：

```bash
python -m link_check_desktop.main
```

## OpenAPI

OpenAPI 主要给桌面工具和外部自动化使用，集中在：

- `web/routes/openapi_materials.py`
- `appcore/openapi_auth.py`
- `appcore/openapi_materials.py`
- `web/services/openapi_*`

规则：

- OpenAPI 通常走 `X-API-Key`，不走 cookie session，也不需要 CSRF。
- 不要把生产 key 写入默认配置、README、示例 JSON、打包产物。
- 服务端鉴权 key 来自 DB provider config，而不是仓库 `.env` 的旧变量。

## 测试策略

通用：

```bash
pytest -q
```

按改动范围选择聚焦测试：

| 改动范围 | 建议测试 |
| --- | --- |
| README/文档安全 | `pytest tests/test_project_docs.py -q` |
| LLM use case/client | `pytest tests/test_llm_client_invoke.py tests/test_llm_use_cases_registry.py -q` |
| 定时任务 | `pytest tests/test_appcore_scheduled_tasks.py tests/test_scheduled_tasks_ui.py -q` |
| 模板/detail shell | `pytest tests/test_translate_detail_shell_templates.py tests/test_translate_detail_protocol.py -q` |
| 素材库 | `pytest tests/test_media_* tests/test_medias_* -q` 中相关子集 |
| Shopify 工具 | `pytest tests/test_shopify_image_localizer_build_exe.py tests/test_shopify_image_localizer_domains.py tests/test_shopify_image_localizer_batch_cdp.py tests/test_shopify_image_localizer_gui.py -q` |
| 数据分析 | `pytest tests/test_order_analytics_realtime_site_filter.py tests/test_order_analytics_true_roas.py tests/test_order_analytics_data_quality.py tests/test_order_profit_aggregation.py tests/test_order_analytics_ads.py tests/test_product_profit_report.py -q` |

Web 路由改动后的手动验证顺序：

1. 跑相关 pytest。
2. 起 dev server 或使用测试环境。
3. 未登录访问新路由应为 302，不是 500。
4. 登录后应为 200 或预期业务状态。
5. 新增 admin 路由应验证普通用户无权访问。
6. POST/PUT/DELETE 前端请求确认带 `X-CSRFToken`。

## 发布流程

用户明确要求“上线”后才能发布生产。当前标准流程见 [AGENTS.md](AGENTS.md)。在 Ubuntu 服务器上通常直接操作本地目录：

```bash
git push origin HEAD:master

cd /opt/autovideosrt-test
git pull origin master --ff-only
systemctl restart autovideosrt-test
sleep 3
systemctl is-active autovideosrt-test
curl -s -o /dev/null -w "TEST HTTP %{http_code}\n" http://127.0.0.1:8080/

cd /opt/autovideosrt
git pull origin master --ff-only
if ! cmp -s deploy/autovideosrt.service /etc/systemd/system/autovideosrt.service; then
  cp deploy/autovideosrt.service /etc/systemd/system/
  systemctl daemon-reload
fi
systemctl restart autovideosrt
sleep 3
systemctl is-active autovideosrt
curl -s -o /dev/null -w "PROD HTTP %{http_code}\n" http://127.0.0.1/
```

验收标准：

- `systemctl is-active` 返回 `active`。
- HTTP 返回 200 或 302。
- 404、500、000 都算失败。
- 如果变更会影响长任务，重启前先检查活跃任务，避免中断正在运行的生产任务。

不要调用 `deploy/publish.sh`，不要走 SSH 跳板，不要 `gh auth login`。

## Git 和 worktree 规则

常规流程：

```bash
git fetch origin
git worktree add ../some-task origin/master
cd ../some-task
# 修改、测试、提交
git push origin HEAD:master
```

注意：

- 不要在主工作目录直接改常规需求。
- 不要 `git reset --hard` 或 `git checkout --` 还原用户改动。
- 工作区出现非自己改动时先识别来源；无关则忽略，相关则兼容。
- 提交前用 `git diff --check` 做空白检查。
- 只提交本任务相关文件，避免带入生成物、日志、下载包、配置密钥。

## Agent 接手 checklist

开始前：

- [ ] 确认 `pwd` 是否在隔离 worktree。
- [ ] `git status --short --branch` 看清分支和脏文件。
- [ ] 读 [AGENTS.md](AGENTS.md)。
- [ ] 找到当前任务的 spec 或模块级文档锚点。
- [ ] 明确是否涉及生产服务、数据库、定时任务、桌面工具打包。

改动中：

- [ ] 先读代码入口，不凭历史 plan 猜。
- [ ] 业务逻辑优先放 `appcore/` 或既有 service 层，路由保持薄。
- [ ] 新 LLM 调用走 use case + `llm_client`。
- [ ] 新 POST/PUT/DELETE 处理 CSRF。
- [ ] 新路由处理登录和权限。
- [ ] 新定时任务登记到 `scheduled_tasks.py`。
- [ ] 数据分析 API 带 `data_quality`。
- [ ] 前端遵守 Ocean Blue 和三态。

收尾：

- [ ] 跑相关 pytest。
- [ ] 跑 `git diff --check`。
- [ ] 如有 Web 改动，验证未登录 302、登录后 200。
- [ ] 如有发布请求，先测试环境，再生产环境。
- [ ] 汇报已改文件、测试结果、未验证项和部署结果。

## 常见排查入口

| 症状 | 优先看 |
| --- | --- |
| 启动失败缺凭据 | `.env`、`infra_credentials`、`config.validate_runtime_config()` |
| 登录后 500 | `FLASK_SECRET_KEY`、模板是否访问未登录用户字段、DB 连接 |
| POST 400 csrf | 前端是否读 `layout.html` meta 并发送 `X-CSRFToken` |
| 任务进度不刷新 | Socket.IO room、runner emitter、`task_state`、浏览器控制台 |
| 服务重启卡住 | `deploy/gunicorn.conf.py`、`shutdown_coordinator`、active tasks |
| 广告费为 0 | `appcore/order_analytics/CLAUDE.md`、实时 fallback、日终表水位、`data_quality` |
| 产品盈亏对不上 | 广告费分摊、未分摊广告费、purchase value fallback、店铺筛选 |
| Shopify 工具提示 key 空 | 发布包 config、BOM、zip 是否重建、bootstrap 鉴权 |
| CDP 自动化异常 | Chrome profile、端口、Playwright sync API 是否跨 asyncio loop |
| 页面布局异常 | Jinja extends/include 规则、Ocean Blue CSS、移动端断点 |

## 重要文档索引

| 文档 | 用途 |
| --- | --- |
| [AGENTS.md](AGENTS.md) | 全项目硬规则、发布、验证和主题指引 |
| [docs/server-environments.md](docs/server-environments.md) | 测试/生产服务器、systemd、数据库、TOS 灾备 |
| [docs/analytics-data-quality-guardrails.md](docs/analytics-data-quality-guardrails.md) | 数据质量 API 契约和实现要求 |
| [appcore/order_analytics/CLAUDE.md](appcore/order_analytics/CLAUDE.md) | 订单/广告分析硬规则 |
| [web/templates/CLAUDE.md](web/templates/CLAUDE.md) | 模板继承、detail shell、CSRF |
| [web/static/CLAUDE.md](web/static/CLAUDE.md) | Ocean Blue、CSRF、前端约束 |
| [tools/shopify_image_localizer/CLAUDE.md](tools/shopify_image_localizer/CLAUDE.md) | Shopify EXE、CDP、发布门禁 |
| [link_check_desktop/README.md](link_check_desktop/README.md) | 链接巡检桌面端 |
| [docs/superpowers/specs/](docs/superpowers/specs/) | 当前功能设计事实来源 |
| [docs/project-audit-2026-05-01.md](docs/project-audit-2026-05-01.md) | 架构审视和历史风险清单 |

## 最后提醒

这个项目的主要风险不在语法，而在“看起来能跑但口径错了”：错误的业务日、错误的广告账户合并、错误的权限范围、缺失的 CSRF、未登记的调度、写死的 provider、发布包中的占位 key、重启时中断长任务。接手时优先证明数据来源、权限边界和发布路径正确，再追求代码表面整洁。
