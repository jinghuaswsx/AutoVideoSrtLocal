# Omni ASR Primary Compact Timeline Design

- Date: 2026-05-13
- Module: Omni translate `shot_decompose + shot_char_limit + sentence_reconcile`
- Anchors:
  - `AGENTS.md` hard rule: document-driven code and Omni/TTS topic pointers.
  - `docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md`: Omni `plugin_config`, `shot_decompose`, `shot_char_limit`, `sentence_reconcile`, `sentence_units`.
  - `docs/superpowers/specs/2026-05-13-tts-deferred-adaptive-speedup-design.md`: TTS convergence first, native speed only inside `[0.95, 1.05]`.
  - `docs/superpowers/specs/2026-05-13-tts-segment-candidate-assembly-design.md`: segment-level final audio selection and diagnostics.

## Problem

Omni tasks that combine `shot_decompose`, `shot_char_limit`, `sentence_reconcile`, and `sentence_units` currently let visual shot boundaries become the primary speech timeline. This can create impossible ad pacing.

Observed task `d8aba350-231a-45f4-909a-fb4ed77b6d75`:

- Source ASR has continuous speech through the early hook.
- Gemini returned shot 2 as `3.0s-6.0s`.
- Existing overlap matching assigned the first ASR sentence only to shot 1 and left shot 2 `silent=true`.
- `shot_char_limit` skipped silent shots, so no target TTS segment was generated for `3.0s-6.0s`.
- Sentence TTS then used shot start times, creating about `3.07s` of silence after the first `2.926s` voice clip.

For short-form ad creative, a 3-second pause in the opening hook is invalid. Normal inter-voice gaps should be compact, generally no more than `0.2s-0.3s`, unless the source ASR itself has a deliberate long pause and the task explicitly preserves source timing.

## Principles

1. ASR is the primary source of speech content and speech timeline.
2. Shot language is auxiliary context for understanding visuals, product actions, and translation wording.
3. Shot boundaries may influence char budgets and prompts, but must not create silent speech windows when ASR content crosses or overlaps those windows.
4. The default ad output timeline is compact. It should collapse accidental source/shot gaps to a maximum compact gap.
5. Segment duration convergence is automated: rewrite text, regenerate ElevenLabs audio, then apply speed only within `[0.95, 1.05]`.
6. A short or long segment may finish with a warning only after allowed automated convergence attempts have been exhausted; it must not be accepted simply by leaving empty timeline space.

## Target Behavior

For `translate_algo=shot_char_limit` with `tts_strategy=sentence_reconcile`:

- Build TTS units from ASR speech units, not from raw shot rows.
- Attach overlapping shot descriptions to each ASR unit as `shot_context`.
- Use an ASR unit's source text as the text to translate.
- Use the ASR unit's source timing as the initial speech target window.
- Compute target character ranges from the target speech duration, optionally adjusted by overlapping shot context.
- Never create a TTS target unit for a shot that has no ASR text.
- Never skip ASR text merely because it overlaps a shot less than another shot.

For final AV audio:

- Build an `audio_start_time` schedule that is compact and monotonic.
- The first voice clip starts at `0.0s` unless explicit lead-in preservation is configured later.
- Between adjacent TTS clips, use `min(source_gap, max_compact_gap)`.
- `max_compact_gap = 0.25s` for the first implementation.
- `audio_end_time = audio_start_time + tts_duration`.
- Source `start_time` / `end_time` remain stored for diagnostics and subtitles, but audio stitching uses `audio_start_time` when present.

For subtitles:

- In compact audio mode, subtitle units should follow `audio_start_time` / `audio_end_time` so text appears with the actual voice.
- The original ASR source window remains visible in diagnostics.

For translate preview:

- The "翻译本土化" process table should be keyed by ASR speech units, not raw shot rows.
- Each preview row must show the ASR unit source text as "原文".
- Overlapping shot descriptions should appear as visual context on that ASR row.
- A shot with no ASR source text must not appear as a standalone translation row; it may only appear inside `shot_context` or diagnostics.
- The summary total should count ASR translation units, not visual shots.

