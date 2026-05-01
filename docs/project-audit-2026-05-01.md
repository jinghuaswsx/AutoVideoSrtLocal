# 项目问题审视报告

审视日期：2026-05-01
审视范围：根目录 README 所描述的系统架构、模块职责、核心设计思路，以及当前代码中与这些设计目标不一致的风险点。
审视方式：静态阅读关键源码、现有 README、部署配置、迁移脚本、历史审查文档和测试目录。本轮未连接本地 MySQL，也未做线上/测试服务器动态验证。

## 整改进展（当前工作区）

本节记录基于本报告 P1/P2 的当前 worktree 整改状态，便于后续验收时区分“已落地”“部分落地”和“仍需单独排期”。

已落地：

- P1-4 分层边界：`preview_artifacts`、`quality_assessment` 下沉到 `appcore`，runner 启动改走 `appcore.runner_dispatch`，语音库同步进度改走 `appcore.realtime_events`，并新增静态测试防止 `appcore` 反向 import `web.*`。
- P1-5 任务状态入口：新增 `appcore.project_state`，已把视频评测、视频创作、批量翻译中的直接 `state_json` 写入逐步改为统一 helper。
- P1-6 数据库初始化：`schema.sql` 移除固定库名，`db/migrate.py` 改为显式连接配置中的 `DB_NAME`，并禁止执行 `CREATE DATABASE` / `USE` 语句。
- P1-7 迁移 baseline：旧库首次 baseline 前增加核心表/列校验，避免把缺列库误标为已迁移。
- P1-9 DB 连接池：连接池上限改为 `DB_POOL_MAX_CONNECTIONS` 配置项，默认 40。
- P2-10 LLM 收敛：补充 LLM provider 与非 LLM provider 分类，并增加 adapter 注册静态回归测试；旧兼容调用暂不在本批强拆。
- P2-11 定时任务登记：任务定义增加 `control_strategy`、`log_source`、`log_available` 元数据测试与实现。
- P2-12 路径安全：新增 `appcore.safe_paths`，覆盖任务清理、artifact 下载、round-file、视频创作素材读取/删除等高风险路径。
- P2-13 OpenAPI 鉴权：新增多 key、多 caller、多 scope 解析与校验，兼容旧单 key。

部分落地：

- P1-8 长任务模型：已抽 runner dispatch，并补充 `appcore.runner_lifecycle` 与 active task 原子注册，覆盖 pipeline / 多语种 / 图片翻译 / 字幕去除 / translate_lab / link_check 等 runner 的重复启动防护与启动恢复误判防护；完整 worker 化、统一 daemon 策略和 graceful shutdown 仍需要独立排期。
- P2-14 大文件拆分：本批只做边界下沉和回归防护，未做 `medias.py`、`runtime.py` 等大文件拆分，避免在同一批整改中扩大业务风险。

验收状态：

