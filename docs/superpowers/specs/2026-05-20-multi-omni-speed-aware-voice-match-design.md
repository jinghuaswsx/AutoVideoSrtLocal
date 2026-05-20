# Multi and Omni Speed-Aware Voice Match Design

Date: 2026-05-20

## Background

English redub already has a speed-aware voice recommendation path:

- First collect the top 20 voices by timbre similarity.
- Rerank only that 20-voice pool by preview speech-rate closeness.
- Return the final top 20 candidates to the user.

The original English redub spec explicitly scoped that behavior away from Multi
and Omni. This spec expands the same recommendation behavior to the two shared
video translation products:

- Multi-language video translation (`multi_translate`)
- Omni video translation (`omni_translate`)

## Required Behavior

For Multi and Omni voice matching:

- Initial `voice_match` must call the shared speed-aware matcher.
- The matcher must request a candidate pool of 20 voices from the timbre matcher.
- The UI-facing candidate list remains top 20 after speed reranking.
- Gender rematch from the voice selection popup must use the same speed-aware
  matcher, preserving the selected gender filter.
- Existing default-voice exclusion must remain unchanged.
- Existing query embedding reuse must remain unchanged; rematch must not
  re-extract audio or re-embed the sample.
- If speech-rate data is incomplete, the shared helper may fall back to legacy
  similarity ordering and annotate candidates with the existing fallback fields.

## Source Speech Rate

Multi and Omni tasks can start from non-English source audio. When estimating the
source speaking speed:

- Prefer `utterances_en` when present because it keeps the original timing while
  providing word-like text for rate estimation.
- Fall back to `utterances` when `utterances_en` is not present.
- Do not block voice matching if neither source can produce a speech-rate
  sample; the shared helper handles fallback.

## Implementation Scope

Modify:

- `appcore/runtime_multi.py`
- `web/routes/multi_translate.py`
- `web/routes/omni_translate.py`
- `tests/test_runtime_multi_voice_match.py`
- `tests/test_multi_translate_routes.py`
- `tests/test_omni_translate_routes.py`

Do not modify:

- TTS duration convergence
- Subtitle generation
- Voice library pagination or lazy loading
- English redub strategy settings
- Database schema

## Acceptance Criteria

- Multi initial voice match uses speed-aware top20-to-top20 reranking.
- Omni initial voice match inherits the same behavior through
  `MultiTranslateRunner`.
- Multi rematch uses speed-aware reranking and forwards gender.
- Omni rematch uses speed-aware reranking and forwards gender.
- Existing default voice exclusion remains active for initial match and rematch.
- Existing tests for English redub speed-aware matching continue to pass.

## Verification

Run:

```bash
pytest tests/test_english_redub_voice_match_speed.py tests/test_runtime_multi_voice_match.py tests/test_multi_translate_routes.py tests/test_omni_translate_routes.py -q
git diff --check
```