## Shot/ASR Matching

`align_asr_to_shots` should keep the existing per-shot diagnostic fields, but it must expose overlap evidence rather than pretending non-primary-overlap shots are silent:

- `asr_segments`: primary segments assigned by max overlap, for backward compatibility.
- `overlapping_asr_segments`: every ASR segment with positive overlap.
- `source_text`: primary assigned text, unchanged for existing UI.
- `overlap_source_text`: text from all overlapping ASR segments.
- `silent`: true only when there is no assigned or overlapping ASR text.

This makes shot 2 in the observed task non-silent for diagnostics, while the TTS unit builder still uses ASR as the speech unit source.

## TTS Convergence

`sentence_reconcile` currently disables text rewrite when `translate_algo=shot_char_limit`. That safety rule prevents bad rewrites, but it also allows short segments to be accepted as `warning_short` and leaves timeline gaps.

Replace the blanket disable with constrained rewrites:

- Keep rewrite enabled for `shot_char_limit`.
- Pass original ASR text, target language, product context, and shot context to the rewrite prompt.
- For `needs_expand`, expand only with meaning already present in ASR or visible shot context.
- For `needs_rewrite`, compress without dropping required product facts or CTA.
- Regenerate the segment with ElevenLabs after each rewrite attempt.
- If the new audio is within `0.95-1.05` of target duration, optionally run one speed adjustment inside `[0.95, 1.05]`.
- If all attempts miss, keep the closest candidate and mark warning metadata.

## Diagnostics

Each final sentence should include:

- `source_start_time` / `source_end_time`
- `audio_start_time` / `audio_end_time`
- `source_gap_before` / `audio_gap_before`
- `compact_gap_applied`
- `shot_context`
- `timeline_mode = "compact_asr_primary"`

Task-level AV state should include:

```json
{
  "audio_timeline_mode": "compact_asr_primary",
  "max_compact_gap": 0.25
}
```

## 2026-05-20 Translation Debug Display

Omni `shot_char_limit` / ASR-primary translation must keep the first-pass localization result debuggable on the detail page. The `translate` step preview is required to show:

- A full-text source/target comparison, labeled as `第一轮全文翻译对照`.
- A sentence-level source/target comparison, labeled as `第一轮逐句翻译对照`, keyed by ASR translation units.
- Each sentence row must include the source sentence, localized target sentence, timing, and visual shot context when available.
- Existing completed tasks whose stored `artifacts.translate` predate this display contract must be augmented from `task.translations`, `task.localized_translation`, `task.script_segments`, and `task.utterances` in the frontend without rerunning the pipeline.
- The old target-only `sentences` preview is not sufficient for debugging; it may remain as a fallback only when no source text exists.

## Verification

Unit tests must cover:

1. A shot with no primary ASR assignment but positive ASR overlap is not marked silent.
2. `shot_char_limit + sentence_reconcile` builds AV sentences from ASR units, preserving ASR content even when shots split inside a sentence.
3. Compact audio scheduling caps a `3.0s` source gap to `0.25s`.
4. `build_source_timeline_audio` uses `audio_start_time` when present.
5. Subtitle units follow `audio_start_time` in compact mode.
6. `shot_char_limit` no longer disables rewrite in `sentence_reconcile`.
7. Alignment preview artifacts keep the `scene_cuts` item before the segment list so the shared workbench renderer can show detected cut points and confirmed script segments together.
8. The translate preview exposes full-text and sentence-level source/target comparisons for ASR-primary Omni tasks, including legacy task artifacts that only stored target sentences.

Regression evidence for task `d8aba350-231a-45f4-909a-fb4ed77b6d75`:

- No generated TTS gap may exceed `0.3s`.
- No `3.0s-6.0s` opening silence may be present.
- ASR text remains the content authority; shot descriptions are only auxiliary prompt context.
