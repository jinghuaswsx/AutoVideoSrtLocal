# Raw Source Title Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce the title format `YYYY.MM.DD-产品名-xxxxxx.mp4` when uploading raw subtitle-removed video sources.

**Architecture:** Add a small backend validation helper in `web/routes/medias.py` close to the raw-source upload route, then call it before storing uploaded bytes. Add mirrored client-side validation in the existing raw-source upload block of `web/static/medias.js` so users see errors before upload starts.

**Tech Stack:** Flask route handlers, pytest route tests, vanilla JavaScript in the existing media management page.

---

### Task 1: Backend Validation

**Files:**
- Modify: `web/routes/medias.py`
- Test: `tests/test_medias_routes.py`

- [ ] **Step 1: Write failing tests**

Append tests that post to `/medias/api/products/123/raw-sources` with stubbed product/access/storage dependencies. Cover one accepted title and one rejected title before any storage write.

```python
def test_create_raw_source_rejects_invalid_display_name(authed_client_no_db, monkeypatch):
    # Arrange product, access, mimetypes, and write spy.
    # POST display_name="bad.mp4" with valid video/cover files.
    # Assert 400, error == "raw_source_title_invalid", and no write occurred.
```

- [ ] **Step 2: Verify tests fail**

Run:

```powershell
pytest tests/test_medias_routes.py::test_create_raw_source_rejects_invalid_display_name -q
```

Expected: FAIL because raw-source title validation does not exist yet.

- [ ] **Step 3: Implement minimal backend validation**

Add helper logic in `web/routes/medias.py`:

```python
_RAW_SOURCE_TITLE_DATE_RE = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})$")

def _suggest_raw_source_title(product: dict) -> str | None:
    product_name = ((product or {}).get("name") or "").strip()
    if not product_name:
        return None
    return f"{datetime.now().strftime('%Y.%m.%d')}-{product_name}-原始视频.mp4"

def _validate_raw_source_display_name(title: str, product: dict) -> list[str]:
    # Trim, check .mp4, split as date/product/rest, validate legal date,
    # require product name match, require non-empty final segment.
```

Call it immediately after reading `display_name`; return a 400 JSON response before object key generation or storage writes.

- [ ] **Step 4: Verify backend tests pass**

Run:

```powershell
pytest tests/test_medias_routes.py -q
```

Expected: all tests pass.

### Task 2: Frontend Submit Guard

**Files:**
- Modify: `web/static/medias.js`

- [ ] **Step 1: Add client-side helper**

Inside the raw-source upload IIFE, add:

```javascript
function validateRawSourceDisplayName(title, productName) {
  const value = normalizeRawSourceTitle(title);
  const errors = [];
  // Mirror backend checks: .mp4 suffix, YYYY.MM.DD date, productName segment,
  // non-empty final segment.
  return errors;
}
```

- [ ] **Step 2: Block invalid submit**

At the start of `submitRawSourceUpload`, validate `uploadNameInput.value` against `uiState.currentName`. If invalid, alert the user with the exact required format and keep the submit button enabled.

- [ ] **Step 3: Preserve current good path**

Keep `setRawSourceUploadVideo(file)` defaulting `uploadNameInput.value = file.name`, because a correctly named selected file should work without extra typing.

### Task 3: Final Verification

**Files:**
- Verify changed files only.

- [ ] **Step 1: Run targeted route tests**

```powershell
pytest tests/test_medias_routes.py -q
```

Expected: `26+` tests pass with no failures.

- [ ] **Step 2: Inspect diff**

```powershell
git diff -- web/routes/medias.py web/static/medias.js tests/test_medias_routes.py
```

Expected: diff only contains raw-source title validation, frontend guard, and tests.
