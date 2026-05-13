# Omni Sentence-Level Alignment Guard Design

- Created: 2026-05-13
- Module: omni-translate `av_sentence` / `sentence_reconcile` / `sentence_units`
- Anchors:
  - `docs/superpowers/specs/2026-04-28-av-sync-v2-sentence-convergence-design.md`
  - `docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md`
  - `docs/superpowers/specs/2026-05-13-multilingual-subtitle-safe-splitting-design.md`

## Problem

The current sentence-level Omni path can produce translations that are fluent
but too compressed. A source sentence with a roughly 3 second speaking window
can become a 1-2 second target sentence because the first translation omits
source anchors such as product parts, scene details, or actions. Once that
happens, TTS duration reconciliation and subtitle splitting are operating on a
semantically incomplete sentence.

Quality assessment catches this late, after subtitle generation, with issues
such as omitted product terms and omitted scene context. That is too late for
the production path.

## Priority Model

The sentence model is:

1. Source ASR sentence and cleaned source copy are the primary timeline and
   semantic authority.
2. One source sentence maps to one target-language sentence. Do not merge,
   split, reorder, or skip source sentences.
3. Target speech duration should match the source ASR sentence window as
   closely as possible.
4. Shot notes and lens boundaries are calibration context only. A lens may
   contain multiple subtitle display blocks, and a lens cut must not override
   source sentence boundaries.
5. Subtitle display may split one target sentence into multiple adjacent
   on-screen blocks inside that sentence's time window.

## Goals

- Make the translation prompt explicitly preserve sentence-level source
  anchors before optimizing for brevity.
- Include non-optional `must_keep_terms` in each sentence input.
- Require the model to report whether it covered or omitted those source
  anchors.
- Add an engineering gate before accepting a sentence for final output: semantic
  coverage problems must trigger a sentence-level repair rewrite before normal
  duration convergence can accept the sentence.
- Keep TTS speed within the existing `[0.95, 1.05]` range.
- Apply safe subtitle splitting to `sentence_units` output so a single sentence
  can display as multiple readable subtitle blocks without changing the
  sentence mapping.

## Non-Goals

- Do not introduce a new database table or route.
- Do not change stable `multi_translate` behavior.
- Do not move or retime original video clips.
- Do not let visual shot decomposition become the primary segmentation unit.
- Do not use local MySQL for verification.

## Translation Prompt Contract

Each sentence input includes:

- `asr_index`
- `source_text`
- `original_source_text`
- `target_duration`
- `target_chars_range`
- `must_keep_terms`
- `shot_context`
- `role_in_structure`

Prompt rules:

- `must_keep_terms` are source anchors, not optional keywords.
- The model may translate or naturally express an anchor, but it must not
  silently drop it.
- If a sentence is too short, expand by restoring source anchors, actions, and
  scene context before adding filler.
- If a sentence is too long, compress fillers and weak modifiers before removing
  source anchors.
- When semantic coverage and timing conflict, preserve source meaning and mark
  `duration_risk`.

The response schema adds per sentence:

- `covered_source_terms: string[]`
- `omitted_source_terms: string[]`
- `coverage_ok: boolean`

## Engineering Gate

After first translation, and again during sentence-level rewrite attempts, a
sentence is considered semantically unsafe when:

- `coverage_ok` is explicitly `false`;
- or `omitted_source_terms` is non-empty.

`sentence_reconcile` must treat this as `needs_semantic_repair`, even if the
current TTS duration already sits inside `[0.95, 1.05]`.

Repair rewrite behavior:

- Rewrite only the current sentence.
- Restore omitted anchors and source actions.
- Keep one target sentence and the same `asr_index`.
- Use the current `target_chars_range`, expanded proportionally when the current
  TTS is too short.
- Regenerate only that sentence's TTS and remeasure duration.
- Continue normal duration convergence only after the repair response reports
  semantic coverage as OK.

If all attempts fail, keep the best candidate but mark the sentence
`warning_semantic` when coverage remains unsafe, rather than reporting it as
`ok`.

## Subtitle Behavior

`sentence_units` keeps source sentence mapping, but the generated subtitle chunks
must pass through the multilingual safe splitter before SRT generation.

This means:

- one source sentence can produce multiple adjacent subtitle display chunks;
- chunk timings stay inside the sentence's source-time window;
- every chunk must respect language line width, max lines, and CPS limits where
  possible;
- no words may be dropped by SRT wrapping.

## Verification

Tests must cover:

- prompt messages include `must_keep_terms` and coverage instructions;
- response schema requires `covered_source_terms`, `omitted_source_terms`, and
  `coverage_ok`;
- merged sentence data persists coverage metadata;
- `sentence_reconcile` repairs a semantically unsafe sentence before accepting
  an otherwise in-range TTS duration;
- repair rewrite prompts include omitted source terms and do not frame the task
  as pure shortening;
- `sentence_units` applies safe subtitle splitting for long target sentences.
