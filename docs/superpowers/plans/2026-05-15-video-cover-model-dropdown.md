# Video Cover Model Dropdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make 文案封面生成 default model configuration use provider-linked model dropdowns with richer scene-specific choices.

**Architecture:** Keep the model catalog in `appcore/video_cover_generation.py` as the backend source of truth. Render the same catalog into `web/templates/video_cover_list.html`, and use JavaScript to refresh model `<select>` options when each step provider changes.

**Tech Stack:** Python 3.12, Flask/Jinja, pytest, vanilla JavaScript.

---

### Task 1: Extend Model Catalog

**Files:**
- Modify: `appcore/video_cover_generation.py`
- Test: `tests/test_video_cover_generation.py`

- [ ] Add assertions that text steps expose multiple Gemini choices and OpenRouter ad copy exposes Claude/GPT choices.
- [ ] Add assertions that cover generation exposes OpenRouter Nano Banana 2 and OpenAI Image 2 quality choices.
- [ ] Extend `TEXT_STEP_MODEL_OPTIONS` and `COVER_MODEL_OPTIONS` with the approved model IDs and labels.
- [ ] Run `pytest tests/test_video_cover_generation.py::test_resolve_video_cover_model_options_matches_requested_mappings -q`.

### Task 2: Render Provider-Linked Dropdowns

**Files:**
- Modify: `web/templates/video_cover_list.html`
- Test: `tests/test_video_cover_generation.py`

- [ ] Add a route-rendering assertion that default config uses `<select name="<step>_model_id">`, includes serialized model options, and shows Nano Banana 2.
- [ ] Replace default config model text inputs with model selects.
- [ ] Add JS that refreshes model options per step/provider, preserves historical values with a temporary option, and saves actual model IDs.
- [ ] Run `pytest tests/test_video_cover_generation.py::test_video_cover_page_renders_default_config_for_superadmin -q`.

### Task 3: Verify

**Files:**
- Test: `tests/test_video_cover_generation.py`

- [ ] Run `pytest tests/test_video_cover_generation.py -q`.
- [ ] Review `git diff --check`.
