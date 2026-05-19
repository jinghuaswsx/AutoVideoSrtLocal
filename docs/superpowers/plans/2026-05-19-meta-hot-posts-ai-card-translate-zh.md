# Meta Hot Posts AI Card Translate Zh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-card `翻译中文` actions for US and Europe AI analysis cards, using OpenRouter `google/gemini-3.1-flash-lite` and persisting translated Chinese fields.

**Architecture:** Reuse existing translation parsers and database cache fields. Keep batch backfill defaults unchanged, but let the manual card endpoint override provider/model to OpenRouter Flash Lite. The route returns a hydrated item so the existing card renderer can replace the current card immediately.

**Tech Stack:** Flask route in `web/routes/xuanpin.py`, service orchestration in `appcore/meta_hot_posts/service.py`, DB helpers in `appcore/meta_hot_posts/store.py`, browser UI in `web/templates/meta_hot_posts.html`, pytest.

---

### Task 1: Translation Helpers Support Manual OpenRouter Overrides

**Files:**
- Modify: `appcore/meta_hot_posts/video_copyability_translation.py`
- Modify: `appcore/meta_hot_posts/europe_fit_translation.py`
- Test: `tests/test_meta_hot_posts_video_copyability_translation.py`
- Test: `tests/test_meta_hot_posts_europe_fit_translation.py`

- [ ] **Step 1: Write failing tests**

Add tests that call `translate_summary(..., provider_override="openrouter", model_override="google/gemini-3.1-flash-lite")` and `translate_assessment(...)` with the same overrides, then assert the fake `invoke_chat_fn` receives those values.

- [ ] **Step 2: Run RED**

Run:

```bash
pytest tests/test_meta_hot_posts_video_copyability_translation.py::test_translate_summary_accepts_manual_openrouter_flash_lite_override tests/test_meta_hot_posts_europe_fit_translation.py::test_translate_europe_fit_accepts_manual_openrouter_flash_lite_override -q
```

Expected: both tests fail because the functions do not accept `provider_override`.

- [ ] **Step 3: Implement minimal helper changes**

Add keyword-only parameters:

```python
provider_override: str = TRANSLATE_PROVIDER
model_override: str = TRANSLATE_MODEL
billing_source: str = "meta_hot_posts_video_copyability_summary_zh"
```

Then pass them to `llm_client.invoke_chat()`.

- [ ] **Step 4: Run GREEN**

Run the same pytest command. Expected: both tests pass.

### Task 2: Store Hydrates Chinese Cache for Single AI Row

**Files:**
- Modify: `appcore/meta_hot_posts/store.py`
- Test: `tests/test_meta_hot_posts_store.py`

- [ ] **Step 1: Write failing test**

Add a test asserting `get_hot_post_ai_analysis_row()` SQL selects:

```text
va.summary_zh AS video_copyability_summary_zh
e.strengths_zh_json AS europe_fit_strengths_zh_json
e.risks_zh_json AS europe_fit_risks_zh_json
e.required_changes_zh_json AS europe_fit_required_changes_zh_json
e.reasoning_zh AS europe_fit_reasoning_zh
```

- [ ] **Step 2: Run RED**

Run:

```bash
pytest tests/test_meta_hot_posts_store.py::test_get_hot_post_ai_analysis_row_selects_chinese_cache_fields -q
```

Expected: fails because the single-row SQL omits these fields.

- [ ] **Step 3: Add missing select columns**

Add the five selected aliases above to `get_hot_post_ai_analysis_row()`.

- [ ] **Step 4: Run GREEN**

Run the same pytest command. Expected: pass.

### Task 3: Service Endpoint for Card Translation

**Files:**
- Modify: `appcore/meta_hot_posts/service.py`
- Test: `tests/test_meta_hot_posts_service.py`

- [ ] **Step 1: Write failing service tests**

Add tests for:

```python
build_ai_analysis_translate_zh_response(7, "us_copyability", user_id=3)
build_ai_analysis_translate_zh_response(7, "europe_translation", user_id=3)
```

The tests monkeypatch `store.get_hot_post_ai_analysis_row`, translation functions, mark/finish store functions, and assert:

- US calls OpenRouter `google/gemini-3.1-flash-lite`.
- US persists via `finish_video_copyability_summary_translation`.
- Europe calls OpenRouter `google/gemini-3.1-flash-lite`.
- Europe persists via `finish_europe_fit_translation`.
- Existing Chinese cache returns `cached=True` without calling the model.

- [ ] **Step 2: Run RED**

Run:

