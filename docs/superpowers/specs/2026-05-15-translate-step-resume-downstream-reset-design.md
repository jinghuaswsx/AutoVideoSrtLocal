# Translate Step Resume Downstream Reset Design

Date: 2026-05-15
Status: approved

## Anchors

- `AGENTS.md`: document-driven code and isolated worktree rules.
- `docs/superpowers/specs/2026-05-07-omni-dynamic-resume-and-prompt-display-fix.md`: Omni resume uses the task's actual dynamic step list and must clear downstream artifacts.
- `docs/superpowers/specs/2026-05-13-multi-translate-optional-progress-design.md`: Multi-translate keeps resume behavior on main workflow steps.

## Requirement

On `/omni-translate/<task_id>` and `/multi-translate/<task_id>`, clicking any step's `从此步继续` button means restarting from the origin of that step.

The system must reset the selected step itself and every later workflow step to a true not-yet-run state. This reset includes backend state, intermediate outputs, review selections, generated artifacts, preview files, model/debug display metadata, and frontend-visible waiting/error/done state. Earlier steps and their source inputs remain intact.

## Design

Add a shared route helper that receives:

- the current task state,
- the ordered resumable step list for that task,
- the selected `start_step`.

The helper derives the reset range as `step_order[start_step:]` and returns a state update payload. The payload clears step-specific products from the selected step onward, including top-level `artifacts`, step messages, model tags, LLM debug refs, preview files, review flags, localized translation state, TTS outputs, subtitle outputs, composition/export outputs, and variant-scoped generated fields.

Omni keeps using the dynamic step order resolved from `task.plugin_config`. Multi-translate uses `MultiTranslateRunner.pipeline_step_names(include_analysis=False)`.

ASR post-processing keeps its existing source-language guard: when restarting from `asr_clean` or `asr_normalize`, the user's selected `source_language` is preserved and standardization artifacts are cleared.

## Non-Goals

- Do not delete files from disk in this change. Clearing state and preview references is enough for the next run to regenerate fresh outputs.
- Do not change step order, optional-progress behavior, preset validation, or runner internals.
- Do not add confirmation dialogs.

## Acceptance

1. Omni resume from `translate` clears stale translate, TTS, subtitle, compose/export, LLM debug, preview, and variant generated state before starting the runner.
2. Omni resume from a dynamic step such as `loudness_match` clears that step and later generated state without touching earlier ASR/translation state.
3. Multi-translate resume follows the same current-and-downstream reset semantics, not only for `asr_normalize`.
4. Invalid `start_step` still returns 400 before state mutation.
5. The workbench sees refreshed task state with selected and later steps set to `pending` and messages reset to `等待中...`.

## Verification

Run:

```bash
pytest tests/test_omni_translate_routes.py tests/test_multi_translate_routes.py -q
pytest tests/test_asr_normalize_render_smoke.py tests/test_prompt_inspector_assets.py -q
python3 -m compileall web/routes/omni_translate.py web/routes/multi_translate.py web/services/translate_step_reset.py
```
