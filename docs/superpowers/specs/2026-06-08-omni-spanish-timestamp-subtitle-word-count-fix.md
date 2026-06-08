# Omni Spanish Timestamp Subtitle Word Count Fix

Date: 2026-06-08

## Anchors

- `AGENTS.md`: video translation fixes must be document-driven, scoped to the active worktree, and verified with focused tests.
- `docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md`: Omni uses `plugin_config` to run the `standard` translation and `five_round_rewrite` TTS path for the task.
- `docs/superpowers/specs/2026-05-26-omni-nonempty-asr-translation-flow.md`: non-empty ASR must continue through translation/TTS instead of being short-circuited.
- `docs/p1p2-acceptance-2026-05-07-omni-translate-route.md`: Omni route behavior must remain compatible with existing task start/resume/artifact flows.

## Problem

Task `363cb6c5bacf58198d41010febeecbe9` fails in Omni TTS with:

```text
tts_script subtitle_chunks must be 10 words or fewer
```

The task is Spanish (`target_lang=es`) and the transcript contains time stamps such as `14:45` and `17:50`. The subtitle splitter decides whether a chunk is short enough using whitespace tokens, while final validation counts words with `_subtitle_word_count()`. A timestamp like `14:45` is one whitespace token but two validation words, so the splitter can keep a chunk that later fails validation.

After the subtitle-count failure is bypassed, the same task can resume into the five-round duration rewrite path and fail with:

```text
localized_translation sentence missing source_segment_indices
```

The single-batch rewrite path already patches missing `source_segment_indices` from the previous localized translation. The batched rewrite resume path can still load older cached `all_sentences` from `_batch_checkpoints` and validate the merged result without reapplying that patch.

## Rule

Subtitle chunk splitting and validation must use the same word-count authority: `_subtitle_word_count()`.

The splitter may still split on whitespace boundaries, but every generated chunk must satisfy the validation word limit under `_subtitle_word_count()`. This applies to all languages and is especially important for Spanish timestamp-heavy videos.

Batched localized rewrite must also reapply `source_segment_indices` repair before final merged validation, including when the merged sentences came entirely from a checkpoint. Rewrite changes wording and duration fit, not the source-segment correspondence, so missing index fields should be inherited from the previous localized translation before failing validation.

## Verification

- `validate_tts_script()` accepts a Spanish block containing `14:45` that has 10 whitespace tokens but 11 validation words.
- Every rebuilt subtitle chunk has at most 10 validation words.
- Batched localized rewrite can resume from a checkpoint whose cached sentence is missing `source_segment_indices` and still validates after inheriting the previous mapping.
- Existing subtitle splitting tests continue to pass.
