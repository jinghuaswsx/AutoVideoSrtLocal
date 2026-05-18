# TTS Preview Rate Prior Design

- Date: 2026-05-18
- Module: TTS duration convergence
- Anchor: extends `2026-05-18-english-redub-speed-aware-voice-match-design.md`,
  `2026-05-13-tts-deferred-adaptive-speedup-design.md`, and
  `2026-05-13-tts-segment-candidate-assembly-design.md`

## Goal

Use cached voice preview speech-rate metadata as a cold-start prior for the
first TTS generation only when no measured TTS speech rate exists for the
selected voice and language. Once real TTS audio has been generated and measured,
the real `voice_speech_rate` value remains the authoritative rate for later
character-range calculation and convergence guidance.

## Data Precedence

Character-per-second rate lookup must follow this order:

1. `voice_speech_rate` measured from actual generated TTS audio.
2. `voice_preview_speech_rate.chars_per_second` for the selected voice,
   language, and the current preview URL hash.
3. Existing language fallback constants.

Preview-derived values must not be written into `voice_speech_rate`. That table
only represents measured TTS output. Preview ASR remains a prior stored in
`voice_preview_speech_rate`.

## Runtime Behavior

- `av_translate.compute_target_chars_range` uses the effective rate lookup above.
- Omni `shot_char_limit` uses the same effective rate when building first-pass
  shot character limits and sentence target ranges.
- English redub `original` mode uses the same effective rate to build initial
  sentence `target_chars_range`, while still preserving the original English
  text as the first TTS input.
- Multi-language video translation uses the same effective rate for cold-start
  Japanese character budgets before the first TTS audio exists.
- The shared multi-language TTS duration loop records the effective rate source
  used for round 1, estimates the first generated audio duration from visible
  characters, and writes the measured round-1 TTS sample to `voice_speech_rate`
  after the audio duration is known.
- Sentence-reconcile TTS records real generated TTS samples back to
  `voice_speech_rate` after audio durations are measured. Future jobs for the
  same voice and language therefore prefer actual TTS speed over preview prior.

## Diagnostics

Task workbench duration logs must expose source metadata for the first TTS
generation so operators can see whether the run used measured TTS speed, preview
ASR prior, or a fallback constant:

- `actual_tts` when the value comes from `voice_speech_rate`.
- `preview_prior` when the value comes from `voice_preview_speech_rate`.
- `fallback` when neither measured source exists.
- The UI shows the baseline cps, estimated first-round audio duration, measured
  first-round duration, percentage delta, and whether later guidance has switched
  to measured TTS speed.

## Verification

- Unit tests prove actual TTS speed takes precedence over preview prior.
- Unit tests prove preview prior is used when actual TTS speed is missing.
- Unit tests prove character-range builders use the effective rate.
- Sentence-reconcile tests prove measured TTS output updates
  `voice_speech_rate`.
- Multi-language TTS loop tests prove round-1 preview priors are surfaced in
  `tts_duration_rounds` and measured samples are recorded.
- Template tests prove the task workbench renders speech-rate diagnostics.
