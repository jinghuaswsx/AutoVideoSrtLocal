# Omni Speech-Shot Alignment Design

- Date: 2026-05-14
- Module: Omni translate post-TTS, pre-compose speech/shot alignment
- Status: revised design draft

## Anchors

- `AGENTS.md`: document-driven code, Omni/TTS topic pointers, and post-change verification rules.
- `docs/superpowers/specs/2026-05-13-omni-asr-primary-compact-timeline-design.md`: ASR remains the speech authority; shot boundaries are visual context and the final audio timeline is compact by default.
- `docs/superpowers/specs/2026-05-13-tts-segment-candidate-assembly-design.md`: final audio selection is segment-based and must preserve text identity.
- `docs/superpowers/specs/2026-05-13-tts-deferred-adaptive-speedup-design.md`: final timing optimization runs after TTS convergence, not as an early shortcut.
- `docs/superpowers/specs/2026-05-14-omni-final-fallback-compose-summary-design.md`: final audio processing must be explicit in task metadata and UI diagnostics.
- `docs/superpowers/specs/2026-05-13-omni-tts-process-visualization-design.md`: the detail page can expose detailed TTS/post-TTS diagnostics without changing the core convergence algorithm.
- `docs/superpowers/specs/2026-05-07-omni-dynamic-resume-and-prompt-display-fix.md`: restart/resume must clear stale downstream state and keep detail cards consistent with the real task state.

## Problem

The current Omni sentence-level path builds a compact ASR-primary audio
timeline: adjacent TTS clips keep only a small source-derived gap, capped at
`0.25s`. This prevents accidental long pauses, but it treats every short gap
equally.

For short-form video ads, a tiny pause is often most natural when it lands on a
visual cut. When a sentence boundary is already close to a shot boundary, adding
`0.2s-0.3s` of silence before the next sentence can make the voice transition
feel intentionally timed to the picture. Adding the same silence blindly can
also hurt the hook, drag the pacing, or push final audio beyond the video.

The important constraint is that the existing compact gap is already silence.
The shot-aware logic must not append a second independent silence segment after
the compact scheduler. It must resolve one final inter-sentence gap from both
the original compact rule and the shot-anchor rule.

## Goal

Add a deterministic speech-shot alignment step after sentence TTS convergence
and before video composition. This step evaluates whether the already generated
voice timeline has safe optimization room, then chooses the final
inter-sentence gaps in one pass.

The TTS convergence loop remains unchanged. Translation, text rewrite, segment
candidate assembly, ElevenLabs speed selection, and semantic checks all finish
before this alignment step starts.

The target is not "hit every shot cut". The target is:

> Keep the ASR-primary speech timeline compact, then opportunistically snap
> natural sentence boundaries toward nearby shot cuts when the required silence
> is small and the total timing budget allows it.

The user-facing target is a new detail-page card named `语音镜头对齐`. The card
must show what was analyzed, what changed, and why candidates were skipped.

## Non-Goals

- Do not split a TTS sentence internally.
- Do not create standalone TTS units for silent shots.
- Do not make shot boundaries the primary speech timeline.
- Do not add silence when final audio would exceed the target video duration.
- Do not add silence before the first voice clip in the first implementation.
- Do not change translation, text rewrite, or ElevenLabs speed selection.
- Do not call an LLM for the first implementation. This is a deterministic
  timing problem and must be reproducible in tests.
- Do not layer a new silence segment on top of an already scheduled compact
  gap. There is only one final `audio_gap_before` per sentence boundary.
- Do not preserve stale speech-shot alignment diagnostics after a force restart.

## Inputs

The alignment step consumes:

- final sentence rows after `reconcile_duration`;
- existing `audio_start_time`, `audio_end_time`, `audio_gap_before`, `tts_duration`;
- source/video duration from task metadata or source timeline;
- shot boundaries from `task.shots` and/or `task.scene_cuts`;
- `max_compact_gap`, currently `0.25s`.

Shot boundaries are normalized to a sorted list of cut times:

- From `shots`: use every shot `start` except `0.0`, plus optional shot `end`
  values that are inside the video duration.
- From `scene_cuts`: include numeric cut times if present.
- Deduplicate within `50ms`.
- Ignore cuts outside `(0.2s, video_duration - 0.2s)`.

## Gap Semantics

For each boundary before sentence `i`, where `i > 0`, the final gap is a single
number:

```text
final_gap_before_i = one resolved gap from compact timing and shot-anchor timing
```

It is not:

```text
compact_gap_before_i + extra_independent_shot_silence
```

The implementation should keep these values separate for diagnostics:

- `base_compact_gap`: what `apply_compact_audio_schedule` would have used.
- `anchor_target_gap`: the gap that would place sentence `i` on a nearby shot cut.
- `anchor_extra_silence`: `max(0, final_gap - base_compact_gap)`.
- `final_gap`: the only gap actually used in `audio_gap_before`.

Example:

- previous speech ends at `10.00s`;
- compact rule gives `base_compact_gap = 0.20s`;
- next sentence would start at `10.20s`;
- shot cut is at `10.28s`;
- final gap becomes `0.28s`;
- anchor extra is only `0.08s`, not `0.20s + 0.28s`.

