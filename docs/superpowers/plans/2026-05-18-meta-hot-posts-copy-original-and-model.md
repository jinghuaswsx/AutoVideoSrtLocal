# Meta 热帖文案原文保留与模型绑定 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Meta 热帖页面移除运营执行按钮，在翻译文案旁提供原文切换，并固定相关文案翻译 use case 使用 OpenRouter Gemini 3.1 Flash-Lite。

**Architecture:** 后端服务层继续负责把英文原文和中文翻译同时返回，并新增显式翻译状态字段。前端只做展示切换，不触发重新翻译。模型约束通过 `USE_CASES` 默认值和 DB migration 双层保证。

**Tech Stack:** Flask/Jinja 模板、内联页面 JavaScript、Python service hydration、MySQL migration、pytest。

---

### Task 1: 写失败测试

**Files:**
- Modify: `tests/test_meta_hot_posts_routes.py`
- Modify: `tests/test_meta_hot_posts_service.py`
- Modify: `tests/test_llm_use_cases_registry.py`
- Modify: `tests/test_db_migration_meta_hot_posts_marked.py`

- [ ] **Step 1: 路由模板测试**

断言页面仍保留“类目分析提示词”和“商品分析失败记录”，但不再渲染工具栏执行按钮；断言存在 `renderMessageBlock(row)`、`toggleMetaHotPostSourceMessage(event)` 和“显示原文案”。

- [ ] **Step 2: 服务层测试**

在已翻译消息 hydrate 后断言 `message_html` 是中文、`message_source_html` 是英文原文、`message_is_translated is True`。

- [ ] **Step 3: 模型绑定测试**

断言 `meta_hot_posts.translate_message`、`title_translate.generate`、`copywriting_translate.generate` 默认 provider/model/service 均为 `openrouter / google/gemini-3.1-flash-lite / openrouter`。

- [ ] **Step 4: 迁移测试**

断言新 migration 文件包含三个 use case，并使用 `ON DUPLICATE KEY UPDATE` 覆盖 `provider_code`、`model_id`、`enabled`。

- [ ] **Step 5: 运行 RED**

Run: `pytest tests/test_meta_hot_posts_routes.py::test_meta_hot_posts_page_renders_tabs_and_api tests/test_meta_hot_posts_service.py::test_build_list_response_prefers_translated_chinese_message tests/test_llm_use_cases_registry.py::test_copy_translation_use_cases_default_to_openrouter_flash_lite tests/test_db_migration_meta_hot_posts_marked.py::test_meta_hot_posts_copy_translation_model_binding_migration -q`

Expected: FAIL，分别指向模板按钮仍存在、`message_is_translated` 缺失、默认模型仍为 Gemini 3 Flash 或 migration 文件缺失。

### Task 2: 实现服务层与模型绑定

**Files:**
- Modify: `appcore/meta_hot_posts/service.py`
- Modify: `appcore/llm_use_cases.py`
- Create: `db/migrations/2026_05_18_meta_hot_posts_copy_translation_flash_lite_binding.sql`

- [ ] **Step 1: 服务层 hydrate**

在 `_hydrate_item` 中保留现有 `message_source_html`，并新增 `message_is_translated = bool(translated_message)`。

- [ ] **Step 2: use case 默认值**

把 `title_translate.generate` 和 `copywriting_translate.generate` 的默认 model 改为 `google/gemini-3.1-flash-lite`；`meta_hot_posts.translate_message` 保持同一值。

- [ ] **Step 3: DB migration**

新增 migration，插入或更新三个 use case 到 `openrouter / google/gemini-3.1-flash-lite`。

- [ ] **Step 4: 运行 GREEN 子集**

Run: Task 1 中的同一条 pytest 命令。

Expected: PASS。

### Task 3: 实现页面展示

**Files:**
- Modify: `web/templates/meta_hot_posts.html`
- Test: `tests/test_meta_hot_posts_routes.py`

- [ ] **Step 1: 移除工具栏执行按钮**

删除 `refreshMetaHotPosts`、`analyzeMetaHotPosts`、`translateMetaHotPostMessages`、`localizeMetaHotPostVideos`、`assessEuropeFitMaterials`、`analyzeMetaHotPostVideos`、`showVideoCopyabilityTop50` 对应按钮，保留排查按钮。

- [ ] **Step 2: 添加卡片文案切换**

新增 `renderMessageBlock(row)`，当 `row.message_is_translated && row.message_source_html` 时，在中文文案后渲染按钮和 data 字段；新增 `toggleMetaHotPostSourceMessage(event)` 切换 `innerHTML` 与按钮文案。

- [ ] **Step 3: 样式**

新增轻量 `.mh-message-wrap`、`.mh-message-toggle`，按钮位于文案后，不挤压视频区域。

- [ ] **Step 4: 运行模板测试**

Run: `pytest tests/test_meta_hot_posts_routes.py::test_meta_hot_posts_page_renders_tabs_and_api -q`

Expected: PASS。

### Task 4: 最终验证

**Files:**
- No code changes.

- [ ] **Step 1: 聚焦回归**

Run: `pytest tests/test_meta_hot_posts_routes.py tests/test_meta_hot_posts_service.py tests/test_meta_hot_posts_message_translation.py tests/test_llm_use_cases_registry.py tests/test_db_migration_meta_hot_posts_marked.py -q`

Expected: PASS。

- [ ] **Step 2: 语法检查**

Run: `python -m compileall appcore/meta_hot_posts/service.py appcore/llm_use_cases.py`

Expected: exit 0。
