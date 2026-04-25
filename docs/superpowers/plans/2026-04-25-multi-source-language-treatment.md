# Multi-Source Language Treatment Plan (治本版)

**Branch:** `feature/multi-source-treatment`
**Started:** 2026-04-25
**Status:** Implementation complete, pending E2E validation with real Spanish sample.

---

## Background

The original pipeline was built assuming source language ∈ {zh, en}, with the
default deeply tied to "Chinese e-commerce video → English translation":

- `pipeline/language_detect.py` only returned `"zh"` / `"en"` (binary heuristic).
- `pipeline/translate.py` exposed `generate_localized_translation(source_full_text_zh: ...)` — parameter name and prompt body both hard-coded "Chinese".
- All `lang_label` dicts in localization modules covered only `{"zh": "Chinese", "en": "English"}`.
- Doubao SeedASR (`volc.seedasr.auc`) — the only ASR engine — does not officially support sources outside zh/en.
- Convergence loop hard-coded `MAX_REWRITE_ATTEMPTS = 5` and `WORD_TOLERANCE = 0.10` regardless of target language difficulty.
- **Net effect on Spanish input → German output:** ASR misidentifies Spanish as English, prompts label it Chinese/English, length budgets calibrated for English source × English target overflow on both sides, and the rewrite loop burns 5×5 = 25 attempts trying (and failing) to compress a German translation that should never have been so long.

Plan A (already shipped to production) bolted a Spanish→English pivot in
front of the existing pipeline — it works, but at the cost of a double
translation and is fragile for non-zh-non-en sources outside Spanish.

This treatment plan removes the underlying assumption.

---

## Scope

