# TTS Aggressive Speed Sampling Design

- Date: 2026-05-13
- Module: multi-translate TTS duration loop
- Anchor: extends `2026-05-13-tts-deferred-adaptive-speedup-design.md`,
  `2026-05-13-tts-segment-candidate-assembly-design.md`, and
  `2026-05-13-tts-speed-voice-settings-fallback-design.md`
- Incident task: `/multi-translate/8540282d-5795-4dde-90f0-a488a35cf392`

## Problem

Native ElevenLabs `voice_settings.speed` is not deterministic playback tempo.
It regenerates a new TTS delivery. Higher speed values usually bias toward
shorter speech, but measured duration can still move non-monotonically because
pauses, emphasis, and model sampling change.

The incident task shows that weakness:

- Video duration: about `26.8s`
- Stage-1 round audio: about `30.5s`
- Existing speed candidates still measured about `30.17s`, `30.25s`, and
  `29.57s`
- Segment assembly could only adopt the closest over-video fallback at about
  `28.60s`

The current candidate policy tries diverse speeds inside `[0.95, 1.05]`. For
over-video audio, testing weaker speeds after the strongest candidate wastes
candidate slots. The system should spend those slots on stronger shortening
samples before falling back to rewrite.

## Goal

For over-video stage-1 and best-pick-overrun audio, make the speed phase more
useful without allowing obvious quality damage:

1. widen the provider speed safety range from `[0.95, 1.05]` to `[0.94, 1.06]`;
2. when the source audio is over the video cap, prefer repeated strongest-speed
   samples before weaker speeds;
3. keep all generated segment files in the existing per-segment candidate pool;
4. keep the same final adoption target: assembled audio hits `[video - 1s,
   video]`, or closest-over fallback is shorter than the original and inside
   `[video - 1s, video + 2s]`;
5. preserve the one-extra-rewrite fallback limit.

## Candidate Policy

The runtime distinguishes over-video and under-floor cases.

### Over-Video Audio

When `base_duration > video_duration`, generate up to three samples using the
strongest safe shortening speed first:

1. `speed=1.06`, speed-only voice settings;
2. `speed=1.06`, balanced voice settings;
3. `speed=1.06`, duration-variation voice settings.

These are intentionally three independent samples at the same speed. They must
not be deduplicated by speed value. Each attempt uses a unique variant path so
the generated segment files are separate candidates.

If future data shows repeated `1.06` samples do not help for a voice or model,
the fallback order may be adjusted to `1.06`, `1.05`, `1.04`, but this spec
starts with repeated `1.06` because the current failure mode is persistent
overrun.

### Under-Floor Audio

When `base_duration < video_duration - 1s`, preserve adaptive slow-down behavior
inside the widened range. The first candidate rounds away from `1.0`, clamps to
`[0.94, 1.06]`, and subsequent attempts step by `0.01` based on measured
feedback.

### Already Video-Capped Audio

When `video_duration - 1s <= base_duration <= video_duration`, no native speed
candidate is needed. The normal round is already video-capped.

## Metadata

Every speed candidate should remain visible in task diagnostics:

- `speedup_candidates[*].speed`
- `speedup_candidates[*].attempt`
- `speedup_candidates[*].sample_index`
- `speedup_candidates[*].voice_settings`
- `speedup_candidates[*].duration`
- selected segment rows keep `speedup_attempt` and `voice_settings_profile`

The detail UI should render repeated equal-speed candidates as distinct
attempts. It should not collapse them into one row just because `speed` matches.

## Runtime Constraints

- Never pass speed outside `[0.94, 1.06]` to the TTS provider.
- Keep maximum native speed samples per speed phase at three.
- Keep the existing segment assembly optimizer and adoption rules unchanged.
- Keep the existing one-extra-rewrite fallback after a stage-1 speed assembly
  miss.
- Do not add LLM speedup evaluation sidecars.

## Verification

Add focused tests before production code changes:

1. `_adaptive_speed_candidate` or its successor allows the widened
   `[0.94, 1.06]` bounds.
2. Over-video audio returns three distinct candidate attempts at `1.06` instead
   of deduplicating equal speed values.
3. Each repeated `1.06` attempt writes a unique variant path and contributes
   distinct per-segment candidates to assembly.
4. Segment assembly can adopt a shorter result from the second or third `1.06`
   sample.
5. Under-floor audio still uses slow-down feedback and never leaves
   `[0.94, 1.06]`.
6. Existing closest-over fallback and one-extra-rewrite behavior remain green.
