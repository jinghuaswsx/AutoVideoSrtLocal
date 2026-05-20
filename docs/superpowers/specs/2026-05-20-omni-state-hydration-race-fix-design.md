# Omni State Hydration Race Fix Design

- Date: 2026-05-20
- Status: approved

## Anchors

- `AGENTS.md`: document-driven code, isolated worktree, and Omni topic pointers.
- `docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md`: Omni `plugin_config` determines whether compose/export read `variants.av` or `variants.normal`.
- `docs/superpowers/specs/2026-05-15-translate-step-resume-downstream-reset-design.md`: Omni resume and detail state must follow the task's real dynamic step list and generated state boundaries.
- `docs/superpowers/specs/2026-05-14-omni-final-fallback-compose-summary-design.md`: safe generated media should continue to final compose; only missing unsafe media should block.

## Problem

The Omni detail API hydrates the in-process `task_state` cache from `projects.state_json` on every poll. While a pipeline runner is executing in the same process, that poll can replace a newer in-memory task with an older DB snapshot. In the observed failure, `subtitle` generated `subtitle.av.srt` and recorded it in preview/artifact state, but the variant-scoped `variants.av.srt_path` field was lost before `compose`. Compose then read `variants.av["srt_path"]` and raised `KeyError: 'srt_path'`.

## Design

1. The detail route must not overwrite a locally active task cache with a DB snapshot. If the current process already has an active task for the requested id and the DB row confirms the viewer can access it, return the in-memory task.
2. Hydration from DB remains allowed when the task is cold in this process or has reached a terminal state.
3. Before compose reads required variant paths, normalize variant state from compatible top-level or preview fields. If the selected variant is missing `srt_path` but `task.srt_path` or `preview_files.srt` points to an existing subtitle path, copy it into the variant state and persist that repair. Do the same for `tts_audio_path` using the existing top-level or preview audio path.
4. Missing real media remains a blocking error with a clear message; the fallback only repairs state references to already generated artifacts.

## Acceptance

1. A stale DB hydrate cannot remove `variants.av.srt_path` from a locally active Omni task.
2. Compose repairs a missing variant `srt_path` from an existing `preview_files.srt`/top-level path before calling `compose_video`.
3. Compose still fails clearly when no subtitle path is available anywhere.
4. Existing Omni route and runtime tests remain green.

## Verification

Run:

```bash
pytest tests/test_omni_translate_routes.py tests/test_runtime_omni_dispatch.py -q
python3 -m compileall web/routes/omni_translate.py appcore/runtime/_pipeline_runner.py
```
