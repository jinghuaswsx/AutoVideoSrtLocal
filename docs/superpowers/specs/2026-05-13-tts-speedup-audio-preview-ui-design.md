# TTS Speedup Audio Preview UI Design

- Date: 2026-05-13
- Module: multi-translate task detail / shared task workbench
- Anchor: follows `2026-03-31-step-preview-design.md`, `2026-05-13-tts-final-overshoot-speedup-design.md`, and `2026-05-13-tts-segment-candidate-assembly-design.md`

## Problem

The speedup diagnostic card currently renders two native audio controls labeled
`变速前` and `变速后`. In narrow task-detail layouts the controls collapse to a
small browser-control remnant, so users cannot reliably play or inspect them.

The generated URLs also point at `/tasks/<id>/artifact?path=...`, which is not a
registered artifact route. This makes the preview look like an audio player while
often serving a 404 instead of the TTS artifact.

## Goal

The task detail page must make speedup audio comparison directly usable:

- `变速前` plays the round `tts_full.round_<round>.mp3` audio.
- `变速后` plays the adopted speedup or segment-assembly audio recorded in
  `segment_assembly_audio_path` or `speedup_audio_path`.
- Candidate and segment links use the same safe artifact path endpoint.
- The UI remains readable in the existing workbench column and on smaller widths.

## Design

Add a safe read-only endpoint for task-local artifact paths. The endpoint accepts
a `path` query parameter, resolves relative paths below the task directory, and
then delegates to the existing safe file response helper. Path traversal and
paths outside the allowed task/output/upload roots still return 404.

Update the shared workbench speedup renderer to build audio URLs from
`TASK_WORKBENCH_CONFIG.apiBase`, not the non-existent `/tasks/...` route. Replace
the two collapsed native audio elements with compact preview panels that contain:

- the label and duration,
- a play button,
- a pause button,
- a live time display,
- an `打开文件` link for direct browser playback/download behavior.

The existing hidden `Audio()` helper already handles playback and time updates,
so the new panels should reuse `playAudio()` and `pauseAudio()` instead of adding
another playback implementation.

## Scope

In scope:

- Shared task workbench JS/CSS for speedup audio cards.
- Safe task-local artifact path response helper.
- Artifact path routes for the translation detail APIs that use this workbench.
- Focused tests for URL generation, player markup, layout CSS, and path safety.

Out of scope:

- TTS generation, speed selection, segment assembly, and duration-loop behavior.
- Waveforms, trimming tools, or timeline editing.
- Production service restart or deployment.

## Verification

- Unit tests prove task-relative artifact serving accepts in-task files and
  rejects traversal/outside paths.
- Template tests prove the speedup renderer uses the configured API base,
  no longer emits the broken `/tasks/<id>/artifact?path=` URL, and renders stable
  preview controls instead of raw collapsed audio tags.
- Route tests cover the multi-translate artifact path endpoint.
- Existing multi-translate route tests still pass.