- 2026-05-01 当前工作区已重新执行 P1/P2 聚焦回归：`275 passed, 2 warnings`。覆盖数据库迁移安全、路径安全、架构边界、OpenAPI、调度元数据、任务状态、runner dispatch / lifecycle、图片翻译 runtime、字幕去除 runtime、translate_lab 路由、link_check runner、视频创作素材删除等。
- 2026-05-02 当前工作区继续补齐 P1-8 第 7 项 Phase 1 验收：本地可执行组合回归已扩展到 `383 passed, 2 warnings`，并修正会误连 Windows 本机 MySQL 的测试隔离问题。
- 2026-05-02 测试环境 `http://172.30.254.14:8080/` 已部署 `docs/graceful-shutdown-worker-lifecycle-spec` 分支，服务启动后已创建 `runtime_active_tasks` / `runtime_active_task_snapshots` 表，`python -m appcore.ops.active_tasks pre-restart` 在无活跃任务时返回 `no active tasks`。
- 2026-05-02 测试环境已验证 preflight 阻断场景：人工登记 `video_creation:phase1-preflight-smoke` 后，`pre-restart` 退出 `2` 并写入 `pre_restart_check` 快照；清理后再次返回 `no active tasks`。
- 2026-05-02 测试环境 `autovideosrt-test.service` 已调整为 `TimeoutStopSec=60`，并显式设置 `AUTOVIDEOSRT_GUNICORN_GRACEFUL_TIMEOUT=45`；重启后服务为 `active (running)`，根路径返回 `302`，最近 5 分钟 warning journal 无新增记录。
- 2026-05-02 使用超级管理员账号完成测试环境只读页面验收：`/scheduled-tasks` 返回 `200`，且页面可见 `active_task_pre_restart_check` 登记项；`/medias/`、`/voice-library/`、`/settings?tab=bindings` 均返回 `200`。
- 2026-05-02 在测试服务器 `/opt/autovideosrt-test` 运行第 7 项关键回归：`122 passed`，覆盖 active task、CLI、runner lifecycle、startup recovery、调度登记、服务调优、安全配置、上传校验、schema safety 和 DB pool 配置。
- 2026-05-02 测试服务器补充执行 `py_compile`、`pre-restart`、HTTP 可达性和最近 10 分钟 warning journal 检查：`pre-restart` 返回 `no active tasks`，根路径返回 `302`，服务保持 `active (running)`，warning journal 无新增记录。
- 2026-05-02 当前工作区补充首次部署缺少 runtime active task 表的 CLI 提示回归后，重新运行同一关键组合回归：`124 passed, 2 warnings`。
- 2026-05-02 测试环境已拉取提交 `4aaf53ad`，服务端使用 `/opt/autovideosrt/venv/bin/python` 重新执行 `tests/test_active_tasks_cli.py`：`6 passed`；`pre-restart` 返回 `no active tasks`，服务保持 `active/running`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐启动恢复后的 live active 行清理：`recover_all_interrupted_tasks` 在任务状态已恢复并写库后，会清理旧进程遗留的 `runtime_active_tasks` 记录，避免后续 `pre-restart` 被已不存在的线程误阻断；相关生命周期回归 `50 passed, 2 warnings`。
- 2026-05-02 当前工作区重新运行第 7 项关键组合回归：`125 passed, 2 warnings`。
- 首次部署 Phase 1 到尚未创建 `runtime_active_tasks` 表的环境时，普通 `pre-restart` 会安全阻塞；CLI 已识别 MySQL 1146 缺少 runtime active task 表的场景，并提示这只应发生在首次部署 migration 前。测试环境采用一次性 `pre-restart --force` 后重启服务触发 migration，再恢复普通 `pre-restart` 验收。后续环境已有表后应使用普通 `pre-restart`。
- 已执行 `git diff --check`，未发现空白错误；仅有 Windows 工作区 LF/CRLF 换行提示。
- 已对本批修改涉及的 63 个 Python 文件执行 `py_compile.compile(..., doraise=True)`，全部通过。
- 未连接 Windows 本地 MySQL，遵守项目规则。
- `G:\Code\AutoVideoSrtLocal\testuser.md` 中原账号在测试环境 `http://172.30.254.14:8080/` 和线上 `http://172.30.254.14/` 均未能登录成功。
- 用户补充的普通测试账号可在测试环境和线上登录，`/medias/`、`/voice-library/` 只读冒烟均为 `200`；`/scheduled-tasks`、`/settings?tab=bindings` 返回 `403`，说明该账号无管理员权限。
- 使用用户确认的超级管理员账号完成只读冒烟：测试环境 `http://172.30.254.14:8080/` 与线上 `http://172.30.254.14/` 均可登录，`/medias/`、`/voice-library/`、`/scheduled-tasks`、`/settings?tab=bindings`、`/settings?tab=providers` 均返回 `200`。未把账号密码写入本文档。

## 总体判断

项目已经具备较完整的业务闭环，核心优点很明确：

- 已形成 `web / appcore / pipeline / db` 的分层雏形。
- 素材库、任务中心、推送、订单分析和调度模块已经覆盖运营链路。
- LLM UseCase、Binding、Provider Config 的统一方向是正确的。
- 测试数量较多，很多子系统已有针对性回归用例。

原始审视时，主要风险集中在：

