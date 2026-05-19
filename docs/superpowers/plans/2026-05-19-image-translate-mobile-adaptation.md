# 图片翻译页面移动端适配 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让图片翻译列表页和详情页在手机宽度下不横向撑爆，并保留现有桌面布局。

**Architecture:** 只做图片翻译页面级模板和样式改动，不碰全局移动端壳层、不改后端接口。用静态测试锁定移动端结构类、docs anchor 和关键 CSS 选择器。

**Tech Stack:** Flask Jinja2 templates, page-scoped CSS, pytest static contract tests.

---

### Task 1: Lock Mobile Contract

**Files:**
- Modify: `tests/test_web_routes.py`

- [ ] **Step 1: Write the failing test**

Add `test_image_translate_templates_have_mobile_adaptation_contract()` near existing image-translate template tests. It reads `image_translate_list.html` and `_image_translate_styles.html`, then asserts the new tabs/table wrappers and mobile CSS selectors exist.

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
pytest tests/test_web_routes.py::test_image_translate_templates_have_mobile_adaptation_contract -q
```

Expected: fail because the wrappers and docs-anchored mobile CSS do not exist yet.

### Task 2: Implement Mobile Structure And CSS

**Files:**
- Modify: `web/templates/image_translate_list.html`
- Modify: `web/templates/_image_translate_styles.html`

- [ ] **Step 1: Add wrappers**

Wrap the historical tabs with `class="it-history-tabs"` and the historical table with `class="it-history-table-wrap"`.

- [ ] **Step 2: Add page-scoped mobile CSS**

Add a `Docs-anchor` comment referencing `docs/superpowers/specs/2026-05-19-image-translate-mobile-adaptation-design.md`, then add `@media (max-width: 767px)` rules for `.it-shell`, `.card`, `.it-pill-group`, `.it-history-tabs`, `.it-history-table-wrap`, `.it-meta-grid`, `.it-item`, and `.it-item-actions`.

- [ ] **Step 3: Run focused tests**

Run:

```bash
pytest tests/test_web_routes.py::test_image_translate_templates_have_mobile_adaptation_contract tests/test_web_routes.py::test_image_translate_templates_show_concurrency_mode_pills tests/test_web_routes.py::test_image_translate_list_template_has_task_channel_controls -q
```

Expected: pass.

### Task 3: Verify The Touched Area

**Files:**
- Test only.

- [ ] **Step 1: Run route/template regression**

Run:

```bash
pytest tests/test_web_routes.py -q
```

Expected: pass.

- [ ] **Step 2: Run a mobile smoke check when the dev server is available**

Run a local dev server on a free port and use an iPhone viewport to verify `/image-translate` does not produce document-level horizontal overflow.
