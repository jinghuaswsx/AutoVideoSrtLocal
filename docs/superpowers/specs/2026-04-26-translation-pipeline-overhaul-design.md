# Translation Pipeline Overhaul — Design

**Date**: 2026-04-26
**Scope**: Source-language pipeline improvements for `omni_translate` (experimental) and `multi_translate` (production), plus a new translation-quality assessment card shared by both.
**Status**: Approved by user 2026-04-26 (skipped doc review per user request, proceeding directly to plan).

---

## 1. Background & Motivation

Side-by-side comparison of the same Spanish source video across the two pipelines:

| Metric | omni (`723e7a3d…`) | multi (`562030b7…`) |
|---|---|---|
| ASR utterance count | 6 segments | 4 segments |
| Round 1 word count / audio duration | 83 words / 24.1s | 43 words / 11.3s |
| Convergence rounds | 2 (real `converged`) | 5 maxed out, fell back to `best_pick` (still 2.6s off target) |
| Rewrite attempts total | 1 | 17 |
| TTS full-audio synthesis | 2 | 5 |
| Final localized text fidelity to source | partially anchored to ASR ("Tokina fire crew", "police situation") | fully fabricated ("Here's something I actually found really useful for staying connected…") |

Root causes of multi's collapse:

1. **Coarser ASR segmentation** — Doubao gave 4 long segments (max 8.7s) on the same audio that yielded 6 finer segments elsewhere. Coarser segments force more words per TTS block and tighter rhythm tolerance.
2. **Noisy ASR text** — single segments containing Spanish, English, and Chinese characters mashed together (e.g. `"As was born in a son the lost chicos lost camisa osama I cannot guess the other阿米塔迪亚"`).
3. **`asr_normalize` amplified the noise** — its English translation step took noise in and produced more confident-sounding noise out.
4. **`translate` step went off-anchor** — given a noisy English mid-representation, Claude wrote generic e-commerce copy unrelated to the source video. With no semantic anchor, subsequent rewrite rounds churned in a 65-70 word band without converging.

The fix is to (a) clean ASR output before translation, (b) keep the source-language anchor available throughout the convergence loop, (c) let `omni` translate directly from the source language so the English mid-step stops washing out meaning, and (d) add a quality-assessment card so we can quantify the impact.

---

## 2. Goals & Non-Goals

### Goals
- Add a manual source-language selector at upload time for both pipelines (optional, with `Auto-detect` as default).
- Add same-language ASR purification (Gemini 3 Flash primary + Claude 4.6 fallback) before any downstream translation, on both pipelines.
- For `omni`: drop the "normalize to English" mid-step. Translate directly from cleaned source-language ASR to the target language, and feed the original-language transcript into the rewrite loop as a permanent anchor.
- For `multi`: keep the existing English mid-representation. Only insert ASR purification inside `asr_normalize`. Conservative — production safety first.
- Add a `translation_quality_assessments` card with two independent scores (translation quality, TTS fidelity), populated asynchronously after the subtitle step, viewable in the detail page, persisted to the database, and re-runnable by admins.

### Non-Goals
- No change to existing `de_translate` / `fr_translate` / `ja_translate` runners.
- No retroactive reprocessing of historical tasks (admin can re-run quality assessment manually per task; no batch backfill).
- No change to the TTS engine (ElevenLabs) or to the duration-convergence outer loop algorithm itself.
- No change to billing, cleanup, or scheduled-tasks systems beyond logging the new LLM use cases.

---

## 3. High-Level Architecture

### 3.1 Step topology

**omni** (changes):
```
extract → asr → asr_clean (REPLACES asr_normalize)
        → voice_match → alignment
        → translate (NEW: source-language → target-language, one-shot)
        → tts (rewrite prompts now carry original ASR anchor)
        → subtitle
        → quality_assessment (NEW, async, non-blocking)
        → compose → export
```

