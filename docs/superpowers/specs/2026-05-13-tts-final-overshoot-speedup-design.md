# TTS Final Overshoot Speedup Design

- Date: 2026-05-13
- Module: multi-translate TTS duration loop
- Anchor: follow-up to `2026-05-04-tts-speedup-shortcut-design.md`

## Goal

After the TTS duration loop has already converged into the final accepted range `[video_duration - 1s, video_duration + 2s]`, handle only the case where the converged audio is still longer than the source video.

In that case, regenerate one candidate with ElevenLabs `voice_settings.speed = audio_duration / video_duration`, compare the candidate with the converged audio through the existing `tts_speedup_eval` flow, and choose the final audio by duration:

- If the regenerated candidate is still inside the final accepted range, adopt the regenerated candidate.
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

It also sets `final_reason` explicitly:

- `converged_speedup_refined` when the regenerated candidate is adopted.
- `converged_speedup_miss_kept_original` when regeneration succeeds but misses the final range.
- `converged_speedup_failed_kept_original` when regeneration fails.
- `converged` when no final-overshoot regeneration is attempted.

## Verification

Add focused tests around `_run_tts_duration_loop`:

1. Final-range audio longer than video triggers ElevenLabs speed regeneration.
2. A regenerated candidate inside the final range is adopted.
3. A regenerated candidate outside the final range is evaluated but not adopted.
4. Final-range audio shorter than video keeps the existing no-speedup path.
