# 明空入库进度弹窗移动端 AI 评估适配 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让“加入素材库”进度弹窗中的 AI 精细评估建议在手机上按国家纵向展示，避免横向表格难滑动。

**Architecture:** `web/templates/mk_selection.html` 继续作为现有页面的单文件 Jinja/JS/CSS 容器；渲染函数同时输出桌面表格和移动端国家卡片，CSS 按 `max-width: 560px` 切换。测试使用现有静态模板断言，覆盖移动端 class 与响应式规则。

**Tech Stack:** Python 3.12, Flask/Jinja, inline JavaScript, CSS media query, pytest static route/template tests.

---

### Task 1: 静态测试先行

**Files:**
- Modify: `tests/test_mk_selection_routes.py`

- [ ] **Step 1: Write the failing test**

Add a test near `test_mk_import_progress_modal_uses_full_padded_overlay_width`:

```python
def test_mk_import_progress_fine_ai_panel_has_mobile_card_layout():
    template = Path("web/templates/mk_selection.html").read_text(encoding="utf-8")

    assert "mki-progress-fine-ai-mobile-list" in template
    assert "mki-progress-fine-ai-mobile-card" in template
    assert "function mkiImportProgressFineAiMobileCards(result)" in template
    assert "@media (max-width: 560px)" in template
    assert ".mki-progress-fine-ai-scroll { display: none; }" in template
    assert ".mki-progress-fine-ai-mobile-list { display: grid; }" in template
```

- [ ] **Step 2: Verify RED**

Run:

```bash
pytest tests/test_mk_selection_routes.py::test_mk_import_progress_fine_ai_panel_has_mobile_card_layout -q
```

Expected: fail because the mobile card layout function/classes do not exist yet.

### Task 2: Mobile AI Panel Markup And CSS

**Files:**
- Modify: `web/templates/mk_selection.html`

- [ ] **Step 1: Implement minimal render helper**

Add `mkiImportProgressFineAiMobileCards(result)` near `mkiImportProgressFineAiTable(result)`. The helper must call `mkiFineAiProgressTableRows(result)` and render one `.mki-progress-fine-ai-mobile-card` per country.

- [ ] **Step 2: Include mobile cards in existing renderer**

Update `mkiImportProgressFineAiTable(result)` to return the existing table plus `${mkiImportProgressFineAiMobileCards(result)}`.

- [ ] **Step 3: Add responsive CSS**

Default:

```css
.mki-progress-fine-ai-mobile-list { display: none; }
```

Under the existing `@media (max-width: 560px)`:

```css
.mki-progress-fine-ai-scroll { display: none; }
.mki-progress-fine-ai-mobile-list { display: grid; }
```

### Task 3: Verify

**Files:**
- Test: `tests/test_mk_selection_routes.py`
- Test: `tests/test_xuanpin_routes.py`

- [ ] **Step 1: Verify focused tests**

Run:

```bash
pytest tests/test_mk_selection_routes.py tests/test_xuanpin_routes.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Verify compile and diff**

Run:

```bash
python3 -m compileall web tests -q
git diff --check
```

Expected: both commands pass with no output indicating errors.
