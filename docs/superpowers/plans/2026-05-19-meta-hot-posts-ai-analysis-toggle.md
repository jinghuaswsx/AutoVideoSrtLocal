# Meta 热帖 AI 分析显示开关 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persisted Meta hot-post page toggle that shows or hides card-level AI analysis blocks.

**Architecture:** Keep API data unchanged and implement the behavior entirely in `web/templates/meta_hot_posts.html`. A boolean frontend state controls whether `copyabilityBlock(row)` and `renderEuropeFitPanel(row)` render content, with `localStorage` persistence.

**Tech Stack:** Flask Jinja template, vanilla JavaScript, pytest route/template assertions.

---

### Task 1: Template Toggle Contract

**Files:**
- Modify: `tests/test_meta_hot_posts_routes.py`
- Modify: `web/templates/meta_hot_posts.html`

- [ ] **Step 1: Write the failing test**

Assert the Meta hot-post page includes `显示AI分析`, `关闭AI分析`, `mhShowAiAnalysis`, `localStorage.setItem('mhShowAiAnalysis'`, `toggleMetaHotAiAnalysis()`, and guard clauses in `copyabilityBlock(row)` and `renderEuropeFitPanel(row)`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_meta_hot_posts_routes.py::test_meta_hot_posts_page_renders_tabs_and_api -q`

Expected: fail because the toggle does not exist yet.

- [ ] **Step 3: Implement minimal template change**

Add the toggle button to the existing tool/status control area, add JS state/restore/toggle helpers, and add `if (!mhShowAiAnalysis) return '';` to both AI analysis render functions.

- [ ] **Step 4: Run route test and focused regression**

Run: `pytest tests/test_meta_hot_posts_routes.py -q`

Expected: pass.
