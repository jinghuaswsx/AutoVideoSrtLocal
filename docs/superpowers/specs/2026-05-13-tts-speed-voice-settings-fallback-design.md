# TTS Speed Voice Settings Fallback Design

- Date: 2026-05-13
- Module: multi-translate TTS duration loop
- Anchor: extends `2026-05-13-tts-deferred-adaptive-speedup-design.md`,
  `2026-05-13-tts-segment-candidate-assembly-design.md`, and
  `2026-05-13-tts-best-pick-overrun-guard-design.md`

## Problem

The current post-stage speed assembly only changes ElevenLabs `speed`. If the
same text, voice, and model produce three speed variants that still cannot be
assembled into `[video_duration - 1s, video_duration]`, the loop finalizes on
the stage-1 audio or falls through to the best-pick guard. This wastes the
opportunity to rerun the downstream convergence path once with a fresh rewrite.

ElevenLabs also exposes voice-setting sliders beyond speed. Changing only speed
is a weak sampling strategy because `stability` and `similarity_boost` can alter
timing, pauses, and delivery while keeping the text fixed.

## Goal

When a stage-1 audio round reaches the speed assembly phase but all native TTS
speed candidates miss, run one extra fallback convergence round:

1. rewrite text with the existing word-count convergence guard;
2. regenerate TTS script and audio;
3. run the same three native speed/assembly attempts again if the new audio is
   stage-1 converged but not video-capped.

The extra fallback is one round only. It must not create an unbounded retry loop.

## Voice Settings Candidate Policy

Native TTS speed candidates keep the existing adaptive speed selection:

- first candidate uses measured `audio_duration / video_duration`, rounded away
  from `1.0`;
- later candidates move by `0.01` based on measured feedback;
- all speeds remain inside `[0.95, 1.05]`.

Each attempt also carries a conservative voice settings profile:

1. attempt 1: speed only, preserving current behavior;
2. attempt 2: `stability=0.50`, `similarity_boost=0.80`;
3. attempt 3: `stability=0.35`, `similarity_boost=0.72`.

The implementation records the settings in `speedup_candidates` and selected
segment metadata so task detail diagnostics show which variant produced the
final audio.

## Runtime Behavior

The fallback branch only runs after a stage-1 speed assembly miss or generation
failure. It sets metadata on that round:

- `stage1_speedup_fallback_triggered = true`
- `speedup_final_audio_choice = "retry_rewrite"`
- `final_reason = "stage1_speedup_miss_retry_rewrite"` or
  `"stage1_speedup_failed_retry_rewrite"`

Then the loop continues to one additional rewrite/TTS round. If the extra round
also misses speed assembly, it falls back to the existing behavior and keeps the
stage-1 converged audio. If no stage-1 round is reached, best-pick overrun guard
behavior stays unchanged.

## Verification

- Unit tests cover voice settings propagation through `pipeline.tts` and the
  ElevenLabs engine wrapper.
- Duration-loop tests cover: speed candidates carrying varied voice settings;
  a stage-1 miss triggering one fallback rewrite; the fallback round adopting a
  video-capped result; and a second miss stopping without further retries.
