# Omni V2 Stable Default Design

- **Date**: 2026-05-24
- **Status**: Approved for implementation
- **Code anchors**:
  - `AGENTS.md` project rules: document-driven code, V2 changes must not affect original `/omni-translate`.
  - `docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md`: original omni plugin flow and preset system.
  - `appcore/runtime_omni.py`: source of truth for original omni step order and runtime behavior.
  - `pipeline/duration_reconcile.py`: source of truth for reliable sentence-level convergence fallback behavior.

## Goal

Make `/omni-translate-v2` the stable efficiency-optimized translation entry while keeping original `/omni-translate` unchanged. V2 must use a single fixed default flow at task creation time and must not expose preset selection to users.

## Non-Goals

- Do not change original `/omni-translate`, its preset UI, or `OmniTranslateRunner`.
- Do not change `pipeline/duration_reconcile.py`.
- Do not expose V2 preset or per-task capability selection in the create modal.
- Do not make AV sentence mode the default until it has separate production validation.

## Fixed V2 Modes

V2 has one hidden runtime setting, `omni_v2.pipeline_mode`, with two allowed values:

- `omni_standard` (default): follows original omni's stable default logic.
- `av_sentence`: opt-in hidden mode for sentence-level AV experiments.

Invalid or missing values fall back to `omni_standard`. The setting is read from `system_settings`; DB read failures must not block task creation or detail rendering.

## Default Flow

`omni_standard` stores this fixed `plugin_config` on every new V2 task:

```json
{
  "asr_post": "asr_clean",
  "shot_decompose": false,
  "translate_algo": "standard",
  "source_anchored": true,
  "tts_strategy": "five_round_rewrite",
  "subtitle": "asr_realign",
  "voice_separation": true,
  "loudness_match": true,
  "av_sync_audit": "off"
}
```

This preserves original omni's primary behavior and its existing five-round logs, artifacts, preview files, subtitle realignment, loudness match, compose, and export flow.

## Hidden AV Mode

`av_sentence` stores this fixed `plugin_config`:

```json
{
  "asr_post": "asr_clean",
  "shot_decompose": false,
  "translate_algo": "av_sentence",
  "source_anchored": false,
  "tts_strategy": "sentence_reconcile",
  "subtitle": "sentence_units",
  "voice_separation": true,
  "loudness_match": true,
  "av_sync_audit": "off"
}
```

This mode uses `SentenceReconcileStrategyV2` and `pipeline.duration_reconcile_v2`. It exists only as a hidden configuration switch, not as a user-facing preset.

## Stability Rules

- V2 route creation ignores submitted `plugin_config` and `preset_id`.
- V2 duplicate is treated as a new V2 task and stores the current hidden mode fixed config, not the source task's historical config.
- V2 detail and resume must compute steps from the V2 fixed config resolver, not from the original omni default preset resolver.
- Existing V2 tasks with stored `plugin_config` remain resumable; the fixed config only applies when a V2 task has no valid stored config.
- The sidebar must only show V2 to users who have `omni_translate_v2` permission.

## Duration Reconcile V2 Reliability

`duration_reconcile_v2` may use local acoustic prediction to reduce unnecessary TTS generation, but physical TTS measurement always wins:

- A candidate may be selected early only when its real measured status is `ok` or when it is real-measured and safely ffmpeg-adjustable.
- A sandbox-perfect prediction cannot override a real measured miss.
- If the top sandbox candidate fails after real TTS, V2 must continue testing other top candidates or continue rewrite attempts up to configured limits.
- If V2 exhausts candidates, it may return best-effort warnings, but it must not stop only because a sandbox estimate was good.

## Logging And Visualization

V2 keeps original omni's visible strengths:

- `omni_standard` uses the original five-round duration log structure.
- `av_sentence` emits `sentence_reconcile_v2` progress records but must keep the same fields used by the existing duration panel: attempt count, TTS count, status, duration ratio, delta, selected attempt, best-effort reason, and real-vs-sandbox marker.
- Every V2 task stores `plugin_config` so detail annotations and dynamic step order are deterministic.

## Verification

Implementation must include tests for:

- V2 create ignores inline `plugin_config` and `preset_id`, then stores `omni_standard` config by default.
- V2 duplicate stores the current fixed config instead of a source task's historical config.
- V2 hidden `av_sentence` setting stores the AV fixed config.
- Invalid hidden mode falls back to `omni_standard`.
- V2 step resolution ignores global omni default preset when a V2 task lacks stored config.
- `duration_reconcile_v2` does not accept a sandbox-perfect but real-measured bad candidate.
- Original omni create behavior remains covered by existing tests.
