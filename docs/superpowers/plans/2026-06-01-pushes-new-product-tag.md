# Pushes New Product Tag Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a product-level `新品` label column to the push management list for products with no successful push history.

**Architecture:** The backend computes a product-level boolean in the existing push list SQL, serializes it through `/pushes/api/items`, and the frontend renders a dedicated stable-width tag column. The change is read-only and does not alter push state, payloads, logs, or cache refresh behavior.

**Tech Stack:** Python 3.12, Flask, pytest, plain JavaScript, CSS.

---

### Task 1: Backend Product Flag

**Files:**
- Modify: `appcore/pushes.py`
- Modify: `web/routes/pushes.py`
- Test: `tests/test_appcore_pushes.py`

- [ ] **Step 1: Write failing SQL and serializer tests**

Add tests that assert `list_items_for_push()` selects `is_new_product_for_push` with `NOT EXISTS`, and `_serialize_row()` exposes `is_new_product_for_push` as a boolean.

- [ ] **Step 2: Run the new tests to verify RED**

Run only the new no-DB tests:

```bash
python -m pytest tests/test_appcore_pushes.py::test_list_items_for_push_selects_new_product_push_flag tests/test_appcore_pushes.py::test_pushes_serialize_row_includes_new_product_push_flag -q
```

Expected: fail because the SQL field and serialized key do not exist yet.

- [ ] **Step 3: Implement the minimal backend change**

Add the `NOT EXISTS` expression to the `SELECT` in `appcore/pushes.py` and include `bool(row.get("is_new_product_for_push"))` in `web/routes/pushes.py`.

- [ ] **Step 4: Run backend tests to verify GREEN**

Run the same command and confirm both tests pass.

### Task 2: Frontend Column And Styling

**Files:**
- Modify: `web/templates/pushes_list.html`
- Modify: `web/static/pushes.js`
- Modify: `web/static/pushes.css`
- Test: `tests/test_web_routes.py`

- [ ] **Step 1: Write failing static frontend tests**

Add tests that assert the template has the `标签` column and colgroup entry, `pushes.js` renders `push-new-product-tag`, and CSS defines the new tag style.

- [ ] **Step 2: Run the new static tests to verify RED**

```bash
python -m pytest tests/test_web_routes.py::test_pushes_list_template_contains_new_product_tag_column tests/test_web_routes.py::test_pushes_scripts_render_new_product_tag tests/test_web_routes.py::test_pushes_css_styles_new_product_tag -q
```

Expected: fail because the template, JavaScript, and CSS do not contain the new column yet.

- [ ] **Step 3: Implement the minimal frontend change**

Add one column after the product column, render `新品` only for truthy `is_new_product_for_push`, increase the table colspan by one, and add responsive column widths.

- [ ] **Step 4: Run frontend tests to verify GREEN**

Run the same command and confirm the static tests pass.

### Task 3: Final Verification

**Files:**
- No new production files beyond Tasks 1 and 2.

- [ ] **Step 1: Run focused verification**

```bash
python -m pytest tests/test_appcore_pushes.py::test_list_items_for_push_selects_new_product_push_flag tests/test_appcore_pushes.py::test_pushes_serialize_row_includes_new_product_push_flag tests/test_web_routes.py::test_pushes_list_template_contains_new_product_tag_column tests/test_web_routes.py::test_pushes_scripts_render_new_product_tag tests/test_web_routes.py::test_pushes_css_styles_new_product_tag -q
```

- [ ] **Step 2: Check diff**

```bash
git diff -- appcore/pushes.py web/routes/pushes.py web/templates/pushes_list.html web/static/pushes.js web/static/pushes.css tests/test_appcore_pushes.py tests/test_web_routes.py
```

- [ ] **Step 3: Report local MySQL limitation**

Do not run DB-backed push list tests against `127.0.0.1:3306`. If server DB verification is needed, use the documented test/server environment.
