# Medias Task Source Filename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让商品级翻译任务管理页展示每个批量任务实际选中的原始视频文件名，并让视频子任务摘要显示具体文件名。

**Architecture:** 在 `appcore.bulk_translate_projection` 中补充原始视频名称解析，再由 `web/static/medias_translation_tasks.js` 渲染到父任务卡片摘要。数据来源仍以父任务 `state` 为主，旧任务通过 `plan.ref` 回退兼容。

**Tech Stack:** Python, Flask projection layer, vanilla JavaScript, pytest

---

### Task 1: 锁定投影层输出

**Files:**
- Modify: `tests/test_bulk_translate_projection.py`
- Modify: `appcore/bulk_translate_projection.py`

- [ ] **Step 1: Write the failing test**

为 `list_product_tasks()` 新增断言，要求父任务返回 `raw_source_display_names`，并要求视频子任务 `summary` 使用文件名而不是 `原始视频 #id`。

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_bulk_translate_projection.py -q`
Expected: 新增断言失败，证明当前投影未输出文件名

- [ ] **Step 3: Write minimal implementation**

在投影层增加原始视频 id 收集、名称解析和父任务/视频子任务序列化字段。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_bulk_translate_projection.py -q`
Expected: 新增断言通过

### Task 2: 渲染父任务卡片元信息

**Files:**
- Modify: `tests/test_medias_translation_assets.py`
- Modify: `web/static/medias_translation_tasks.js`

- [ ] **Step 1: Write the failing test**

新增脚本断言，要求任务卡片元信息里渲染 `原始视频` 和 `task.raw_source_display_names`。

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_medias_translation_assets.py -q`
Expected: 新增断言失败，证明页面尚未展示该信息

- [ ] **Step 3: Write minimal implementation**

在 `renderTaskCard()` 的任务元信息中追加 `原始视频` 文案，复用后端返回的文件名列表。

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_medias_translation_assets.py -q`
Expected: 新增断言通过

### Task 3: 回归验证

**Files:**
- Test: `tests/test_bulk_translate_projection.py`
- Test: `tests/test_medias_translation_tasks_routes.py`
- Test: `tests/test_medias_translation_assets.py`

- [ ] **Step 1: Run focused regression suite**

Run: `pytest tests/test_bulk_translate_projection.py tests/test_medias_translation_tasks_routes.py tests/test_medias_translation_assets.py -q`
Expected: 与本次改动相关的新增与既有用例通过；若存在无关基线失败，单独记录

- [ ] **Step 2: Record verification notes**

在最终说明中明确：父任务卡片和视频子任务摘要都已切到文件名展示，并注明是否存在无关基线失败。