- README 中理想的分层边界还没有真正落稳，`appcore` 存在反向依赖 `web` 的情况。
- 任务状态并非单一事实来源，内存态、`projects.state_json` 和多个路由内写法并存。
- 凭据、CSRF、部署权限和 Cookie 安全配置存在生产硬化不足。
- 当前单进程多线程模型支撑了快速迭代，但对并发、重启、扩容和长任务稳定性都有天然限制。
- 数据库迁移和多环境初始化仍有踩错库、漏迁移或假基线的风险。

## P0 / 高优先级问题

### 1. 管理后台明文渲染敏感凭据

证据：

- `web/routes/settings.py:4`、`web/routes/settings.py:10` 明确说明 providers Tab 明文渲染。
- `web/templates/settings.html:286` 把 `row.api_key` 直接放入 input value。
- `web/templates/settings.html:550`、`web/templates/settings.html:561` 把推送 Authorization 和 Cookie 直接回显到页面。

影响：

- 超级管理员页面源码、浏览器自动填充、共享屏幕、前端 XSS 或日志抓取都可能泄露供应商 key、外部系统 token 和 cookie。
- 数据库中的 `llm_provider_configs.api_key` 与 `api_keys.key_value` 也是明文字段，数据库泄露时没有第二层保护。

建议：

- 页面只显示“已配置/未配置”和后 4 位掩码，提交表单时只在非空字段更新。
- 后端增加“清空凭据”显式动作，避免空字符串误覆盖。
- 中期将敏感字段加密存储或迁移到专用 secret store。

### 2. 生产服务以 root 运行并直接监听 80

证据：

- `deploy/autovideosrt.service:6` 使用 `User=root`。
- `deploy/gunicorn.conf.py:14` 监听 `0.0.0.0:80`。

影响：

- 任意应用层漏洞、路径删除漏洞、模板注入或依赖漏洞都会被 root 权限放大。
- 无反向代理层时，TLS、限流、上传缓冲、静态资源缓存和安全 header 都难统一治理。

建议：

- 切换到专用低权限用户运行 Web 服务。
- 前置 Nginx/Caddy，应用只监听本地高端口。
- 增加 HTTPS、请求体限制、访问日志、基础限流和安全 header。

### 3. 大量 cookie 认证 JSON 蓝图豁免 CSRF

证据：

- `web/app.py:162-218` 对多个内部蓝图执行 `csrf.exempt(...)`，包括翻译、素材、推送、图片翻译、订单分析、导入等。
- `web/app.py:134` 将 CSRF token 时间限制设为 `None`。

影响：

- 这些接口多数仍依赖浏览器 cookie 会话，不是纯 API key 调用。跨站 POST、恶意表单或已有 XSS 一旦命中，就可能执行状态变更。
- 项目没有显式设置 `SESSION_COOKIE_SECURE`、`SESSION_COOKIE_SAMESITE` 等生产 cookie 策略，风险进一步扩大。

建议：

- 内部 cookie API 改为统一要求 `X-CSRFToken` 或自定义 `X-Requested-With` 加 CSRF 校验。
- OpenAPI 这类 `X-API-Key` 鉴权接口可继续豁免，但要与 cookie session 接口分开。
- 显式配置 `SESSION_COOKIE_HTTPONLY`、`SESSION_COOKIE_SAMESITE`、线上 `SESSION_COOKIE_SECURE`。

## P1 / 架构和运行稳定性问题

### 4. `web / appcore / pipeline` 分层边界被打穿

README 的设计目标是 Web 层薄入口，业务编排下沉到 `appcore`，媒体处理放在 `pipeline`。当前有明显反向依赖：

- `appcore/runtime.py:38` 导入 `web.preview_artifacts`。
- `appcore/runtime_multi.py:44` 导入 `web.preview_artifacts`。
- `appcore/bulk_translate_runtime.py:617` 导入 `web.services.image_translate_runner`。
- `appcore/bulk_translate_runtime.py:969`、`appcore/bulk_translate_runtime.py:1030` 导入 `web.routes.image_translate.start_image_translate_runner`。

影响：

- `appcore` 变得无法作为纯业务层独立测试或迁移到 worker。
- 未来拆 Celery/RQ/独立 worker 时会连带加载 Flask 路由、Socket.IO 和模板协议。

建议：

