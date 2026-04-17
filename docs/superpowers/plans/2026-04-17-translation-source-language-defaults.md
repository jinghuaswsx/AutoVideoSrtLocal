# Translation Source Language Defaults Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the English video translation module default to Chinese source language, and make the German and French video translation modules default to English source language across both initial UI rendering and new-task state.

**Architecture:** Keep the change narrow by updating creation-time task state in the three TOS-complete routes, and by threading an explicit page-level default into the shared workbench template used by German and French. English keeps its own detail-page fallback at `zh`, with a regression test to ensure that behavior stays intact.

**Tech Stack:** Flask, Jinja2 templates, in-memory `appcore.task_state`, pytest

---

### Task 1: Add failing tests for creation-time source language defaults

**Files:**
- Modify: `tests/test_tos_upload_routes.py`

- [ ] **Step 1: Write the failing tests**

Add assertions that the three task-creation complete flows persist the expected `source_language`:

```python
assert task["source_language"] == "zh"
assert task["source_language"] == "en"
assert task["source_language"] == "en"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tos_upload_routes.py -q -k "source_language or pure_tos"`
Expected: FAIL because the complete routes do not yet persist `source_language`.

- [ ] **Step 3: Write minimal implementation**

Update the English, German, and French TOS-complete routes to pass the module default into `store.update(...)`:

```python
store.update(
    task_id,
    ...,
    source_language="zh",
)
```

```python
store.update(
    task_id,
    ...,
    source_language="en",
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tos_upload_routes.py -q -k "source_language or pure_tos"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_tos_upload_routes.py web/routes/tos_upload.py web/routes/de_translate.py web/routes/fr_translate.py
git commit -m "fix: persist module source language defaults"
```

### Task 2: Add failing tests for detail-page initial selection

**Files:**
- Modify: `tests/test_web_routes.py`
- Modify: `web/templates/de_translate_detail.html`
- Modify: `web/templates/fr_translate_detail.html`
- Modify: `web/templates/_task_workbench.html`
- Modify: `web/templates/_task_workbench_scripts.html`

- [ ] **Step 1: Write the failing tests**

Add detail-page rendering tests that check the initial selected source-language button:

```python
assert 'data-default-source-language="en"' in body
assert 'data-lang="en" class="sl-btn sl-active"' in body
```

and keep an English regression assertion that the English page still falls back to Chinese.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_web_routes.py -q -k "source_language_default or de_translate_detail or fr_translate_detail"`
Expected: FAIL because the shared workbench markup still hard-codes the Chinese button as active.

- [ ] **Step 3: Write minimal implementation**

Thread a page variable like `default_source_language` into the shared workbench and use it to set:

```jinja2
{% set _default_source_language = default_source_language|default('zh') %}
```

```jinja2
data-default-source-language="{{ _default_source_language }}"
```

```jinja2
class="sl-btn{% if _default_source_language == 'en' %} sl-active{% endif %}"
```

Update the workbench script fallback to:

```javascript
const defaultLang = root?.dataset.defaultSourceLanguage || "zh";
const lang = (currentTask && currentTask.source_language) || defaultLang;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_web_routes.py -q -k "source_language_default or de_translate_detail or fr_translate_detail"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_web_routes.py web/templates/de_translate_detail.html web/templates/fr_translate_detail.html web/templates/_task_workbench.html web/templates/_task_workbench_scripts.html
git commit -m "fix: render module-specific source language defaults"
```

### Task 3: Run focused regression coverage for the full change

**Files:**
- No code changes required unless regressions are found

- [ ] **Step 1: Run focused regression suite**

Run:

```bash
pytest tests/test_tos_upload_routes.py -q
pytest tests/test_web_routes.py -q -k "source_language_default or de_translate_detail or fr_translate_detail"
```

Expected: PASS

- [ ] **Step 2: Run existing upload-validation smoke test**

Run:

```bash
pytest tests/test_security_upload_validation.py -q -k "local_mp4_upload_and_requires_tos_direct_upload"
```

Expected: PASS

- [ ] **Step 3: If a regression appears, fix minimally and rerun the failing command**

Use the smallest fix needed in the touched route/template file before moving on.

- [ ] **Step 4: Final commit**

```bash
git add tests/test_tos_upload_routes.py tests/test_web_routes.py tests/test_security_upload_validation.py web/routes/tos_upload.py web/routes/de_translate.py web/routes/fr_translate.py web/templates/de_translate_detail.html web/templates/fr_translate_detail.html web/templates/_task_workbench.html web/templates/_task_workbench_scripts.html
git commit -m "fix: align translation source language defaults"
```
