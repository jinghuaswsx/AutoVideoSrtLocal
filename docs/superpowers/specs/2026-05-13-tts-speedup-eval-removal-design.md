# TTS Speedup Evaluation Removal Design

- Date: 2026-05-13
- Module: multi-translate TTS duration loop
- Anchor: supersedes the evaluation sidecar from `2026-05-04-tts-speedup-shortcut-design.md`; keeps the segment assembly decision logic from `2026-05-13-tts-segment-candidate-assembly-design.md`

## Goal

Remove the automatic TTS speedup AI evaluation module from the production path. Segment candidate assembly is now the source of truth for choosing final TTS audio. The old evaluation page does not affect audio selection, can evaluate a candidate that is not the final assembled audio, and adds synchronous LLM latency/cost.

## Scope

Remove:

- Synchronous `tts_speedup_eval.run_evaluation` calls from the TTS duration loop.
- `speedup_eval_id` metadata and task-detail links/buttons for retrying evaluation.
- Admin routes, template, service helpers, navigation entry, and app blueprint registration for `/admin/tts-speedup-evaluations`.
- The `video_translate.tts_speedup_quality_review` runtime use case registration.
- Tests that only cover the removed evaluation route/service/orchestrator.

Keep:

- Segment candidate generation and assembly.
- `speedup_candidates`, `segment_assembly_*`, and existing speedup duration diagnostics used by the task detail UI.
- Existing historical DB table and old migration files. Production data is not dropped by this cleanup.

Add:

- A forward migration that disables existing `video_translate.tts_speedup_quality_review` bindings so fresh installs and existing systems no longer expose it as an active LLM use case.

## Runtime Behavior

When a speedup candidate is generated, the duration loop records candidate duration metadata and immediately runs the segment assembly optimizer. It does not create comparison MP3 files, does not call an LLM, and does not block on evaluation timeout.

If assembly hits `[video_duration - 1s, video_duration]`, the assembled audio is adopted. If it misses, the existing branch behavior remains unchanged: final-converged overshoot keeps the converged audio; shortcut-window misses continue the rewrite loop.

## Verification

- Static tests assert the removed route/service/orchestrator/use case are no longer registered.
- Duration-loop tests assert speedup assembly still converges and no longer depends on `appcore.tts_speedup_eval`.
- Route tests for the removed admin evaluation page are deleted.
