# LLM Provider DB-config —— 实现落地记录 (2026-04-25)

> 对应需求文档：[2026-04-25-llm-provider-db-config.md](./2026-04-25-llm-provider-db-config.md)

**分支**：`codex/claude-llm-provider-db-config`
**Worktree**：`.worktrees/claude-llm-provider-db-config`
**基线**：`origin/master @ bb17a86`

## 1. 验收结论

| 验收项 | 结果 |
|---|---|
| 所有模型 / API 供应商 key、base_url、default model 不从 .env 读取 | ✅ |
| 新增 `llm_provider_configs` 表 + migration（db/migrations/2026_04_25_llm_provider_configs.sql） | ✅ |
| admin 可在 /settings「服务商接入」分组、明文、逐字段编辑 + 复制按钮 | ✅ |
| 非 admin 访问 /settings 返回 403 | ✅（复用旧 `admin_config_required` 装饰器） |
| 所有敏感字段明文，`type="password"` 绝不出现 | ✅（测试断言覆盖） |
| 14 个 provider_code 独立配置、彼此无静默回落 | ✅（`_LEGACY_SERVICE_MAP` + 独立 `_resolve_*_credentials` helper） |
| 修改 DB 后新请求立即生效，无进程级缓存 | ✅（DAO 无缓存，每次命中 DB 单行主键查询） |
| `llm_use_case_bindings` 未被破坏 | ✅（settings bindings Tab + routes 未改动路由逻辑） |
| 聚焦 pytest 全绿 | ✅ `142 passed` |
| 禁止 Windows 本地初始化 MySQL | ✅（所有测试不需 live DB；全量套件里因无 MySQL 而失败的全是 pymysql 相关预期失败） |

## 2. 交付物

### 数据库
- `db/migrations/2026_04_25_llm_provider_configs.sql`：建表 + 种子 14 行 + 默认 base_url 回填 + 幂等地从 admin api_keys 迁移已有凭据
- `db/schema.sql`：追加建表语句供新库初始化使用
- `appcore/db_migrations.py`（未改动）启动时自动 apply

### 运行时统一入口
- `appcore/llm_provider_configs.py`：DAO + `LlmProviderConfig` dataclass + `ProviderConfigError` + `credential_provider_for_adapter`
- `appcore/api_keys.py`：移除 env fallback；`_LEGACY_SERVICE_MAP` 把老 service 名路由到新 provider_code；`set_key` 拒写供应商 service
- `config.py`：瘦身到只保留基础设施（TOS/VOD/DB/路径/端口/flag），供应商常量全部删除

### Adapter / 其他 appcore 模块
- `appcore/llm_providers/openrouter_adapter.py`（OpenRouter + Doubao）
- `appcore/llm_providers/gemini_vertex_adapter.py`
- `appcore/llm_providers/gemini_aistudio_adapter.py`
- `appcore/gemini.py`（文本 + 视频多模态 Gemini）
- `appcore/gemini_image.py`（5 通道图像生成，独立凭据 helper）
- `appcore/subtitle_removal_provider.py`
- `appcore/voice_library_sync_task.py`
- `appcore/image_translate_runtime.py`（recovery 路径 APIMART key）

### Pipeline
- `pipeline/translate.py`（`_call_vertex_json` 走 gemini_cloud_text 行的 api_key 或 project）
- `pipeline/asr.py`（doubao_asr 行的 api_key + extra_config.resource_id）
- `pipeline/tts.py`（ElevenLabs 每次新建 client，改 key 立即生效）
- `pipeline/elevenlabs_voices.py`
- `pipeline/copywriting.py`

### Web / UI
- `web/routes/settings.py`：providers Tab 完全 DAO 驱动；`provider_<code>_api_key|base_url|model_id|extra_config` 四字段 POST；extra_config 解析 JSON；translate_pref / image_translate_channel / OpenAI Image 2 开关保留旧路径
- `web/templates/settings.html`：providers Tab 按 group 渲染；每行 provider_code 独立 inputs；extra_config textarea；保留 bindings/pricing/push 三个 Tab 不变；复制按钮仍由页脚 JS 自动挂载
- `web/routes/openapi_materials.py`：X-API-Key 从 `openapi_materials` provider_code 读取

