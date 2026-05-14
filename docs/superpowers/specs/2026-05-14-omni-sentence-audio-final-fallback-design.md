# Omni Sentence Audio Final Fallback Design

- Date: 2026-05-14
- Module: Omni translate `sentence_reconcile` TTS and task detail UI
- Anchors:
  - `AGENTS.md`: document-driven code, Omni/TTS topic pointers, and verification order.
  - `docs/superpowers/specs/2026-05-13-omni-asr-primary-compact-timeline-design.md`: ASR-primary compact timeline and sentence-level convergence.
  - `docs/superpowers/specs/2026-05-13-omni-tts-process-visualization-design.md`: task-detail visibility for sentence rewrite, TTS regeneration, and speed adjustment events.
  - `docs/superpowers/specs/2026-05-14-omni-final-fallback-compose-summary-design.md`: final fallback compose summary, clipped output, and front-end diagnostics.

## Problem

Omni `sentence_reconcile` can still finish with sentence audio that does not fit the target sentence window after all normal rewrite and TTS regeneration attempts. The current behavior has two gaps:

1. Near-miss audio inside the `0.9-1.1` ratio range may be sent through provider speed regeneration or left as a warning instead of being deterministically aligned to the target sentence duration.
2. Final non-converged audio does not distinguish the required fallback actions clearly enough: overlong audio should be clipped, but too-short audio should receive one final expansion chance before the task settles on a review-needed output.

Production needs the Omni detail page to show these final actions explicitly so the operator can tell whether the final audio was ffmpeg-aligned, clipped, or given a second expansion opportunity.

## Target Behavior

This design applies to Omni tasks using `tts_strategy = "sentence_reconcile"`.

1. If a sentence audio duration ratio is within `[0.9, 1.1]`, the sentence uses ffmpeg tempo alignment to hit the target duration. This applies whether the audio is too long or too short.
2. If the ratio is above `1.1` after normal convergence attempts, the final output proceeds with clipping/truncation instead of running another text rewrite. The clipped segment must be marked visibly in metadata and UI.
3. If the ratio is below `0.9` after normal convergence attempts, the sentence gets one final expansion rewrite opportunity. If that extra expansion reaches `[0.9, 1.1]`, ffmpeg tempo alignment may finish it. If it still misses, keep the closest candidate and mark the output as fallback/review-needed.
4. The final expansion opportunity is one bounded chance per sentence. It must not create an unbounded retry loop and must not reset the existing normal rewrite attempt counters.
5. Existing semantic coverage repair remains higher priority than duration-only final fallback. If required source terms are still missing, the sentence remains review-needed even if ffmpeg duration alignment succeeds.

## Runtime Flow

For each sentence:

1. Measure the initial TTS audio against `target_duration`.
2. If semantic coverage is missing, run the existing semantic repair flow.
3. If duration ratio is inside the existing `0.95-1.05` convergence band, keep the existing success path, but use ffmpeg tempo alignment when alignment is needed and possible.
4. If duration ratio is outside `0.95-1.05`, run the existing rewrite/regenerate loop.
5. After the normal loop is exhausted:
   - ratio in `[0.9, 1.1]`: run ffmpeg tempo alignment directly to `target_duration`.
   - ratio `> 1.1`: keep the selected overlong candidate and let source-timeline audio stitching clip it to its sentence window or final output timeline.
   - ratio `< 0.9`: run one final expansion rewrite, regenerate audio once, then re-evaluate. If the new ratio enters `[0.9, 1.1]`, run ffmpeg tempo alignment. Otherwise keep the closest candidate as `warning_short`.

The ffmpeg tempo step uses `atempo = current_duration / target_duration`. That keeps long audio faster and short audio slower. The ratio range `[0.9, 1.1]` is well inside ffmpeg `atempo`'s valid range and is intentionally wider than the previous native TTS speed range.

## Metadata

Sentence records should expose final fallback details without hiding existing fields:

- `status`: existing statuses remain valid; aligned near-miss audio may use `speed_adjusted`.
- `duration_ratio`: final measured ratio after the adopted candidate is selected.
- `final_fallback_action`: one of `ffmpeg_tempo_align`, `clip_overlong`, `extra_expand`, `extra_expand_failed`, or empty.
- `final_fallback_reason`: human-readable machine string such as `near_miss_ratio`, `overlong_after_attempts`, or `short_after_attempts`.
- `ffmpeg_tempo_applied`: boolean.
- `ffmpeg_tempo_ratio`: current duration divided by target duration.
- `ffmpeg_tempo_pre_duration` and `ffmpeg_tempo_post_duration`.
- `ffmpeg_tempo_audio_path`.
- `final_extra_expand_attempted`: boolean.
- `final_extra_expand_result`: `aligned`, `still_short`, `still_long`, `rewrite_failed`, or empty.
- `final_extra_expand_before_text` and `final_extra_expand_after_text` when an extra rewrite ran.
- `audio_clipped`, `audio_clip_reason`, `audio_clip_duration`, and `audio_clipped_seconds` continue to be written by the timeline audio builder for truncation.

Progress events in `tts_duration_rounds` should include:

- `phase = "ffmpeg_tempo_align"` when near-miss audio is aligned.
- `phase = "final_extra_expand_start"` before the bounded extra expansion rewrite.
- `phase = "final_extra_expand_result"` after the extra expansion TTS measurement.
- `phase = "final_clip_fallback"` when the sentence is knowingly left overlong for clipping.

## Front-End Display

The Omni task detail page should show the fallback in the existing "语音生成过程" and "最终合成说明" surfaces:

1. Sentence rows show a compact badge for final fallback action:
   - `FFmpeg 对齐`
   - `超长截断`
   - `二次扩写`
   - `二次扩写未收敛`
2. Attempt details show pre/post durations and ratio for ffmpeg alignment.
3. The final compose summary already shows clipping; update the wording so overlong fallback is described as an intentional final truncation path, not only as a generic overflow.
4. When short audio receives the extra expansion chance, the modal shows the before/after text and measured duration.
5. Old tasks without these new fields continue to render through existing inferred summary fallback.

## Error Handling

- ffmpeg alignment failure must not fail the whole task. The sentence keeps the pre-alignment candidate and records `ffmpeg_tempo_failed_reason`.
- Extra expansion rewrite failure must not fail the whole task. The sentence keeps the best previous candidate, records `final_extra_expand_result = "rewrite_failed"`, and remains `warning_short`.
- Missing audio files, corrupt media, invalid timeline data, and ffmpeg stitching failures remain blocking errors as defined by the final compose summary design.

## Verification

Add focused tests before implementation:

1. `reconcile_duration` aligns near-miss long audio inside `[0.9, 1.1]` with ffmpeg metadata and no extra rewrite.
2. `reconcile_duration` aligns near-miss short audio inside `[0.9, 1.1]` with ffmpeg metadata and no extra rewrite.
3. A final overlong sentence beyond `1.1` records `clip_overlong` and the final compose summary renders clipped output.
4. A final short sentence below `0.9` runs exactly one extra expansion attempt and can adopt a ffmpeg-aligned result.
5. A failed extra expansion remains `warning_short`, marks `extra_expand_failed`, and does not loop.
6. Template tests confirm the new fallback labels and phase labels are present in the task-detail script.

Run:

```bash
pytest tests/test_duration_reconcile.py tests/test_sentence_translate_runtime.py tests/test_translate_detail_shell_templates.py -q
```

Then start a dev server on a free port and verify the Omni detail route keeps the existing auth behavior: unauthenticated requests return `302`, authenticated requests return `200`.
