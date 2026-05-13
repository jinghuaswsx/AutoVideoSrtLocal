# Multi-Translate Optional Progress Design

Date: 2026-05-13

## Document Anchors

- `AGENTS.md` declares that `docs/superpowers/specs/` is the source of truth and that code changes need a document anchor.
- `docs/superpowers/specs/2026-04-18-multi-translate-design.md` defines the multi-translate workbench as a reuse of `_task_workbench.html`.
- `docs/superpowers/specs/2026-04-16-de-fr-soft-video-and-optional-analysis-design.md` establishes that optional analysis is shown separately and excluded from main progress.
- `docs/superpowers/specs/2026-05-07-omni-av-sync-audit-design.md` defines AV sync audit as a report / safe-fix quality check that must not block the pipeline on audit failure.

## Requirement

On `/multi-translate/<task_id>`, optional items must not count toward the task progress bar.

For multi-translate, the top status card has two concepts:

- Main workflow state: steps that can run, wait, fail, or be resumed.
- Progress denominator: only required production steps that decide whether the task's primary output is complete.

`analysis` and translation quality assessment are already displayed in the optional area and are not part of `MAIN_STEPS`. `av_sync_audit` remains visible as a resumable step because it can run in the pipeline, but it is a quality/report step and must not inflate or hold back the progress percentage.

## Design

The route will pass a third step list to `_task_workbench_scripts.html`:

- `pipeline_step_order`: all rendered step cards, including optional cards.
- `pipeline_main_steps`: steps that drive running/waiting/error/resume behavior.
- `pipeline_progress_steps`: steps that count toward the percentage bar.

For multi-translate:

```text
pipeline_main_steps:
extract, asr, separate, asr_normalize, voice_match, alignment, translate,
tts, loudness_match, subtitle, compose, av_sync_audit, export

pipeline_progress_steps:
extract, asr, separate, asr_normalize, voice_match, alignment, translate,
tts, loudness_match, subtitle, compose, export
```

The front-end status card will compute percentage from `PROGRESS_STEPS`, while keeping current-stage, waiting-state, failure-state, and resume-button logic on `MAIN_STEPS`.

If `pipeline_progress_steps` is not provided by a route, it defaults to `pipeline_main_steps` to preserve existing behavior for other workbench users.

## Acceptance

1. The multi-translate detail page renders `PROGRESS_STEPS` without `av_sync_audit`.
2. The multi-translate detail page still renders `MAIN_STEPS` with `av_sync_audit`, so status and resume behavior stay available.
3. The status card percentage uses `PROGRESS_STEPS`, not `MAIN_STEPS`.
4. Existing optional `analysis` and translation quality assessment remain excluded from the progress denominator.

## Verification

Run:

```bash
pytest tests/test_multi_translate_routes.py::test_multi_translate_progress_steps_exclude_optional_av_sync_audit tests/test_multi_translate_routes.py::test_task_status_progress_uses_progress_steps_for_percentage -q
pytest tests/test_multi_translate_routes.py tests/test_asr_normalize_render_smoke.py tests/test_prompt_inspector_assets.py -q
```

