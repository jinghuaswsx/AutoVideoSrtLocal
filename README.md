# AutoVideoSrtLocal

带货短视频本土化与素材运营平台。基于 Flask + Socket.IO + MySQL，端到端覆盖：素材入库 → 多语言翻译/配音/字幕/合成 → 推送投放 → 订单回流分析。

> 仓库说明：默认开发仓库 `jinghuaswsx/AutoVideoSrtLocal`；旧服务器版仓库 `jinghuaswsx/AutoVideoSrt` 仅作迁移参考，建议另设远程名（如 `server-origin`）。
>
> 产品名称说明：仓库已切到本地版，但应用内产品名、页面标题、历史文档仍大量使用 `AutoVideoSrt`，这只是产品名而非仓库名。

## 0. 运行环境

| 环境 | 地址 | 目录 | systemd |
|------|------|------|---------|
| 线上 | `http://172.30.254.14/` | `/opt/autovideosrt` | `autovideosrt.service` |
| 测试 | `http://172.30.254.14:8080/` | `/opt/autovideosrt-test` | `autovideosrt-test.service` |
| 本地开发 | `http://127.0.0.1:5000` | 仓库工作区 | — |

- 数据目录：`/data/autovideosrt/uploads`、`/data/autovideosrt/output`
- MySQL 库：`auto_video`（不在 Windows 开发机本地安装 MySQL，统一连测试环境）
- 部署：`gunicorn` 直接监听 `80`，无 nginx；详见 `deploy/`、`docs/server-environments.md`

## 1. 当前业务能力

`projects.type` 枚举（见 `db/schema.sql`）：

| 类型 | 说明 |
|------|------|
| `translation` | 中文 → 英文本土化视频（主线） |
| `de_translate` / `fr_translate` / `ja_translate` | 德/法/日 单语视频翻译 |
| `multi_translate` / `bulk_translate` | 多语言并行 / 批量翻译 |
| `text_translate` / `copywriting_translate` | 纯文本翻译 / 文案翻译 |
| `copywriting` | 关键帧 + 商品信息 → 带货文案 + 配音 |
| `image_translate` | 商详图片翻译（Seedream 等） |
| `subtitle_removal` | 视频内嵌字幕擦除（goodline / 火山 VOD 双 provider） |
| `video_review` | Gemini / OpenRouter 视频质量评测 |
| `video_creation` | Seedance 视频生成 |
| `link_check` | 商品落地页与素材一致性检查 |
| `translate_lab` | 翻译实验台（调试 Prompt / 对比模型） |

外围功能：

- **素材库 `medias`**：项目当前的内容中枢，统一管理原始素材、本土化产物、推送状态（路由 `web/routes/medias.py`，是热点路径）
- **推送管理 `pushes`**：把本土化成品 push 到外部站点（newjoyloo 等）
- **OpenAPI**：`web/routes/openapi_materials.py` 暴露给 link-check / Shopify localizer 等外部工具
- **订单分析 `order_analytics`**：店小秘订单回流统计
- **定时任务 `scheduled_tasks`**：APScheduler 调度物料评测、字幕擦除轮询、Shopify 同步等
- **桌面工具**：`link_check_desktop/`（Tk GUI + Playwright）、`tools/shopify_image_localizer/`

## 2. 架构总览

```text
Browser / Templates
  → web/routes/*.py            HTTP / SocketIO 蓝图
  → web/services/*.py          薄适配层（EventBus → SocketIO）
  → appcore/runtime*.py        任务编排，决定步骤顺序与状态流转
  → pipeline/*.py              纯处理：ffmpeg / ASR / 翻译 / TTS / 合成 / CapCut / Seedance
  → appcore/task_state.py      内存任务态 + DB state_json 双写
  → appcore/llm_client.py      统一 LLM 入口（UseCase → Binding → Adapter）
  → appcore/db.py + db/schema.sql
```

四层约定：

