# Omni TTS Card Collapse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a right-side expand/collapse control to the Omni shared「语音生成」card so long TTS logs can be hidden.

**Architecture:** Keep the feature inside the existing shared task workbench templates. The HTML provides one button in `#step-tts`; CSS styles the button and collapsed state; JS stores the collapsed state by task ID and reapplies it after live refreshes.

**Tech Stack:** Flask Jinja templates, vanilla JavaScript, pytest static template assertions.

---

### Task 1: Template Regression Test

**Files:**
- Modify: `tests/test_translate_detail_shell_templates.py`

- [ ] **Step 1: Write the failing test**

Add a test that reads `_task_workbench.html`, `_task_workbench_scripts.html`, and `_task_workbench_styles.html`, then asserts:

```python
def test_tts_card_has_expand_collapse_control():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "_task_workbench.html").read_text(encoding="utf-8")
    script = (root / "web" / "templates" / "_task_workbench_scripts.html").read_text(encoding="utf-8")
    styles = (root / "web" / "templates" / "_task_workbench_styles.html").read_text(encoding="utf-8")

    assert 'id="ttsCardCollapseToggle"' in template
    assert 'aria-controls="preview-tts ttsDurationLog"' in template
    assert "syncTtsCardCollapseState" in script
    assert "ttsCardCollapsed:" in script
    assert "tts-card-collapsed" in script
    assert "preview-tts" in script
    assert "ttsDurationLog" in script
    assert "aria-expanded" in script
    assert ".tts-card-collapse-toggle" in styles
    assert ".step.tts-card-collapsed #preview-tts" in styles
    assert ".step.tts-card-collapsed #ttsDurationLog" in styles
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_translate_detail_shell_templates.py::test_tts_card_has_expand_collapse_control -q
```

Expected: fail because the button and JS helper do not exist yet.

### Task 2: Add Collapse UI

**Files:**
- Modify: `web/templates/_task_workbench.html`
- Modify: `web/templates/_task_workbench_styles.html`
- Modify: `web/templates/_task_workbench_scripts.html`

- [ ] **Step 1: Add the button**

In `#step-tts .step-name-row`, add a button with:

```html
<button type="button"
        class="tts-card-collapse-toggle"
        id="ttsCardCollapseToggle"
        aria-controls="preview-tts ttsDurationLog"
        aria-expanded="true">收拢</button>
```

- [ ] **Step 2: Add styles**

Add a small token-based button style and collapsed selectors:

```css
.tts-card-collapse-toggle { ... }
.step.tts-card-collapsed #preview-tts,
.step.tts-card-collapsed #ttsDurationLog { display: none !important; }
```

- [ ] **Step 3: Add state logic**

In `_task_workbench_scripts.html`, add `syncTtsCardCollapseState()` and call it after `renderStepPreviews(...)` and `renderTtsDurationLog()`. The function reads/writes `localStorage` key `ttsCardCollapsed:${taskId || "global"}`, toggles `.tts-card-collapsed`, and updates button text plus `aria-expanded`.

- [ ] **Step 4: Run focused test**

Run:

```bash
pytest tests/test_translate_detail_shell_templates.py::test_tts_card_has_expand_collapse_control -q
```

Expected: pass.

### Task 3: Route Smoke Tests

**Files:**
- No code changes expected.

- [ ] **Step 1: Run shared template and Omni route tests**

Run:

```bash
pytest tests/test_translate_detail_shell_templates.py tests/test_omni_translate_routes.py -q
```

Expected: pass.

- [ ] **Step 2: Run local route smoke**

Start the dev server on an available local port and request an unauthenticated Omni detail URL.

Expected: HTTP 302 for unauthenticated access, not 500.