### 测试
- `tests/test_llm_provider_configs.py`：DAO 全覆盖 + 静态防护（禁止 import os/dotenv）+ 环境变量穿透检查
- `tests/test_appcore_api_keys.py`：重写为"legacy service 映射 DAO + 无 env fallback"
- `tests/test_llm_providers_openrouter.py`：独立 openrouter_text / openrouter_image；DoubaoAdapter 不回落 Seedream/ASR
- `tests/test_gemini_image.py`：通道级凭据 helper mock；独立 provider_code 断言
- `tests/test_settings_routes_new.py`：新 provider_<code>_* 字段 + extra_config JSON 解析 + 非法 JSON 拒写
- `tests/test_image_translate_runtime.py`：recovery 路径 APIMART key 经 DAO helper

## 3. 14 个 provider_code 速查

| provider_code | group | 负责的业务入口 |
|---|---|---|
| openrouter_text | text_llm | OpenRouter 文本 / 本土化 LLM |
| openrouter_image | image | OpenRouter 图片模型 |
| gemini_aistudio_text | text_llm | Gemini AI Studio 文本 |
| gemini_aistudio_image | image | Gemini AI Studio 图片 |
| gemini_cloud_text | text_llm | Google Cloud / Vertex AI 文本 |
| gemini_cloud_image | image | Google Cloud / Vertex AI 图片 |
| doubao_llm | text_llm | 豆包 ARK 文本模型 |
| doubao_seedream | image | 豆包 Seedream 图片生成 |
| doubao_asr | asr | 火山 ASR（原 VOLC_API_KEY） |
| seedance_video | video | Seedance 视频生成 |
| apimart_image | image | APIMART / GPT Image 2 |
| elevenlabs_tts | tts | ElevenLabs 配音 |
| subtitle_removal | aux | 字幕移除服务 |
| openapi_materials | aux | 素材 OpenAPI |

## 4. 发布流程

### 测试环境（默认先发这里）
```bash
ssh 172.30.254.14  # 走内网
cd /opt/autovideosrt-test
git fetch origin
git checkout codex/claude-llm-provider-db-config
sudo systemctl restart autovideosrt-test.service
# migration 会在启动时由 appcore/db_migrations.py 自动应用
```
验证 checklist：
1. 打开 http://172.30.254.14:8080/settings，确认 admin 可见、分组展示、明文 + 复制按钮
2. 非 admin 登录访问 /settings 应得 403
3. 填一组 provider key 保存；刷新页面后字段仍显示
4. 触发一次 ASR 任务 / 一次翻译任务 / 一次图片翻译任务验证 DB 凭据被读取
5. 改 DB 后无需重启，新建任务立即使用新 key

### 生产环境（测试验证通过后）
合并到 master → push → 部署到 `/opt/autovideosrt` → `systemctl restart autovideosrt.service`

## 5. 已知限制 / 后续优化

- `scripts/debug_vertex*.py`：离线调试脚本仍写死 `GEMINI_CLOUD_API_KEY` 读取，未改（不在主线运行路径，首次上线不阻断）。
- `pipeline/copywriting.py` 的 `_resolve_model_only`：model_id 的 fallback 从 `config.CLAUDE_MODEL / DOUBAO_LLM_MODEL` 切到内联常量；如需管理员覆盖，应改为 `llm_provider_configs.*.model_id`。
- 全量 pytest 在 Windows 开发机跑不通是因为大量测试需要 live MySQL（无本地 MySQL 是项目硬规则），不是本次改动引入；所有聚焦测试 142/142 全绿。

## 6. 提交列表

```
fed6ca1 feat(llm-provider-config): add llm_provider_configs schema + migration
de4859d feat(llm-provider-config): add llm_provider_configs DAO with 23 unit tests
5b47dae refactor(config): drop supplier env reads; keep infra/storage/path only
099155c refactor(llm): route api_keys + adapters + gemini + subtitle + voice-lib through DAO
fe56012 refactor(gemini-image): route image channels through llm_provider_configs
5d22f80 refactor(pipeline): route supplier credentials through llm_provider_configs
8159ffc refactor(openapi): read X-API-Key from llm_provider_configs.openapi_materials
d9ed959 refactor(settings): DB-driven providers tab + per-provider_code inputs
d027067 test(llm-provider-config): retarget tests onto DAO-driven provider resolution
```