- 将 artifact builder 下沉到 `appcore/artifacts.py` 或 `appcore/preview_artifacts.py`。
- 将 runner 启动接口抽为 `appcore` 层服务，Web 只订阅事件。
- 增加边界测试：`appcore` 不允许 import `web.*`。

### 5. 任务状态不是单一事实来源

证据：

- `appcore/task_state.py:79` 同步内存态到 DB，失败只记录 warning。
- `appcore/task_state.py:197` 冷启动可从 DB 回读到内存。
- `web/routes/video_review.py:319` 和 `web/routes/video_creation.py:699` 各自实现 `_update_state()` 直接改 `projects.state_json`。
- `appcore/bulk_translate_runtime.py:1336`、`appcore/bulk_translate_runtime.py:1341` 直接写 `projects.state_json`。

影响：

- 同一任务可能同时被内存态、DB 字段、路由私有 helper 修改。
- DB 写失败不会阻止任务继续跑，前端刷新或服务重启后可能看到旧状态。
- 不同任务类型的恢复、重试、删除和展示逻辑很难统一。

建议：

- 定义唯一 `TaskStateRepository`，所有读写都通过它。
- 对“只内存可见”“必须持久化”“可重建产物”做字段分级。
- DB 同步失败时至少对关键状态返回错误或标记不可恢复，避免静默漂移。

### 6. 数据库初始化脚本和多环境数据库存在冲突

证据：

- `db/schema.sql:1-2` 固定 `CREATE DATABASE auto_video` 和 `USE auto_video`。
- `db/migrate.py:9` 直接读取并执行 `schema.sql`。
- 测试环境文档要求数据库为 `auto_video_test`，README 也要求数据库验证默认走测试环境。

影响：

- 如果在测试环境执行 `python db/migrate.py`，脚本可能不按 `DB_NAME=auto_video_test` 工作，而是切到 `auto_video`。
- 新环境初始化和测试库维护容易误操作线上库名。

建议：

- `schema.sql` 移除固定库名，库选择由连接参数控制。
- `db/migrate.py` 显式连接 `DB_NAME`，并在执行前打印目标库名。
- 为 `DB_NAME != auto_video` 增加回归测试，防止再出现硬编码库名。

### 7. 启动自动迁移的 baseline 策略可能掩盖漏迁移

证据：

- `appcore/db_migrations.py:13` 说明旧部署会把当前 `db/migrations/` 全部标记为 applied。
- `appcore/db_migrations.py:66-74` 在已有 `projects` 表但无 `schema_migrations` 时，直接 baseline 标记所有文件。

影响：

- 如果旧库实际漏跑某个历史 migration，引入 `schema_migrations` 时会把漏跑文件也标记为已应用。
- 后续服务启动会认为 schema 已最新，但运行时 SELECT/INSERT 仍可能因为缺列报错。

建议：

- baseline 前做关键列/表校验，至少覆盖近 30 天迁移新增的核心列。
- 提供 `--verify-schema` 或启动自检，只读验证必需表和列。
- 对生产首次启用 baseline 生成报告，不要静默吞掉历史差异。

### 8. 单进程多线程模型与长任务稳定性存在边界

证据：

- `deploy/gunicorn.conf.py:16-17` 固定 `workers = 1`、`threads = 32`。
- `web/extensions.py:13` 使用 Socket.IO `async_mode="threading"`。
- `web/services/pipeline_runner.py:61`、`web/services/multi_pipeline_runner.py:33` 使用非 daemon 线程跑长任务。
- `web/services/image_translate_runner.py:47`、`web/services/subtitle_removal_runner.py:47`、`web/services/translate_lab_runner.py:35` 使用 daemon 线程。
- `appcore/task_recovery.py:326-340` 又对部分 `image_translate` 任务做启动后自动恢复。

影响：

- 非 daemon 线程会拖慢重启；daemon 线程会被直接杀掉，不同模块行为不一致。
- 单 worker 无法横向扩容，内存态、Socket.IO room 和后台线程都无法跨进程共享。
- 服务重启时，部分任务只标中断，部分任务自动恢复，用户预期不一致。

建议：

