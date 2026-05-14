# Omni Shot Anchor Silence Design

- Date: 2026-05-14
- Module: Omni translate `sentence_reconcile` final audio scheduling
- Status: design draft

## Anchors

- `AGENTS.md`: document-driven code, Omni/TTS topic pointers, and post-change verification rules.
- `docs/superpowers/specs/2026-05-13-omni-asr-primary-compact-timeline-design.md`: ASR remains the speech authority; shot boundaries are visual context and the final audio timeline is compact by default.
- `docs/superpowers/specs/2026-05-13-tts-segment-candidate-assembly-design.md`: final audio selection is segment-based and must preserve text identity.
- `docs/superpowers/specs/2026-05-13-tts-deferred-adaptive-speedup-design.md`: final timing optimization runs after TTS convergence, not as an early shortcut.
- `docs/superpowers/specs/2026-05-14-omni-final-fallback-compose-summary-design.md`: final audio processing must be explicit in task metadata and UI diagnostics.

## Problem

The current Omni sentence-level path builds a compact ASR-primary audio timeline:
adjacent TTS clips keep only a small source-derived gap, capped at `0.25s`.
This prevents accidental long pauses, but it treats every short gap equally.

For short-form video ads, a tiny pause is often most natural when it lands on a
visual cut. When a sentence boundary is already close to a shot boundary, adding
`0.2s-0.3s` of silence before the next sentence can make the voice transition
feel intentionally timed to the picture. Adding the same silence blindly can
also hurt the hook, drag the pacing, or push final audio beyond the video.

## Goal

Add a conservative shot-anchor silence optimizer after sentence TTS convergence
and before final audio rebuilding. The optimizer may add small inter-sentence
silence only when it improves alignment to a nearby shot cut without breaking
the compact timeline contract.

The target is not "hit every shot cut". The target is:

> Keep the ASR-primary speech timeline compact, then opportunistically snap
> natural sentence boundaries toward nearby shot cuts when the required silence
> is small and the total timing budget allows it.

## Non-Goals

- Do not split a TTS sentence internally.
- Do not create standalone TTS units for silent shots.
- Do not make shot boundaries the primary speech timeline.
- Do not add silence when final audio would exceed the target video duration.
- Do not add silence before the first voice clip in the first implementation.
- Do not change translation, text rewrite, or ElevenLabs speed selection.

## Inputs

The optimizer consumes:

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

3. Per-boundary silence cap:
   - Preferred maximum added silence: `0.25s`.
   - Hard maximum added silence: `0.30s`.
   - The implementation should default to `0.25s`; `0.30s` is only for small
     rounding differences or a later configurable preset.

4. Global silence budget:
   - Total optimizer-added silence must not exceed the smaller of `1.5s` and
     `5%` of video duration.
   - For videos shorter than `20s`, cap total added silence at `1.0s`.

5. Hook protection:
   - During the first `3.0s`, add silence only when the required addition is
     `<= 0.12s`.
   - If the next sentence starts inside the first `3.0s` and speech is already
     dense, prefer no added silence.

6. No overrun:
   - If shifting sentence `i` and all following sentences would make final
     audio content exceed `video_duration`, skip that candidate.
   - If final content already exceeds video duration, the optimizer is disabled.

7. Prefer fewer, higher-value snaps:
   - Rank candidates by smaller required added silence, closer cut alignment,
     and whether the boundary is already a natural source/ASR pause.
   - Do not spend budget on weak anchors when later stronger anchors exist.

8. Diagnostics are mandatory:
   - Every applied shift records cut time, sentence index, added silence,
     before/after start time, and reason.
   - Skipped candidates should be summarized by reason counts, not logged as
     unbounded per-cut noise.

## Algorithm

The first implementation can be deterministic and greedy:

1. Build the current compact sentence schedule with `apply_compact_audio_schedule`.
2. Build normalized shot cut anchors.
3. For each sentence boundary `i > 0`, compute:
   - `prev_end = sentence[i - 1].audio_end_time`
   - `current_start = sentence[i].audio_start_time`
   - candidate cuts in `(prev_end, current_start + hard_max_added]`
   - `required_add = cut - current_start` when the cut is after current start,
     or `0` when the current start already lands within tolerance.
4. Keep only candidates with `0 < required_add <= per_boundary_cap`.
5. Reject candidates that violate hook protection, global budget, or final
   video duration.
6. Sort candidates by:
   - required add ascending;
   - absolute post-snap distance to the cut ascending;
   - later hook-safe boundaries before early-hook boundaries;
   - sentence index ascending for deterministic output.
7. Apply accepted shifts by adding silence before sentence `i` and moving
   sentence `i` plus all following sentence audio times forward by the same
   amount.
8. Stop when no candidate remains or the global budget is exhausted.

This greedy path is sufficient because the added-silence budget is tiny and the
optimizer is a quality polish, not the primary duration solver. If later data
shows competing anchors are common, the candidate selector can become dynamic
programming without changing the external data shape.

## Metadata

Each shifted sentence should preserve the existing compact fields and add:

```json
{
  "shot_anchor_silence_added": 0.22,
  "shot_anchor_cut_time": 12.48,
  "shot_anchor_before_start": 12.26,
  "shot_anchor_after_start": 12.48,
  "shot_anchor_reason": "nearby_cut_soft_snap"
}
```

Task-level `final_compose_summary` should add:

```json
{
  "shot_anchor_silence_enabled": true,
  "shot_anchor_silence_applied": true,
  "shot_anchor_silence_total": 0.64,
  "shot_anchor_silence_count": 3,
  "shot_anchor_cut_count": 18,
  "shot_anchor_silence_budget": 1.5,
  "shot_anchor_skip_reasons": {
    "over_budget": 2,
    "hook_protection": 1,
    "would_overrun_video": 0,
    "too_far_from_cut": 14
  }
}
```

The existing `silence_gap_duration` remains total inter-sentence silence after
all scheduling. A separate `shot_anchor_silence_total` explains how much of that
silence was intentionally added for visual-cut alignment.

## UI Behavior

The main final processing card should remain concise:

- Add a short note when shot-anchor silence was applied:
  `镜头锚点微调：新增 0.6s 句间静音，贴合 3 个镜头切点。`
- If disabled or no anchor applied, do not make the card noisy.
- Detailed rows can live in the existing TTS process modal or diagnostics JSON.

## Rollout

Start enabled only for Omni sentence-level path with:

- `shot_decompose = true`;
- `tts_strategy = sentence_reconcile`;
- compact ASR-primary audio mode;
- non-empty shot cut anchors.

If any prerequisite is missing, skip the optimizer and keep current behavior.

Later this can become a preset flag, but the first implementation should avoid
adding new UI configuration unless production review shows per-task control is
needed.

## Verification

Unit tests:

1. Adds `0.2s` silence to snap a sentence start to a nearby shot cut.
2. Does not add silence when the required shift is greater than `0.25s`.
3. Does not add silence when final content would exceed video duration.
4. Applies hook protection in the first `3.0s`.
5. Respects the global silence budget.
6. Keeps sentence order monotonic and shifts all following sentence times.
7. Writes task-level and sentence-level diagnostics.

Runtime tests:

- `sentence_reconcile` final TTS path applies the optimizer only when Omni
  shot anchors are available and compact ASR-primary mode is active.
- `final_compose_summary` includes shot-anchor metrics without hiding existing
  truncation, best-effort, or semantic warning metrics.

Manual acceptance:

- On a representative Omni task, no generated gap exceeds the compact budget
  plus an applied shot-anchor addition.
- Opening hook has no noticeable artificial pause.
- Final audio/video duration remains within the existing compose contract.
