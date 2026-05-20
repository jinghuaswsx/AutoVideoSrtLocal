# Omni ASR Window Audio Alignment Design

Date: 2026-05-20

## Anchors

- `AGENTS.md`: video translation/TTS changes must be document-driven, scoped to an isolated worktree, and verified with focused tests.
- `docs/superpowers/specs/2026-05-13-omni-asr-primary-compact-timeline-design.md`: ASR is the speech content authority; the previous first implementation compacted the first voice clip to `0.0s`.
- `docs/superpowers/specs/2026-05-14-audio-separation-background-preserve-design.md`: `voice_separation` preserves original accompaniment so translated TTS can be mixed with source BGM/background.
- `docs/superpowers/specs/2026-05-14-omni-shot-anchor-silence-design.md`: shot alignment is a post-TTS timing polish and must not create independent extra silence layers.

## Problem

Task `a389551e-c4a6-4047-b7d7-19ffed2dc550` is a representative video:

- Source ASR first speech starts at `13.979s`.
- The source has a music/silent lead-in before speech and a silent tail after speech.
- Current sentence-level Omni output places the first translated TTS sentence at `audio_start_time=0.0s`.
- Final audio metadata shows `48.181s` output, but speech content is compacted into about `24.942s`, leaving the real lead-in lost and a long artificial tail.

This violates the expected rule: when the original video has no ASR speech, the translated track should not add translated speech there. Those windows should remain source music/background or silence.

## Goal

Use ASR speech windows as the translated speech placement authority:

1. If ASR has text in a window, generate and place corresponding translated TTS in that window.
2. If ASR has no text in a window, do not synthesize translated speech for that window.
3. Preserve long lead-in, middle, and tail gaps as background/silence.
4. Continue compacting only small ASR segmentation gaps so normal sentence splitting does not sound stuttered.
5. Keep `voice_separation` and `loudness_match` behavior: source accompaniment remains the background layer behind TTS.

## Non-Goals

- Do not change ASR, translation, voice matching, or ElevenLabs generation logic.
- Do not add LLM calls for timing.
- Do not move video frames or edit source visual timing.
- Do not create TTS units for shot-only/music-only windows.
- Do not change `multi_translate` five-round rewrite timeline behavior.

## Design

Add an ASR-window audio scheduler beside the existing compact scheduler:

```text
apply_asr_window_audio_schedule(sentences, max_gap=0.25, preserve_gap_threshold=1.0)
```

The scheduler keeps the existing sentence order and stores source diagnostics. For each sentence:

- `source_start_time` / `source_end_time` come from ASR-derived sentence timing.
- First sentence:
  - if `source_start_time >= preserve_gap_threshold`, set `audio_start_time = source_start_time`;
  - otherwise use the compact behavior and start at `0.0s`.
- Later sentences:
  - compute `source_gap = source_start_time - previous_source_end_time`;
  - if `source_gap >= preserve_gap_threshold`, preserve the full gap;
  - otherwise use `min(source_gap, max_gap)`.
- `audio_end_time = audio_start_time + tts_duration`.
- Mark preserved large gaps with `asr_window_gap_preserved=true`.
- Mark compacted small/medium gaps with `compact_gap_applied=true`.
- Set `timeline_mode="asr_window_primary"`.

The threshold starts at `1.0s` per user confirmation. This preserves obvious music/silence windows while still cleaning up ASR micro-gaps.

## Integration

Use the ASR-window scheduler in sentence-level AV paths:

- `appcore/tts_strategies/sentence_reconcile.py`
- `appcore/runtime/__init__.py` legacy AV helper path

When rebuilding the final TTS track from segment files, allow callers to pass the source `video_duration`. This pads the generated TTS track with silence through the whole original video duration even when background mixing is unavailable. If no duration is passed, keep the old source-end fallback.

## Expected Result For The Representative Task

For `a389551e-c4a6-4047-b7d7-19ffed2dc550`:

- First translated sentence should start near `13.979s`, not `0.0s`.
- Gaps between normal speech sentences below `1.0s` may still compact to at most `0.25s`.
- The no-ASR tail after the last ASR end remains silent/background through the `48.181s` source video end.
- Final output still mixes TTS with accompaniment when separation succeeds.

## Verification

Focused tests must prove:

1. A long initial ASR gap is preserved.
2. A long middle ASR gap is preserved.
3. A short ASR split gap is compacted to `max_gap`.
4. The final rebuilt TTS full audio can be padded to the full source video duration.
5. Existing compact scheduler behavior remains available for callers/tests that explicitly use it.