**multi** (changes):
```
extract → asr → asr_normalize (NEW: internal purify pre-step)
        → voice_match → alignment
        → translate (unchanged: utterances_en → target-language)
        → tts (unchanged)
        → subtitle
        → quality_assessment (NEW, async, non-blocking)
        → compose → export
```

### 3.2 Shared infrastructure (consumed by both pipelines)

- `pipeline/asr_clean.py` — same-language ASR purification function with primary + fallback model.
- `pipeline/translation_quality.py` — quality-assessment LLM call returning a strict JSON schema.
- `web/services/quality_assessment.py` — background-thread runner; idempotent per `(task_id, run_id)`.
- `web/routes/translation_quality.py` — API endpoints for both project types.
- New table `translation_quality_assessments` — one row per assessment run.
- New LLM use cases: `asr_clean.purify_primary`, `asr_clean.purify_fallback`, `translation_quality.assess`.
- New prompt slots: `asr_clean.purify`, `translation_quality.assess`, optionally per-language refinements.

---

## 4. Module: ASR Same-Language Purification

### 4.1 Function signature

```python
# pipeline/asr_clean.py
def purify_utterances(
    utterances: list[dict],
    *,
    language: str,            # ISO-639-1, e.g. 'es', 'en', 'zh'
    task_id: str,
    user_id: int | None,
) -> dict:
    """Return:
      {
        "utterances": list[dict],       # cleaned, same length & indexes as input
        "cleaned": bool,                # True if model returned valid output
        "fallback_used": bool,          # True if Claude fallback was used
        "model_used": str,              # provider/model that produced the result
        "raw_response_primary": str,    # for audit
        "raw_response_fallback": str | None,
        "validation_errors": list[str], # if any, why primary was rejected
      }
    """
```

### 4.2 Prompt template (slot `asr_clean.purify`)

```
You are a {language_label} ASR proofreader. The JSON below is timestamped ASR
output from a short product video. It may contain spelling errors, words
mis-recognized as another language, or noise.

Rules:
1. Preserve every entry's index, start, end. Same count, same indexes, no merging, no splitting.
2. Fix obvious spelling errors. If a word is clearly recognized in a wrong
   language, restore it to {language_label}. Brand names stay verbatim.
3. Do NOT paraphrase, expand, or add explanatory content.
4. If a segment is genuinely unintelligible, return its text unchanged. Do
   NOT fabricate.
5. Output strict JSON only:
   {"utterances": [{"index": 0, "text": "..."}, ...]}
```

### 4.3 Validator (Python-side, runs after every model call)

A primary response is accepted iff **all** of:
- JSON parses against the schema `{"utterances": [{"index": int, "text": str}]}`
- `len(out) == len(in)`
- `set(out_indexes) == set(in_indexes)`
- For each cleaned entry, the text is non-empty
- Per-language character-set heuristics:
  - Spanish/Portuguese/French/Italian/German/Dutch/Swedish/Finnish task: text must contain `>= 50%` Latin alphabet chars and `0` CJK chars (`[一-鿿]`)
  - Chinese task: text must contain CJK chars
  - Japanese task: text must contain at least one of hiragana/katakana/CJK
  - English task: text must contain `0` CJK chars

If validator fails on the primary call, retry once with the Claude fallback. If fallback also fails, return `cleaned=False` with `utterances` set to the original input untouched. Record `validation_errors` for audit.

### 4.4 Token budget & cost expectation

- Primary (Gemini Flash): ~500 input + ~500 output tokens for typical 30s video. ~¥0.005 per call.
- Fallback (Claude Sonnet 4.6): ~¥0.05 per call. Hit rate target: <5% of tasks.

### 4.5 Use-case registration

```python
# appcore/llm_use_cases.py
"asr_clean.purify_primary": _uc(
    provider="gemini_aistudio",
    model="gemini-3-flash-lite-preview",
    label="ASR 同语言纯净化（主路）",
    log_service="asr_clean.purify",
),
"asr_clean.purify_fallback": _uc(
    provider="openrouter",
    model="anthropic/claude-sonnet-4.6",
    label="ASR 同语言纯净化（兜底）",
    log_service="asr_clean.purify",
),
```