**Source languages supported:** `zh`, `en`, `es` (extensible).
**Target languages affected:** all existing (en/de/fr/ja/es/pt/it/nl/sv/fi).
**Out of scope:** Japanese / Korean / Portuguese as *source* (UI doesn't expose them yet); `av_localize` pipeline (separate code path).

---

## Commit-by-commit breakdown

### `5a25770` — Phase 1 step 1.1 + 1.2: LID trichotomy + main prompt parameterization

- `pipeline/language_detect.py`: zh/en binary → zh/en/es trichotomy.
  Spanish-specific characters (ñ, ¿, ¡, áéíóú) and stopwords push the
  classifier to `"es"`; previously they collapsed to `"en"`.
- `pipeline/lang_labels.py` (new): single source of truth mapping language
  codes to English/Chinese labels. Used by every prompt builder downstream.
- `pipeline/localization.py`: `LOCALIZED_TRANSLATION_SYSTEM_PROMPT` /
  `HOOK_CTA_TRANSLATION_SYSTEM_PROMPT` parameterize "Chinese" → `{source_language_label}`.
  `build_localized_translation_messages` accepts `source_language: str = "zh"`
  (backward compatible).
- Tests: 19 LID assertions, 5 lang_labels assertions.

### `805c872` — Phase 1 step 1.3 + 1.4: per-language modules + `generate_localized_translation` source_language

- `pipeline/localization_de.py` / `localization_fr.py`: replaced hard-coded
  `{"zh":..., "en":...}` dicts with `lang_label()`; "from Chinese or English"
  in prompts → `{source_language_label}`.
- `pipeline/translate.py`: `generate_localized_translation()` adds
  keyword-only `source_language: str = "zh"` and forwards to the message
  builder.

### `8d51864` — Phase 1 step 1.5: runtime callers forward `source_language` + rewrite uses shared `lang_label`

- `appcore/runtime.py` / `runtime_de.py` / `runtime_fr.py` / `runtime_multi.py`:
  read `task.source_language` and pass it into `generate_localized_translation`.
- All 4 `build_localized_rewrite_messages` implementations now use the
  shared `pipeline.lang_labels.lang_label` instead of their own hard-coded dict.

### `6bfdf80` — Phase 1 step 1.7: route validation accepts `"es"`

- `web/routes/multi_translate.py` / `de_translate.py` / `fr_translate.py`:
  six validation gates flip `("zh", "en")` → `("zh", "en", "es")`.

### `1fe8896` + `034e92e` — Phase 2a + Phase 3.1 + Phase 3.2: dynamic tolerance + Scribe + dispatcher

- `appcore/runtime.py`: hardcoded `MAX_REWRITE_ATTEMPTS=5` / `WORD_TOLERANCE=0.10`
  replaced by per-target-language lookup tables. de/ja/fi (slow / long-word
  targets) get tolerance 0.15-0.18 and 7 attempts; en stays at 0.10 / 5.
  Without this, slow-target × non-English-source combos reliably exhaust 25
  attempts.
- `pipeline/asr_scribe.py` (new): ElevenLabs Scribe adapter. POST
  `/v1/speech-to-text` with `model_id=scribe_v2`, `timestamps_granularity=word`.
  `_parse_scribe_response` aggregates word-level output into sentence
  segments matching `pipeline.asr.transcribe`'s shape.
- `pipeline/asr.py`: `transcribe_local_audio_for_source(local_audio_path, source_language, ...)` dispatcher routes zh/en → Doubao, anything else → Scribe.
- Tests: 7 Scribe parser + 8 dispatcher router cases.

### `217b475` — Phase 3.2 (initial ASR step) + Phase 4a (target_words hint)

- `appcore/runtime.py:_step_asr`: source-language-aware initial ASR. zh/en
  via Doubao + TOS upload; anything else via Scribe direct multipart upload.
  All `PipelineRunner` subclasses (de/fr/multi) inherit this automatically.
- `localization.py` / `localization_de.py` / `localization_fr.py`:
  `build_localized_translation_messages` accepts keyword-only `target_words` +
  `video_duration`. When both are given, the user content gets a closing
  hint: *"The X script will be dubbed over a Ys video. Aim for approximately
  N words... Stay within ±10% — overshooting forces a rewrite loop."* This
  steers round 1 toward the right length **before** the rewrite loop kicks
  in, avoiding the 5×5 cascade.
- 4 runner sites (`runtime.py` / `runtime_de.py` / `runtime_fr.py` /
  `runtime_multi.py`) call new `_compute_initial_target_words(video_duration, target_label)` and forward to the message builder.

### `2df22af` — Phase 2 follow-up: refine target_words hint copy

Polishes the round-1 hint phrasing so the LLM treats it as a hard constraint.

### `cf08b18` — Phase 5 partial: integration test for `_step_asr` source-language dispatch

`tests/test_step_asr_dispatch.py` exercises the runtime entry point with mocked
Doubao + Scribe backends, asserting routing for zh/en → Doubao, es → Scribe.

---

## Verification matrix

| Layer | Coverage |
|---|---|
| LID | 19 unit tests (zh/en/es trichotomy, edge cases, realistic short-form lines) |
| lang_labels | 5 assertions covering en + zh labels + unknown fallback |
| Scribe parser | 7 unit tests (empty, fallback, period split, silence-gap split, Spanish punctuation, spacing/audio_event filter, word timestamps) |
| ASR dispatcher | 8 router tests (zh/en/None → Doubao, es/pt/de → Scribe, key forwarding) |
| `_step_asr` integration | (added in `cf08b18`) — mocked end-to-end |
| Existing translate / route / runtime regression | All previously-passing tests continue to pass |

**Full regression run after final HEAD:** *(to be filled by Phase 5)*.

---

## Backward compatibility

Every parameterized function keeps `source_language="zh"` as default so
existing zh→en/de/fr/ja flows behave bit-identically. New behavior is
opt-in via task.source_language ∈ {"en", "es"} (or LID auto-detect).

---

## Phase 5: pending E2E validation

**Sample:** `C:\Users\admin\Desktop\德国法国测试\西班牙语视频.mp4` (35.4s, h264+aac).

**Steps:**
1. Submit task with target=de, source_language=es via `/api/multi-translate/...`.
2. Observe `_step_asr`: should route to Scribe and return Spanish utterances
   with `language_code="es"`, language_probability ≥ 0.95.
3. `_step_translate`: round 1 should land within ±15% of `target_words`
   computed from `video_duration × _DEFAULT_WPS["de"] (= 2.0)`.
4. Convergence loop: should resolve in ≤ 2 outer rounds (vs. previous 5×5
   exhaustion).
5. Capture `tts_duration_rounds` JSON and append to this doc as evidence.

**Success criteria:**
- All 5 outer rounds available; converges in ≤ 2.
- Total LLM rewrite calls ≤ 7 (vs. 25 before).
- Final TTS audio duration in `[video_duration - 1, video_duration + 2]`.
- Translated subtitle text reads natural German (manual review).

---

## Phase 6: open PR

**Branch:** `feature/multi-source-treatment`
**Target:** `master`
**PR title:** `feat(multi-source): support es as a first-class source language (treatment for 25/25 convergence runaway)`

PR body should link this doc, list the 9 commits, and call out the breaking
nature of `_DOUBAO_NATIVE_LANGUAGES = {"zh", "en"}` (any future source
language requires explicit registration).

After merge: deploy + monitor `tts_duration_rounds` for the first batch of
real Spanish-source tasks to confirm the convergence improvement holds.
