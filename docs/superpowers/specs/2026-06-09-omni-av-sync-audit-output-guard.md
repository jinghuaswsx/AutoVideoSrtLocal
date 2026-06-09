# Omni AV Sync Audit Output Guard

- Date: 2026-06-09
- Module: `omni_translate` audio-visual sync audit

## Anchors

- `docs/superpowers/specs/2026-05-07-omni-av-sync-audit-design.md`: `av_sync_audit` is a quality gate and its `audit_timeline` is the operator-facing display contract.
- `docs/superpowers/specs/2026-05-13-omni-av-sync-gemini-scorecard-design.md`: Gemini assess must produce structured per-ASR scorecard rows; missing fields mean the audit should be rerun or marked failed, not invented in the browser.
- `web/templates/CLAUDE.md`: translation detail additions must stay inside the shared task workbench structure.
- `docs/superpowers/specs/2026-06-08-targeted-pytest-verification.md`: run focused pytest instead of full pytest by default.

## Problem

Task `5e2a8f2542f15b8a94a81a2edd84f3f2` showed a long repeated garbage phrase in the "画面内容" cell of the AV sync audit table. The Doubao video-understanding response was normal. The garbage came from Gemini assess returning a malformed `timeline[0].visual_observation`; backend validation only checked that the field was non-empty, then promoted it into `audit_timeline` and the workbench rendered it as a valid audit result.

The same task also exposed a config mismatch: its stored `plugin_config` was empty, but the detail step order contained `av_sync_audit`. `pipeline.omni_av_sync_audit.run()` resolved an empty config directly through hard-coded defaults, while the route/runtime step builder can fall back to the configured default preset.

## Goal

1. Keep `av_sync_audit` as a quality safeguard.
2. Make the audit runner resolve configuration through the same runner/default-preset path as the Omni step builder when task `plugin_config` is missing.
3. Reject malformed Gemini scorecard rows before they become operator-facing `audit_timeline`.
4. Detect obvious garbage in visual-observation text: overlong values, heavy repeated fragments, and repeated suffix loops.
5. When structured assessment is invalid, mark the audit as failed/skipped for display and keep downstream video generation non-blocking. Do not show garbage as correct audit evidence.

## Non-Goals

- Do not remove the AV sync audit feature.
- Do not backfill or edit existing production task state in this change.
- Do not change Doubao video understanding.
- Do not change safe-auto audio/text mutation rules beyond preventing invalid audit inputs from being accepted.

## Backend Rules

- `run()` must resolve `cfg` with task `plugin_config` first, then `runner._resolve_plugin_config(task_id)` when available, then hard-coded defaults.
- `mode == "off"` must still be a no-op when reached defensively.
- Scorecard rows must have one row per input ASR row in report-only mode.
- Each row must include valid `asr_index`, `visual_observation`, `sync_score`, `diagnosis`, and `recommendation`.
- `visual_observation` must be concise and non-repetitive. Invalid values raise a structured assessment error.
- `_visual_observation_for_timeline()` must ignore invalid hint/issue visual text and fall back to shot notes or the manual-review placeholder.

## Verification

Focused tests:

```bash
python3 -m pytest tests/test_omni_av_sync_audit.py -q
```

Route smoke after code changes:

```bash
python3 -m web.app
curl -I http://127.0.0.1:<port>/omni-translate/5e2a8f2542f15b8a94a81a2edd84f3f2
```

Unauthenticated route should return `302`, not `500`.
