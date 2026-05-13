# TTS Speedup Audio Preview UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `变速前` / `变速后` speedup audio preview controls playable and readable in the task detail workbench.

**Architecture:** Add a safe task-local artifact path response helper and expose it through the translation APIs used by `_task_workbench_scripts.html`. Reuse the existing hidden `Audio()` playback helpers, and render stable preview panels instead of narrow native `<audio>` controls.

**Tech Stack:** Flask routes, Jinja templates, vanilla JavaScript, CSS, pytest.

---

### Task 1: Safe Artifact Path Endpoint

**Files:**
- Modify: `web/services/artifact_download.py`
- Modify: `web/routes/multi_translate.py`
- Modify: `web/routes/omni_translate.py`
- Modify: `web/routes/ja_translate.py`
- Modify: `web/routes/de_translate.py`
- Modify: `web/routes/fr_translate.py`
- Modify: `web/routes/task.py`
- Test: `tests/test_artifact_download_safety.py`
- Test: `tests/test_multi_translate_routes.py`

- [x] **Step 1: Write failing helper and route tests**

Add tests that create an in-task MP3, request it through the safe helper and `/api/multi-translate/<task_id>/artifact-path?path=...`, and assert traversal paths are rejected.

- [x] **Step 2: Run tests to verify RED**

Run: `pytest tests/test_artifact_download_safety.py::test_safe_task_relative_file_response_sends_task_relative_path tests/test_multi_translate_routes.py::test_multi_translate_artifact_path_route_serves_task_relative_audio -q`

Expected: fail because the helper and route do not exist yet.

- [x] **Step 3: Implement the helper and route wrappers**

Add `safe_task_relative_file_response(task, path, **kwargs)` that joins relative paths to `task["task_dir"]` before delegating to `safe_task_file_response`. Add `artifact-path` GET routes that call this helper after existing task access checks.

- [x] **Step 4: Run focused route/helper tests**

Run: `pytest tests/test_artifact_download_safety.py tests/test_multi_translate_routes.py::test_multi_translate_artifact_path_route_serves_task_relative_audio tests/test_multi_translate_routes.py::test_multi_translate_artifact_path_route_rejects_traversal -q`

Expected: all selected tests pass.

### Task 2: Workbench Audio Preview UI

**Files:**
- Modify: `web/templates/_task_workbench_scripts.html`
- Modify: `web/templates/_task_workbench_styles.html`
- Test: `tests/test_task_workbench_media_source_assets.py`
- Test: `tests/test_translate_detail_shell_templates.py`

- [x] **Step 1: Write failing template tests**

Assert the speedup renderer uses `TASK_WORKBENCH_CONFIG.apiBase` for path artifacts, does not contain `/tasks/${encodeURIComponent(tid)}/artifact?path=`, renders `.tts-speedup-player-card`, and does not emit raw `<audio>` tags in `renderSpeedupCard`.

- [x] **Step 2: Run tests to verify RED**

Run: `pytest tests/test_task_workbench_media_source_assets.py::test_task_workbench_speedup_audio_uses_configured_artifact_path_route tests/test_translate_detail_shell_templates.py::test_tts_speedup_players_render_as_readable_preview_cards -q`

Expected: fail because the renderer still uses the broken route and native audio controls.

- [x] **Step 3: Implement the renderer and CSS**

Add `_speedupArtifactUrl(rel)` and `_speedupAudioPreview(gid, title, duration, url)`. Render play/pause/time/open controls for before and after audio. Add CSS for a two-column responsive grid with stable min widths and readable controls.

- [x] **Step 4: Run focused template tests**

Run: `pytest tests/test_task_workbench_media_source_assets.py tests/test_translate_detail_shell_templates.py::test_tts_speedup_players_render_as_readable_preview_cards -q`

Expected: selected tests pass.

### Task 3: Final Verification

**Files:**
- Modify only files already listed above.

- [x] **Step 1: Run related pytest suite**

Run: `pytest tests/test_artifact_download_safety.py tests/test_task_workbench_media_source_assets.py tests/test_translate_detail_shell_templates.py tests/test_multi_translate_routes.py -q`

Expected: all selected tests pass.

- [x] **Step 2: Run route smoke verification**

Start a local dev server on an unused port with `python -m web.app`, then check an unauthenticated detail route returns 302 rather than 500. If authenticated browser verification is not available in this session, report that limitation instead of claiming visual verification.

Result: actual dev-server smoke was stopped because startup recovery attempted to connect to local MySQL, which is blocked by project rules. Route behavior was verified through the patched Flask test client in `tests/test_multi_translate_routes.py`.

- [x] **Step 3: Inspect git diff**

Run: `git diff --check` and `git status --short`.

Expected: no whitespace errors; diff contains the spec, plan, tests, route helper, routes, and workbench UI changes.
