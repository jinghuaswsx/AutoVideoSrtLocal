# TTS Best-Pick Overrun Guard Design

- Date: 2026-05-13
- Module: multi-translate TTS duration loop
- Anchor: extends `2026-05-13-tts-segment-candidate-assembly-design.md`
- Incident task: `/multi-translate/cb4c96ef-776b-4535-a565-5ccc980ff0ca`

## Problem

The duration loop can miss the existing speedup branches when generated audio oscillates around the target but never lands inside the configured windows.

Observed task `cb4c96ef-776b-4535-a565-5ccc980ff0ca`:

- Source video duration: `37.872s`
- Final accepted range: `[36.872s, 39.872s]`
- Speedup shortcut range: `[34.085s, 41.659s]`
- Round durations: `33.567s`, `42.423s`, `31.922s`, `42.449s`, `28.604s`
- Best pick selected round 2 at `42.423s`, still `2.551s` over the final upper bound.

Because round 2 and round 4 were about `+12%` over video duration, they were outside the old `±10%` speedup shortcut and never produced speed candidates. Post-processing then truncated the final audio to the final upper bound, while the hard-video timeline still ended at the source video duration. This made the last TTS segment partially inaudible in the exported video.

## Goal

When all rewrite rounds miss the final range and the selected best-pick audio is still longer than the source video, try the existing segment candidate assembly before truncating or exporting. The final adopted audio for video composition must not exceed the source video duration unless an explicit future spec allows over-video output.

The immediate target remains `[video_duration - 1s, video_duration]` for speedup/assembly products.

## Runtime Design

### Best-Pick Overrun Branch

After `best_i` is selected in `_run_tts_duration_loop`, but before `_maybe_tempo_align`, run this branch only when:

- `best_record["audio_duration"] > video_duration`
- `best_product` has `tts_audio_path` and `tts_segments`
- the task uses a TTS engine that supports native speed parameters

The branch reuses the same segment candidate assembly helper used by final overshoot and shortcut-window paths:

1. Build the baseline candidate group from `best_product["tts_segments"]`.
2. Generate up to three native speed candidates using `ceil(audio_duration / video_duration, 2dp)` clamped to `[1.01, 1.05]`.
3. Run the beam-search assembly optimizer.
4. If assembly lands inside `[video_duration - 1s, video_duration]`, adopt it as the best-pick product.
5. If assembly misses or generation fails, keep the original best-pick product and continue to the hard overrun guard.

This branch may run even when the original audio is more than `+10%` over the source video. In the incident task, the first candidate speed would be clamped to `1.05`, giving the system one last native-speed attempt before destructive truncation.

### Hard Overrun Guard

After speed assembly and after any allowed atempo fallback, the final audio path used by downstream composition must be checked again.

If measured final audio duration is greater than `video_duration`, truncate to `video_duration`, not to `video_duration + 2s`. The metadata must be fitted to the audible prefix using `_fit_tts_segments_to_duration`, so `timeline_manifest` and `tts_result` no longer claim that inaudible tail audio is part of the final deliverable.

This guard applies to best-pick output only. Normal converged output keeps the existing behavior unless it enters the post-convergence overshoot assembly branch.

## Round Metadata

Best-pick speed assembly writes the existing fields used by the speedup UI, plus explicit context:

- `speedup_applied`
- `speedup_context = "best_pick_overrun"`
- `speedup_final_audio_choice = "assembly" | "best_pick" | "truncated"`
- `segment_assembly_applied`
- `segment_assembly_hit`
- `segment_assembly_duration`
- `segment_assembly_selected`
- `speedup_candidates`

The hard overrun guard writes:

- `hard_overrun_guard_applied`
- `hard_overrun_pre_duration`
- `hard_overrun_post_duration`
- `hard_overrun_removed_count`
- `hard_overrun_removed_duration`
- `hard_overrun_audio_path`
- `final_reason = "best_pick_segment_assembly_refined"` when assembly hits
- `final_reason = "best_pick_hard_truncated"` when hard truncation is required
- `final_reason = "best_pick"` only when no final overrun remains

The persisted `tts_duration_rounds.json` and `projects.state_json.tts_duration_rounds` must reflect these final metadata updates.

## Rewrite Oscillation Follow-Up

This spec does not change rewrite prompt generation or word-count tolerance. The incident showed a separate quality issue: accepted rewrite candidates oscillated between too short and too long. That requires a separate design focused on duration-improvement acceptance criteria for rewrite rounds.

This guard is intentionally narrower: it prevents bad final audio export when the current rewrite loop fails to converge.

## Verification

Add focused tests before implementation:

1. A best-pick audio at `42.4s` for a `37.872s` video triggers `best_pick_overrun` speed candidate assembly even though it is outside the old `±10%` shortcut window.
2. If the best-pick assembly hits `[v-1, v]`, the returned `tts_audio_path` and `tts_segments` use the assembled product, and the round record says `best_pick_segment_assembly_refined`.
3. If best-pick assembly misses and the original best-pick audio remains over video duration, the hard overrun guard truncates to `video_duration`, not `video_duration + 2s`.
4. The hard overrun guard updates returned segment metadata so the final segment is shortened or dropped to match the audible prefix.
5. Existing shortcut-window and final-overshoot tests remain green.
