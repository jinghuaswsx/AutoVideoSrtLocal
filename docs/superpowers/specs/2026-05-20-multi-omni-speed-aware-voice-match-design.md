# Multi and Omni Speed-Aware Voice Match Design

Date: 2026-05-20

## Background

English redub already has a speed-aware voice recommendation path:

- First collect the top 20 voices by timbre similarity.
- Rerank only that 20-voice pool by a timbre-dominant combined score. Preview
  speech-rate closeness is a weak secondary signal, not the primary rank key.
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
- The UI-facing candidate list remains top 20 after combined-score reranking.
- The combined score remains timbre dominant (`TIMBRE_WEIGHT=0.75`,
  `SPEED_WEIGHT=0.25`); speech-rate score must not outrank a better timbre
  candidate when the weighted combined score is lower.
- The UI should label speech-rate metadata as a reference signal rather than a
  definitive match.
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
- If fallback `utterances` contain non-word-like timed tokens, such as Chinese
  phrases grouped as one ASR word, treat source speech rate as unavailable
  instead of comparing that token count to English preview words per second.
- Do not block voice matching if neither source can produce a speech-rate
  sample; the shared helper handles fallback.

## Implementation Scope

Modify:

- `appcore/runtime_multi.py`
- `pipeline/voice_match_speed.py`
- `web/routes/multi_translate.py`
- `web/routes/omni_translate.py`
- `web/static/voice_selector_multi.js`
- `tests/test_english_redub_voice_match_speed.py`
- `tests/test_voice_selector_multi_assets.py`
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

- Multi initial voice match uses timbre-dominant speed-aware top20-to-top20
  reranking.
- Omni initial voice match inherits the same behavior through
  `MultiTranslateRunner`.
- Multi rematch uses timbre-dominant speed-aware reranking and forwards gender.
- Omni rematch uses timbre-dominant speed-aware reranking and forwards gender.
- The shared voice selector labels speed data as `语速参考`.
- Existing default voice exclusion remains active for initial match and rematch.
- Existing tests for English redub speed-aware matching continue to pass.

## 2026-05-20 Multi/Omni Shared Selector Service

- Multi (`/api/multi-translate`) and Omni (`/api/omni-translate`) must reuse the
  same TTS voice-selection backend service for gender rematch, candidate
  `extra_items` hydration, speed-aware candidate generation, and task-state
  update payloads.
- Route files may keep project-specific access control, project lookup, response
  wrapping, and artifact endpoints, but they must not fork the core TTS
  voice-selection behavior.
- A regression in either product is unacceptable when the other product still
  works. Tests must cover the shared service directly and route contracts for
  both Multi and Omni so the modules cannot drift apart during merges.

## 2026-05-21 Auto-Confirm Idempotency

- The shared voice selector may auto-confirm the AI-ranked top voice only when
  the task is still blocked at `voice_match=waiting` and no
  `selected_voice_id` has been persisted.
- Once `selected_voice_id` exists, or once `voice_match` is already `done`, page
  load and background refresh must never call `/confirm-voice` automatically.
- This guard applies equally to Multi and Omni because both pages load
  `web/static/voice_selector_multi.js`.
- Manual re-confirmation through an explicit user click remains allowed.

## Verification

Run:

```bash
pytest tests/test_english_redub_voice_match_speed.py tests/test_runtime_multi_voice_match.py tests/test_multi_translate_routes.py tests/test_omni_translate_routes.py -q
git diff --check
```