- 明确“Web 进程只接请求，后台任务进 worker”的中期方向。
- 短期至少统一 daemon 策略和恢复策略，并在 README/页面上说明。
- 为每个 runner 增加统一 active registry、graceful shutdown、重复启动保护。

### 9. DB 连接池容量低于 Web 并发线程数

证据：

- `appcore/db.py:25` 连接池 `maxconnections=10`。
- `deploy/gunicorn.conf.py:17` Web 线程数为 32。
- APScheduler 和后台 runner 也会共享同一 DB 池。

影响：

- 高峰时 32 个请求线程加后台任务可能争抢 10 个连接。
- 长查询、上传回调、批量任务或调度任务会放大连接等待和偶发失败。

建议：

- 按 `gunicorn threads + scheduler + runner` 估算连接池上限。
- 为批量任务和调度任务引入独立连接策略或队列。
- 增加 DB 池耗尽时的日志指标。

## P2 / 设计一致性与维护性问题

### 10. LLM 统一调用还没有完全收敛

证据：

- `pipeline/translate.py:5` 仍直接 `from openai import OpenAI`。
- `appcore/gemini_image.py:577` 直接创建 OpenAI client。
- `appcore/llm_use_cases.py:121`、`appcore/llm_use_cases.py:141` 包含 `elevenlabs`、`doubao_asr` 等非 `llm_client` adapter provider。
- `appcore/llm_providers/__init__.py:21-27` 实际 adapter 只注册 LLM provider。

影响：

- README 中“新代码统一走 `appcore.llm_client`”是方向，但代码中仍存在多套兼容路径。
- use_case、provider_config 和 adapter provider 的概念容易混淆，新增模块可能选错入口。

建议：

- 明确分类：LLM adapter、ASR adapter、TTS provider、图片生成 provider 不共用同一个 provider 枚举。
- 旧调用路径标注 deprecated，并给迁移清单。
- 增加测试校验：所有 `llm_client` 可调用 use_case 的 provider 必须存在 adapter。

### 11. 定时任务登记已集中，但控制能力不完全一致

证据：

- `appcore/scheduled_tasks.py:17-213` 同时登记 systemd、windows、subtask、apscheduler、in_process、cron。
- 多个任务 `log_table` 为空，如 `product_cover_backfill_tick`、`material_evaluation_tick`、`subtitle_removal_vod_tick`、`cleanup`。
- `appcore/scheduled_tasks.py:461` 可从 Web 侧执行控制命令，`appcore/scheduled_tasks.py:516`、`appcore/scheduled_tasks.py:522` 涉及 systemctl/schtasks。

影响：

- 后台“统一管理”容易给人错觉，但部分任务只能登记不可控制，部分无统一日志。
- Web 进程触发系统命令需要非常严格的权限边界和审计。

建议：

- 为每个任务明确 `control_strategy`、`readonly`、`log_source`。
- 没有日志表的任务至少写 `scheduled_task_runs` 或明确 journal/log 文件。
- 控制 systemd/schtasks 的接口增加二次确认、审计日志和最小权限执行方式。

### 12. 文件删除和下载缺少统一路径边界策略

证据：

- `appcore/cleanup.py:62` 根据 DB 中的 `task_dir` 执行 `shutil.rmtree`。
- `pipeline/capcut.py:33`、`pipeline/capcut.py:54`、`pipeline/capcut.py:124` 有递归删除。
- 多个路由直接 `send_file(os.path.abspath(path))` 或 `send_file(path)`，例如 `web/routes/de_translate.py:396`、`web/routes/multi_translate.py:609`、`web/routes/video_creation.py:478`。

影响：

- 如果 DB 状态或任务 state 被污染，删除/下载可能越过预期工作目录。
- 各模块各自做校验，安全边界难以审计。

建议：

- 提供统一 `safe_project_path(project_id, path)` 和 `safe_delete_tree(path, allowed_roots)`。
- 递归删除前强制 `resolve()`，并校验位于 `OUTPUT_DIR`、`UPLOAD_DIR` 或明确允许目录内。
- 下载接口只接受 artifact id，由服务端查白名单路径。

### 13. OpenAPI 仍是单 key 粗粒度鉴权

证据：