---

## 5. Pipeline: omni Source-Language Track

### 5.1 Source-language selection at upload

Front-end (`web/templates/omni_translate_list.html`, create modal):
- New `<select name="source_language">` with options `Auto-detect` (default), 11 supported languages with Chinese labels.

Back-end (`web/routes/omni_translate.py.upload_and_start`):
- Already partially supports `source_language` for `zh/en/es/pt`. Extend allow-list to all 11 codes.
- `user_specified_source_language` defaults `False` if user keeps `Auto-detect`.

### 5.2 New step `_step_asr_clean` (replaces `_step_asr_normalize`)

In `appcore/runtime_omni.py` (override `MultiTranslateRunner._step_asr_normalize` and rename in `_get_pipeline_steps`):

```
def _step_asr_clean(task_id):
    if user_specified=False:
        run LLM-LID via pipeline.language_detect_llm (current logic)
        update task.source_language with detected code (if confidence >= 0.7)
    else:
        use task.source_language as-is

    call pipeline.asr_clean.purify_utterances(utterances, language=source_language, ...)

    if cleaned:
        task.utterances = result.utterances    # OVERWRITE; keep raw under utterances_raw
        task.utterances_raw = original         # for audit
    else:
        # noise survived; keep going with original utterances
        log warning to artifact

    set step done; write artifact
```

Crucially: `omni` no longer produces `utterances_en`. Downstream `alignment`, `translate`, `tts` all consume `utterances` directly in the source language.

### 5.3 Pipeline-step list change

In `runtime_omni.py._get_pipeline_steps`, replace the `asr_normalize` slot with `asr_clean`. Also update `RESUMABLE_STEPS` in `web/routes/omni_translate.py` and any front-end step labels.

### 5.4 `_step_translate` override (omni-only)

```python
def _step_translate(self, task_id):
    task = task_state.get(task_id)
    source_language = task.get("source_language", "es")
    target_lang = self._resolve_target_lang(task)

    # script_segments now contain source-language text (no utterances_en)
    source_full_text = build_source_full_text_in_lang(
        task.get("script_segments", []),
        language=source_language,
    )

    system_prompt = self._build_system_prompt_with_source(
        target_lang=target_lang, source_language=source_language,
    )

    localized_translation = generate_localized_translation(
        source_full_text=source_full_text,
        script_segments=task.get("script_segments", []),
        variant="normal",
        custom_system_prompt=system_prompt,
        provider=provider, user_id=self.user_id,
    )
    # … rest mirrors parent _step_translate
```

`_build_system_prompt_with_source` prepends the existing `base_translation` prompt with:

```
INPUT NOTICE: The source script provided below is in {source_language_label}
({source_language}). It came from automatic speech recognition of the original
video and may contain transcription artifacts. Treat it as the source of truth
for content; do NOT invent details that are not implied by it. If a segment
is unintelligible, keep your version brief instead of fabricating context.
```

This single sentence is the most important defense against the fabrication failure mode. We have empirical evidence (the multi `562030b7…` task) of LLM going off-anchor when given noisy English; the same defense applies when input is the noisy original language, perhaps even more strongly.

### 5.5 Rewrite messages with original-ASR anchor

Override `_PromptLocalizationAdapter.build_localized_rewrite_messages` in a new `OmniLocalizationAdapter`:

```
USER MESSAGE BLOCK 1:
  ORIGINAL VIDEO TRANSCRIPT ({source_language_label}, ground truth):
  <utterances joined>

USER MESSAGE BLOCK 2:
  INITIAL LOCALIZATION (target language, written from the transcript above):
  <round-1 localized_translation JSON>

USER MESSAGE BLOCK 3:
  REWRITE TASK:
  Rewrite the initial localization to {direction} to ~{target_words} words.
  STAY ANCHORED in the original transcript. Do not fabricate details that
  are not in the transcript.
```

