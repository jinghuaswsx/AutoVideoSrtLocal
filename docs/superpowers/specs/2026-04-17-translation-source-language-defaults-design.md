# Translation Source Language Defaults Design

## Goal

Make the three video translation modules start with the correct default source
language:

- `视频翻译` default source language: `zh`
- `视频翻译（德语）` default source language: `en`
- `视频翻译（法语）` default source language: `en`

The default must stay consistent across the detail page UI, task state, and
subsequent page refreshes.

## Confirmed Requirements

1. Scope only includes the three video translation modules shown in the left
   navigation:
   - English video translation
   - German video translation
   - French video translation
2. English video translation should default source language to Chinese.
3. German and French video translation should default source language to
   English.
4. The change should follow the recommended two-layer approach:
   - page initial selection
   - new task initial state

## Out Of Scope

- Auto-detecting source language from audio
- Changing target language defaults
- Changing source-language options available to the user
- Any non-video translation module

## Options Considered

### Option 1: UI Default Only

Only change which button is highlighted on first render.

Why not recommended:

- refreshes can fall back to the old task state
- backend-created tasks can still start with the wrong default
- page and state can drift apart

### Option 2: Task-State Default Only

Only change the initial `source_language` saved with new tasks.

Why not recommended:

- the shared workbench toggle currently has a static Chinese-first markup
- first paint can still briefly show the wrong selection

### Option 3: UI + Task-State Defaults Together (Recommended)

Set module-specific defaults in both the rendered UI and the initial task
state.

Why this is recommended:

- keeps first paint, refresh, and API readback aligned
- is the smallest safe change for the requested behavior
- stays within the existing route/template structure

## Recommended Design

### 1. Module-Specific Default Source Language

Introduce one explicit default per module:

- English module: `zh`
- German module: `en`
- French module: `en`

These defaults apply only when the task does not already have a saved
`source_language`.

### 2. Detail Page Rendering

The English detail page already computes a fallback source language in its own
template. Keep that mechanism, but confirm its fallback remains `zh`.

The German and French detail pages share `_task_workbench.html` and
`_task_workbench_scripts.html`. They should provide a module-specific default
value into the shared workbench instead of relying on the static Chinese-active
markup.

Expected render behavior:

- existing task with saved `source_language`: show the saved value
- new German/French task without saved value yet: show English selected
- new English task without saved value yet: show Chinese selected

### 3. New Task Initial State

When a new task is created through the current creation path for each module,
write `source_language` into the task state immediately:

- English task creation writes `zh`
- German task creation writes `en`
- French task creation writes `en`

This must happen before the user revisits the detail page or starts the
pipeline, so the stored state and rendered state stay aligned.

This applies to the current new-task creation endpoints:

- English TOS complete flow
- German TOS complete flow
- French TOS complete flow

Legacy local-upload endpoints are already disabled for new tasks, so they do
not need separate default-state handling.

### 4. Shared Workbench Contract

The shared task workbench should stop assuming Chinese is always the initial
selected option. Instead, it should consume a per-page default value and mark
the matching button as active.

The front-end sync rule is:

- prefer `currentTask.source_language` when present
- otherwise use the page-provided module default

This keeps the shared template reusable while supporting different defaults per
module.

## Testing Strategy

Add or update targeted tests that verify:

1. English creation path stores `source_language == "zh"`.
2. German creation path stores `source_language == "en"`.
3. French creation path stores `source_language == "en"`.
4. German detail page renders the source-language toggle with English selected
   when no saved value exists.
5. French detail page renders the source-language toggle with English selected
   when no saved value exists.
6. English detail page continues to render Chinese selected by default.

## Risks And Mitigations

### Risk: Shared template accidentally changes other modules

Mitigation:

- drive the default through explicit per-page template variables
- keep the fallback logic narrow and limited to the three video translation
  pages

### Risk: UI default and stored state drift apart

Mitigation:

- update both creation-time state and first-render UI in the same change
- add tests for both layers