1. `web/`：HTTP 路由、模板、SocketIO 房间、前端工作台
2. `appcore/`：任务状态、运行时编排、DB、密钥/设置/调度/清理
3. `pipeline/`：具体处理能力，无状态、无 web 依赖
4. `db/`：schema、迁移、初始化脚本

## 3. 目录速查

```text
main.py                         启动入口；启 socketio + APScheduler
config.py                       env 读取、必填凭据校验
.env.example                    凭据模板

appcore/
  runtime.py / runtime_de.py / runtime_fr.py / runtime_ja.py / runtime_multi.py
                                各语种翻译主流程编排（步骤、状态、事件）
  bulk_translate_*.py           批量翻译：plan / projection / runtime / recovery
  copywriting_runtime.py        文案创作编排
  image_translate_runtime.py    图片翻译编排
  subtitle_removal_runtime*.py  字幕擦除（含 VOD provider）
  link_check_*.py               链接对照检查
  medias.py                     素材库聚合查询、状态机
  task_state.py                 内存态 + DB state_json 同步
  task_recovery.py              冷启动回收中断任务（不自动重启 runner）
  scheduler.py                  APScheduler 单例
  llm_client.py + llm_use_cases.py + llm_bindings.py + llm_providers/
                                统一 LLM 调用三层架构
  ai_billing.py / usage_log.py / pricing.py
                                调用量与计费
  api_keys.py / settings.py     用户级密钥、系统设置
  tos_clients.py                火山 TOS 客户端（公网 / 私网双端点 + 签名 URL）
  cleanup.py                    过期项目 / 孤儿上传 / TOS 对象清理

pipeline/
  extract.py / asr.py / alignment.py    音频提取、豆包 ASR、分段
  localization*.py                       各语种 Prompt 与本土化规则
  translate*.py / text_translate.py      LLM 翻译封装
  tts*.py / audio_stitch.py              ElevenLabs TTS + 拼接
  duration_reconcile.py / speech_rate_model.py  时长校准
  subtitle*.py / timeline.py / compose.py 字幕生成、时间线、合成
  capcut.py                              CapCut / 剪映工程导出
  copywriting.py / keyframe.py           文案生成、关键帧
  video_review.py / video_score.py       视频评测
  seedance.py                            Seedance 视频生成
  voice_library*.py / voice_match.py / elevenlabs_voices.py
                                          音色库、匹配、ElevenLabs 同步
  shot_decompose.py / shot_notes.py / video_csk.py
                                          镜头解析与分镜批注
  languages/                             各语种字幕规则、prompt 默认值

web/
  app.py                        Flask 工厂；注册全部蓝图
  extensions.py / auth.py       SocketIO、Flask-Login
  store.py                      task_state 兼容 facade
  preview_artifacts.py          工作台前端依赖的 artifact 协议
  upload_util.py / tos_upload   上传与直传
  services/                     pipeline runner ↔ SocketIO 适配
  routes/                       30+ 蓝图，按业务模块切分（见 web/app.py 注册顺序）
  templates/ static/            页面模板与前端脚本（Ocean Blue 设计系统）

db/
  schema.sql                    主表
  migrations/                   增量 SQL
  migrate.py / create_admin.py  执行 schema、初始化管理员

deploy/                         systemd / gunicorn 配置 / publish.sh
scripts/                        一次性运维脚本（迁移、回填、smoke）
tools/                          shopify_image_localizer 等独立工具
link_check_desktop/             商详图片对照桌面端
docs/server-environments.md     服务器环境说明
docs/superpowers/               设计稿 + 实现计划（历史方案，不一定等于现状）
tests/                          按子系统切分的 pytest 用例
```

## 4. 主线翻译流程

英文翻译由 `appcore/runtime.py` 编排，固定步骤：

```
extract → asr → alignment → translate → tts → subtitle → compose → export
```

