# Meta 热帖文案翻译模型与调度设计

## 背景

Meta 热帖文案翻译运行 use case 是 `meta_hot_posts.translate_message`，用于把热帖英文文案缓存为简体中文 `message_zh_html`。当前该 use case 与部分共享文案翻译入口一同被迁移到 OpenRouter Gemini 3.1 Flash-Lite；运营希望 Meta 热帖文案翻译独立配置，默认改用 OpenRouter Gemini 3 Flash，并提高定时任务节奏。

## Scope

1. `meta_hot_posts.translate_message` 默认绑定改为 `openrouter / google/gemini-3-flash-preview / openrouter`。
2. `/settings?tab=providers` 新增 Meta 热帖文案翻译专属配置项，只保存 `meta_hot_posts.translate_message` 这一条 binding。
3. 可选 provider 为 `gemini_aistudio`、`gemini_vertex`、`gemini_vertex_adc`、`openrouter`；模型选项统一显示为 Gemini 3 Flash 与 Gemini 3 Flash-Lite。
4. OpenRouter 保存 `google/gemini-3-flash-preview` 或 `google/gemini-3.1-flash-lite`；Google AI Studio / Vertex / Vertex ADC 保存不带 `google/` 前缀的模型 id。
5. 定时任务保持每 10 分钟触发，每轮默认 30 条，条目之间不额外 sleep。
6. 新增 migration 覆盖旧 Flash-Lite binding，仅影响 `meta_hot_posts.translate_message`。

## Verification

- `tests/test_settings_routes_new.py` 覆盖专属配置项渲染与保存。
- `tests/test_llm_use_cases_registry.py` 覆盖默认模型。
- `tests/test_llm_bindings_dao.py` 覆盖该 use case 允许 Vertex ADC。
- `tests/test_meta_hot_posts_scheduler.py` 与 `tests/test_appcore_scheduled_tasks.py` 覆盖 10 分钟 / 30 条 / 0 秒间隔。
- `tests/test_db_migration_meta_hot_posts_translate_gemini3_flash.py` 覆盖 DB binding 迁移。
