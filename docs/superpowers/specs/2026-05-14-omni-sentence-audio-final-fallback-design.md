# Omni Sentence Audio Final Fallback Design

- Date: 2026-05-14
- Module: Omni translate `sentence_reconcile` TTS and task detail UI
- Anchors:
  - `AGENTS.md`: document-driven code, Omni/TTS topic pointers, and verification order.
  - `docs/superpowers/specs/2026-05-13-omni-asr-primary-compact-timeline-design.md`: ASR-primary compact timeline and sentence-level convergence.
  - `docs/superpowers/specs/2026-05-13-omni-tts-process-visualization-design.md`: task-detail visibility for sentence rewrite, TTS regeneration, and speed adjustment events.
  - `docs/superpowers/specs/2026-05-14-omni-final-fallback-compose-summary-design.md`: final fallback compose summary, clipped output, and front-end diagnostics.

## Problem

Omni `sentence_reconcile` can still finish with sentence audio that does not fit the target sentence window after all normal rewrite and TTS regeneration attempts. The final fallback must prevent unusable overlong audio without turning every near-miss sentence into a forced FFmpeg alignment pass.

1. FFmpeg tempo alignment is a final overlong fallback, not a global exact-duration normalizer. It is also behind an admin-controlled global switch that defaults to off.
2. Sentences already accepted by the normal `0.95-1.05` convergence band should keep their measured audio. Short accepted sentences leave natural gap; they are not slowed down to fill the whole source ASR window.
3. Final non-converged audio must still distinguish fallback actions clearly: speed-align overlong audio when safe, clip audio that is too long to speed-align, and give too-short audio one final expansion chance before review-needed output.

Production needs the Omni detail page to show these final actions explicitly so the operator can tell whether the final audio was ffmpeg-aligned, clipped, or given a second expansion opportunity.

## Target Behavior

This design applies to Omni tasks using `tts_strategy = "sentence_reconcile"`.

1. Initial TTS measurement never goes directly into FFmpeg tempo alignment. If the sentence is already `ok`, keep it as-is.
2. Rewrite/regenerate attempts also keep `ok` candidates as-is; accepted short audio must not be slowed down just to fill the source ASR window.
3. FFmpeg tempo alignment is only allowed when the global switch is enabled, after the normal convergence loop has exhausted, and the selected final candidate is still overlong: `duration_ratio > 1.05` and `duration_ratio <= 1.1`. The intent is to avoid clipping or timeline overflow for a near-miss overlong sentence.
4. If the ratio is above `1.1` after normal convergence attempts, the final output proceeds with clipping/truncation instead of another text rewrite. The clipped segment must be marked visibly in metadata and UI.
5. If the ratio is below `0.95` after normal convergence attempts, the sentence gets one final expansion rewrite opportunity. If that extra expansion reaches `0.95-1.05`, accept it without FFmpeg. If it remains short, keep the closest candidate and mark the output as fallback/review-needed.
6. The final expansion opportunity is one bounded chance per sentence. It must not create an unbounded retry loop and must not reset the existing normal rewrite attempt counters.
7. Existing semantic coverage repair remains higher priority than duration-only final fallback. If required source terms are still missing, the sentence remains review-needed even if ffmpeg duration alignment succeeds.

## Runtime Flow

For each sentence:

1. Measure the initial TTS audio against `target_duration`.
2. If semantic coverage is missing, run the existing semantic repair flow.
3. If duration ratio is inside the existing `0.95-1.05` convergence band, keep the existing success path without FFmpeg.
4. If duration ratio is outside `0.95-1.05`, run the existing rewrite/regenerate loop.
5. After the normal loop is exhausted:
   - ratio in `(1.05, 1.1]` and the global FFmpeg tempo fallback switch is enabled: run FFmpeg tempo alignment directly to `target_duration`.
   - ratio in `(1.05, 1.1]` and the switch is disabled: keep the selected overlong candidate, mark `ffmpeg_tempo_skipped_reason = "disabled"`, and let downstream stitching/clipping handle the remaining overflow.
   - ratio `> 1.1`: keep the selected overlong candidate and let source-timeline audio stitching clip it to its sentence window or final output timeline.
   - ratio `< 0.95`: run one final expansion rewrite, regenerate audio once, then re-evaluate. If the new ratio enters `0.95-1.05`, accept it without FFmpeg. Otherwise keep the closest candidate as `warning_short` or `warning_long`.

