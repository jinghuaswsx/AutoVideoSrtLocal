# Subtitle Removal Backend Type Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make subtitle-removal `火山` and `本地 VSR` independent processing paths with separate upload behavior, filtering, and UI affordances.

**Architecture:** Keep `subtitle_backend` in `state_json` and route upload/bootstrap/complete by that value. Fire volc uploads through TOS signed PUT and local VSR uploads through the existing local PUT endpoint. Filter list items after state parsing so no schema migration is needed.

**Tech Stack:** Flask routes, Jinja templates, plain JavaScript, existing TOS helpers, pytest.

**Spec:** `docs/superpowers/specs/2026-05-07-subtitle-removal-backend-type-design.md`

---

## File Structure

| File | Change |
| --- | --- |
| `web/routes/subtitle_removal.py` | Normalize backend in bootstrap/complete/list, route volc uploads to TOS and local VSR uploads to local PUT, return backend labels. |
| `web/templates/subtitle_removal_list.html` | Add top-right backend filter pills and a list column for processing type. |
| `web/templates/_subtitle_removal_upload_panel.html` | Keep new-task backend selection, let scripts hide erase type for local VSR. |
| `web/templates/subtitle_removal_detail.html` | Allow hiding erase type controls/status for local VSR. |
| `web/templates/_subtitle_removal_scripts.html` | Send backend to bootstrap, use upload backend response, hide erase type for local VSR, add list filter behavior. |
| `web/templates/_subtitle_removal_styles.html` | Style filter pills and hidden local-VSR erase controls using existing ocean-blue tokens where available. |
| `tests/test_subtitle_removal_routes.py` | Route regression tests for split upload behavior, list filtering, labels, and local VSR erase-type suppression. |

## Tasks

### Task 1: Route upload behavior by backend

- [ ] Write failing tests in `tests/test_subtitle_removal_routes.py` for default volc bootstrap returning TOS URL, local VSR bootstrap returning local URL, invalid backend 400, volc complete downloading from TOS, and local VSR complete keeping local-only state.
- [ ] Run the new route tests and confirm they fail for the missing behavior.
- [ ] Update `web/routes/subtitle_removal.py` bootstrap reservations to store `subtitle_backend`.
- [ ] Update bootstrap to return `upload_backend="tos"` and TOS signed URL for volc, or `upload_backend="local"` and local PUT URL for local VSR.
- [ ] Update complete to require backend match, download TOS source for volc, and clear/ignore `erase_text_type` for local VSR.
- [ ] Run the new route tests and confirm they pass.

### Task 2: List filtering and labels

- [ ] Write failing tests for `GET /api/subtitle-removal/list?subtitle_backend=local_vsr`, invalid backend 400, and returned `subtitle_backend_label`.
- [ ] Run the new list tests and confirm they fail.
- [ ] Update `list_tasks()` to parse the backend query parameter, filter items after state parsing, and return `subtitle_backend_label`.
- [ ] Run the focused list tests and confirm they pass.

### Task 3: Frontend controls

- [ ] Update templates and scripts so the list header shows backend filter pills next to the page title action area.
- [ ] Send `subtitle_backend` to upload bootstrap, respect `upload_backend`, and hide erase type when local VSR is selected.
- [ ] Hide detail erase-type controls/status for local VSR tasks.
- [ ] Run route render tests plus `tests/test_subtitle_removal_routes.py`.

### Task 4: Verification

- [ ] Run `pytest tests/test_subtitle_removal_routes.py tests/test_subtitle_removal_runtime.py tests/test_subtitle_removal_provider.py -q`.
- [ ] Run `git diff --check`.
- [ ] Inspect the diff for unrelated changes before final response.
