# Multilingual Subtitle Safe Splitting Design

- Created: 2026-05-13
- Module: multi-translate / omni-translate subtitle generation
- Anchor: `docs/superpowers/specs/2026-04-18-multi-translate-design.md`

## Problem

The subtitle stage can receive semantically valid chunks that are too long for the configured on-screen subtitle box. German is the clearest case: `MAX_CHARS_PER_LINE = 38` and `MAX_LINES = 2`, so one subtitle item can safely render about 76 characters. A 100+ character German sentence currently falls through `pipeline.subtitle.wrap_text()`, fills two lines, and silently drops the remaining words. This causes CapCut/Jianying imports to miss subtitle tails even though the translation and TTS audio contain the full sentence.

## Goals

- Preserve complete subtitle text. No generated subtitle path may silently discard words.
- Keep subtitles semantically coherent and readable on short-form video.
- Respect language display limits from `pipeline/languages/<lang>.py`: line width, max lines, weak boundary words, and max characters per second.
- Split oversized subtitle chunks before SRT generation and re-align each new chunk to the target-language audio timeline.
- Prefer real subtitle ASR word timestamps. Fall back to proportional timing only when word timestamps are unavailable.

## Non-Goals

- Do not rewrite translated copy or TTS text in the subtitle stage.
- Do not alter TTS duration convergence logic.
- Do not introduce database changes, new routes, or UI changes.
- Do not restart services or validate against local MySQL.

## Subtitle Limits

Each language keeps its existing rule module. The safe splitter derives:

- `max_chars_per_line`: language `MAX_CHARS_PER_LINE`, default 42.
- `max_lines`: language `MAX_LINES`, default 2.
- `hard_char_limit`: `max_chars_per_line * max_lines`.
- `soft_char_limit`: `floor(hard_char_limit * 0.9)`, minimum one line.
- `max_chars_per_second`: language `MAX_CHARS_PER_SECOND`, default 17.

A chunk is oversized if:

- its display text length exceeds `soft_char_limit`;
- its text cannot be formatted into `max_lines` lines without exceeding `max_chars_per_line`;
- its CPS exceeds `max_chars_per_second`;
- or its text would still be truncated by the legacy formatter.

## Splitting Algorithm

The splitter accepts corrected subtitle chunks after `align_subtitle_chunks_to_asr()`. It returns chunks with the same metadata shape, but long chunks may become multiple adjacent chunks.

For each chunk:

1. Normalize text by trimming terminal punctuation only for display checks, matching current SRT behavior.
2. If the chunk is safe, keep it unchanged.
3. If oversized, split into pieces using ranked boundaries:
   - strong sentence punctuation: `.`, `?`, `!`, `;`, `:`;
   - comma and phrase boundaries;
   - weak-boundary-aware word splits near the target length, avoiding a next piece that starts with weak words like `und`, `oder`, `der`, `die`, `das`;
   - forced word splits only when no semantic boundary keeps the piece safe.
4. Re-check every piece recursively until each piece fits the language display limits.
5. Preserve all words in order.

## Timeline Re-Alignment

When a source chunk has matched subtitle ASR words, each split piece is aligned by its own token span:

- `start_time` is the first matched word's `start_time`.
- `end_time` is the last matched word's `end_time`.
- Adjacent pieces are nudged to avoid overlap and preserve monotonic order.

When word timestamps are missing or incomplete:

- Split the original chunk duration proportionally by piece text length.
- Enforce a small positive duration for every piece.
- Clamp pieces inside the original chunk time range.

The splitter must not create negative durations or overlapping timestamps.

## Integration

Apply the splitter after subtitle ASR alignment and before `build_srt_from_chunks()` in:

- `appcore/runtime_multi.py`
- `appcore/runtime_omni_steps.py`

Update legacy language-specific runners only if tests show the same unsafe path still matters for active routes.

Update `pipeline/subtitle.py` so text wrapping never silently drops remaining words. SRT text may exceed the ideal limits only as a last-resort fallback, but it must remain complete.

## Verification

Tests must cover:

- the reported German long sentence is split and all words remain in the SRT;
- split pieces fit `38 x 2` for German;
- word timestamp alignment creates non-overlapping piece timings;
- proportional fallback works without word timestamps;
- `build_srt_from_chunks()` no longer drops overflow text.
