# Editable Listing Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the material list `上架` column editable with a two-value selector: `上架` or `下架`.

**Architecture:** Reuse the existing `PUT /medias/api/products/<id>` route, because it already accepts and validates `listing_status`. Keep the change inside the material list front-end rendering layer and add a static asset regression test for the wiring.

**Tech Stack:** Flask routes already present, vanilla JavaScript in `web/static/medias.js`, CSS in `web/templates/medias_list.html`, pytest for regression checks.

---

### Task 1: Add Regression Coverage

**Files:**
- Modify: `tests/test_web_routes.py`

- [ ] **Step 1: Write the failing test**

Add a static asset regression test asserting that `web/static/medias.js` contains `listingStatusSelect`, `startListingStatusInlineEdit`, `data-listing-status`, `data-listing-edit`, and a `PUT` body containing `{ listing_status: nextStatus }`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_web_routes.py::test_medias_scripts_make_listing_status_inline_editable -q`

Expected: FAIL because `function listingStatusSelect` is not in `web/static/medias.js`.

### Task 2: Implement Inline Listing Status Edit

**Files:**
- Modify: `web/static/medias.js`
- Modify: `web/templates/medias_list.html`

- [ ] **Step 1: Add rendering helpers**

Add a `listingStatusSelect(status)` helper beside `listingStatusPill(status)`. It should render a `<select>` with exactly two options, keep the current status selected, and use existing `escapeHtml`.

- [ ] **Step 2: Make the table cell editable**

Change the listing status `<td>` in `rowHTML(p)` to include `class="listing-status-cell"`, `data-pid`, `data-listing-status`, and a click-edit title.

- [ ] **Step 3: Bind click editing**

After the existing `mk-id-cell` binding in `renderList`, bind `td.listing-status-cell` to `startListingStatusInlineEdit(td)`.

- [ ] **Step 4: Save through the existing API**

Implement `startListingStatusInlineEdit(td)` so it sends:

```javascript
await fetchJSON('/medias/api/products/' + pid, {
  method: 'PUT',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ listing_status: nextStatus }),
});
```

On success, update `td.dataset.listingStatus` and restore the pill. On cancel/no change, restore the original pill. On failure, alert the user and restore the original pill.

- [ ] **Step 5: Style the selector**

Add compact selector styles near `.oc-listing-pill` using existing Ocean Blue CSS variables.

### Task 3: Verify

**Files:**
- Modify: `tests/test_web_routes.py`
- Modify: `web/static/medias.js`
- Modify: `web/templates/medias_list.html`

- [ ] **Step 1: Run focused tests**

Run: `pytest tests/test_web_routes.py::test_medias_scripts_make_listing_status_inline_editable tests/test_material_product_fields.py -q`

Expected: all selected tests pass.

- [ ] **Step 2: Inspect diff**

Run: `git diff -- tests/test_web_routes.py web/static/medias.js web/templates/medias_list.html`

Expected: only the inline listing status edit, selector styling, and regression test changed.
