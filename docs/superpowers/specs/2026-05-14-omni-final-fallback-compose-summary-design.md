# Omni Final Fallback Compose Summary Design

- Date: 2026-05-14
- Module: Omni translate `sentence_reconcile` TTS and task detail UI
- Anchors:
  - `AGENTS.md`: document-driven code and Omni/TTS topic pointers.
  - `docs/superpowers/specs/2026-05-13-omni-asr-primary-compact-timeline-design.md`: compact ASR-primary audio timeline.
  - `docs/superpowers/specs/2026-05-13-omni-tts-process-visualization-design.md`: sentence reconcile process visibility.

## Problem

Sentence reconcile can finish with usable but imperfect results: semantic coverage warnings, long/short sentence duration warnings, or best-effort candidates after all rewrite attempts. The current UI marks the overall TTS step as done and shows a generic sync warning, but it does not clearly explain why a final video can still be produced, how the final audio was assembled, or whether any audio tail was truncated.

When a final sentence audio spills past its target timeline, the current source-timeline audio builder can raise `TimelineAudioOverflowError` and stop the task. Production needs a final video whenever a safe media file can be produced. Tail overflow should be clipped to the target video timeline and surfaced as a visible fallback, not silently hidden or treated the same as full convergence.

## Rules

1. Soft quality issues continue to final output:
   - `warning_long`, `warning_short`, `warning_semantic`, and `best_effort` sentence results are allowed to proceed to final audio/video composition.
   - The task must mark the final compose status as fallback/review needed when any such sentence exists.

2. Timeline overflow should prefer output over failure:
   - If generated speech extends beyond its sentence window, overlaps the next scheduled sentence, or extends past the final output timeline, the builder should still produce a final audio track capped to the target timeline.
   - The affected sentence indices and overflow seconds must be recorded.
   - The final output track is capped by ffmpeg `-t` to the target timeline duration, so any tail beyond that duration is truncated.

3. Only unsafe media failures should stop the task:
   - Missing audio files, invalid timeline data, ffmpeg failures, missing subtitles, and corrupt media remain blocking errors.
   - Sentence quality warnings and audio tail overflow are not blocking by themselves.

4. The detail page must include a final compose explanation:
   - Original video duration.
   - Effective speech duration, computed from sentence `tts_duration` sum.
   - Final output audio duration.
   - Final video duration when known, otherwise the target timeline duration.
   - Stitching method: sentence `audio_start_time`, compact gaps capped by `max_compact_gap`, silence for gaps, final ffmpeg duration cap.
   - Whether audio was truncated or overflow-clipped, how many seconds were affected, and which sentences are most likely affected.
   - Output state: fully converged, fallback output, clipped output, or review needed.

5. Semantic coverage must be visible:
   - Sentence rows and attempt rows must show semantic coverage as pass/fail using clear success/error styling.
   - When coverage fails, show omitted source anchors if available.

6. The sentence reconcile process must not dominate the main workbench:
   - The main TTS card should show compact progress, summary metrics, final compose explanation, and one prominent "语音生成过程" button.
   - Full per-sentence rows and per-attempt tables should render in a modal dialog opened by that button.
   - The button must remain available while TTS is running, using the latest progress rows available in `tts_duration_rounds`.
   - The modal should reuse the existing sentence reconcile rendering data; it must not require a second backend request.

7. Final processing must be visually prominent:
   - The detail page must make the final processing result stand out more than the intermediate sentence reconcile metrics.
   - The card must clearly separate final product duration from spoken content duration.
   - It must show how the final track is made: sentence audio placed by `audio_start_time`, silence inserted between sentences, tail silence padded when the target timeline is longer, and `ffmpeg -t` used as the final duration cap.
   - It must explicitly state whether audio was truncated. When truncation exists, show removed seconds and affected sentence indices. When no truncation exists, state that no speech was cut and explain whether the remaining time is silence padding.

## Data Shape

`variants.av.final_compose_summary` and top-level `final_compose_summary` should use:

```json
{
  "status": "fully_converged | fallback_output | clipped_output | review_needed",
  "video_duration": 31.35,
  "effective_speech_duration": 29.1,
  "final_audio_duration": 31.35,
  "final_video_duration": 31.35,
  "target_timeline_duration": 31.35,
  "timeline_mode": "compact_asr_primary",
  "max_compact_gap": 0.25,
  "stitching_method": "audio_start_time_compact_gaps",
  "silence_gap_count": 8,
  "silence_gap_duration": 2.25,
  "audio_content_duration": 32.2,
  "tail_padding_duration": 1.15,
  "final_processing_label": "最终输出 33.7s = 口播 30.6s + 句间静音 1.6s + 尾部静音 1.5s；无截断",
  "has_best_effort": true,
  "warning_sentence_count": 1,
  "semantic_warning_count": 1,
  "overflow_clipped": true,
  "audio_truncated": true,
  "truncation_seconds": 0.42,
  "affected_sentence_indices": [8],
  "notes": ["..."]
}
```

Missing values should render as unknown instead of hiding the card.

`audio_content_duration` means the scheduled speech track before the final tail
pad: effective spoken duration plus inserted inter-sentence silence, after
sentence placement on the compact audio timeline. `tail_padding_duration` is the
remaining silent tail between that scheduled content and the final output
duration. These two fields are presentation diagnostics; the media source of
truth remains the generated audio file and the final compose command.

## Verification

- Unit tests cover source-timeline audio overflow producing an output command instead of raising, with overflow diagnostics.
- Runtime tests cover `sentence_reconcile` writing `final_compose_summary` when best-effort or warning sentences exist.
- Template tests cover final compose summary labels and semantic coverage pass/fail markers.
- Template tests cover the prominent "语音生成过程" modal trigger and ensure the full sentence reconcile table is rendered through modal-specific functions.
