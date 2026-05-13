# TTS Final Overshoot Speedup Design

- Date: 2026-05-13
- Module: multi-translate TTS duration loop
- Anchor: follow-up to `2026-05-04-tts-speedup-shortcut-design.md`

## Goal

After the TTS duration loop has already converged into the final accepted range `[video_duration - 1s, video_duration + 2s]`, handle only the case where the converged audio is still longer than the source video.

In that case, regenerate one candidate with ElevenLabs `voice_settings.speed`, compare the candidate with the converged audio through the existing `tts_speedup_eval` flow, and choose the final audio by duration:

- Compute raw speed as `audio_duration / video_duration`.
- Clamp speed to `[0.95, 1.05]`.
- Format speed to two decimals by always rounding upward, not by normal rounding. Examples: `1.0071 -> 1.01`, `1.0012 -> 1.01`.
- If the regenerated candidate is still inside the final accepted range and is shorter than the original converged audio, adopt the regenerated candidate.
- If the regenerated candidate is inside the final accepted range but is equal to or longer than the original converged audio, keep the original converged audio.
- If the regenerated candidate is outside the final accepted range, keep the original converged audio.
- If regeneration fails, keep the original converged audio.
- If the converged audio is shorter than or equal to the video duration, keep the existing logic unchanged and do not regenerate.

## Scope

This change is intentionally limited to the existing converged branch in `appcore/runtime/_pipeline_runner.py`.

The older shortcut for audio that is within `[0.9v, 1.1v]` but outside the final accepted range stays unchanged. That branch does not have a converged original to preserve, so this new keep-original decision applies only after final convergence.

## Runtime Metadata

The final-overshoot regeneration reuses the existing speedup round fields:

- `speedup_applied`
- `speedup_speed`
- `speedup_pre_duration`
- `speedup_post_duration`
- `speedup_hit_final`
- `speedup_audio_path`
- `speedup_chars_used`
- `speedup_eval_id`
- `speedup_failed_reason`
- `speedup_context = "final_converged_overshoot"` so the UI and stats can distinguish this post-convergence regeneration from the older speedup shortcut.
- `speedup_final_audio_choice = "speedup" | "converged"` to show which audio is used for final video composition.

It also sets `final_reason` explicitly:

- `converged_speedup_refined` when the regenerated candidate is adopted.
- `converged_speedup_longer_kept_original` when regeneration succeeds and hits the final range, but is not shorter than the original converged audio.
- `converged_speedup_miss_kept_original` when regeneration succeeds but misses the final range.
- `converged_speedup_failed_kept_original` when regeneration fails.
- `converged` when no final-overshoot regeneration is attempted.

## UI And Stats

The task detail page must make this extra step explicit:

- The speedup card title is `收敛音频变速生成` for this post-convergence branch.
- The card shows the processing result and whether video composition uses the regenerated speedup audio or the original converged audio.
- The final summary repeats the adopted audio choice next to the final duration/composition explanation.
- `tts_generation_summary` includes `converged_speedup_audio_generations`, counted only when this final-overshoot branch successfully produces an extra speedup audio file.
- The detail summary lists this count separately as `收敛音频变速生成音频 N 次`.

## Verification

Add focused tests around `_run_tts_duration_loop`:

1. Final-range audio longer than video triggers ElevenLabs speed regeneration.
2. Speed is clamped to `[0.95, 1.05]` and rounded upward to two decimals.
3. A regenerated candidate inside the final range and shorter than the converged audio is adopted.
4. A regenerated candidate inside the final range but longer than the converged audio is evaluated but not adopted.
5. A regenerated candidate outside the final range is evaluated but not adopted.
6. Final-range audio shorter than video keeps the existing no-speedup path.
7. The detail UI and generation summary expose the extra audio generation count and final adopted audio choice.
