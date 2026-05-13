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

The implementation can use beam search. Segment counts are small, but beam search avoids exponential blow-up if a future task has many segments.

## Runtime Decisions

Post-convergence overshoot branch:

- Trigger only when the regular round already converged to `[v-1, v+2]` and `audio_duration > video_duration`.
- Try segment assembly over original candidates plus up to three speed variants.
- If assembly hits `[v-1, v]`, adopt the assembled audio.
- If assembly misses, keep the original converged audio.

Older shortcut branch (`[0.9v, 1.1v]` but outside final range):

- Try the same segment assembly.
- If assembly hits `[v-1, v]`, adopt the assembled audio and finish.
- If assembly misses, continue the normal rewrite loop instead of finalizing on an over-video speedup product.

For diagnostics, write metadata to the round record:

- `segment_assembly_applied`
- `segment_assembly_hit`
- `segment_assembly_audio_path`
- `segment_assembly_duration`
- `segment_assembly_gap`
- `segment_assembly_selected`
- `speedup_candidates`
- Existing `speedup_*` fields remain populated for UI compatibility.

## Output

The selected segment files are concatenated into:

`tts_full.round_<round>.assembled.mp3`

The selected segment metadata becomes the final `tts_segments` for composition. This means the final video composition consumes the exact segment files chosen by the optimizer.

## Verification

- Pure optimizer tests cover exact-hit selection, over-video rejection, and tie-breaking toward fewer speed-modified segments.
- Duration-loop tests cover final overshoot adoption, final overshoot miss keeping the original audio, and shortcut miss continuing to rewrite.
- UI shell tests confirm the detail page knows how to display segment assembly metadata without breaking older speedup records.
