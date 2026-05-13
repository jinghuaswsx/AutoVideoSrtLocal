# Omni Sentence Reconcile Parallel UI Design

## Context

Omni tasks that use `shot_decompose + shot_char_limit + sentence_reconcile + sentence_units` know the full sentence list before TTS convergence starts. The existing sentence reconciliation loop processes sentences one by one and the detail page exposes a standalone "句级收敛" panel plus an older "音画同步洞察" panel. That makes long tasks look stuck and delays visible progress for later sentences.

This design extends:

- `docs/superpowers/specs/2026-04-28-av-sync-v2-sentence-convergence-design.md`
- `docs/superpowers/specs/2026-05-07-omni-av-sync-audit-design.md`
- `docs/superpowers/specs/2026-05-13-omni-asr-primary-compact-timeline-design.md`

## Requirements

1. Sentence reconciliation must pre-seed UI progress for every known sentence before per-sentence convergence starts.
2. The outer sentence loop must run with a 5-worker pool by default.
3. A single sentence's internal loop remains serial: measure, rewrite, regenerate TTS, measure again, then decide the next attempt from that state.
4. When one sentence finishes, the worker pool starts the next pending sentence until all sentences complete.
5. Final assembly waits for all sentence workers, then restores original sentence order before compact scheduling, rebuilding `tts_full.av.mp3`, writing `tts_result.av.json`, and continuing to subtitles.
6. Progress events are keyed by `sentence_position` and `asr_index`; UI rows update fixed slots instead of appending in completion order.
7. Sentence rewrite for this flow uses OpenRouter Gemini 3 Flash: `google/gemini-3-flash-preview`.
8. The standalone "句级收敛" panel moves into the "语音生成" card because it is part of TTS generation.
9. "字幕编排" appears after the "语音生成" card.
10. The standalone "音画同步洞察" panel is removed from the task detail page. This does not remove the backend `av_sync_audit` step or report capability.

## Backend Design

`pipeline.duration_reconcile.reconcile_duration()` keeps the same public role and return shape, but it separates work into two layers:

- A per-sentence reconcile function owns all state for one sentence.
- A coordinator builds initial sentence states, emits `queued` records for all of them, runs up to 5 sentence workers, drains progress records, and returns final sentences sorted by original position.

The coordinator must not mutate shared sentence dictionaries from multiple threads. Each worker receives its own `current` dictionary and communicates progress through a callback or queue. If a worker raises, the coordinator emits a failed sentence record for that position and re-raises so the task can fail visibly rather than silently producing a partial final audio file.

The default pool size is 5. Tests may pass a smaller `max_sentence_workers` to make ordering and concurrency deterministic.

## Frontend Design

The detail page already has a TTS preview container (`preview-tts`) and `renderSentenceReconcileDurationLog()`. That rendering becomes the primary sentence convergence UI:

- It pre-renders rows/cards for all sentence slots when queued progress exists.
- It labels `queued` as pending, `running` phases as in progress, and terminal phases as converged, warning, or failed.
- It uses `sentence_position` as the stable sort key, falling back to `asr_index` only when needed.
- It includes the same summary metrics previously shown in the standalone "句级收敛" panel.

The old standalone `avConvergencePanel` is removed after its fields are represented inside the TTS card. `avSubtitleUnitsPanel` remains a separate card and is placed immediately after the task workbench so it visually follows the TTS step.

The old standalone `avInsightsPanel` is removed. Shot notes and warnings remain available through sentence rows, attempts, final sentence metadata, and the optional backend `av_sync_audit` report if that step is enabled.

## Model Binding

`video_translate.av_rewrite` uses:

- provider: `openrouter`
- model: `google/gemini-3-flash-preview`
- usage service: `openrouter`

Runtime defaults and the production binding row must both move to Gemini 3 Flash. A migration updates only old default AV rewrite rows (`openai/gpt-5.5` or the earlier Claude default) so manually changed non-default bindings are not overwritten.

`video_translate.av_localize` remains on its existing default unless a separate spec changes the first-pass localization model.

## Verification

Required tests:

1. `reconcile_duration()` runs sentence workers concurrently while returning final sentences in original order.
2. `reconcile_duration()` emits queued progress records for every sentence before worker records.
3. `video_translate.av_rewrite` defaults to OpenRouter Gemini 3 Flash.
4. The detail template no longer contains the standalone "音画同步洞察" panel.
5. Sentence convergence rendering remains inside the TTS duration log.
6. Subtitle arrangement markup remains present and is rendered after the TTS step markup.

Manual route verification after tests:

- An unauthenticated task detail route returns 302 instead of 500.
- An authenticated task detail route returns 200 and includes the updated TTS/subtitle UI.
