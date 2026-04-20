# Image Translate Product Name Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the image-translate page require product-name entry earlier by moving it to the top of the form, enlarging the input, and adding a bold red pre-submit warning.

**Architecture:** Keep the change within the existing server-rendered image-translate page. Lock the requirement with a template-level regression test, then update the Jinja template, page-scoped CSS, and page-scoped JavaScript to strengthen both visual emphasis and submit-time focus behavior.

**Tech Stack:** Flask, Jinja2 templates, page-scoped HTML/CSS/JavaScript, pytest

---

### Task 1: Lock the new UX requirement with tests

**Files:**
- Modify: `tests/test_image_translate_routes.py`

- [ ] **Step 1: Write the failing test**

```python
def test_image_translate_page_emphasizes_product_name_before_submit(authed_client_no_db, monkeypatch):
    from appcore import db as app_db

    monkeypatch.setattr(app_db, "query", lambda *args, **kwargs: [])
    resp = authed_client_no_db.get("/image-translate")
    body = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert "提交任务前先输入产品名" in body
    assert 'class="it-product-name-callout"' in body
    assert 'class="it-product-name-input"' in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_image_translate_routes.py::test_image_translate_page_emphasizes_product_name_before_submit -q`
Expected: FAIL because the current page has no red callout or enlarged input class.

- [ ] **Step 3: Keep the rest of the image-translate route tests available as regression coverage**

Run: `pytest tests/test_image_translate_routes.py tests/test_web_routes.py -q`
Expected: PASS before the new assertion is introduced, establishing the current baseline for the touched area.

### Task 2: Implement the top-of-form product-name emphasis

**Files:**
- Modify: `web/templates/image_translate_list.html`
- Modify: `web/templates/_image_translate_styles.html`
- Modify: `web/templates/_image_translate_scripts.html`
- Test: `tests/test_image_translate_routes.py`

- [ ] **Step 1: Update the template structure**

```html
<div class="form-row it-product-name-row">
  <label for="itProductName">产品名 <span class="required">*</span></label>
  <p class="it-product-name-callout">提交任务前先输入产品名</p>
  <input
    id="itProductName"
    class="it-product-name-input"
    type="text"
    maxlength="60"
    autocomplete="off"
    placeholder="例如：三轮童车、儿童智能手表"
  >
  <p class="hint">项目名将自动生成为「产品名-语言-日期」，用于后续存档检索</p>
</div>
```

- [ ] **Step 2: Add the visual emphasis styles**

```html
.it-product-name-row {
  margin-bottom: 24px;
}

.it-product-name-callout {
  margin: 0 0 12px;
  color: #dc2626;
  font-size: 28px;
  font-weight: 800;
  line-height: 1.25;
}

.it-product-name-input {
  width: 100%;
  min-height: 96px;
  padding: 20px 22px;
  border: 2px solid #f87171;
  border-radius: 12px;
  font-size: 32px;
  font-weight: 700;
}

.it-product-name-input:focus-visible {
  outline: none;
  border-color: #dc2626;
  box-shadow: 0 0 0 4px rgba(220, 38, 38, 0.18);
}
```

- [ ] **Step 3: Strengthen submit-time behavior**

```javascript
if (!productName) {
  productNameEl.focus();
  productNameEl.scrollIntoView({ behavior: "smooth", block: "center" });
  return showError("请先输入产品名，再提交任务");
}
```

- [ ] **Step 4: Run the targeted tests**

Run: `pytest tests/test_image_translate_routes.py::test_image_translate_page_emphasizes_product_name_before_submit tests/test_image_translate_routes.py::test_image_translate_empty_state_container tests/test_web_routes.py -q`
Expected: PASS

- [ ] **Step 5: Run the full touched-area regression suite**

Run: `pytest tests/test_image_translate_routes.py tests/test_web_routes.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/plans/2026-04-20-image-translate-product-name-input.md \
        tests/test_image_translate_routes.py \
        web/templates/image_translate_list.html \
        web/templates/_image_translate_styles.html \
        web/templates/_image_translate_scripts.html
git commit -m "feat: emphasize product name before image translate submit"
```
