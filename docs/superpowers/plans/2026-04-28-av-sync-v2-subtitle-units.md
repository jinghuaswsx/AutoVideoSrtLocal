# AV Sync V2 Subtitle Units Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a hybrid subtitle-unit layer to AV sync so final subtitles are generated from controlled sentence/TTS structure instead of ASR re-recognition.

**Architecture:** Keep sentence-level localization and convergence as the control layer. Add `subtitle_units` as a presentation/composition layer built from final sentences and TTS durations, then generate SRT from units with existing subtitle chunk formatting.

**Tech Stack:** Flask, Jinja, vanilla JavaScript, pytest, existing `pipeline.subtitle` utilities.

---

## File Map

- `appcore/av_translate_inputs.py`: persist `sync_granularity` with default `hybrid`.
- `pipeline/av_subtitle_units.py`: build subtitle units from final AV sentences.
- `pipeline/subtitle.py`: use existing `build_srt_from_chunks` for unit SRT.
- `appcore/runtime.py`: persist units and generate AV SRT from units.
- `web/routes/task.py`: rebuild units and SRT after manual sentence rewrite.
- `web/templates/_task_workbench.html`: add subtitle-unit panel shell.
- `web/templates/_task_workbench_scripts.html`: collect mode input and render units.
- `web/templates/_task_workbench_styles.html`: style unit panel with existing tokens.
- `tests/test_av_translate_inputs.py`: cover mode normalization.
- `tests/test_av_subtitle_units.py`: cover sentence and hybrid grouping.
- `tests/test_appcore_runtime.py`: cover AV runtime subtitle unit persistence and SRT.
- `tests/test_web_routes.py`: cover manual rewrite refreshing units and template hooks.

## Tasks

- [ ] Add failing tests for AV sync granularity normalization.
- [ ] Implement `sync_granularity` normalization.
- [ ] Add failing tests for subtitle-unit building.
- [ ] Implement `pipeline.av_subtitle_units`.
- [ ] Add failing runtime test proving AV SRT comes from subtitle units.
- [ ] Wire runtime to persist units and use `build_srt_from_chunks`.
- [ ] Add failing route/template tests for manual rewrite and UI hooks.
- [ ] Wire manual rewrite and workbench rendering.
- [ ] Run focused AV sync tests and whitespace check.