If the compact rule already gives `0.25s`, the shot rule has at most `0.05s`
of additional room when the hard total gap cap is `0.30s`.

## Rules

1. Only sentence boundaries are eligible:
   - Candidate boundary is before sentence `i`, where `i > 0`.
   - The previous sentence's `audio_end_time` and the next sentence's current
     `audio_start_time` define the available inter-sentence gap.

2. Shot cuts are soft anchors:
   - A cut is relevant when it falls after the previous sentence's audio end
     and before or near the next sentence's current start.
   - A boundary can be shifted only by increasing silence before the next
     sentence. The optimizer never pulls speech earlier.

3. Per-boundary final gap cap:
   - Preferred maximum final gap: `0.25s`.
   - Hard maximum final gap: `0.30s`.
   - `base_compact_gap + anchor_extra_silence` must not exceed the hard cap.
   - This is the main guard against stacked `0.5s-1.0s` pauses.

4. Global silence budget:
   - Total `anchor_extra_silence` must not exceed the smaller of `1.5s` and
     `5%` of video duration.
   - For videos shorter than `20s`, cap total `anchor_extra_silence` at `1.0s`.
   - Existing compact gaps do not count against this extra budget because they
     were already part of the current pipeline.

5. Hook protection:
   - During the first `3.0s`, add silence only when `anchor_extra_silence` is
     `<= 0.12s`.
   - If the next sentence starts inside the first `3.0s` and speech is already
     dense, prefer no added silence.

6. No overrun:
   - If shifting sentence `i` and all following sentences would make final
     audio content exceed `video_duration`, skip that candidate.
   - If final content already exceeds video duration, the optimizer is disabled.

7. Prefer fewer, higher-value snaps:
   - Rank candidates by smaller anchor extra silence, closer cut alignment,
     and whether the boundary is already a natural source/ASR pause.
   - Do not spend budget on weak anchors when later stronger anchors exist.

8. Diagnostics are mandatory:
   - Every analyzed boundary records the base gap, final gap, candidate cut
     time if any, decision, and reason.
   - Every applied shift records cut time, sentence index, extra silence,
     before/after start time, and reason.
   - Skipped candidates should be summarized by reason counts, not logged as
     unbounded per-cut noise.

## Algorithm

The first implementation can be deterministic and greedy:

1. Build a baseline compact sentence schedule with `apply_compact_audio_schedule`.
2. Build normalized shot cut anchors.
3. For each sentence boundary `i > 0`, compute:
   - `prev_end = sentence[i - 1].audio_end_time`
   - `current_start = sentence[i].audio_start_time`
   - `base_compact_gap = sentence[i].audio_gap_before`
   - candidate cuts in `(current_start, prev_end + hard_final_gap_cap]`
   - `anchor_target_gap = cut - prev_end`
   - `anchor_extra_silence = anchor_target_gap - base_compact_gap`
4. Keep only candidates with:
   - `anchor_extra_silence > 0`;
   - `anchor_target_gap <= hard_final_gap_cap`;
   - `anchor_extra_silence` inside the remaining global extra budget.
5. Reject candidates that violate hook protection, global budget, or final
   video duration.
6. Sort candidates by:
   - anchor extra silence ascending;
   - absolute post-snap distance to the cut ascending;
   - later hook-safe boundaries before early-hook boundaries;
   - sentence index ascending for deterministic output.
7. Apply accepted shifts by replacing sentence `i`'s `audio_gap_before` with
   `anchor_target_gap`, then moving sentence `i` plus all following sentence
   audio times forward by `anchor_extra_silence`.
8. Stop when no candidate remains or the global budget is exhausted.

This greedy path is sufficient because the added-silence budget is tiny and the
optimizer is a quality polish, not the primary duration solver. If later data
shows competing anchors are common, the candidate selector can become dynamic
programming without changing the external data shape.

Implementation should live either inside a new wrapper around
`apply_compact_audio_schedule` or immediately after it, but it must update the
same sentence timing fields before `_build_av_tts_segments` and
`_rebuild_tts_full_audio_from_segments` run. The final audio builder should see
only one resolved `audio_gap_before` per sentence.

## Metadata

Each shifted sentence should preserve the existing compact fields and add:

```json
{
  "base_compact_gap": 0.2,
  "shot_anchor_final_gap": 0.28,
  "shot_anchor_extra_silence": 0.08,
  "shot_anchor_cut_time": 12.48,
  "shot_anchor_before_start": 12.26,
  "shot_anchor_after_start": 12.48,
  "shot_anchor_reason": "nearby_cut_soft_snap"
}
```

Task-level `final_compose_summary` should add:

