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
- 2026-05-02 测试环境已拉取提交 `e78459b9`，服务端运行 `tests/test_task_recovery.py tests/test_active_tasks.py tests/test_active_tasks_cli.py`：`43 passed`；`pre-restart` 返回 `no active tasks`，服务保持 `active/running`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐 active task 快照降级开关 `AUTOVIDEOSRT_ACTIVE_TASK_SNAPSHOT_ENABLED=0`，用于快照写入异常时快速回滚；重新运行第 7 项关键组合回归：`126 passed, 2 warnings`。
- 2026-05-02 测试环境已拉取提交 `91c1afba`，服务端运行 `tests/test_active_tasks.py tests/test_active_tasks_cli.py tests/test_task_recovery.py`：`44 passed`；快照关闭开关返回 `{'count': 1, 'target': 'disabled'}`，`pre-restart` 返回 `no active tasks`，服务保持 `active/running`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐 `active_tasks list` 首次部署缺表指导，避免普通 list 命令在 runtime active task 表尚未创建时直接抛原始异常；重新运行第 7 项关键组合回归：`127 passed, 2 warnings`。
- 2026-05-02 测试环境已拉取提交 `96e33d29`，服务端运行 `tests/test_active_tasks_cli.py`：`7 passed`；实际 `list` 与 `pre-restart` 均返回 `no active tasks`，服务保持 `active/running`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐 Gunicorn `worker_exit` 停机日志明细：shutdown snapshot 会使用同一批 active task，并逐条输出未完成任务的 `project_type:task_id`、policy、stage、runner；重新运行第 7 项关键组合回归：`128 passed, 2 warnings`。
- 2026-05-02 测试环境已拉取提交 `0bd168dd` 并重启 `autovideosrt-test.service` 加载新 Gunicorn 配置；服务端运行 `tests/test_web_service_tuning.py`：`6 passed`，重启前 `pre-restart` 返回 `no active tasks`，重启后服务保持 `active/running`、根路径返回 `302`、journal 出现 `active task shutdown snapshot` 记录，最近 3 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐 `pre-restart` snapshot 写入失败时的降级处理：CLI 会打印 warning，并继续输出活跃任务和 blocking 结果，避免发布前检查被日志写入异常截断；重新运行第 7 项关键组合回归：`129 passed, 2 warnings`。
- 2026-05-02 测试环境已拉取提交 `94fd7ff4`，服务端运行 `tests/test_active_tasks_cli.py`：`8 passed`；实际 `pre-restart` 返回 `no active tasks`，服务保持 `active/running`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐定时任务后台对 active task snapshot 的运行日志展示：`active_task_pre_restart_check` 会读取 `runtime_active_task_snapshots`，且默认“全部日志”也会合入该快照表；`block_restart` 快照在后台标记为失败以突出重启风险。重新运行第 7 项关键组合回归：`135 passed, 2 warnings`。
- 2026-05-02 测试环境已拉取提交 `62e6cfa3` 并重启 `autovideosrt-test.service` 加载新代码；服务端运行 `tests/test_appcore_scheduled_tasks.py tests/test_scheduled_tasks_ui.py`：`24 passed`。测试环境实际写入一条 `scheduled_log_smoke` active task snapshot，`list_runs('active_task_pre_restart_check')` 与 `list_runs('all')` 均可读到该记录，登录后访问 `/scheduled-tasks?view=logs&task=active_task_pre_restart_check` 返回 `200` 且页面包含 smoke 快照。
- 2026-05-02 当前工作区补齐定时任务管理页的日志入口：新增 `log_link_available` 元数据，只有后台可查询的 DB 日志源展示为可点击入口；`active_task_pre_restart_check` 现在会在管理页直接链接到 `db:runtime_active_task_snapshots`，文件、journal、service 类日志继续显示原日志归属。重新运行第 7 项关键组合回归：`136 passed, 2 warnings`。
- 2026-05-02 测试环境已拉取提交 `b8ebf80f` 并重启 `autovideosrt-test.service` 加载新模板；服务端运行 `tests/test_appcore_scheduled_tasks.py tests/test_scheduled_tasks_ui.py`：`25 passed`。登录后访问 `/scheduled-tasks?view=management` 返回 `200`，页面包含 `active_task_pre_restart_check`、`db:runtime_active_task_snapshots` 和对应日志筛选链接；`pre-restart` 返回 `no active tasks`，服务为 `active`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐视频评估后台任务的原子启动保护：`/api/video-review/<task_id>/review` 启动前改用 `try_register_active_task` 抢占 active 记录，重复点击会返回 `already_running` 且不再派发第二个后台任务；active 记录同步写入 user、runner、entrypoint、stage 和模型信息，便于 `pre-restart` 排查。重新运行第 7 项关键组合回归：`137 passed, 2 warnings`。
- 2026-05-02 测试环境已拉取提交 `24d01b9f` 并重启 `autovideosrt-test.service` 加载新路由；服务端运行视频评估启动保护聚焦回归：`2 passed`，`py_compile` 通过；`pre-restart` 返回 `no active tasks`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐视频创作生成任务的原子启动保护：上传后自动生成与 `/api/video-creation/<task_id>/regenerate` 均改用 `try_register_active_task` 抢占 active 记录，重复 regenerate 会返回 `already_running` 且不再派发第二个后台任务；active 记录同步写入 user、runner、entrypoint、stage、模型和生成参数。重新运行第 7 项关键组合回归：`139 passed, 2 warnings`。
- 2026-05-02 测试环境已拉取提交 `03cf3fb3` 并重启 `autovideosrt-test.service` 加载新路由；服务端运行视频创作启动保护聚焦回归：`3 passed`，`py_compile` 通过；`pre-restart` 返回 `no active tasks`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐文案创作后台任务的原子启动保护：上传后自动抽帧/生成、`/api/copywriting/<task_id>/generate` 与 `/api/copywriting/<task_id>/tts` 均改用 `try_register_active_task` 抢占 active 记录，重复生成或 TTS 请求会返回 `already_running` 且不再派发第二个后台任务；active 记录同步写入 user、runner、entrypoint、stage 和动作参数。重新运行第 7 项关键组合回归：`142 passed, 2 warnings`。
- 2026-05-02 测试环境已拉取提交 `7d5d09de` 并重启 `autovideosrt-test.service` 加载新路由；服务端运行文案创作启动保护聚焦回归：`4 passed`，`py_compile` 通过；`pre-restart` 返回 `no active tasks`，服务为 `active`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐批量翻译父调度器的 active 启动保护：`/api/bulk-translate/<task_id>/start`、`resume`、`retry-item`、`retry-failed` 以及素材页原始素材翻译、语音确认后恢复父任务链路均改走 `start_bulk_scheduler_background`，启动前写入 `bulk_translate:<task_id>` active 记录，重复调度返回 `already_running` 且不再派发第二个父调度器；active 记录包含 user、runner、entrypoint、stage 和动作参数。重新运行第 7 项关键组合回归：`184 passed, 2 warnings`。
- 2026-05-02 测试环境已拉取提交 `a8154705` 并重启 `autovideosrt-test.service` 加载 bulk 父调度器保护；服务端运行 bulk 调度器启动保护与语音确认父任务恢复聚焦回归：`6 passed`，`py_compile` 通过；`pre-restart` 返回 `no active tasks`，服务为 `active`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐文案翻译子任务的 active 启动保护：手动 `/api/copywriting-translate/start` 与 bulk 父调度器创建 `copywriting_translate` 子任务时均会先登记 `copywriting_translate:<task_id>` active 记录，再启动 runner；bulk runtime 中 Runner 构造已移动到后台线程内，避免启动线程前读库并确保 active 先登记。重新运行第 7 项关键组合回归：`193 passed, 2 warnings`。
- 2026-05-02 测试环境已拉取提交 `f92223c7` 并重启 `autovideosrt-test.service` 加载文案翻译子任务保护；服务端运行文案翻译子任务启动保护聚焦回归：`3 passed`，`py_compile` 通过；`pre-restart` 返回 `no active tasks`，服务为 `active`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐素材 AI 评估任务的 active 可见性：素材页触发的后台评估改走 `runner_lifecycle.start_tracked_thread`，定时任务 `material_evaluation_tick` 在同步评估每个商品前登记 `material_evaluation:<product_id>` active 记录，重复活跃商品会跳过，评估完成或异常后统一清理。重新运行第 7 项关键组合回归：`222 passed, 2 warnings`。
- 2026-05-02 测试环境已拉取提交 `7e162dd6` 并重启 `autovideosrt-test.service` 加载素材 AI 评估 active 保护；服务端运行素材评估后台入口与定时 tick 聚焦回归：`4 passed`，`py_compile` 通过；`pre-restart` 返回 `no active tasks`，服务为 `active`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐管理员语音库同步任务的 active 可见性：`start_sync` 改走 `runner_lifecycle.start_tracked_thread` 登记 `voice_library_sync:global`，保留原有进程内忙碌保护，并新增 active registry 全局防重；API Key 缺失时不再留下假 running 状态。重新运行第 7 项关键组合回归：`148 passed, 2 warnings`。
- 2026-05-02 测试环境已拉取提交 `cb002966` 并重启 `autovideosrt-test.service` 加载语音库同步 active 保护；服务端运行 `tests/test_voice_library_sync_task.py tests/test_voice_library_sync_admin.py`：`17 passed`，`py_compile` 通过；重启前后 `pre-restart` 均返回 `no active tasks`，服务为 `active`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐翻译质量评估后台任务的 active 可见性：自动/手动触发的 `translation_quality` 评估线程改走 `runner_lifecycle.start_tracked_thread` 登记 `translation_quality:<task_id>`，重复 active 任务不再派发第二个线程，并会把刚插入的评估行标记为 failed，避免 pending 假任务。无本地 DB 相关回归：`17 passed, 1 warning`。
- 2026-05-02 测试环境已拉取提交 `288a9b61` 并重启 `autovideosrt-test.service` 加载翻译质量评估 active 保护；服务端运行 `tests/test_quality_assessment_service.py tests/test_translation_quality.py` 加架构边界聚焦用例：`21 passed`，`py_compile` 通过；重启前后 `pre-restart` 均返回 `no active tasks`，服务为 `active`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐普通/德语/法语手动 AI 视频分析任务的 active 可见性：三个 `run_analysis` 入口改走 `runner_lifecycle.start_tracked_thread` 登记对应项目的 `<project_type>:<task_id>`，与主 pipeline 共用 active key，避免运行中重复启动；路由在 runner 拒绝启动时返回 `409`，不再误报 started。重新运行第 7 项关键组合回归：`174 passed, 2 warnings`。
- 2026-05-02 测试环境已拉取提交 `4c6a8afc` 并重启 `autovideosrt-test.service` 加载手动 AI 视频分析 active 保护；服务端运行三个 runner 和三个路由聚焦回归：`9 passed`，`py_compile` 通过；重启前后 `pre-restart` 均返回 `no active tasks`，服务为 `active`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐内部 cookie 会话 JSON API 的统一请求门禁：保留 OpenAPI 的 `X-API-Key` 豁免；对批量翻译、素材、推送、图片翻译、订单分析等仍使用 cookie 登录的旧 exempt 蓝图，非 GET 请求必须携带 `X-CSRFToken`/`X-CSRF-Token` 或 `X-Requested-With: XMLHttpRequest`，并在全局模板中给同源 fetch/XHR 自动补 header。重新运行第 7 项关键组合加安全回归：`187 passed, 2 warnings`。
- 2026-05-02 测试环境已拉取提交 `6c722ead` 并重启 `autovideosrt-test.service` 加载内部 cookie API 请求门禁；服务端运行 `tests/test_security_config.py::TestInternalCookieApiCsrfGuard`：`4 passed`，`py_compile` 通过；重启前后 `pre-restart` 均返回 `no active tasks`，服务为 `active`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐设置页敏感凭据回显保护：`/settings?tab=providers` 不再把真实 provider API key 放入 HTML，`/settings?tab=push` 不再回显 Authorization、Cookie 和 Basic Auth 密码；敏感输入留空默认保留旧值，只有勾选“清空该字段”才清除。无本地 DB 相关回归：`35 passed, 2 warnings`，`py_compile` 与 `git diff --check` 通过。
- 2026-05-02 测试环境已拉取提交 `ed2da7fa` 并重启 `autovideosrt-test.service` 加载设置页凭据掩码；服务端运行 `tests/test_settings_routes_new.py tests/test_security_config.py`：`34 passed`，`py_compile` 通过；重启前后 `pre-restart` 均返回 `no active tasks`，服务为 `active`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐 Cookie 安全配置显式化：默认 `SESSION_COOKIE_HTTPONLY=True`、`SESSION_COOKIE_SAMESITE=Lax`，并同步设置 remember cookie；`SESSION_COOKIE_SECURE` 默认保持 `False` 以兼容当前 HTTP 测试/线上访问，后续 HTTPS 前置完成后可通过环境变量启用。重新运行安全与设置页回归：`36 passed, 2 warnings`，`py_compile` 与 `git diff --check` 通过。
- 2026-05-02 测试环境已拉取提交 `5c375ba4` 并重启 `autovideosrt-test.service` 加载 Cookie 安全配置；服务端运行 `tests/test_security_config.py tests/test_settings_routes_new.py`：`36 passed`，`py_compile` 通过；重启前后 `pre-restart` 均返回 `no active tasks`，服务为 `active`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区收敛视频创作路由内的状态写入：删除/追加素材和重新生成重置状态改用 `appcore.project_state.save_project_state`，并新增架构边界测试禁止 `video_creation` 路由直接拼写 `UPDATE projects SET state_json`。本地回归：`10 passed, 2 warnings`，`py_compile` 与 `git diff --check` 通过。
- 2026-05-02 测试环境已拉取提交 `0ce3cae6` 并重启 `autovideosrt-test.service` 加载视频创作状态写入收敛；服务端运行视频创作与架构边界聚焦回归：`10 passed`，`py_compile` 通过；重启前后 `pre-restart` 均返回 `no active tasks`，服务为 `active`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区继续收敛多语种/全能/日语翻译路由的语音状态写入：`update_voice`、`rematch`、`confirm_voice` 相关写回改用 `appcore.project_state.save_project_state`，并新增架构边界测试禁止这些路由直接拼写 `UPDATE projects SET state_json`。本地回归：`26 passed, 2 warnings`，`py_compile` 与 `git diff --check` 通过。
- 2026-05-02 测试环境已拉取提交 `60c8ee0b` 并重启 `autovideosrt-test.service` 加载翻译语音状态写入收敛；服务端运行架构边界、多语种、日语和全能翻译聚焦回归：`26 passed`，`py_compile` 通过；重启前后 `pre-restart` 均返回 `no active tasks`，服务为 `active`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐视频评分播放接口路径边界：`/api/video-review/<task_id>/video` 改用 `safe_task_file_response`，只允许返回任务目录、输出目录或上传目录内的文件；新增路由级测试覆盖任务目录内正常播放和目录外路径拒绝。聚焦回归：`8 passed, 2 warnings`，`py_compile` 与 `git diff --check` 通过。
- 2026-05-02 测试环境已拉取提交 `2be0ca61` 并重启 `autovideosrt-test.service` 加载视频评分文件服务路径边界；服务端运行 `tests/test_video_review_routes.py tests/test_artifact_download_safety.py` 及视频评分启动保护用例：`8 passed`，`py_compile` 通过；重启前后 `pre-restart` 均返回 `no active tasks`，服务为 `active`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐 link-check 图片预览路径边界：站点图片与参考图片预览接口改用 `safe_task_file_response`，只允许返回任务目录、输出目录或上传目录内的图片；新增路由测试覆盖目录外拒绝与目录内正常返回。聚焦回归：`13 passed, 2 warnings`，`py_compile` 与 `git diff --check` 通过。
- 2026-05-02 测试环境已拉取提交 `d55ef34c` 并重启 `autovideosrt-test.service` 加载 link-check 图片预览路径边界；服务端运行 `tests/test_link_check_routes.py tests/test_artifact_download_safety.py`：`13 passed`，`py_compile` 通过；重启前后 `pre-restart` 均返回 `no active tasks`，服务为 `active`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐文案创作文件响应路径边界：下载产物、关键帧、源视频、商品图、缩略图、TTS 音频和视频预览均改用 `safe_task_file_response`，目录外路径即使文件存在也拒绝返回；新增路由测试覆盖目录外拒绝和目录内正常返回。聚焦回归：`11 passed, 2 warnings`，`py_compile` 与 `git diff --check` 通过。
- 2026-05-02 测试环境已拉取提交 `c9dfe139` 并重启 `autovideosrt-test.service` 加载文案创作文件响应路径边界；服务端运行 `tests/test_copywriting_file_routes.py tests/test_artifact_download_safety.py` 及文案创作后台启动保护用例：`11 passed`，`py_compile` 通过；重启前后 `pre-restart` 均返回 `no active tasks`，服务为 `active`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐字幕去除 artifact 路径边界：源缩略图、源视频、本地结果视频和下载响应均改用 `safe_task_file_response`，并把正向测试样例调整为任务目录内文件，新增目录外拒绝用例。聚焦回归：`46 passed, 2 warnings`，`py_compile` 与 `git diff --check` 通过。
- 2026-05-02 测试环境已拉取提交 `d105a70a` 并重启 `autovideosrt-test.service` 加载字幕去除 artifact 路径边界；服务端运行 `tests/test_subtitle_removal_routes.py tests/test_artifact_download_safety.py`：`46 passed`，`py_compile` 通过；重启前后 `pre-restart` 均返回 `no active tasks`，服务为 `active`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐 translate-lab artifact 路径边界：字幕下载、分镜音频和最终视频均改用 `safe_task_file_response`，并新增三类目录外拒绝用例，同时保持任务目录内文件正常返回。聚焦回归：`27 passed, 2 warnings`，`py_compile` 与 `git diff --check` 通过。
- 2026-05-02 测试环境已拉取提交 `816393bd` 并重启 `autovideosrt-test.service` 加载 translate-lab artifact 路径边界；服务端运行 `tests/test_translate_lab_routes.py tests/test_artifact_download_safety.py`：`27 passed`，`py_compile` 通过；重启前后 `pre-restart` 均返回 `no active tasks`，服务为 `active`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐 raw-video-pool 下载和 voice-library 匹配样本音频路径边界：两个接口均改用 `safe_task_file_response`，仅允许返回上传/输出存储根内文件；新增允许根内下载与根外拒绝用例。聚焦回归：`33 passed, 2 warnings`，`py_compile` 与 `git diff --check` 通过。
- 2026-05-02 测试环境已拉取提交 `248cc564` 并重启 `autovideosrt-test.service` 加载 raw-video-pool/voice-library 文件响应路径边界；服务端运行 `tests/test_raw_video_pool_routes.py tests/test_voice_library_routes.py tests/test_artifact_download_safety.py`：`33 passed`，`py_compile` 通过；重启前后 `pre-restart` 均返回 `no active tasks`，服务为 `active`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐任务缩略图路径边界：`/api/tasks/<task_id>/thumbnail` 查询同步带出 `task_dir` 并改用 `safe_task_file_response`，管理员仍可按既有规则查看其他用户缩略图，但文件路径必须位于任务目录或存储根内。聚焦回归：`7 passed, 2 warnings`，`py_compile` 与 `git diff --check` 通过。
- 2026-05-02 测试环境已拉取提交 `da25c8db` 并重启 `autovideosrt-test.service` 加载任务缩略图路径边界；服务端运行任务缩略图聚焦用例及 `tests/test_artifact_download_safety.py`：`7 passed`，`py_compile` 通过；重启前后 `pre-restart` 均返回 `no active tasks`，服务为 `active`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐素材缩略图路径边界：`/medias/thumb/<item_id>` 保留既有产品访问校验，同时改用 `safe_task_file_response` 约束 `thumbnail_path` 必须位于输出存储根内；新增输出根内正常返回与 `../` 逃逸拒绝用例。聚焦回归：`6 passed, 2 warnings`，`py_compile` 与 `git diff --check` 通过。
- 2026-05-02 测试环境已拉取提交 `c9a8d625` 并重启 `autovideosrt-test.service` 加载素材缩略图路径边界；服务端运行素材缩略图聚焦用例及 `tests/test_artifact_download_safety.py`：`6 passed`，`py_compile` 通过；重启前后 `pre-restart` 均返回 `no active tasks`，服务为 `active`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐素材封面缓存路径边界：`/medias/cover/<pid>` 在写入/读取本地封面缓存前校验语言码只允许 `[a-z0-9_-]`，避免污染的语言码参与缓存文件名拼接后形成目录逃逸。聚焦回归：`7 passed, 2 warnings`，`py_compile` 与 `git diff --check` 通过。
- 2026-05-02 测试环境已拉取提交 `59fbc68d` 并重启 `autovideosrt-test.service` 加载素材封面缓存路径边界；服务端运行素材缩略图、封面缓存和 artifact 下载安全聚焦用例：`7 passed`，`py_compile` 通过；重启前后 `pre-restart` 均返回 `no active tasks`，服务为 `active`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐素材对象键异常收口：`/medias/object` 和素材上传完成链路遇到 `../`、绝对路径等非法 `object_key` 时不再抛 500，统一按 404 或 `object not found` 拒绝；正常共享素材访问模型不变。同时修正素材评估调试请求测试对旧 OpenRouter 默认值的断言，并调整产品详情页 CSS 顺序，避免桌面规则覆盖移动端全屏化规则。路径安全组合回归扩展为 `113 passed, 2 warnings`，完整 `tests/test_medias_routes.py` 恢复为 `66 passed, 2 warnings`。
- 2026-05-02 测试环境已拉取提交 `7d96a49c` 并重启 `autovideosrt-test.service` 加载素材对象键异常收口与素材路由测试修正；服务端完整 `tests/test_medias_routes.py` 为 `66 passed`，路径安全组合回归为 `113 passed`，`py_compile` 通过；重启后 `pre-restart` 返回 `no active tasks`，服务为 `active`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
- 2026-05-02 当前工作区补齐任务重启清理路径边界：`web.services.task_restart._purge_task_dir` 现在要求待清理目录位于 `OUTPUT_DIR` 内，且每个子项删除前再次校验在该任务目录下；污染的 `task_dir` 会跳过清理而不是删除目录内容。`tests/test_task_restart.py` 同步隔离 DB 写入，避免本地测试误连 Windows MySQL。聚焦回归：`tests/test_safe_paths.py tests/test_task_restart.py` 为 `11 passed`，`py_compile` 通过。
- 2026-05-02 测试环境已拉取提交 `639e8d6a` 并重启 `autovideosrt-test.service` 加载任务重启清理路径边界；服务端运行 `tests/test_safe_paths.py tests/test_task_restart.py` 为 `11 passed`，`py_compile` 通过；重启后 `pre-restart` 返回 `no active tasks`，服务为 `active`，根路径返回 `302`，最近 10 分钟 warning journal 无记录。
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