德/法/日 复用 `PipelineRunner`，只在 `runtime_de/fr/ja.py` 覆盖语言相关步骤；语言规则放在 `pipeline/localization_*.py` 与 `pipeline/languages/`。批量翻译走独立的 `appcore/bulk_translate_runtime.py`。

工作台前端依赖 `web/preview_artifacts.py` 返回的 artifact（`utterances` / `segments` / `tts_blocks` / `subtitle_chunks` / `side_by_side` / `download` 等）；后端步骤产物变化时同步检查前端渲染逻辑与对应测试。

## 5. 数据与状态

`projects` 主表关键字段：`id` `user_id` `type` `display_name` `status` `task_dir` `state_json` `expires_at` `deleted_at`。

任务态采用**双写**：

- 运行时优先写 `appcore.task_state` 进程内字典
- 每次更新同步回 `projects.state_json`
- 冷启动 `task_state.get()` 回退 DB 读取
- 启动回收（`task_recovery.recover_all_interrupted_tasks`）只标记中断状态，**不自动重启 runner**，避免重启风暴

其他主要表：`users` / `api_keys` / `user_voices` / `user_prompts` / `system_settings` / `usage_logs` / `ai_model_prices` / `copywriting_inputs` / `elevenlabs_voices` / `elevenlabs_voice_variants` / `voice_speech_rate`，以及各业务模块自带的迁移表（`db/migrations/` 下）。

## 6. LLM 统一调用

新代码一律走 `appcore.llm_client.invoke_chat / invoke_generate`，不要直接用 OpenAI SDK 或 `appcore.gemini`。

```python
from appcore import llm_client

result = llm_client.invoke_chat(
    "video_translate.localize",                # use_case code
    messages=[...],
    user_id=42, project_id="task-xxx",
    temperature=0.2,
)
```

三层架构：

| 层 | 职责 | 定义 |
|----|------|------|
| UseCase | 业务功能 → 默认 provider/model/usage_log service | `appcore/llm_use_cases.py` |
| Binding | UseCase → Provider × Model 运行时绑定（DB 可覆盖） | `appcore/llm_bindings.py` + `llm_use_case_bindings` 表 |
| Adapter | Provider → 具体 SDK 调用 | `appcore/llm_providers/` |

Adapter 枚举：`openrouter` / `doubao` / `gemini_aistudio` / `gemini_vertex`。管理员可在 `/settings?tab=bindings` 覆盖默认绑定，点「恢复默认」回到注册表值。

完整方案：`docs/superpowers/plans/2026-04-19-llm-call-unification.md`。

## 7. 本地启动

```bash
pip install -r requirements.txt
cp .env.example .env             # 填入数据库/存储/服务运行参数；FLASK_SECRET_KEY 必填
python db/migrate.py             # 执行 schema（连测试环境 MySQL）
python db/create_admin.py        # 初始化管理员
python main.py                   # 默认 http://127.0.0.1:5000
```

`.env` 只保留基础设施和运行参数：MySQL、TOS/VOD、服务地址、路径、推送参数、`FLASK_SECRET_KEY`。模型/API 供应商的 `api_key`、`base_url`、`model_id` 统一存入 `llm_provider_configs`，由管理员账号 `admin` 在 `/settings` 的「API 配置 / 服务商接入」维护，保存后新请求实时生效。

测试：

```bash
pytest tests -q                                       # 全量
pytest tests/test_pipeline_runner.py -q               # 翻译主流程
pytest tests/test_image_translate_runtime.py -q       # 图片翻译
pytest tests/test_appcore_medias_*.py -q              # 素材库
```

## 8. 改动入口速查