```json
{
  "speech_shot_alignment_enabled": true,
  "speech_shot_alignment_applied": true,
  "speech_shot_alignment_status": "optimized",
  "shot_anchor_extra_silence_total": 0.24,
  "shot_anchor_aligned_boundary_count": 3,
  "shot_anchor_cut_count": 18,
  "shot_anchor_extra_silence_budget": 1.5,
  "speech_shot_alignment_analyzed_boundaries": 9,
  "speech_shot_alignment_decisions": [
    {
      "sentence_index": 4,
      "asr_index": 4,
      "decision": "applied",
      "reason": "nearby_cut_soft_snap",
      "cut_time": 12.48,
      "base_compact_gap": 0.2,
      "final_gap": 0.28,
      "extra_silence": 0.08
    },
    {
      "sentence_index": 5,
      "asr_index": 5,
      "decision": "skipped",
      "reason": "would_exceed_final_gap_cap",
      "cut_time": 16.9,
      "base_compact_gap": 0.25,
      "required_final_gap": 0.54
    }
  ],
  "shot_anchor_skip_reasons": {
    "over_budget": 2,
    "hook_protection": 1,
    "would_overrun_video": 0,
    "would_exceed_final_gap_cap": 4,
    "too_far_from_cut": 10
  }
}
```

The existing `silence_gap_duration` remains total inter-sentence silence after
all scheduling. A separate `shot_anchor_extra_silence_total` explains how much
additional silence was introduced beyond the existing compact schedule.

## UI Behavior

Add a standalone `语音镜头对齐` card after the TTS card and before subtitle/video
composition cards in the Omni detail workbench.

The card states:

- Whether the step ran, was skipped, or had no optimization opportunity.
- That no LLM was used and the decision is deterministic.
- Number of sentence boundaries analyzed.
- Number of shot cuts available.
- Number of boundaries optimized.
- Total extra silence introduced beyond the compact schedule.
- Final maximum gap observed after alignment.
- A compact decision table:
  - sentence/asr index;
  - nearby cut time;
  - base compact gap;
  - final gap;
  - extra silence;
  - decision;
  - reason.

The card must show "why not" cases, including:

- no shot anchors;
- no nearby sentence boundary;
- candidate too far from cut;
- would exceed per-boundary final gap cap;
- would exceed global extra-silence budget;
- hook protection;
- would overrun video;
- final speech already exceeds video duration.

The final processing summary can include a one-line cross-reference:
`语音镜头对齐：优化 3 个断点，额外静音 0.24s。`

## Rollout

Start enabled only for Omni sentence-level path with:

- `shot_decompose = true`;
- `tts_strategy = sentence_reconcile`;
- compact ASR-primary audio mode;
- non-empty shot cut anchors.

If any prerequisite is missing, skip the optimizer and keep current behavior.
The card should still render a skipped/no-op state for debug visibility.

Later this can become a preset flag, but the first implementation should avoid
adding new UI configuration unless production review shows per-task control is
needed.

## Restart and Resume Semantics

The `强制重新开始` button calls `/restart` and must clear every speech-shot
alignment output from the previous run before the new pipeline starts.

Restart clearing must remove:

- top-level `speech_shot_alignment`;
- `final_compose_summary.speech_shot_alignment_*`;
- `final_compose_summary.shot_anchor_*`;
- per-sentence fields such as `base_compact_gap`, `shot_anchor_final_gap`,
  `shot_anchor_extra_silence`, `shot_anchor_cut_time`,
  `shot_anchor_before_start`, `shot_anchor_after_start`,
  `shot_anchor_reason`;
- the same fields inside `variants.av.final_compose_summary`,
  `variants.av.sentences`, and `variants.av.av_debug.final_compose_summary`;
- any preview/artifact entry used only by the `语音镜头对齐` card.

After restart, the card should render the normal pending/skipped-empty state
from the refreshed task, not the previous run's table. Resume from `tts` or a
later step should recompute alignment from the current TTS sentences before
subtitle/compose. Resume from an earlier step naturally clears downstream state
through the existing restart/resume cleanup.

## Verification

Unit tests:

1. Adds `0.2s` silence to snap a sentence start to a nearby shot cut.
2. Resolves one final gap rather than producing two independent gap layers.
3. Does not add silence when the resulting final gap would exceed `0.30s`.
4. Does not add silence when final content would exceed video duration.
5. Applies hook protection in the first `3.0s`.
6. Respects the global extra-silence budget.
7. Keeps sentence order monotonic and shifts all following sentence times.
8. Writes task-level and sentence-level diagnostics, including skipped reasons.
9. Does not call an LLM.

Runtime tests:

- `sentence_reconcile` final TTS path runs speech-shot alignment only after
  TTS convergence output exists and before final audio is rebuilt for compose.
- `final_compose_summary` includes speech-shot alignment metrics without hiding
  existing truncation, best-effort, or semantic warning metrics.
- `restart_task` clears top-level, variant-level, sentence-level, and debug
  speech-shot alignment fields so the detail card cannot show stale data after
  `强制重新开始`.
- The detail shell renders the `语音镜头对齐` card for applied, no-op, and
  skipped states.

Manual acceptance:

- On a representative Omni task, no generated gap exceeds the configured hard
  final gap cap.
- Opening hook has no noticeable artificial pause.
- Final audio/video duration remains within the existing compose contract.
- The card explains both applied optimizations and skipped candidates.