```bash
pytest tests/test_meta_hot_posts_service.py::test_build_ai_analysis_translate_zh_response_translates_us_with_openrouter tests/test_meta_hot_posts_service.py::test_build_ai_analysis_translate_zh_response_translates_europe_with_openrouter tests/test_meta_hot_posts_service.py::test_build_ai_analysis_translate_zh_response_uses_existing_cache -q
```

Expected: fails because the service function does not exist.

- [ ] **Step 3: Implement service function**

Implement:

```python
MANUAL_AI_TRANSLATE_PROVIDER = "openrouter"
MANUAL_AI_TRANSLATE_MODEL = "google/gemini-3.1-flash-lite"

def build_ai_analysis_translate_zh_response(post_id, mode, *, user_id=None):
    ...
```

Use `_normalize_ai_analysis_mode`, `_get_ai_analysis_row`, `_hydrate_ai_analysis_row`, existing translation helpers, `store.mark_*_translation_running`, and `store.finish_*_translation`. Return `_build_ai_analysis_result_payload()` from the refreshed row with `cached` set.

- [ ] **Step 4: Run GREEN**

Run the same pytest command. Expected: pass.

### Task 4: Route Wiring

**Files:**
- Modify: `web/routes/xuanpin.py`
- Test: `tests/test_meta_hot_posts_routes.py`

- [ ] **Step 1: Write failing route test**

Add a test for:

```text
POST /xuanpin/api/meta-hot-posts/7/ai-analysis/us_copyability/translate-zh
```

Assert the route delegates to `service.build_ai_analysis_translate_zh_response(7, "us_copyability", user_id=current_user.id)`.

- [ ] **Step 2: Run RED**

Run:

```bash
pytest tests/test_meta_hot_posts_routes.py::test_meta_hot_posts_ai_analysis_translate_zh_api_passes_current_user -q
```

Expected: 404 because the route does not exist.

- [ ] **Step 3: Add Flask route**

Add route before the generic `/<mode>` POST route so `translate-zh` is not captured by the generic path.

- [ ] **Step 4: Run GREEN**

Run the same pytest command. Expected: pass.

### Task 5: Template Buttons and Immediate Card Refresh

**Files:**
- Modify: `web/templates/meta_hot_posts.html`
- Test: `tests/test_meta_hot_posts_routes.py`

- [ ] **Step 1: Write failing template test**

Assert rendered template contains:

```text
翻译中文
/ai-analysis/${mode}/translate-zh
translateMetaHotAiAnalysisToChinese
```

Also assert the US and Europe panel renderers both call the button helper, and that the AI result modal summary renderer calls the helper with `payload.item.id` and `payload.mode`.

- [ ] **Step 2: Run RED**

Run:

```bash
pytest tests/test_meta_hot_posts_routes.py::test_meta_hot_posts_template_has_ai_card_translate_zh_buttons -q
```

Expected: fail because no button/helper exists.

- [ ] **Step 3: Implement template behavior**

Add:

```javascript
function renderAiTranslateZhButton(postId, mode) { ... }
async function translateMetaHotAiAnalysisToChinese(event, postId, mode) { ... }
```

Call the button from `copyabilityBlock(row)`, `renderEuropeFitPanel(row)`, and the modal `renderAiSummarySection(result, payload)`. On success call `updateMetaHotCard(data.item)`, and if `mhAiState.postId/mode` match the returned payload, replace `mhAiResultPanel` with `renderMetaHotAiResult(data)`.

- [ ] **Step 4: Run GREEN**

Run the same pytest command. Expected: pass.

### Task 6: Full Verification and Commit

**Files:**
- Modify: all files above
- Test: related pytest suites

- [ ] **Step 1: Run focused tests**

```bash
pytest tests/test_meta_hot_posts_video_copyability_translation.py tests/test_meta_hot_posts_europe_fit_translation.py tests/test_meta_hot_posts_store.py tests/test_meta_hot_posts_service.py tests/test_meta_hot_posts_routes.py -q
```

Expected: all pass.

- [ ] **Step 2: Run compile and diff checks**

```bash
python3 -m compileall -q appcore/meta_hot_posts web/routes/xuanpin.py
git diff --check
```

Expected: exit 0.

- [ ] **Step 3: Amend the existing docs commit**

```bash
git add docs/superpowers/specs/2026-05-19-meta-hot-posts-ai-card-translate-zh-design.md docs/superpowers/plans/2026-05-19-meta-hot-posts-ai-card-translate-zh.md appcore/meta_hot_posts web/routes/xuanpin.py web/templates/meta_hot_posts.html tests
git commit --amend --no-edit
```

Expected: one feature commit containing the spec, plan, code, and tests.
