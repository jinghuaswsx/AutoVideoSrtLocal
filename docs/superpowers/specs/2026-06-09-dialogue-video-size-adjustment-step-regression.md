# Dialogue Video Size Adjustment Step Regression

- Date: 2026-06-09
- Module: Dialogue translate runtime
- Anchors:
  - `docs/superpowers/specs/2026-06-05-dialogue-flow-omni-alignment-design.md`: dialogue detail step cards must match `DialogueTranslateRunner.pipeline_step_names_for_config()`.
  - `docs/superpowers/specs/2026-06-08-omni-video-size-adjustment-design.md`: Omni and Omni V2 insert `video_size_adjustment` after `compose` and before `export`.

## Problem

Dialogue translate inherits the Omni V2 step order. After `video_size_adjustment` was inserted into that order, the dialogue detail page and restart path include the step, but `DialogueTranslateRunner._get_pipeline_steps()` did not provide a handler for it.

On force restart, the restart API resets the task successfully, then the background runner crashes while building steps with `KeyError: 'video_size_adjustment'`. The task remains before speaker detection and `voice_match_ab` never reaches `waiting`, so the A/B voice selection UI has no selectable speaker candidates.

## Requirement

When `DialogueTranslateRunner.pipeline_step_names_for_config()` includes any shared Omni step, `_get_pipeline_steps()` must either provide a handler for that step or explicitly remove it from the dialogue step order. A force restart must not reset a dialogue task into a state that the runner cannot execute.

For the current inherited `video_size_adjustment` step, dialogue translate must dispatch to the shared `_step_video_size_adjustment()` implementation so the configured step order remains executable and the task can continue to A/B voice review.

## Verification

Focused tests must cover that dialogue pipeline step construction succeeds when the configured step order includes `video_size_adjustment`, and that the step remains after `compose` and before `export`.
