# TTS Segment Candidate Assembly Design

- Date: 2026-05-13
- Module: multi-translate TTS duration loop
- Anchor: extends `2026-05-04-tts-speedup-shortcut-design.md` and supersedes the single whole-track candidate behavior in `2026-05-13-tts-final-overshoot-speedup-design.md`

## Goal

When TTS audio is close to the video duration but still too long, stop treating native speedup as one whole-track gamble. Each generated segment audio file is a reusable candidate as long as its `tts_text` is unchanged. The runtime should build a per-segment candidate pool, choose one candidate per segment, and assemble a final full audio track whose total duration fits inside the source video while staying as close as possible to the video duration.

The primary target window for assembled speedup audio is `[video_duration - 1s, video_duration]`. This is stricter than the generic TTS convergence window `[video_duration - 1s, video_duration + 2s]` because speedup exists to remove overrun, not to produce another over-video audio file.

## Candidate Pool

For each duration-loop round, the candidate pool starts with the regular TTS result:

- `segment_index`
- `tts_text_hash`
- `audio_path`
- `duration`
- `round_index`
- `speed = 1.0`
- `source = "round"`

If the round needs speedup, the runtime may generate up to three native ElevenLabs speed variants. Each speed variant contributes one additional candidate per segment:

- Speed candidates start at `ceil(audio_duration / video_duration, 2dp)`.
- Speed is clamped to `[1.01, 1.05]`.
- The next two candidates add `0.01` each, still capped at `1.05`.
- Duplicate speed values are skipped.
- If `audio_duration <= video_duration`, no speedup candidates are generated.

The first implementation uses full-track speed regeneration for each speed value because the existing `regenerate_full_audio_with_speed` function already returns per-segment files and durations. The optimizer then mixes those per-segment files instead of blindly adopting the whole regenerated track.

## Assembly Optimizer

Given `N` segment groups, the optimizer picks exactly one candidate per segment.

Hard constraints:

- Every selected candidate must have the same `tts_text_hash` as the segment group baseline.
- Total duration must be `<= video_duration`.
- The assembled track is considered a hit only when total duration is also `>= video_duration - 1s`.

Ranking:

1. Prefer hit combinations inside `[video_duration - 1s, video_duration]`.
2. Within valid combinations, maximize total duration, which minimizes the remaining video gap.
3. Prefer fewer speed-modified segments.
4. Prefer lower total speed penalty.

2026-05-13 follow-up: the optimizer should prefer an exact dynamic-programming
search over beam search. Candidate counts are small, and the optimizer is not
the runtime bottleneck compared with ElevenLabs generation. The exact search
should quantize durations to milliseconds, keep one best combination per total
duration bucket, and preserve the same ranking rules above. Beam search remains
only as a safety fallback for unexpectedly huge state spaces.

## Runtime Decisions

Post-convergence overshoot branch:

- Trigger only when the regular round already converged to `[v-1, v+2]` and `audio_duration > video_duration`.
- Try segment assembly over original candidates plus up to three speed variants.
- If assembly hits `[v-1, v]`, adopt the assembled audio.
- If assembly misses but the closest over-video assembly is shorter than the
  original round audio and still inside `[v-1, v+2]`, assemble that closest
  over-video combination, immediately truncate it to `video_duration`, and
  adopt the truncated audio for video composition.
- If no assembly improves the original converged audio, keep the original
  converged audio.

Older shortcut branch (`[0.9v, 1.1v]` but outside final range):

- Try the same segment assembly.
- If assembly hits `[v-1, v]`, adopt the assembled audio and finish.
- If assembly misses but the closest over-video assembly is shorter than the
  original round audio and enters `[v-1, v+2]`, assemble that closest
  over-video combination, immediately truncate it to `video_duration`, adopt
  the truncated audio, and finish.
- If assembly misses without an adoptable closest-over fallback, run at most one
  extra rewrite fallback round (`speedup_final_audio_choice = "retry_rewrite"`).
  After that fallback is spent, keep the best available stage-1 audio rather
  than looping indefinitely.

For diagnostics, write metadata to the round record:

- `segment_assembly_applied`
- `segment_assembly_hit`
- `segment_assembly_candidate_count`
- `segment_assembly_audio_path`
- `segment_assembly_duration`
- `segment_assembly_gap`
- `segment_assembly_selected`
- `segment_assembly_best_under_duration` and `segment_assembly_best_under_selected`
- `segment_assembly_closest_over_duration` and `segment_assembly_closest_over_selected`
- `segment_assembly_min_duration` and `segment_assembly_min_selected`
- `segment_assembly_fallback_applied = true` when the selected assembly did
  not hit `[v-1, v]` but is adopted because it is the best shorter over-video
  fallback inside `[v-1, v+2]`
- `segment_assembly_fallback_reason = "closest_over_improved"`
- `segment_assembly_truncated = true` when that closest-over fallback is
  trimmed before composition
- `segment_assembly_pre_truncation_duration`
- `segment_assembly_post_truncation_duration`
- `segment_assembly_removed_count` and `segment_assembly_removed_duration`
- `segment_assembly_untrimmed_audio_path`
- `speedup_candidates`
- `speedup_final_audio_choice = "assembly_truncated"` when the adopted final
  artifact is the truncated closest-over assembly
- `speedup_final_audio_choice = "retry_rewrite"` when a stage-1 post-process miss consumes the one extra rewrite fallback
- Existing `speedup_*` fields remain populated for UI compatibility.

The task detail UI must show every speed candidate as a separate diagnostic row
and, when assembly succeeds, show the exact selected segment list: segment
index, source (`round` or `speedup`), speed, duration, attempt, and audio path.
When the closest over-video assembly is adopted, the UI must show both the
untrimmed assembly duration and the truncated final duration, plus removed
duration/count. When assembly misses without adoption, the UI should say that
no segment combination fit inside `[v-1, v]` and the rewrite loop continues. It
must also show the nearest non-adopted assembly evidence: either the best
combination under video, the closest combination over video, or the shortest
combination, including the segment list used for that diagnostic result. This
distinguishes "no assembly ran" from "assembly ran but every combination was
still unusable."

## Output

The selected segment files are concatenated into:

`tts_full.round_<round>.assembled.mp3`

The selected segment metadata becomes the final `tts_segments` for composition. This means the final video composition consumes the exact segment files chosen by the optimizer.

## 2026-05-13 Update: AI Evaluation Removal

The automatic `tts_speedup_eval` sidecar is removed from the production path. Segment assembly remains the decision source for final audio selection. The runtime no longer calls an LLM, writes `speedup_eval_id`, or links to `/admin/tts-speedup-evaluations`; historical database rows may remain for old tasks, but new tasks only write speedup and segment assembly diagnostics.

## Verification

- Pure optimizer tests cover exact-hit selection, over-video rejection, and tie-breaking toward fewer speed-modified segments.
- Duration-loop tests cover final overshoot adoption, final overshoot miss keeping the original audio, and shortcut miss continuing to rewrite.
- UI shell tests confirm the detail page knows how to display segment assembly metadata without breaking older speedup records.