| 想改 | 先看 |
|------|------|
| 翻译流程 / Prompt / 文案结构 | `appcore/runtime.py`、`pipeline/localization.py`、`pipeline/translate.py`、`web/routes/task.py`、`web/preview_artifacts.py` |
| 德/法/日 规则 | `appcore/runtime_{de,fr,ja}.py`、`pipeline/localization_{de,fr}.py`、`pipeline/languages/`、对应 `web/routes/*_translate.py` |
| 批量翻译 | `appcore/bulk_translate_*.py`、`web/routes/bulk_translate.py` |
| 多语言并行 | `appcore/runtime_multi.py`、`web/routes/multi_translate.py` |
| 素材库 | `appcore/medias.py`、`web/routes/medias.py`、`web/static/medias.js`、`web/templates/medias_list.html` |
| 推送 | `appcore/pushes.py`、`web/routes/pushes.py` |
| 字幕擦除 | `appcore/subtitle_removal_runtime*.py`、`appcore/vod_*`、`web/routes/subtitle_removal.py` |
| 图片翻译 | `appcore/image_translate_runtime.py`、`appcore/image_translate_settings.py`、`web/routes/image_translate.py` |
| 链接对照 | `appcore/link_check_*.py`、`web/routes/link_check.py`、`link_check_desktop/` |
| TTS / 音色 | `pipeline/tts*.py`、`pipeline/voice_*.py`、`pipeline/elevenlabs_voices.py`、`web/routes/voice*.py` |
| 字幕 / 时间线 / 合成 | `pipeline/subtitle*.py`、`pipeline/timeline.py`、`pipeline/compose.py` |
| CapCut / 剪映导出 | `pipeline/capcut.py`、`web/routes/task.py` 下载/部署接口、`web/routes/settings.py` 剪映目录配置 |
| 文案 | `appcore/copywriting_runtime.py`、`pipeline/copywriting.py`、`pipeline/keyframe.py`、`web/routes/copywriting*.py` |
| 评测 / 生成 | `appcore/material_evaluation.py`、`pipeline/video_review.py`、`pipeline/seedance.py`、`web/routes/video_*.py` |
| 计费 / 调用量 | `appcore/ai_billing.py`、`appcore/usage_log.py`、`appcore/pricing.py`、`web/routes/admin_*.py` |
| 上传 / 权限 | `web/routes/task.py`、`web/routes/tos_upload.py`、`web/routes/projects.py`、`web/auth.py` |
| 定时任务 | `appcore/scheduler.py`、`appcore/scheduled_tasks.py`、各模块的 `*_scheduler.py` |
| OpenAPI | `web/routes/openapi_materials.py`（鉴权 key 来自 `llm_provider_configs.openapi_materials`） |

## 9. 关键约定

- **代码真相优先级**：`runtime / route / pipeline / tests` > `docs/superpowers/*` > `PLAN.md` / `readme_codex.md`
- **多用户隔离**是硬约束。新增路由继续走 `current_user.id` 过滤与资源归属校验
- 翻译工作台严重依赖 artifact 协议；后端结构变化要同步前端 `_task_workbench_*.html` 与对应测试
- 启动时不自动重启 runner，只回收状态；前端 / 用户主动恢复
- 前端遵循 `CLAUDE.md` 中的 **Ocean Blue Admin** 设计系统（hue 严格限定 200–240，禁止紫色）
- 不要在 Windows 开发机本地装/启 MySQL，所有数据库验证都连测试环境
- 大改动一律走 worktree（`.worktrees/` 已 ignore），`master` 仅限 hotfix

## 10. 第一次接手时的阅读顺序

1. `main.py` → `web/app.py`（启动与蓝图注册）
2. `db/schema.sql`（数据模型）
3. `appcore/runtime.py` + `appcore/task_state.py`（主线编排与状态）
4. `pipeline/localization.py` + `pipeline/translate.py` + `pipeline/tts.py` + `pipeline/subtitle.py` + `pipeline/compose.py` + `pipeline/capcut.py`（翻译主线）
5. `appcore/llm_client.py` + `appcore/llm_use_cases.py`（统一 LLM 调用）
6. `web/routes/medias.py` + `appcore/medias.py`（当前内容中枢）
7. `web/routes/bulk_translate.py` + `appcore/bulk_translate_runtime.py`（批量场景）

读完这一组，可以开始接手任意子模块。