This guarantees: in every rewrite round, the LLM sees the source video transcript again, not just its own previous output. The `prev_localized_translation` parameter remains pinned to round-1 (existing semantics — never accumulate).

### 5.6 Backwards compatibility

- Tasks created before this change have `asr_normalize_artifact` and `utterances_en` in their `state_json`. Detail page must check both old and new fields and render accordingly.
- `RESUMABLE_STEPS` keeps both `asr_normalize` (legacy) and `asr_clean` (new) so historical resume requests still work.
- `_step_asr_normalize` method body kept on the parent class for `multi`'s use.

---

## 6. Pipeline: multi Conservative Track

### 6.1 Source-language selection at upload

Same UI as omni. Back-end `web/routes/multi_translate.py.upload_and_start`:
- Add `source_language` form field validation (allow-list 11 codes + empty).
- Add `user_specified_source_language` to task state.
- ASR engine routing **stays as Doubao for everything** (deliberate — multi is the production track and doesn't gain ASR engine routing in this overhaul).

### 6.2 ASR purification inside `asr_normalize`

Modify `pipeline/asr_normalize.run_asr_normalize` and `run_user_specified` to insert purification between detect and translate:

```python
detect_result, detect_tokens = detect_language(...)   # unchanged
lang = detect_result["language"]

# NEW: same-language clean before translating to English
clean_result = purify_utterances(utterances, language=lang, task_id=..., user_id=...)
if clean_result["cleaned"]:
    utterances = clean_result["utterances"]
artifact["asr_clean"] = {
    "cleaned": clean_result["cleaned"],
    "fallback_used": clean_result["fallback_used"],
    "model_used": clean_result["model_used"],
    "validation_errors": clean_result["validation_errors"],
}

# UNCHANGED: translate cleaned utterances to en-US
utterances_en, translate_tokens = translate_to_en(utterances, ...)
```

If `clean_result["cleaned"]` is False, log a warning and proceed with original utterances. multi never falls back to source-language translation. The English mid-representation contract is preserved.

---

## 7. Translation Quality Assessment Card

### 7.1 Trigger and async model

After `_step_subtitle` writes `done`, the runner immediately schedules a background thread (similar pattern to `web/services/omni_pipeline_runner.py`):

```python
# inside _step_subtitle, at the end
self._set_step(task_id, "subtitle", "done", ...)
# fire-and-forget assessment, do NOT block compose
from web.services import quality_assessment
quality_assessment.trigger_assessment(
    task_id=task_id,
    project_type=self.project_type,
    triggered_by="auto",
    user_id=self.user_id,
)
```

The runner then proceeds to `_step_compose` immediately. The assessment thread:
1. Inserts a `pending` row into `translation_quality_assessments`
2. Builds the prompt input from `utterances` (source) + `localized_translation.full_text` + `english_asr_result.full_text`
3. Calls Gemini 3 Flash via `llm_client.invoke_chat("translation_quality.assess", ...)`, 60s timeout
4. Validates the JSON schema, writes scores into the DB row, sets status `done` (or `failed` with error_text)
5. Emits a SocketIO event so the front-end card can refresh without polling

### 7.2 Assessment LLM input/output

**System prompt (slot `translation_quality.assess`)**:
```
You are a short-form video translation quality assessor.

You will receive three texts:
1. ORIGINAL_ASR (source language): real content the original video says
2. TRANSLATION (target language): LLM-written script
3. TTS_RECOGNITION (target language): the TTS-generated audio re-transcribed

Score TWO dimensions, each 0-100:

[TRANSLATION_SCORE] compares ORIGINAL_ASR vs TRANSLATION:
  - semantic_fidelity: did the translation capture the source video meaning, no hallucinations?
  - completeness: are key selling points preserved?
  - naturalness: does the target language read naturally?

[TTS_SCORE] compares TRANSLATION vs TTS_RECOGNITION:
  - text_recall: did the TTS faithfully recite the script?
  - pronunciation_fidelity: are key product/brand terms pronounced correctly?
  - rhythm_match: are pauses and segmentation reasonable?

verdict mapping:
  recommend                 — both totals >= 85
  usable_with_minor_issues — both totals >= 70
  needs_review              — either total in [60, 70)
  recommend_redo            — either total < 60
```

**Output schema** (strict JSON):
```json
{
  "translation_score": 85,
  "translation_dimensions": {"semantic_fidelity": 85, "completeness": 80, "naturalness": 90},
  "translation_issues": ["Brand name 'Tokina' was rendered as 'Tokima'."],
  "translation_highlights": ["Key product claim preserved."],
  "tts_score": 90,
  "tts_dimensions": {"text_recall": 92, "pronunciation_fidelity": 88, "rhythm_match": 90},
  "tts_issues": [],
  "tts_highlights": ["Smooth pacing on the closing CTA."],
  "verdict": "recommend",
  "verdict_reason": "..."
}
```

Each total = arithmetic mean of its three sub-dimensions, rounded to int.

### 7.3 Database table

```sql
CREATE TABLE translation_quality_assessments (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    task_id VARCHAR(36) NOT NULL,
    project_type VARCHAR(32) NOT NULL,
    run_id INT NOT NULL DEFAULT 1,
    model VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    triggered_by VARCHAR(16) NOT NULL DEFAULT 'auto',
    triggered_by_user_id INT NULL,
    translation_score INT NULL,
    tts_score INT NULL,
    translation_dimensions JSON NULL,
    tts_dimensions JSON NULL,
    verdict VARCHAR(32) NULL,
    verdict_reason TEXT NULL,
    translation_issues JSON NULL,
    translation_highlights JSON NULL,
    tts_issues JSON NULL,
    tts_highlights JSON NULL,
    prompt_input JSON NULL,
    raw_response JSON NULL,
    error_text TEXT NULL,
    elapsed_ms INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME NULL,
    UNIQUE KEY uk_task_run (task_id, run_id),
    KEY idx_task_id (task_id),
    KEY idx_status (status),
    KEY idx_project_verdict (project_type, verdict)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

Migration file: `db/migrations/2026_04_27_add_translation_quality_assessments.sql`. Auto-applied at server start.

### 7.4 API endpoints

- `GET /api/{project_type}/<task_id>/quality-assessments`
  - Returns: `{"assessments": [{...}, ...]}` ordered by `run_id DESC`. Includes only viewable tasks (owner or admin).
- `POST /api/{project_type}/<task_id>/quality-assessments/run`
  - Admin only. Creates a new row with `run_id = max(existing) + 1`, `status=pending`, `triggered_by=manual`. Spawns background thread. Returns new row id immediately.

### 7.5 Front-end card

Location: detail page right column, below the existing TTS / subtitle artifacts.

States:
- `loading` (no row yet, or row in `pending`/`running`): grey skeleton with "评估中…" text.
- `done`: two large circular progress rings labelled "翻译质量" and "TTS 还原度", each showing the integer score. A coloured verdict pill below (green/blue/yellow/red per Ocean Blue tokens). Three small bars per dimension, collapsed by default. Issues (top 3) and highlights (top 3) in two coloured callouts.
- `failed`: red icon, error message, "重跑评估" button visible to admin.

Admin-visible "重跑评估" button (regardless of state) triggers the POST API and switches the card back to loading state.

History selector: if multiple runs exist, dropdown to pick which run to display. Default = latest.

---

## 8. Failure & Edge Cases

| Case | Behaviour |
|---|---|
| ASR purification primary fails schema | Try fallback model once. |
| Both purification models fail | Log warning, keep original utterances, continue pipeline. omni's translate still benefits from `INPUT NOTICE` system prompt directive. |
| Quality assessment LLM call times out (>60s) | Mark row `failed`, store `error_text`, do not retry automatically. |
| Quality assessment returns malformed JSON | Mark row `failed`, store raw response and parse error. |
| User selects source_language=es but ASR is actually fr | LLM-LID is bypassed (user-specified path). Purification will likely fail validator (text not Latin/no CJK rule passes either way for fr) — falls back to original noise. Translation can still run. Add a warning in the assessment card prompt explaining likely cause. |
| Resume an old omni task (pre-overhaul, has `utterances_en`) | Detect `asr_normalize_artifact` field; use legacy translate path for that task only; mark task with `_legacy_pipeline=True`. |
| Resume an old multi task | No change; `asr_normalize` path remains the same. |
| Compose finishes before assessment | Compose is independent. Assessment may still be in progress. Card shows current state. |
| User triggers manual rerun while one is still running | API returns 409 `assessment_in_progress` with the current run_id. |

---

## 9. Testing Plan

### 9.1 Unit tests (pytest)

- `tests/test_asr_clean.py` — purifier validator: length mismatch, missing index, CJK contamination in Spanish task, primary-success, fallback-success, both-fail.
- `tests/test_translation_quality.py` — schema validation, score arithmetic, verdict mapping at boundary values (84/85, 69/70, 59/60).
- `tests/test_omni_translate_routes.py` — extend with new `source_language` allow-list, manual-trigger admin endpoint, admin-only enforcement.
- `tests/test_multi_translate_routes.py` — same source_language coverage.
- `tests/test_quality_assessment_service.py` — background thread launches once, idempotent on duplicate triggers, status transitions.

### 9.2 Integration test (manual)

1. Upload the same Spanish test video to omni and to multi (matching the existing 723e7a3d / 562030b7 pair).
2. omni: select `Auto-detect`. Verify LID detects `es`, purification cleans the input, translation goes directly `es → en`, rewrite messages contain the Spanish anchor.
3. multi: same upload, `Auto-detect`. Verify purification runs but mid-representation is still English.
4. Both detail pages: quality-assessment card appears, two scores, verdict.
5. Compare convergence rounds and final scores between the two.

### 9.3 Server-side smoke

After deploy: use the existing test command from project memory (`tests/test_productivity_stats_routes.py` pattern):
```
ssh -i C:/Users/admin/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 \
  'cd /opt/autovideosrt-test && git pull && systemctl restart autovideosrt-test && sleep 3 && \
   /opt/autovideosrt/venv/bin/python -m pytest tests/test_asr_clean.py tests/test_translation_quality.py \
     tests/test_omni_translate_routes.py tests/test_multi_translate_routes.py -q 2>&1 | tail -20'
```

---

## 10. Rollout & Risk

- All changes ride a single feature branch `feature/translation-pipeline-overhaul`. Worktree at `.worktrees/translation-pipeline-overhaul`.
- Commits split per concern: (a) infrastructure (table + use cases + asr_clean module + quality module), (b) omni runner changes, (c) multi runner changes, (d) UI changes, (e) tests. Each commit individually revertable.
- New table is additive; no destructive schema change.
- Quality-assessment service is on a separate code path; if it crashes, the main pipeline produces video as before.
- omni create modal gets a small banner reading: "实验通道：源语言直翻链路。问题反馈 → @owner。线上业务请用多语视频翻译。"
- Deploy step is the existing `git pull && systemctl restart` flow per project memory; migration auto-applies; no extra ops.
- If field testing shows omni's source-language-direct route is worse than multi's English-mid route (we expect the opposite), `feature` branch can be reverted and omni keeps its old behaviour. The shared infrastructure (purification + assessment) stays useful for multi alone.

---

## 11. Out-of-Scope Future Work

- Automatic prompt-tuning loop driven by aggregated assessment scores.
- Per-language-pair native prompt slots (e.g. dedicated `es → de` instead of going through English) — current design uses a single prompt with `source_language` injected; if dedicated prompts move the needle, build a slot table later.
- multi getting ASR engine routing (Doubao for zh/en, ElevenLabs Scribe for others). Requires a second design pass on multi's stability profile.
- A bulk admin tool to re-run quality assessment for date-range historical tasks. Manual single-task trigger covers ad-hoc needs first.