The FFmpeg tempo step uses `atempo = current_duration / target_duration`. It is only used to make final overlong audio faster; it is not used to slow short audio down.

The system setting `omni_ffmpeg_tempo_fallback_enabled` controls this feature globally from **Settings -> Omni 实验预设**. Missing or unreadable settings default to disabled so video generation never enters FFmpeg tempo alignment unless the admin explicitly enables it.

## Metadata

Sentence records should expose final fallback details without hiding existing fields:

- `status`: existing statuses remain valid; aligned final overlong audio may use `speed_adjusted`.
- `duration_ratio`: final measured ratio after the adopted candidate is selected.
- `final_fallback_action`: one of `ffmpeg_tempo_align`, `clip_overlong`, `extra_expand`, `extra_expand_failed`, or empty.
- `final_fallback_reason`: human-readable machine string such as `overlong_after_attempts`, `overlong_after_extra_expand`, or `short_after_attempts`.
- `ffmpeg_tempo_applied`: boolean.
- `ffmpeg_tempo_ratio`: current duration divided by target duration.
- `ffmpeg_tempo_pre_duration` and `ffmpeg_tempo_post_duration`.
- `ffmpeg_tempo_audio_path`.
- `ffmpeg_tempo_skipped_reason`: currently `disabled` when the global switch prevents a final overlong near-miss from entering FFmpeg tempo alignment.
- `final_extra_expand_attempted`: boolean.
- `final_extra_expand_result`: `accepted`, `aligned`, `still_short`, `still_long`, `rewrite_failed`, or empty. `aligned` means FFmpeg was actually applied to an overlong final expansion result; `accepted` means the expansion reached the normal convergence band without FFmpeg.
- `final_extra_expand_before_text` and `final_extra_expand_after_text` when an extra rewrite ran.
- `audio_clipped`, `audio_clip_reason`, `audio_clip_duration`, and `audio_clipped_seconds` continue to be written by the timeline audio builder for truncation.

Progress events in `tts_duration_rounds` should include:

- `phase = "ffmpeg_tempo_align"` when final overlong fallback audio is aligned.
- `phase = "ffmpeg_tempo_skipped"` when final overlong fallback would be eligible for FFmpeg alignment but the global switch is disabled.
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

The settings page exposes a compact **FFmpeg 变速兜底** switch under the global default preset selector. It defaults to off and writes through `/api/omni-presets/ffmpeg-tempo-fallback`.

## Error Handling

- ffmpeg alignment failure must not fail the whole task. The sentence keeps the pre-alignment candidate and records `ffmpeg_tempo_failed_reason`.
- Extra expansion rewrite failure must not fail the whole task. The sentence keeps the best previous candidate, records `final_extra_expand_result = "rewrite_failed"`, and remains `warning_short`.
- Missing audio files, corrupt media, invalid timeline data, and ffmpeg stitching failures remain blocking errors as defined by the final compose summary design.

## Verification

Add focused tests before implementation:

1. `reconcile_duration` keeps initial `ok` audio as-is and emits no FFmpeg metadata.
2. `reconcile_duration` keeps rewrite-produced `ok` audio as-is and emits no FFmpeg metadata.
3. A final overlong sentence inside `(1.05, 1.1]` aligns with FFmpeg only after normal attempts are exhausted.
4. With the global switch disabled, the same final overlong near-miss skips FFmpeg and records `ffmpeg_tempo_skipped_reason = "disabled"`.
5. A final overlong sentence beyond `1.1` records `clip_overlong` and the final compose summary renders clipped output.
6. A final short sentence below `0.95` runs exactly one extra expansion attempt and never uses FFmpeg just to slow audio down.
7. API and settings template tests confirm the admin switch is readable, writable, and rendered under the global preset selector.
8. Template tests confirm the new fallback labels and phase labels are present in the task-detail script.

Run:

```bash
pytest tests/test_duration_reconcile.py tests/test_omni_ffmpeg_tempo_config.py tests/test_omni_preset_api.py tests/test_settings_omni_preset_tab.py tests/test_sentence_translate_runtime.py tests/test_translate_detail_shell_templates.py -q
```

Then start a dev server on a free port and verify the Omni detail route keeps the existing auth behavior: unauthenticated requests return `302`, authenticated requests return `200`.
