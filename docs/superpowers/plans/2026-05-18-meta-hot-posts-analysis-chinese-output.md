# Meta 热帖分析中文输出 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all new Meta hot-post US copyability and Europe fit analysis results return Chinese operator-facing content.

**Architecture:** Keep existing table fields and ranking logic unchanged. Tighten the Gemini prompts and response schemas so new analyses produce Chinese content directly, while the existing US `summary_zh` fallback/backfill remains available for missing Chinese summaries.

**Tech Stack:** Python 3.12, Flask service modules under `appcore/meta_hot_posts`, pytest.

---

### Task 1: US Copyability Chinese Output Contract

**Files:**
- Modify: `tests/test_meta_hot_posts_video_copyability.py`
- Modify: `appcore/meta_hot_posts/video_copyability.py`

- [ ] **Step 1: Write the failing test**

Add assertions that `_response_schema()` requires `summary_zh`, and `build_prompt()` explicitly asks for Simplified Chinese `summary_zh`, `winning_angles`, `copy_notes`, and `risk_notes` while keeping `summary` as English compatibility text.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_meta_hot_posts_video_copyability.py -q`

Expected: failure because `summary_zh` is not currently required and list fields are not explicitly constrained to Chinese.

- [ ] **Step 3: Write minimal implementation**

Update `_response_schema()` required fields and `build_prompt()` instructions only. Do not change provider/model, score fields, queue behavior, or storage contract.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_meta_hot_posts_video_copyability.py -q`

Expected: pass.

### Task 2: Europe Fit Chinese Output Contract

**Files:**
- Modify: `tests/test_meta_hot_posts_europe_fit.py`
- Modify: `appcore/meta_hot_posts/europe_fit.py`

- [ ] **Step 1: Write the failing test**

Add assertions that `build_system_prompt()` and `build_prompt()` require Chinese operator-facing output for `strengths`, `risks`, `required_changes`, and `reasoning`, while preserving recommendation enum values.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_meta_hot_posts_europe_fit.py -q`

Expected: failure because the current Europe prompt asks for English-style assessment and does not require Chinese field content.

- [ ] **Step 3: Write minimal implementation**

Update Europe system/prompt/schema descriptions to require Simplified Chinese field content. Keep `best_countries` and recommendation enum compatibility unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_meta_hot_posts_europe_fit.py -q`

Expected: pass.

### Task 3: Regression Verification and Production Sync

**Files:**
- Verify: `tests/test_meta_hot_posts_video_copyability.py`
- Verify: `tests/test_meta_hot_posts_europe_fit.py`
- Verify: related Meta hot-post service/store/routes tests
- Deploy: `/opt/autovideosrt/appcore/meta_hot_posts/video_copyability.py`
- Deploy: `/opt/autovideosrt/appcore/meta_hot_posts/europe_fit.py`

- [ ] **Step 1: Run focused regression tests**

Run: `pytest tests/test_meta_hot_posts_video_copyability.py tests/test_meta_hot_posts_europe_fit.py tests/test_meta_hot_posts_service.py tests/test_meta_hot_posts_store.py tests/test_meta_hot_posts_routes.py -q`

Expected: pass.

- [ ] **Step 2: Sync production files**

Copy only the changed spec and runtime files into `/opt/autovideosrt`; do not touch the running `summary_zh` backfill process.

- [ ] **Step 3: Verify production import and restart**

Run production `py_compile`, restart `autovideosrt`, confirm `systemctl is-active autovideosrt` is `active`, and `curl http://127.0.0.1/` returns `302` or `200`.

- [ ] **Step 4: Check background translation process**

Confirm the existing 2-second backfill process is still running or report if it has completed. Check latest log for 429 strategy adjustment.
