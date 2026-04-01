# Unified Task Workbench Design

Date: 2026-04-01
Status: Approved

## Overview

Unify the current upload/execution page and the project detail page into a single task workbench experience. The workbench should preserve and render every important intermediate artifact inline, let users continue processing from the project page, and keep the UI aligned with the existing execution page instead of maintaining a separate read-only summary screen.

The second requirement is a confirmation mode switch on the workbench:

- Default mode: full auto
- Optional mode: manual confirmation

When manual confirmation is enabled, steps that currently require or imply human review must pause and wait for the user to explicitly continue. When full auto is enabled, the pipeline should continue without blocking.

---

## Goals

- Make `/projects/<task_id>` look and behave like the execution page, not like a compact summary page.
- Keep all intermediate outputs available after refresh, reopening the page, or navigating from the project list.
- Let unfinished tasks continue from the same workbench page.
- Add a clear `全自动 / 手动确认` switch to control whether reviewable steps pause.
- Ensure the page can render both historical completed tasks and active in-progress tasks from persisted state.

---

## Non-Goals

- No new pipeline stages.
- No redesign of the visual language beyond aligning pages to the current execution UI.
- No per-step granular confirmation settings; a single task-level mode switch is sufficient.
- No separate lightweight “detail page” once the unified workbench exists.

---

## UX Design

### Unified Workbench

Both entry points should converge on the same workbench:

- New task flow: upload on `/api/tasks/upload-page`, then navigate to `/projects/<task_id>`
- Existing task flow: open `/projects/<task_id>` from the project list

The workbench keeps the same main sections already present on the execution page:

1. Task header / upload result card
2. Generation config card
3. Pipeline progress card with all step previews
4. Conditional review panels
5. Result downloads/actions

### Configuration Card

The config card remains visible on the workbench and becomes the single source of truth for:

- voice selection
- subtitle position
- confirmation mode

For completed tasks, these fields are displayed read-only in practice because processing is already done. For uploaded or paused tasks, they remain actionable.

### Confirmation Mode

Add a task-level control:

- `全自动` (default)
- `手动确认`

Reviewable steps:

- alignment
- translation

Behavior:

- In `全自动`, alignment and translation compute outputs and immediately mark themselves confirmed.
- In `手动确认`, alignment and translation compute outputs, set the step to `waiting`, persist the review payload, and stop the pipeline until the user confirms.

The workbench must make the paused state visually obvious and preserve the confirmation controls after refresh.

---

## State Model

The current `task_state` already persists most artifacts and preview files. The unified workbench needs a few more persisted fields so refresh/reopen behavior matches live execution:

- `interactive_review`: boolean task-level mode
- `step_messages`: latest human-readable message per step
- `current_review_step`: optional string (`alignment`, `translate`, or empty)
- `_alignment_confirmed`: existing internal flag
- `_segments_confirmed`: existing internal flag

Existing persisted structures remain the main source of rendering:

- `steps`
- `artifacts`
- `preview_files`
- `variants.*`
- `result`
- `exports`

The project detail route should continue reading `state_json`; no separate denormalized page model is needed.

---

## Rendering Strategy

The existing execution page already contains the richer renderer for artifact layouts:

- `variant_compare`
- `audio`
- `video`
- `download`
- `action`
- `text`
- `scene_cuts`
- `utterances`
- `segments`
- `sentences`
- `tts_blocks`
- `subtitle_chunks`

Instead of re-implementing that logic in `project_detail.html`, extract the workbench markup and JS renderer into reusable template/script fragments, then use them from:

- upload/execution page
- project workbench page

This avoids page drift and guarantees that any new artifact type appears the same way in both entry points.

---

## Pipeline Control Flow

### Alignment

After computing alignment:

- persist alignment artifact
- if `interactive_review` is false:
  - mark confirmed
  - step becomes `done`
- if `interactive_review` is true:
  - step becomes `waiting`
  - emit review payload
  - stop pipeline progression until confirmation API call updates state

### Translation

After computing translation variants:

- persist translation artifacts
- if `interactive_review` is false:
  - mark confirmed
  - step becomes `done`
- if `interactive_review` is true:
  - step becomes `waiting`
  - expose editable translated segments in the workbench
  - stop pipeline progression until confirmation API call updates state

This fixes the current mismatch where translation has confirmation UI semantics but the runtime never truly waits.

### Resume Behavior

When a paused task is reopened on `/projects/<task_id>`:

- the workbench should reconstruct the step cards from persisted artifacts
- the review panel should reopen based on persisted step status and task data
- confirming the paused step should continue processing from that point, not restart from scratch

---

## Routing

### `/api/tasks/upload-page`

Remains the new-task entry point and upload surface.

### `/projects/<task_id>`

Becomes the canonical workbench for an existing task, whether:

- uploaded but not started
- running
- waiting for confirmation
- done
- error

After upload succeeds on the new-task page, the browser should navigate to `/projects/<task_id>` and continue there.

---

## Error Handling

- If a task is expired or deleted, keep the existing expired-state treatment.
- If a preview file is gone but metadata remains, show the step shell and fallback text instead of breaking the whole page.
- If the task is paused in manual mode and the browser reloads, the page should recover the review UI from persisted state without relying on a live socket event replay.

---

## Testing Strategy

### Template / UI

- Project workbench page contains the same step preview renderer hooks as the upload/execution page.
- Project workbench exposes confirmation mode controls and start/continue actions when appropriate.

### Runtime

- Default mode keeps alignment and translation non-blocking.
- Manual mode pauses at alignment and translation with `waiting` status.
- Confirming alignment resumes the pipeline correctly.
- Confirming translation resumes the pipeline correctly.

### Persistence

- `step_messages` and waiting states survive `state_json` serialization.
- Project workbench rebuilds review panels from persisted state.

---

## Success Criteria

- A user can upload a task, land on `/projects/<task_id>`, and complete the whole flow there.
- A user can reopen any task from the project list and see the same step cards and artifacts as the execution page.
- Manual confirmation genuinely pauses alignment and translation until the user confirms.
- Full auto remains the default and preserves the current fast path.
