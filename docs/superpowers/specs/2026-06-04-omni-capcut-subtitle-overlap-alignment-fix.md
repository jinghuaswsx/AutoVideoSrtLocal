# Omni CapCut Subtitle Overlap Alignment Fix

- Date: 2026-06-04
- Status: confirmed, implementation in progress

## Anchors

- `AGENTS.md`: video translation/TTS changes must be document-driven, scoped to an isolated worktree, and verified with focused tests.
- `docs/superpowers/specs/2026-04-16-subtitle-config-design.md`: subtitle output must use one timing/position contract through preview, compose, and export.
- `docs/superpowers/specs/2026-05-13-tts-segment-candidate-assembly-design.md`: final TTS audio may be assembled from per-segment candidates; downstream subtitle/export must follow the final audio artifact.
- `docs/superpowers/specs/2026-05-13-omni-asr-primary-compact-timeline-design.md`: subtitle units must follow the actual generated voice timeline when post-TTS timing changes exist.

## Problem

Task `0f24060de8b4596c8d217fd01c2b239b` finished successfully, but the exported CapCut project package has subtitle/voice mismatch around the early section.

Observed evidence from the downloaded package:

- `tts_full.normal.mp3` contains leading silence until about `11.443s`.
- The first subtitle starts at `11.519s`, so this is not a global offset problem.
- The task SRT has `93` subtitle entries.
- The CapCut package SRT has `90` subtitle entries.
- Task SRT entries 17-19 (`31.539s` to `39.425s`) overlap entries 13-16 (`31.519s` to `37.820s`).
- `pipeline.capcut._fix_srt_overlaps()` sorts entries and drops any entry whose start is before the previous kept entry's end, so entries 17-19 are removed from the CapCut package.

The upstream alignment problem is in `pipeline.subtitle_alignment.align_subtitle_chunks_to_asr()`: when one target token cannot be found in ASR words, the current search advances the global cursor to the end. Later subtitle chunks then fall back to proportional timestamps instead of continuing from the last matched ASR position, which can create out-of-order subtitle timestamps.

## Required Behavior

1. Subtitle ASR alignment must not consume the global ASR cursor to EOF when a token is missing.
2. A chunk with partial ASR matches should keep the matched word timing and advance the global cursor only to the last matched word.
3. A chunk with no ASR match should use a monotonic fallback window after the previous subtitle end, not an absolute proportional window that can move backwards.
4. The final aligned chunks returned by `align_subtitle_chunks_to_asr()` must be nondecreasing by start/end time.
5. CapCut export must not silently delete subtitle text to resolve overlap. If an imported SRT still contains overlaps, `_fix_srt_overlaps()` must preserve all entries by shifting or clipping timings into a monotonic sequence.

## Non-Goals

- Do not change translation prompts, TTS generation, voice matching, or speedup candidate assembly.
- Do not change subtitle styling, font, size, or position.
- Do not change hard-subtitle compose behavior except through cleaner SRT timing input.
- Do not add database schema or new routes.

## Verification

Focused tests:

```bash
pytest tests/test_subtitle_alignment.py tests/test_capcut_export.py -q
```

The regression must cover:

- A missing word inside one subtitle chunk does not prevent later chunks from matching ASR words.
- Aligned chunks remain monotonic when a chunk has no match.
- CapCut SRT overlap repair preserves every subtitle entry and produces non-overlapping timestamps.

