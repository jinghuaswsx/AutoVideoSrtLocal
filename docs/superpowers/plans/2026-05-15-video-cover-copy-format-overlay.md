# Video Cover Copy Format Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make video-cover ad copy use the required title/message/description format and render cover text deterministically in backend post-processing.

**Architecture:** Keep the existing 4-step workflow. Change the ad-copy data contract, normalize legacy copy structures at the service boundary, generate text-free cover backgrounds, then draw the selected title onto the final 1080x1920 PNG with PIL.

**Tech Stack:** Python 3.12, Flask, Pillow, pytest, Jinja/vanilla JavaScript.

---

### Task 1: Update Service Tests First

**Files:**
- Modify: `tests/test_video_cover_generation.py`

- [x] **Step 1: Add failing tests for the new copy contract**

Add tests that expect `generate_ad_copy_sets()` to accept `english.title/message/description`, reject missing fields, and normalize legacy `headline/body_text/cta`.

- [x] **Step 2: Run red tests**

Run: `pytest tests/test_video_cover_generation.py::test_generate_ad_copy_sets_uses_user_prompt_and_validates_json tests/test_video_cover_generation.py::test_generate_video_covers_respects_image_count_and_copy_metadata -q`

Expected before implementation: failures mentioning missing `headline/body_text/cta` or missing `title/message/description`.

### Task 2: Implement Copy Normalization

**Files:**
- Modify: `appcore/video_cover_generation.py`

- [x] **Step 1: Change `AD_COPY_PROMPT_TEMPLATE`**

Update the output schema from `headline/body_text/cta` to `title/message/description`, and include the exact three-line text format in the prompt.

- [x] **Step 2: Add normalization helpers**

Add helpers that return standard `english.title/message/description` and `chinese_translation.title/message/description`, mapping legacy fields where needed.

- [x] **Step 3: Run green tests**

Run: `pytest tests/test_video_cover_generation.py -q`

Expected: copy contract tests pass; remaining overlay tests may still fail until Task 3.

### Task 3: Add Deterministic Overlay

**Files:**
- Modify: `appcore/video_cover_generation.py`

- [x] **Step 1: Add failing overlay tests**

Assert that generated covers include `overlay_text`, `overlay_box`, `overlay_font_size`, and `formatted_copy`, and that image prompt says not to render text.

- [x] **Step 2: Implement PIL text drawing**

Add a helper that draws the selected `english.title` after image normalization and returns PNG bytes plus overlay metadata.

- [x] **Step 3: Save actual per-image prompts**

Store each real prompt in the cover result and expose `image_prompts` for route-level request payloads.

- [x] **Step 4: Run tests**

Run: `pytest tests/test_video_cover_generation.py -q`

Expected: all video-cover service tests pass.

### Task 4: Update Route and Template Views

**Files:**
- Modify: `web/routes/video_cover.py`
- Modify: `web/templates/video_cover_detail.html`
- Modify: `tests/test_video_cover_generation.py`

- [x] **Step 1: Update route request/result payloads**

Make `cover_generation` step request store actual image prompts after generation.

- [x] **Step 2: Update final UI copy rendering**

Display and copy `标题 / 文案 / 描述` using `title/message/description`, with fallback for legacy fields.

- [x] **Step 3: Run route/template tests**

Run: `pytest tests/test_video_cover_generation.py -q`

Expected: all tests pass.

### Task 5: Final Verification

**Files:**
- Read: `docs/superpowers/specs/2026-05-15-video-cover-copy-format-overlay-design.md`
- Read: `AGENTS.md`

- [x] **Step 1: Run focused regression**

Run: `pytest tests/test_copywriting_parser.py tests/test_video_cover_generation.py -q`

Expected: all tests pass.

- [x] **Step 2: Check worktree**

Run: `git status --short`

Expected: only the intended docs, service, route/template, and test files changed.