- `web/routes/openapi_materials.py:38-43` 用 `llm_provider_configs.openapi_materials` 中的单个 `X-API-Key` 做校验。

影响：

- link-check、Shopify localizer、push-items 等不同外部调用方共享同一鉴权粒度。
- 无调用方身份、权限范围、key 轮换窗口和速率限制时，一旦泄露影响面较大。

建议：

- 将 OpenAPI key 拆成多 caller、多 scope、多用途。
- 增加 key 前缀、过期时间、最近使用时间、IP 限制和访问日志。
- 对写接口加入幂等 key 和频率限制。

### 14. 大文件承担过多职责

证据：

- `web/routes/medias.py` 约 3304 行。
- `appcore/runtime.py` 约 3061 行。
- `appcore/order_analytics.py` 约 2607 行。
- `web/routes/task.py` 约 1457 行。

影响：

- 单文件内混合路由、校验、查询、状态机、文件处理和响应拼装。
- 未来改动很容易形成隐式耦合，审查和测试成本高。

建议：

- 按业务子域拆 service 和 route helper，而不是按技术层机械拆分。
- 优先拆高频变更区域：素材详情图、素材上传、主线 task artifact、订单分析聚合。
- 每次拆分配套保留现有 route 行为测试。

## P3 / 文档、依赖和演进问题

### 15. 文档与代码存在局部漂移

例子：

- README 强调“启动时不自动重启 runner”，但 `appcore/task_recovery.py:326-340` 对部分 `image_translate` 会自动拉起 runner。
- `web/services/image_translate_runner.py` 文件头说明不再承担 resume 逻辑，但启动恢复实际会调用它。

建议：

- 将恢复策略写成矩阵：项目类型、启动状态、是否自动恢复、用户入口、重复提交保护。
- 对 README 中的“原则性描述”增加例外说明。

### 16. 依赖版本不是可复现锁定

证据：

- `requirements.txt` 使用范围约束，如 `flask>=3.0.0,<4.0`、`google-genai>=1.0.0,<2.0`。

影响：

- 线上、测试和本地可能安装到不同小版本，视频/LLM/Socket.IO 依赖尤其容易产生行为差异。

建议：

- 增加 `requirements.lock.txt` 或使用 `uv`/`pip-tools` 锁定部署版本。
- 约定升级流程：先测试环境验证，再发布线上。

### 17. 测试覆盖广，但缺少“架构边界测试”

当前测试已经覆盖大量功能路径，但以下约束更适合用轻量静态测试守住：

- `appcore` 不 import `web.*`。
- 新增 use_case 的 provider 必须可被对应 adapter 或 provider 类别解析。
- 新增 migration 不能硬编码生产库名。
- 新增 JSON cookie API 不能直接整蓝图 CSRF exempt。
- 新增定时任务必须登记到 `TASK_DEFINITIONS` 并声明日志归属。

## 建议整改路线

### 第一阶段：安全止血

1. 设置页停止回显真实 key、token、cookie。
2. 内部 JSON API 恢复 CSRF 防护或加入统一请求校验。
3. 服务改为低权限用户运行，前置 HTTPS 反向代理。
4. 明确 Cookie 安全配置。

### 第二阶段：状态和迁移收敛

1. 修复 `schema.sql` 和 `db/migrate.py` 的固定库名问题。
2. 为启动迁移 baseline 增加 schema 校验。
3. 建立统一任务状态 repository，逐步移除各路由私有 `_update_state()`。
4. 写恢复策略矩阵，统一 daemon/non-daemon 和自动恢复口径。

### 第三阶段：架构边界治理

1. 把 artifact builder 从 `web` 下沉到中性模块。
2. 将 appcore 中启动 Web runner 的代码改为事件或服务接口。
3. 拆分 `web/routes/medias.py`、`appcore/runtime.py` 等大文件的热点职责。
4. 梳理 LLM/ASR/TTS/Image provider 的枚举边界。

### 第四阶段：运维可复现

1. 引入依赖锁文件。
2. 增加 DB 连接池和后台任务指标。
3. 为 OpenAPI 做 caller/scope/key 轮换设计。
4. 将无日志表的定时任务接入统一运行日志或明确只读登记。
