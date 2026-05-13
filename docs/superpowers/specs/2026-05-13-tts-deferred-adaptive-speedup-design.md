# TTS Deferred Adaptive Speedup Design

- Date: 2026-05-13
- Module: multi-translate TTS duration loop
- Anchor: refines `2026-05-13-tts-segment-candidate-assembly-design.md`

## Goal

The rewrite loop should not run native speedup as a mid-loop shortcut on every
round. It should first let the existing rewrite/TTS process converge into the
stage-1 duration window, then run a single post-processing speedup phase.

Stage-1 convergence is `video_duration * 0.9` through `video_duration * 1.1`
or the language-specific profile window. The final audio target for speed
assembly remains `[video_duration - 1s, video_duration]`.

## Flow

1. Generate normal TTS rounds with the existing rewrite logic.
2. If the normal round lands in `[video - 1s, video]`, finish without speedup.
3. If the normal round lands in the stage-1 window but outside `[video - 1s, video]`,
   treat rewrite as converged and enter the speedup post-process once.
4. The post-process starts with the converged segment set plus up to three native
   ElevenLabs speed variants.
5. After each speed variant is generated, add its segment files to the candidate
   groups and try segment assembly.
6. If assembly hits `[video - 1s, video]`, adopt the assembled audio.
7. If assembly misses but the closest over-video assembly is shorter than the
   normal round audio and lands inside `[video - 1s, video + 2s]`, adopt that
   assembly as the final audio. It may still be trimmed by video composition,
   but it minimizes the tail cut compared with keeping the longer original
   round audio.
8. If all speed attempts fail or miss without an adoptable closest-over fallback,
   consume at most one extra rewrite fallback round. Keep the failed/missed
   round metadata for diagnostics with
   `speedup_final_audio_choice = "retry_rewrite"`.
9. If the extra fallback has already been spent, finalize on the best available
   stage-1 audio rather than looping indefinitely. The final task may still
   finish through a direct `[video - 1s, video]` hit, a later segment assembly
   hit, or the existing best-pick fallback after all allowed rounds.

This avoids falsely marking a stage-1-only audio as converged when the stricter
video-capped assembly target was not met.

## Adaptive Speed Selection

The first speed is based on the measured stage-1 audio duration:

- `speed = audio_duration / video_duration`
- Round away from `1.0` to two decimals so the first request actually changes
  timing.
- Clamp every value to `[0.95, 1.05]`.

Later speed values are chosen from feedback:

- If the previous speed result is still longer than video, move speed upward by
  `0.01`.
- If the previous speed result is shorter than `video - 1s`, move speed downward
  by `0.01`.
- `0.01` is 10% of the total allowed speed range (`0.95` to `1.05`).
- If the preferred direction hits a boundary or duplicate value, use the nearest
  unused in-range speed to keep segment durations diverse.
- Never pass a speed outside `[0.95, 1.05]` to the TTS provider.

The speedup phase stops early when segment assembly succeeds.

## Metadata

Round records should use:

- `speedup_context = "stage1_converged_postprocess"`
- `speedup_candidates` with one item per generated speed variant
- `segment_assembly_target_lo = video - 1s`
- `segment_assembly_target_hi = video`
- `speedup_final_audio_choice = "assembly"` when adopted
- `speedup_final_audio_choice = "assembly_closest_over"` when a shorter
  over-video assembly fallback is adopted
- `segment_assembly_fallback_applied = true` for that closest-over fallback
- `speedup_final_audio_choice = "retry_rewrite"` when all candidates miss and
  the one extra rewrite fallback is consumed

The UI should describe this as "stage-1 converged audio speedup assembly", not
as a mid-loop shortcut. It should also expose every generated speed candidate
and the selected segment list when assembly succeeds.
