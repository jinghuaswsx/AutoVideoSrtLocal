# Translation Pipeline Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add ASR same-language purification, source-language direct translate path for omni, and an async translation-quality assessment card shared by omni and multi.

**Architecture:** Shared infrastructure (purifier module + assessment module + DB table + LLM use cases) reused by both pipelines. omni gets a new `_step_asr_clean` replacing `_step_asr_normalize`, plus an overridden translate that skips the English mid-step. multi gets purification injected inside the existing `asr_normalize` step but keeps its English mid-representation. Both pipelines fire an async quality-assessment job after subtitle, non-blocking.

**Tech Stack:** Python 3.12 / Flask / Flask-Login / SocketIO / pymysql + DBUtils / pytest. LLM via `appcore.llm_client` only. Frontend: Jinja2 + vanilla JS, Ocean Blue design tokens.

**Reference spec:** `docs/superpowers/specs/2026-04-26-translation-pipeline-overhaul-design.md`

**Branch:** `feature/translation-pipeline-overhaul`
**Worktree:** `.worktrees/translation-pipeline-overhaul`

---

## File Structure

### New files
| Path | Responsibility |
|---|---|
| `db/migrations/2026_04_26_add_translation_quality_assessments.sql` | Create the assessment table |
| `pipeline/asr_clean.py` | Same-language ASR purification with primary + fallback model |
| `pipeline/translation_quality.py` | Quality assessment LLM call + verdict mapping |
| `web/services/quality_assessment.py` | Async background-thread runner that calls into `translation_quality` |
| `web/routes/translation_quality.py` | Flask blueprint exposing `GET/POST /api/{project_type}/<task_id>/quality-assessments[/run]` |
| `tests/test_asr_clean.py` | Unit tests for purifier validator + fallback flow |
| `tests/test_translation_quality.py` | Unit tests for verdict mapping + score arithmetic |
| `tests/test_quality_assessment_service.py` | Tests for trigger idempotency + status transitions |

### Modified files
| Path | Why |
|---|---|
| `appcore/llm_use_cases.py` | Add 3 use cases (`asr_clean.purify_primary`, `asr_clean.purify_fallback`, `translation_quality.assess`) |
| `appcore/llm_prompt_configs.py` | Add default prompt content for `asr_clean.purify` and `translation_quality.assess` slots |
| `appcore/runtime_omni.py` | Replace `_step_asr_normalize` with `_step_asr_clean`; override `_step_translate`; override rewrite-messages adapter |
| `appcore/runtime_multi.py` | Trigger async quality assessment at end of `_step_subtitle` |
| `appcore/runtime.py` | Trigger async quality assessment at end of base `_step_subtitle` (so legacy single-target runners get it too if they share that step path) |
| `pipeline/asr_normalize.py` | Inject same-language purification before `translate_to_en` (multi path) |
| `web/routes/omni_translate.py` | Extend `source_language` allow-list to 11 codes; update `RESUMABLE_STEPS` to add `asr_clean` while keeping `asr_normalize` for legacy resume |
| `web/routes/multi_translate.py` | Add `source_language` form field with same allow-list and `user_specified_source_language` plumbing |
| `web/templates/omni_translate_list.html` | Create-modal source-language `<select>` |
| `web/templates/multi_translate_list.html` | Create-modal source-language `<select>` |
| `web/templates/omni_translate_detail.html` | Quality-assessment card |
| `web/templates/multi_translate_detail.html` | Quality-assessment card |
| `web/app.py` | Register new `translation_quality` blueprint |
| `tests/test_omni_translate_routes.py` | Cover new source_language allow-list + manual rerun endpoint |
| `tests/test_multi_translate_routes.py` | Cover new source_language allow-list |

---

## Task 1: Database migration for translation_quality_assessments

**Files:**
- Create: `db/migrations/2026_04_26_add_translation_quality_assessments.sql`

- [ ] **Step 1: Write the migration file**

Create `db/migrations/2026_04_26_add_translation_quality_assessments.sql`:

```sql
CREATE TABLE IF NOT EXISTS translation_quality_assessments (
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

- [ ] **Step 2: Apply migration locally to verify SQL parses**

Run on the test server (per project memory, deploy migrations there first):

```bash
ssh -i C:/Users/admin/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 \
  'cd /opt/autovideosrt-test && git pull && systemctl restart autovideosrt-test && sleep 3 && \
   /opt/autovideosrt/venv/bin/python -c "
import sys; sys.path.insert(0, \"/opt/autovideosrt-test\")
from appcore.db import query
print(query(\"SHOW TABLES LIKE %s\", (\"translation_quality_assessments\",)))
print(query(\"DESCRIBE translation_quality_assessments\"))
"'
```

Expected: table created, all 23 columns listed.

- [ ] **Step 3: Commit**

```bash
git add db/migrations/2026_04_26_add_translation_quality_assessments.sql
git commit -m "feat(translation-quality): add assessments table migration"
```

---

## Task 2: LLM use case registration (3 entries)

**Files:**
- Modify: `appcore/llm_use_cases.py:41` (top of `USE_CASES` dict, after the `omni_translate.lid` entry)

- [ ] **Step 1: Add the three use case entries**

Locate `USE_CASES = { ... }` and after the `omni_translate.lid` entry add:

```python
    # ASR 同语言纯净化（omni 用于 _step_asr_clean，multi 用于 asr_normalize 前置）
    "asr_clean.purify_primary": _uc(
        "asr_clean.purify_primary",
        "asr_clean",
        "ASR 同语言纯净化（主路）",
        "Gemini Flash 主路：把 ASR 结果纯净化为同语言纯净文本，保留时间戳",
        "gemini_vertex",
        "gemini-3.1-flash-lite-preview",
        "gemini_vertex",
        "tokens",
    ),
    "asr_clean.purify_fallback": _uc(
        "asr_clean.purify_fallback",
        "asr_clean",
        "ASR 同语言纯净化（兜底）",
        "Claude Sonnet 兜底：主路校验失败时重跑同样 prompt",
        "openrouter",
        "anthropic/claude-sonnet-4.6",
        "openrouter",
        "tokens",
    ),
    # 翻译质量评估（subtitle 完成后异步触发，omni / multi 共用）
    "translation_quality.assess": _uc(
        "translation_quality.assess",
        "translation_quality",
        "翻译质量评估",
        "对比原始 ASR / 翻译文案 / 二次 ASR 字幕，给出翻译质量分 + TTS 还原度分",
        "gemini_vertex",
        "gemini-3.1-flash-lite-preview",
        "gemini_vertex",
        "tokens",
    ),
```

- [ ] **Step 2: Verify registry loads**

```bash
cd .worktrees/translation-pipeline-overhaul
python -c "from appcore.llm_use_cases import USE_CASES; \
print(USE_CASES['asr_clean.purify_primary']['default_model']); \
print(USE_CASES['asr_clean.purify_fallback']['default_model']); \
print(USE_CASES['translation_quality.assess']['default_model'])"
```

Expected output (3 lines):
```
gemini-3.1-flash-lite-preview
anthropic/claude-sonnet-4.6
gemini-3.1-flash-lite-preview
```

- [ ] **Step 3: Commit**

```bash
git add appcore/llm_use_cases.py
git commit -m "feat(translation-quality): register 3 LLM use cases (purify primary/fallback, assess)"
```

---

## Task 3: ASR purification module — validator and prompt template

**Files:**
- Create: `tests/test_asr_clean.py`
- Create: `pipeline/asr_clean.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_asr_clean.py`:

```python
"""Unit tests for ASR same-language purification."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from pipeline import asr_clean


SAMPLE_ES_UTTS = [
    {"index": 0, "start": 0.0, "end": 2.0, "text": "Hola amigos, today vamos a hablar de"},
    {"index": 1, "start": 2.0, "end": 4.0, "text": "este producto que es 太棒了 increíble"},
]


def _ok_response(items):
    import json
    return {"text": json.dumps({"utterances": items}), "usage": {"input_tokens": 1, "output_tokens": 1}}


def test_validator_accepts_clean_es_output():
    items = [
        {"index": 0, "text": "Hola amigos, hoy vamos a hablar de"},
        {"index": 1, "text": "este producto que es increíble"},
    ]
    errors = asr_clean._validate_against_input(items, SAMPLE_ES_UTTS, language="es")
    assert errors == []


def test_validator_rejects_length_mismatch():
    items = [{"index": 0, "text": "Hola"}]
    errors = asr_clean._validate_against_input(items, SAMPLE_ES_UTTS, language="es")
    assert any("length" in e for e in errors)


def test_validator_rejects_index_set_mismatch():
    items = [
        {"index": 0, "text": "Hola"},
        {"index": 5, "text": "increíble"},
    ]
    errors = asr_clean._validate_against_input(items, SAMPLE_ES_UTTS, language="es")
    assert any("index" in e for e in errors)


def test_validator_rejects_cjk_in_es():
    items = [
        {"index": 0, "text": "Hola amigos"},
        {"index": 1, "text": "这是一段中文 而不是西语"},  # contaminated
    ]
    errors = asr_clean._validate_against_input(items, SAMPLE_ES_UTTS, language="es")
    assert any("cjk" in e.lower() for e in errors)


def test_validator_accepts_cjk_in_zh():
    items = [
        {"index": 0, "text": "你好朋友们今天我们来聊"},
        {"index": 1, "text": "这个产品真的太棒了"},
    ]
    errors = asr_clean._validate_against_input(items, SAMPLE_ES_UTTS, language="zh")
    assert errors == []


def test_validator_rejects_empty_text():
    items = [
        {"index": 0, "text": "Hola"},
        {"index": 1, "text": ""},
    ]
    errors = asr_clean._validate_against_input(items, SAMPLE_ES_UTTS, language="es")
    assert any("empty" in e for e in errors)


def test_purify_primary_success_returns_cleaned():
    cleaned_items = [
        {"index": 0, "text": "Hola amigos, hoy vamos a hablar de"},
        {"index": 1, "text": "este producto que es increíble"},
    ]
    with patch("pipeline.asr_clean.llm_client.invoke_chat", return_value=_ok_response(cleaned_items)):
        result = asr_clean.purify_utterances(
            SAMPLE_ES_UTTS, language="es", task_id="t-1", user_id=1,
        )
    assert result["cleaned"] is True
    assert result["fallback_used"] is False
    assert result["utterances"][0]["text"] == "Hola amigos, hoy vamos a hablar de"
    assert result["utterances"][0]["start"] == 0.0  # timestamps preserved
    assert result["utterances"][0]["end"] == 2.0


def test_purify_falls_back_when_primary_invalid():
    bad = [{"index": 0, "text": "only one"}]  # length mismatch
    good = [
        {"index": 0, "text": "Hola amigos, hoy vamos a hablar de"},
        {"index": 1, "text": "este producto que es increíble"},
    ]
    responses = iter([_ok_response(bad), _ok_response(good)])
    with patch("pipeline.asr_clean.llm_client.invoke_chat", side_effect=lambda *a, **kw: next(responses)):
        result = asr_clean.purify_utterances(
            SAMPLE_ES_UTTS, language="es", task_id="t-2", user_id=1,
        )
    assert result["cleaned"] is True
    assert result["fallback_used"] is True


def test_purify_returns_uncleaned_when_both_fail():
    bad = [{"index": 0, "text": "only one"}]
    with patch("pipeline.asr_clean.llm_client.invoke_chat", return_value=_ok_response(bad)):
        result = asr_clean.purify_utterances(
            SAMPLE_ES_UTTS, language="es", task_id="t-3", user_id=1,
        )
    assert result["cleaned"] is False
    assert result["fallback_used"] is True
    assert result["utterances"] == SAMPLE_ES_UTTS  # original returned untouched
    assert result["validation_errors"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd .worktrees/translation-pipeline-overhaul
python -m pytest tests/test_asr_clean.py -v 2>&1 | tail -20
```

Expected: all tests fail with `ModuleNotFoundError: No module named 'pipeline.asr_clean'`.

- [ ] **Step 3: Implement `pipeline/asr_clean.py`**

Create `pipeline/asr_clean.py`:

```python
"""ASR same-language purification.

Given utterances in some source language, return a cleaned version in the same
language with: (1) spelling corrected, (2) words mis-recognized as another
language restored to the source, (3) timestamps preserved 1:1, (4) no fabrication.

Primary: Gemini Flash (cheap, fast).
Fallback: Claude Sonnet (slower, stronger language adherence).

Both go through llm_client; provider/model are owned by the use-case registry.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from appcore import llm_client

log = logging.getLogger(__name__)


# Iso-639-1 → human-friendly Chinese label, used in prompt only.
_LANG_LABEL: dict[str, str] = {
    "zh": "中文", "en": "English", "es": "español", "pt": "português",
    "fr": "français", "it": "italiano", "ja": "日本語", "de": "Deutsch",
    "nl": "Nederlands", "sv": "svenska", "fi": "suomi",
}

_CJK_RE = re.compile(r"[一-鿿]")
_KANA_RE = re.compile(r"[぀-ヿ]")
_LATIN_RE = re.compile(r"[A-Za-zÀ-ÿ]")


def _system_prompt(language: str) -> str:
    label = _LANG_LABEL.get(language, language)
    return (
        f"You are a {label} ASR proofreader. The JSON below is timestamped ASR "
        f"output from a short product video. It may contain spelling errors, "
        f"words mis-recognized as another language, or noise.\n\n"
        f"Rules:\n"
        f"1. Preserve every entry's index. Same count, same indexes, no merging, no splitting.\n"
        f"2. Fix obvious spelling errors. If a word is clearly recognized in a wrong "
        f"language, restore it to {label}. Brand names stay verbatim.\n"
        f"3. Do NOT paraphrase, expand, or add explanatory content.\n"
        f"4. If a segment is genuinely unintelligible, return its text unchanged. "
        f"Do NOT fabricate.\n"
        f"5. Output strict JSON only:\n"
        '   {"utterances": [{"index": 0, "text": "..."}, ...]}\n'
    )


def _response_format() -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "asr_clean_utterances",
            "schema": {
                "type": "object",
                "properties": {
                    "utterances": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "index": {"type": "integer"},
                                "text": {"type": "string"},
                            },
                            "required": ["index", "text"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["utterances"],
                "additionalProperties": False,
            },
        },
    }


def _validate_against_input(
    items: list[dict], original: list[dict], *, language: str,
) -> list[str]:
    """Return list of validation error strings; empty list = passed."""
    errors: list[str] = []
    if len(items) != len(original):
        errors.append(f"length mismatch: in={len(original)} out={len(items)}")
        return errors
    in_indexes = {int(u.get("index", i)) for i, u in enumerate(original)}
    out_indexes = {int(it.get("index", -1)) for it in items}
    if in_indexes != out_indexes:
        errors.append(f"index set mismatch: in={sorted(in_indexes)} out={sorted(out_indexes)}")
        return errors
    for it in items:
        text = (it.get("text") or "").strip()
        if not text:
            errors.append(f"empty text at index={it.get('index')}")
            continue
        # Per-language character-set heuristic
        has_cjk = bool(_CJK_RE.search(text))
        has_kana = bool(_KANA_RE.search(text))
        has_latin = bool(_LATIN_RE.search(text))
        if language == "zh":
            if not has_cjk:
                errors.append(f"zh text has no CJK at index={it.get('index')}: {text[:40]!r}")
        elif language == "ja":
            if not (has_cjk or has_kana):
                errors.append(f"ja text has no CJK/kana at index={it.get('index')}: {text[:40]!r}")
        elif language in {"es", "pt", "fr", "it", "de", "nl", "sv", "fi", "en"}:
            if has_cjk:
                errors.append(f"{language} text has CJK at index={it.get('index')}: {text[:40]!r}")
            if not has_latin:
                errors.append(f"{language} text has no latin chars at index={it.get('index')}: {text[:40]!r}")
    return errors


def _call(use_case_code: str, *, system: str, user_payload: dict,
          task_id: str, user_id: int | None) -> tuple[list[dict] | None, dict, str]:
    """Return (parsed items or None, usage, raw_text).

    None items = LLM error / non-JSON response.
    """
    try:
        result = llm_client.invoke_chat(
            use_case_code,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            response_format=_response_format(),
            temperature=0.0,
            max_tokens=4000,
            user_id=user_id,
            project_id=task_id,
        )
    except Exception:
        log.warning("[asr_clean] %s call raised", use_case_code, exc_info=True)
        return None, {}, ""
    raw = (result.get("text") or "").strip()
    try:
        payload = json.loads(raw)
        items = payload.get("utterances")
        if not isinstance(items, list):
            return None, result.get("usage") or {}, raw
        return items, result.get("usage") or {}, raw
    except Exception:
        log.warning("[asr_clean] %s returned non-JSON: %r", use_case_code, raw[:200])
        return None, result.get("usage") or {}, raw


def purify_utterances(
    utterances: list[dict],
    *,
    language: str,
    task_id: str,
    user_id: int | None,
) -> dict:
    """Same-language ASR purification with primary + fallback.

    Returns:
      {
        "utterances": cleaned list (same length & indexes) | original list when both fail,
        "cleaned": True if any model produced valid output,
        "fallback_used": True if primary failed and fallback was tried,
        "model_used": str,
        "raw_response_primary": str,
        "raw_response_fallback": str | None,
        "validation_errors": list of error strings (combined),
        "usage": {"primary": {...}, "fallback": {...}},
      }
    """
    user_payload = {
        "language": language,
        "utterances": [{"index": int(u.get("index", i)), "text": u.get("text", "")}
                       for i, u in enumerate(utterances)],
    }
    system = _system_prompt(language)

    all_errors: list[str] = []
    primary_items, primary_usage, primary_raw = _call(
        "asr_clean.purify_primary", system=system, user_payload=user_payload,
        task_id=task_id, user_id=user_id,
    )
    if primary_items is not None:
        errors = _validate_against_input(primary_items, utterances, language=language)
        if not errors:
            return {
                "utterances": _attach_timestamps(primary_items, utterances),
                "cleaned": True,
                "fallback_used": False,
                "model_used": "asr_clean.purify_primary",
                "raw_response_primary": primary_raw,
                "raw_response_fallback": None,
                "validation_errors": [],
                "usage": {"primary": primary_usage, "fallback": {}},
            }
        all_errors.extend(f"primary: {e}" for e in errors)
    else:
        all_errors.append("primary: model error or non-JSON")

    fallback_items, fallback_usage, fallback_raw = _call(
        "asr_clean.purify_fallback", system=system, user_payload=user_payload,
        task_id=task_id, user_id=user_id,
    )
    if fallback_items is not None:
        errors = _validate_against_input(fallback_items, utterances, language=language)
        if not errors:
            return {
                "utterances": _attach_timestamps(fallback_items, utterances),
                "cleaned": True,
                "fallback_used": True,
                "model_used": "asr_clean.purify_fallback",
                "raw_response_primary": primary_raw,
                "raw_response_fallback": fallback_raw,
                "validation_errors": all_errors,
                "usage": {"primary": primary_usage, "fallback": fallback_usage},
            }
        all_errors.extend(f"fallback: {e}" for e in errors)
    else:
        all_errors.append("fallback: model error or non-JSON")

    return {
        "utterances": utterances,  # untouched
        "cleaned": False,
        "fallback_used": True,
        "model_used": "none",
        "raw_response_primary": primary_raw,
        "raw_response_fallback": fallback_raw,
        "validation_errors": all_errors,
        "usage": {"primary": primary_usage, "fallback": fallback_usage},
    }


def _attach_timestamps(items: list[dict], original: list[dict]) -> list[dict]:
    """LLM only returns index+text; merge back start/end from original utterances."""
    by_index = {int(it["index"]): it["text"] for it in items}
    out: list[dict] = []
    for i, u in enumerate(original):
        idx = int(u.get("index", i))
        out.append({
            "index": idx,
            "start": u.get("start", u.get("start_time")),
            "end": u.get("end", u.get("end_time")),
            "text": by_index.get(idx, u.get("text", "")),
        })
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_asr_clean.py -v 2>&1 | tail -20
```

Expected: all 9 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_asr_clean.py pipeline/asr_clean.py
git commit -m "feat(asr-clean): same-language ASR purifier with primary+fallback"
```

---

## Task 4: Translation quality assessment module

**Files:**
- Create: `tests/test_translation_quality.py`
- Create: `pipeline/translation_quality.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_translation_quality.py`:

```python
"""Unit tests for translation quality assessment scoring + verdict mapping."""
from __future__ import annotations

import json
from unittest.mock import patch

from pipeline import translation_quality as tq


def _llm_response(payload):
    return {"text": json.dumps(payload), "usage": {"input_tokens": 1, "output_tokens": 1}}


def test_compute_score_arithmetic_mean():
    dims = {"semantic_fidelity": 80, "completeness": 90, "naturalness": 85}
    assert tq._compute_score(dims) == 85  # (80+90+85)/3 = 85


def test_compute_score_rounds_half_to_int():
    dims = {"a": 70, "b": 80, "c": 81}
    # (70+80+81)/3 = 77.0
    assert tq._compute_score(dims) == 77


def test_verdict_recommend_when_both_high():
    assert tq._verdict(85, 85) == "recommend"
    assert tq._verdict(100, 90) == "recommend"


def test_verdict_usable_when_both_above_70():
    assert tq._verdict(70, 80) == "usable_with_minor_issues"
    assert tq._verdict(84, 84) == "usable_with_minor_issues"


def test_verdict_needs_review_when_in_60_70():
    assert tq._verdict(65, 90) == "needs_review"
    assert tq._verdict(90, 60) == "needs_review"


def test_verdict_recommend_redo_when_below_60():
    assert tq._verdict(59, 90) == "recommend_redo"
    assert tq._verdict(90, 50) == "recommend_redo"


def test_verdict_boundary_85_85_recommend():
    assert tq._verdict(85, 85) == "recommend"


def test_verdict_boundary_84_85_usable():
    # one side below 85 → drops to usable
    assert tq._verdict(84, 85) == "usable_with_minor_issues"


def test_verdict_boundary_69_70():
    assert tq._verdict(69, 70) == "needs_review"
    assert tq._verdict(70, 70) == "usable_with_minor_issues"


def test_verdict_boundary_59_60():
    assert tq._verdict(59, 70) == "recommend_redo"
    assert tq._verdict(60, 70) == "needs_review"


def test_assess_returns_full_payload():
    response = {
        "translation_dimensions": {"semantic_fidelity": 90, "completeness": 85, "naturalness": 80},
        "tts_dimensions": {"text_recall": 95, "pronunciation_fidelity": 90, "rhythm_match": 85},
        "translation_issues": ["minor"],
        "translation_highlights": ["clear"],
        "tts_issues": [],
        "tts_highlights": ["smooth"],
        "verdict_reason": "good"
    }
    with patch("pipeline.translation_quality.llm_client.invoke_chat",
               return_value=_llm_response(response)):
        result = tq.assess(
            original_asr="Hola amigos",
            translation="Hi friends",
            tts_recognition="Hi friends here",
            source_language="es",
            target_language="en",
            task_id="t-1",
            user_id=1,
        )
    assert result["translation_score"] == 85  # (90+85+80)/3
    assert result["tts_score"] == 90          # (95+90+85)/3
    assert result["verdict"] == "recommend"
    assert result["translation_dimensions"]["semantic_fidelity"] == 90
    assert result["raw_response"] is not None


def test_assess_raises_on_malformed_response():
    with patch("pipeline.translation_quality.llm_client.invoke_chat",
               return_value=_llm_response({"foo": "bar"})):
        try:
            tq.assess(
                original_asr="x", translation="y", tts_recognition="z",
                source_language="es", target_language="en",
                task_id="t-2", user_id=1,
            )
            assert False, "expected exception"
        except tq.AssessmentResponseInvalidError:
            pass
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
python -m pytest tests/test_translation_quality.py -v 2>&1 | tail -20
```

Expected: import error / module not found.

- [ ] **Step 3: Implement `pipeline/translation_quality.py`**

Create `pipeline/translation_quality.py`:

```python
"""Translation quality assessment via Gemini 3 Flash.

Compares (original ASR, target-language translation, target-language second-pass
ASR) and produces two scores 0-100 plus a verdict.

Output schema is strict; malformed responses raise AssessmentResponseInvalidError.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from appcore import llm_client

log = logging.getLogger(__name__)


_LANG_LABEL: dict[str, str] = {
    "zh": "中文", "en": "English", "es": "español", "pt": "português",
    "fr": "français", "it": "italiano", "ja": "日本語", "de": "Deutsch",
    "nl": "Nederlands", "sv": "svenska", "fi": "suomi",
}


class AssessmentResponseInvalidError(RuntimeError):
    """LLM returned a payload that doesn't match the expected schema."""


def _system_prompt() -> str:
    return (
        "You are a short-form video translation quality assessor.\n\n"
        "You will receive three texts:\n"
        "1. ORIGINAL_ASR (source language): real content the original video says\n"
        "2. TRANSLATION (target language): LLM-written script\n"
        "3. TTS_RECOGNITION (target language): the TTS-generated audio re-transcribed\n\n"
        "Score TWO dimensions, each subscore 0-100:\n\n"
        "[TRANSLATION_SCORE] compares ORIGINAL_ASR vs TRANSLATION:\n"
        "  - semantic_fidelity: did the translation capture the source video meaning, no hallucinations?\n"
        "  - completeness: are key selling points / information preserved?\n"
        "  - naturalness: does the target language read naturally and conversationally?\n\n"
        "[TTS_SCORE] compares TRANSLATION vs TTS_RECOGNITION:\n"
        "  - text_recall: did the TTS faithfully recite the script?\n"
        "  - pronunciation_fidelity: are key product/brand terms pronounced correctly?\n"
        "  - rhythm_match: are pauses and segmentation reasonable?\n\n"
        "Provide up to 3 short issue strings and up to 3 short highlight strings per dimension. "
        "verdict_reason should be one short sentence in Chinese explaining the worst-scoring dimension."
    )


def _response_format() -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "translation_quality_assessment",
            "schema": {
                "type": "object",
                "properties": {
                    "translation_dimensions": {
                        "type": "object",
                        "properties": {
                            "semantic_fidelity": {"type": "integer", "minimum": 0, "maximum": 100},
                            "completeness":      {"type": "integer", "minimum": 0, "maximum": 100},
                            "naturalness":       {"type": "integer", "minimum": 0, "maximum": 100},
                        },
                        "required": ["semantic_fidelity", "completeness", "naturalness"],
                        "additionalProperties": False,
                    },
                    "tts_dimensions": {
                        "type": "object",
                        "properties": {
                            "text_recall":             {"type": "integer", "minimum": 0, "maximum": 100},
                            "pronunciation_fidelity":  {"type": "integer", "minimum": 0, "maximum": 100},
                            "rhythm_match":            {"type": "integer", "minimum": 0, "maximum": 100},
                        },
                        "required": ["text_recall", "pronunciation_fidelity", "rhythm_match"],
                        "additionalProperties": False,
                    },
                    "translation_issues":      {"type": "array", "items": {"type": "string"}},
                    "translation_highlights":  {"type": "array", "items": {"type": "string"}},
                    "tts_issues":              {"type": "array", "items": {"type": "string"}},
                    "tts_highlights":          {"type": "array", "items": {"type": "string"}},
                    "verdict_reason":          {"type": "string"},
                },
                "required": [
                    "translation_dimensions", "tts_dimensions",
                    "translation_issues", "translation_highlights",
                    "tts_issues", "tts_highlights", "verdict_reason",
                ],
                "additionalProperties": False,
            },
        },
    }


def _compute_score(dims: dict[str, int]) -> int:
    if not dims:
        return 0
    return int(round(sum(int(v) for v in dims.values()) / len(dims)))


def _verdict(translation_score: int, tts_score: int) -> str:
    if translation_score >= 85 and tts_score >= 85:
        return "recommend"
    if translation_score >= 70 and tts_score >= 70:
        return "usable_with_minor_issues"
    if translation_score < 60 or tts_score < 60:
        return "recommend_redo"
    return "needs_review"


def assess(
    *,
    original_asr: str,
    translation: str,
    tts_recognition: str,
    source_language: str,
    target_language: str,
    task_id: str,
    user_id: int | None,
) -> dict[str, Any]:
    t0 = time.monotonic()
    src_label = _LANG_LABEL.get(source_language, source_language)
    tgt_label = _LANG_LABEL.get(target_language, target_language)
    user_payload = (
        f"ORIGINAL_ASR ({src_label}, may contain ASR artifacts):\n{original_asr}\n\n"
        f"TRANSLATION ({tgt_label}):\n{translation}\n\n"
        f"TTS_RECOGNITION ({tgt_label}, second-pass ASR of generated audio):\n{tts_recognition}\n"
    )

    try:
        result = llm_client.invoke_chat(
            "translation_quality.assess",
            messages=[
                {"role": "system", "content": _system_prompt()},
                {"role": "user",   "content": user_payload},
            ],
            response_format=_response_format(),
            temperature=0.0,
            max_tokens=1500,
            user_id=user_id,
            project_id=task_id,
        )
    except Exception as exc:
        raise AssessmentResponseInvalidError(f"LLM call failed: {exc}") from exc

    raw_text = (result.get("text") or "").strip()
    try:
        payload = json.loads(raw_text)
    except Exception as exc:
        raise AssessmentResponseInvalidError(f"non-JSON: {raw_text[:200]!r}") from exc
    if not isinstance(payload, dict):
        raise AssessmentResponseInvalidError("response is not an object")

    for required in ("translation_dimensions", "tts_dimensions"):
        if required not in payload or not isinstance(payload[required], dict):
            raise AssessmentResponseInvalidError(f"missing or invalid {required}")

    translation_score = _compute_score(payload["translation_dimensions"])
    tts_score = _compute_score(payload["tts_dimensions"])
    verdict = _verdict(translation_score, tts_score)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    return {
        "translation_score": translation_score,
        "tts_score": tts_score,
        "translation_dimensions": payload["translation_dimensions"],
        "tts_dimensions": payload["tts_dimensions"],
        "translation_issues":     payload.get("translation_issues") or [],
        "translation_highlights": payload.get("translation_highlights") or [],
        "tts_issues":             payload.get("tts_issues") or [],
        "tts_highlights":         payload.get("tts_highlights") or [],
        "verdict": verdict,
        "verdict_reason": payload.get("verdict_reason") or "",
        "raw_response": payload,
        "usage": result.get("usage") or {},
        "elapsed_ms": elapsed_ms,
    }
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
python -m pytest tests/test_translation_quality.py -v 2>&1 | tail -20
```

Expected: 12 tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_translation_quality.py pipeline/translation_quality.py
git commit -m "feat(translation-quality): assess module with strict schema + verdict mapping"
```

---

## Task 5: Async assessment service (DB row + background thread)

**Files:**
- Create: `tests/test_quality_assessment_service.py`
- Create: `web/services/quality_assessment.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_quality_assessment_service.py`:

```python
"""Tests for the async quality-assessment service."""
from __future__ import annotations

import time
from unittest.mock import patch, MagicMock

from web.services import quality_assessment as svc


def _fake_assessment_result():
    return {
        "translation_score": 88,
        "tts_score": 92,
        "translation_dimensions": {"semantic_fidelity": 90, "completeness": 88, "naturalness": 86},
        "tts_dimensions": {"text_recall": 95, "pronunciation_fidelity": 90, "rhythm_match": 91},
        "translation_issues": [],
        "translation_highlights": ["clear"],
        "tts_issues": [],
        "tts_highlights": ["smooth"],
        "verdict": "recommend",
        "verdict_reason": "high scores",
        "raw_response": {},
        "usage": {},
        "elapsed_ms": 1234,
    }


def test_build_inputs_extracts_three_texts():
    task = {
        "utterances": [{"text": "hola amigos"}, {"text": "que tal"}],
        "localized_translation": {"full_text": "hi friends, what's up"},
        "english_asr_result": {"full_text": "hi friends what's up here"},
        "source_language": "es",
        "target_lang": "en",
    }
    inputs = svc._build_inputs(task)
    assert inputs["original_asr"] == "hola amigos que tal"
    assert inputs["translation"] == "hi friends, what's up"
    assert inputs["tts_recognition"] == "hi friends what's up here"
    assert inputs["source_language"] == "es"
    assert inputs["target_language"] == "en"


def test_build_inputs_handles_missing_full_text():
    task = {
        "utterances": [{"text": "hola"}],
        "localized_translation": {"sentences": [{"text": "hi"}, {"text": "world"}]},
        "english_asr_result": {"utterances": [{"text": "hi"}, {"text": "world"}]},
        "source_language": "es",
        "target_lang": "en",
    }
    inputs = svc._build_inputs(task)
    assert inputs["translation"] == "hi world"
    assert inputs["tts_recognition"] == "hi world"


def test_trigger_inserts_pending_row(db_clean):
    with patch("web.services.quality_assessment._run_assessment_job"):
        run_id = svc.trigger_assessment(
            task_id="task-x", project_type="omni_translate",
            triggered_by="auto", user_id=1, run_in_thread=False,
        )
    assert run_id == 1
    row = db_clean.query_one(
        "SELECT status, triggered_by FROM translation_quality_assessments WHERE task_id=%s",
        ("task-x",),
    )
    assert row["status"] == "pending"
    assert row["triggered_by"] == "auto"


def test_second_trigger_when_first_pending_returns_409(db_clean):
    with patch("web.services.quality_assessment._run_assessment_job"):
        first = svc.trigger_assessment(
            task_id="task-y", project_type="omni_translate",
            triggered_by="auto", user_id=1, run_in_thread=False,
        )
        try:
            svc.trigger_assessment(
                task_id="task-y", project_type="omni_translate",
                triggered_by="manual", user_id=1, run_in_thread=False,
            )
            assert False, "expected error"
        except svc.AssessmentInProgressError as exc:
            assert exc.run_id == first


def test_run_assessment_writes_done_row(db_clean):
    db_clean.execute(
        "INSERT INTO translation_quality_assessments "
        "(task_id, project_type, run_id, model, status) "
        "VALUES (%s, %s, %s, %s, %s)",
        ("task-z", "omni_translate", 1, "gemini-3.1-flash-lite-preview", "pending"),
    )
    fake_task = {
        "utterances": [{"text": "hola"}],
        "localized_translation": {"full_text": "hi"},
        "english_asr_result": {"full_text": "hi"},
        "source_language": "es",
        "target_lang": "en",
    }
    with patch("appcore.task_state.get", return_value=fake_task), \
         patch("pipeline.translation_quality.assess", return_value=_fake_assessment_result()):
        svc._run_assessment_job(task_id="task-z", project_type="omni_translate", run_id=1, user_id=1)
    row = db_clean.query_one(
        "SELECT status, translation_score, tts_score, verdict FROM translation_quality_assessments "
        "WHERE task_id=%s AND run_id=%s",
        ("task-z", 1),
    )
    assert row["status"] == "done"
    assert row["translation_score"] == 88
    assert row["tts_score"] == 92
    assert row["verdict"] == "recommend"


def test_run_assessment_writes_failed_row_on_exception(db_clean):
    db_clean.execute(
        "INSERT INTO translation_quality_assessments "
        "(task_id, project_type, run_id, model, status) "
        "VALUES (%s, %s, %s, %s, %s)",
        ("task-fail", "omni_translate", 1, "gemini-3.1-flash-lite-preview", "pending"),
    )
    fake_task = {
        "utterances": [{"text": "hola"}],
        "localized_translation": {"full_text": "hi"},
        "english_asr_result": {"full_text": "hi"},
        "source_language": "es", "target_lang": "en",
    }
    with patch("appcore.task_state.get", return_value=fake_task), \
         patch("pipeline.translation_quality.assess", side_effect=RuntimeError("boom")):
        svc._run_assessment_job(task_id="task-fail", project_type="omni_translate", run_id=1, user_id=1)
    row = db_clean.query_one(
        "SELECT status, error_text FROM translation_quality_assessments "
        "WHERE task_id=%s AND run_id=%s",
        ("task-fail", 1),
    )
    assert row["status"] == "failed"
    assert "boom" in (row["error_text"] or "")
```

(`db_clean` fixture: a thin wrapper around `appcore.db.query` / `query_one` / `execute` that truncates `translation_quality_assessments` at start. If the project doesn't already define such a fixture in `tests/conftest.py`, add one in this task's Step 3.)

- [ ] **Step 2: Run tests, verify they fail**

```bash
python -m pytest tests/test_quality_assessment_service.py -v 2>&1 | tail -20
```

Expected: import errors (`web.services.quality_assessment` not found, `db_clean` fixture not found).

- [ ] **Step 3: Add `db_clean` fixture to `tests/conftest.py`**

If `tests/conftest.py` doesn't already have a `db_clean` fixture, append this:

```python
import pytest
from appcore import db as _db


class _DBHelper:
    def query(self, sql, args=None): return _db.query(sql, args)
    def query_one(self, sql, args=None): return _db.query_one(sql, args)
    def execute(self, sql, args=None): return _db.execute(sql, args)


@pytest.fixture
def db_clean():
    helper = _DBHelper()
    helper.execute("DELETE FROM translation_quality_assessments WHERE task_id LIKE 'task-%'")
    yield helper
    helper.execute("DELETE FROM translation_quality_assessments WHERE task_id LIKE 'task-%'")
```

- [ ] **Step 4: Implement `web/services/quality_assessment.py`**

Create `web/services/quality_assessment.py`:

```python
"""Async translation-quality assessment service.

Triggered at the end of `_step_subtitle`. Inserts a `pending` row, then either
runs the LLM call inline (tests) or in a background thread (production).
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Any

from appcore import task_state
from appcore.db import execute as db_execute, query_one as db_query_one
from pipeline import translation_quality

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "gemini-3.1-flash-lite-preview"


class AssessmentInProgressError(RuntimeError):
    def __init__(self, run_id: int):
        super().__init__(f"assessment in progress (run_id={run_id})")
        self.run_id = run_id


def _build_inputs(task: dict) -> dict:
    """Extract the three texts the assessor needs."""
    utterances = task.get("utterances") or []
    original_asr = " ".join(
        (u.get("text") or "").strip() for u in utterances if u.get("text")
    ).strip()

    loc = task.get("localized_translation") or {}
    translation = (loc.get("full_text") or "").strip()
    if not translation:
        sentences = loc.get("sentences") or []
        translation = " ".join(
            (s.get("text") or "").strip() for s in sentences if s.get("text")
        ).strip()

    asr2 = task.get("english_asr_result") or {}
    tts_recognition = (asr2.get("full_text") or "").strip()
    if not tts_recognition:
        utts = asr2.get("utterances") or []
        tts_recognition = " ".join(
            (u.get("text") or "").strip() for u in utts if u.get("text")
        ).strip()

    return {
        "original_asr": original_asr,
        "translation": translation,
        "tts_recognition": tts_recognition,
        "source_language": task.get("source_language") or "",
        "target_language": task.get("target_lang") or "",
    }


def _next_run_id(task_id: str) -> int:
    row = db_query_one(
        "SELECT MAX(run_id) AS max_run FROM translation_quality_assessments WHERE task_id=%s",
        (task_id,),
    )
    return (row["max_run"] or 0) + 1 if row else 1


def trigger_assessment(
    *,
    task_id: str,
    project_type: str,
    triggered_by: str = "auto",
    user_id: int | None,
    run_in_thread: bool = True,
) -> int:
    """Insert a pending row + spawn worker. Returns the run_id."""
    existing = db_query_one(
        "SELECT run_id FROM translation_quality_assessments "
        "WHERE task_id=%s AND status IN ('pending', 'running')",
        (task_id,),
    )
    if existing:
        raise AssessmentInProgressError(existing["run_id"])

    run_id = _next_run_id(task_id)
    db_execute(
        "INSERT INTO translation_quality_assessments "
        "(task_id, project_type, run_id, model, status, triggered_by, triggered_by_user_id) "
        "VALUES (%s, %s, %s, %s, 'pending', %s, %s)",
        (task_id, project_type, run_id, _DEFAULT_MODEL, triggered_by, user_id),
    )

    if run_in_thread:
        threading.Thread(
            target=_run_assessment_job,
            kwargs={
                "task_id": task_id, "project_type": project_type,
                "run_id": run_id, "user_id": user_id,
            },
            daemon=True,
        ).start()
    else:
        # tests path: caller decides whether to drive _run_assessment_job
        pass
    return run_id


def _run_assessment_job(
    *, task_id: str, project_type: str, run_id: int, user_id: int | None,
) -> None:
    """Background worker: pull task state, call assessor, write result."""
    db_execute(
        "UPDATE translation_quality_assessments SET status='running' "
        "WHERE task_id=%s AND run_id=%s",
        (task_id, run_id),
    )
    try:
        task = task_state.get(task_id)
        if not task:
            raise RuntimeError(f"task {task_id} not found")
        inputs = _build_inputs(task)
        if not inputs["original_asr"] or not inputs["translation"]:
            raise RuntimeError("missing original_asr or translation")
        result = translation_quality.assess(
            original_asr=inputs["original_asr"],
            translation=inputs["translation"],
            tts_recognition=inputs["tts_recognition"],
            source_language=inputs["source_language"],
            target_language=inputs["target_language"],
            task_id=task_id, user_id=user_id,
        )
        db_execute(
            "UPDATE translation_quality_assessments SET "
            "  status='done', "
            "  translation_score=%s, tts_score=%s, "
            "  translation_dimensions=%s, tts_dimensions=%s, "
            "  verdict=%s, verdict_reason=%s, "
            "  translation_issues=%s, translation_highlights=%s, "
            "  tts_issues=%s, tts_highlights=%s, "
            "  prompt_input=%s, raw_response=%s, "
            "  elapsed_ms=%s, completed_at=NOW() "
            "WHERE task_id=%s AND run_id=%s",
            (
                result["translation_score"], result["tts_score"],
                json.dumps(result["translation_dimensions"]),
                json.dumps(result["tts_dimensions"]),
                result["verdict"], result["verdict_reason"],
                json.dumps(result["translation_issues"], ensure_ascii=False),
                json.dumps(result["translation_highlights"], ensure_ascii=False),
                json.dumps(result["tts_issues"], ensure_ascii=False),
                json.dumps(result["tts_highlights"], ensure_ascii=False),
                json.dumps(inputs, ensure_ascii=False),
                json.dumps(result["raw_response"], ensure_ascii=False),
                result["elapsed_ms"],
                task_id, run_id,
            ),
        )
        log.info("[quality-assessment] task=%s run=%d done verdict=%s",
                 task_id, run_id, result["verdict"])
    except Exception as exc:
        log.exception("[quality-assessment] task=%s run=%d failed", task_id, run_id)
        db_execute(
            "UPDATE translation_quality_assessments SET "
            "  status='failed', error_text=%s, completed_at=NOW() "
            "WHERE task_id=%s AND run_id=%s",
            (str(exc), task_id, run_id),
        )
```

- [ ] **Step 5: Run tests, verify they pass**

```bash
python -m pytest tests/test_quality_assessment_service.py -v 2>&1 | tail -20
```

Expected: 6 tests pass. (Tests that touch DB only run if `db_clean` is wired up; if local pytest can't reach DB, run on test server per project memory.)

- [ ] **Step 6: Commit**

```bash
git add tests/test_quality_assessment_service.py tests/conftest.py web/services/quality_assessment.py
git commit -m "feat(translation-quality): async assessment service with DB persistence"
```

---

## Task 6: Quality assessment API blueprint

**Files:**
- Create: `web/routes/translation_quality.py`
- Modify: `web/app.py` (register blueprint)

- [ ] **Step 1: Create blueprint module**

Create `web/routes/translation_quality.py`:

```python
"""Quality-assessment API: list + manual rerun.

Mounted twice under different URL prefixes (omni / multi) so each project type's
detail page hits its own URL family.
"""
from __future__ import annotations

import json
import logging

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from appcore.db import query as db_query, query_one as db_query_one
from web.services import quality_assessment as svc

log = logging.getLogger(__name__)

bp = Blueprint("translation_quality", __name__)


def _is_admin() -> bool:
    return getattr(current_user, "is_admin", False)


def _can_view(task_row: dict) -> bool:
    if not task_row:
        return False
    if _is_admin():
        return True
    return int(task_row.get("user_id") or 0) == int(getattr(current_user, "id", 0))


def _load_task(task_id: str, project_type: str) -> dict | None:
    return db_query_one(
        "SELECT id, user_id, type FROM projects WHERE id=%s AND type=%s AND deleted_at IS NULL",
        (task_id, project_type),
    )


def _row_to_dict(row: dict) -> dict:
    out = dict(row)
    for col in ("translation_dimensions", "tts_dimensions",
                "translation_issues", "translation_highlights",
                "tts_issues", "tts_highlights",
                "prompt_input", "raw_response"):
        v = out.get(col)
        if isinstance(v, str) and v:
            try:
                out[col] = json.loads(v)
            except Exception:
                pass
    for col in ("created_at", "completed_at"):
        if out.get(col):
            out[col] = out[col].isoformat() if hasattr(out[col], "isoformat") else str(out[col])
    return out


def _list_route(project_type: str):
    def view(task_id):
        task_row = _load_task(task_id, project_type)
        if not _can_view(task_row):
            return jsonify({"error": "Task not found"}), 404
        rows = db_query(
            "SELECT * FROM translation_quality_assessments "
            "WHERE task_id=%s ORDER BY run_id DESC",
            (task_id,),
        )
        return jsonify({"assessments": [_row_to_dict(r) for r in rows]})
    view.__name__ = f"list_assessments_{project_type}"
    return view


def _run_route(project_type: str):
    def view(task_id):
        if not _is_admin():
            return jsonify({"error": "admin only"}), 403
        task_row = _load_task(task_id, project_type)
        if not task_row:
            return jsonify({"error": "Task not found"}), 404
        try:
            run_id = svc.trigger_assessment(
                task_id=task_id, project_type=project_type,
                triggered_by="manual", user_id=current_user.id,
                run_in_thread=True,
            )
        except svc.AssessmentInProgressError as exc:
            return jsonify({"error": "assessment_in_progress", "run_id": exc.run_id}), 409
        return jsonify({"ok": True, "run_id": run_id})
    view.__name__ = f"run_assessment_{project_type}"
    return view


# Register both project type prefixes on the same blueprint
for project_type in ("omni_translate", "multi_translate"):
    url_prefix = "/api/omni-translate" if project_type == "omni_translate" else "/api/multi-translate"
    bp.add_url_rule(
        f"{url_prefix}/<task_id>/quality-assessments",
        view_func=login_required(_list_route(project_type)),
        methods=["GET"],
    )
    bp.add_url_rule(
        f"{url_prefix}/<task_id>/quality-assessments/run",
        view_func=login_required(_run_route(project_type)),
        methods=["POST"],
    )
```

- [ ] **Step 2: Register blueprint in `web/app.py`**

Find the section where other blueprints are registered (search for `app.register_blueprint`). Add:

```python
from web.routes.translation_quality import bp as translation_quality_bp
app.register_blueprint(translation_quality_bp)
```

Place it next to `omni_translate` blueprint registration to keep related code together.

- [ ] **Step 3: Smoke-test routes (server-side)**

```bash
ssh -i C:/Users/admin/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 \
  'cd /opt/autovideosrt-test && git pull && systemctl restart autovideosrt-test && sleep 3 && \
   /opt/autovideosrt/venv/bin/python -c "
from web.app import create_app
app = create_app()
print([r.rule for r in app.url_map.iter_rules() if \"quality-assessments\" in r.rule])
"'
```

Expected output (4 routes):
```
['/api/omni-translate/<task_id>/quality-assessments',
 '/api/omni-translate/<task_id>/quality-assessments/run',
 '/api/multi-translate/<task_id>/quality-assessments',
 '/api/multi-translate/<task_id>/quality-assessments/run']
```

- [ ] **Step 4: Commit**

```bash
git add web/routes/translation_quality.py web/app.py
git commit -m "feat(translation-quality): API blueprint for omni/multi assessment list+rerun"
```

---

## Task 7: Trigger async assessment from `_step_subtitle`

**Files:**
- Modify: `appcore/runtime_multi.py:338` (end of `_step_subtitle` method)
- Modify: `appcore/runtime.py` (find base `_step_subtitle` and add identical hook at end)

- [ ] **Step 1: Add trigger after subtitle done in runtime_multi**

In `appcore/runtime_multi.py`, locate the `_step_subtitle` method. After the line `self._set_step(task_id, "subtitle", "done", f"{lang.upper()} 字幕生成完成")`, add:

```python
        # Fire-and-forget translation-quality assessment. Failures don't block compose.
        try:
            from web.services import quality_assessment as _qa
            _qa.trigger_assessment(
                task_id=task_id, project_type=self.project_type,
                triggered_by="auto", user_id=self.user_id,
            )
        except _qa.AssessmentInProgressError:
            log.info("[%s] quality assessment already running for task %s", self.project_type, task_id)
        except Exception:
            log.warning("[%s] failed to trigger quality assessment for task %s",
                        self.project_type, task_id, exc_info=True)
```

Verify `import logging` and `log = logging.getLogger(__name__)` exist at top of file (they do per current code).

- [ ] **Step 2: Add identical trigger in base runtime**

In `appcore/runtime.py`, find the base `_step_subtitle` method (search for `def _step_subtitle`). At the end of its successful path (right after the final `_set_step(... "done" ...)`), add the same try/except block as in Step 1.

This ensures legacy single-target runners (de_translate, fr_translate, ja_translate that use the parent class's subtitle path) also get assessment triggered.

> Note: only add the trigger inside paths where `subtitle` is being set to `done`. If there are early-exit branches (passthrough), do not trigger.

- [ ] **Step 3: Smoke test**

```bash
ssh -i C:/Users/admin/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 \
  'cd /opt/autovideosrt-test && git pull && systemctl restart autovideosrt-test && sleep 3 && \
   /opt/autovideosrt/venv/bin/python -m pytest tests/test_omni_translate_routes.py tests/test_multi_translate_routes.py -q 2>&1 | tail -15'
```

Expected: existing tests still pass; no regression.

- [ ] **Step 4: Commit**

```bash
git add appcore/runtime_multi.py appcore/runtime.py
git commit -m "feat(translation-quality): trigger async assessment after subtitle"
```

---

## Task 8: omni — extend source_language allow-list to 11 codes

**Files:**
- Modify: `web/routes/omni_translate.py:285` (the `if raw_source_language not in ...` allow-list)
- Modify: `pipeline/asr_normalize.py:318` (`_USER_SPECIFIED_ROUTES` dict)
- Modify: `tests/test_omni_translate_routes.py` (add coverage)

- [ ] **Step 1: Extend allow-list and routes**

In `web/routes/omni_translate.py`, find:

```python
if raw_source_language not in ("", "zh", "en", "es", "pt"):
    return jsonify({"error": "source_language must be one of '', 'zh', 'en', 'es', 'pt'"}), 400
```

Replace both lines with:

```python
ALLOWED_SOURCE_LANGUAGES = ("", "zh", "en", "es", "pt", "fr", "it", "ja", "de", "nl", "sv", "fi")
if raw_source_language not in ALLOWED_SOURCE_LANGUAGES:
    return jsonify({"error": f"source_language must be one of {list(ALLOWED_SOURCE_LANGUAGES)}"}), 400
```

Define `ALLOWED_SOURCE_LANGUAGES` at module level (right after the existing `SUPPORTED_LANGS` constant, around line 33).

Search the whole file for any other place that hard-codes the same 4-tuple and replace with the constant. There's another such check in `update_source_language` route (around line 423); update that too.

- [ ] **Step 2: Extend `_USER_SPECIFIED_ROUTES` in pipeline/asr_normalize.py**

Replace:

```python
_USER_SPECIFIED_ROUTES: dict[str, str] = {
    "zh": "zh_skip",
    "en": "en_skip",
    "es": "es_specialized",
    "pt": "generic_fallback",
}
```

with:

```python
_USER_SPECIFIED_ROUTES: dict[str, str] = {
    "zh": "zh_skip",
    "en": "en_skip",
    "es": "es_specialized",
    "pt": "generic_fallback",
    "fr": "generic_fallback",
    "it": "generic_fallback",
    "ja": "generic_fallback",
    "de": "generic_fallback",
    "nl": "generic_fallback",
    "sv": "generic_fallback",
    "fi": "generic_fallback",
}
```

- [ ] **Step 3: Add test coverage**

In `tests/test_omni_translate_routes.py`, find an existing test that calls `/api/omni-translate/start`. Add a new parametrized test:

```python
import pytest

@pytest.mark.parametrize("lang", ["fr", "it", "ja", "de", "nl", "sv", "fi"])
def test_upload_accepts_extended_source_languages(client, login_user, lang, tiny_video_file):
    """source_language=fr/it/ja/de/nl/sv/fi must not be rejected."""
    resp = client.post(
        "/api/omni-translate/start",
        data={
            "video": (tiny_video_file, "test.mp4"),
            "target_lang": "en",
            "source_language": lang,
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201, resp.get_json()


def test_upload_rejects_unsupported_source_language(client, login_user, tiny_video_file):
    resp = client.post(
        "/api/omni-translate/start",
        data={
            "video": (tiny_video_file, "test.mp4"),
            "target_lang": "en",
            "source_language": "ru",  # Russian not supported
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert "source_language" in resp.get_json()["error"]
```

The `client`, `login_user`, `tiny_video_file` fixtures are assumed to exist in this test module. If they don't, copy them from a similar existing test file (e.g. `tests/test_multi_translate_routes.py`).

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_omni_translate_routes.py -v 2>&1 | tail -20
```

Expected: new tests pass.

- [ ] **Step 5: Commit**

```bash
git add web/routes/omni_translate.py pipeline/asr_normalize.py tests/test_omni_translate_routes.py
git commit -m "feat(omni): extend source_language allow-list to 11 codes"
```

---

## Task 9: omni — replace `_step_asr_normalize` with `_step_asr_clean`

**Files:**
- Modify: `appcore/runtime_omni.py` (add new method, change `_get_pipeline_steps` override path through parent)

- [ ] **Step 1: Add `_step_asr_clean` method to OmniTranslateRunner**

Open `appcore/runtime_omni.py`. After the existing `_step_asr` method, add:

```python
    def _step_asr_clean(self, task_id: str) -> None:
        """Same-language ASR purification (replaces asr_normalize for omni).

        Detect if needed, then purify utterances in their own language. Does
        NOT translate to English — downstream omni runs alignment / translate
        on source-language utterances directly.
        """
        from pipeline import asr_clean as _asr_clean

        task = task_state.get(task_id)
        utterances = task.get("utterances") or []
        if not utterances:
            self._set_step(task_id, "asr_clean", "done", "无音频文本，跳过纯净化")
            return

        # Resume idempotency: skip if already cleaned
        if task.get("utterances_raw"):  # set only after successful purify
            self._set_step(task_id, "asr_clean", "done", "已纯净化（resume 跳过）")
            return

        source_language = task.get("source_language", "zh")
        user_specified = bool(task.get("user_specified_source_language"))
        self._set_step(task_id, "asr_clean", "running",
                       f"正在纯净化 {source_language.upper()} ASR 文本…")

        result = _asr_clean.purify_utterances(
            utterances, language=source_language,
            task_id=task_id, user_id=self.user_id,
        )

        artifact = {
            "language": source_language,
            "user_specified": user_specified,
            "cleaned": result["cleaned"],
            "fallback_used": result["fallback_used"],
            "model_used": result["model_used"],
            "validation_errors": result["validation_errors"],
            "input_preview": " ".join(u.get("text", "") for u in utterances)[:200],
            "output_preview": " ".join(u.get("text", "") for u in result["utterances"])[:200],
        }
        task_state.set_artifact(task_id, "asr_clean", artifact)

        if result["cleaned"]:
            task_state.update(
                task_id,
                utterances=result["utterances"],
                utterances_raw=utterances,  # keep original for audit
            )
            msg = "ASR 同语言纯净化完成"
            if result["fallback_used"]:
                msg += "（兜底）"
            self._set_step(task_id, "asr_clean", "done", msg)
        else:
            # Both models failed. Keep original utterances and continue.
            log.warning("[asr_clean] task=%s purify failed: %s", task_id, result["validation_errors"])
            self._set_step(
                task_id, "asr_clean", "done",
                "ASR 纯净化未通过校验，保留原文本继续",
            )
```

- [ ] **Step 2: Override `_get_pipeline_steps` to insert asr_clean instead of asr_normalize**

Still in `appcore/runtime_omni.py`, the parent `MultiTranslateRunner._get_pipeline_steps` already adds `asr_normalize` after `asr`. Override it:

```python
    def _get_pipeline_steps(self, task_id: str, video_path: str, task_dir: str) -> list:
        """Replace parent's asr_normalize step with asr_clean (omni-specific)."""
        # Skip parent's MultiTranslateRunner._get_pipeline_steps and go to grandparent
        # PipelineRunner._get_pipeline_steps to get the base step list, then insert
        # asr_clean + voice_match ourselves.
        from appcore.runtime import PipelineRunner
        base_steps = PipelineRunner._get_pipeline_steps(self, task_id, video_path, task_dir)
        out = []
        for name, fn in base_steps:
            out.append((name, fn))
            if name == "asr":
                out.append(("asr_clean", lambda: self._step_asr_clean(task_id)))
                out.append(("voice_match", lambda: self._step_voice_match(task_id)))
        return out
```

- [ ] **Step 3: Update `web/routes/omni_translate.py` RESUMABLE_STEPS**

Find:

```python
RESUMABLE_STEPS = ["extract", "asr", "asr_normalize", "voice_match", "alignment", "translate", "tts", "subtitle", "compose", "export"]
```

Replace with:

```python
# Resumable step list. Includes both 'asr_clean' (new) and 'asr_normalize' (legacy)
# so historical tasks can still resume from their old artifacts.
RESUMABLE_STEPS = ["extract", "asr", "asr_clean", "asr_normalize", "voice_match",
                   "alignment", "translate", "tts", "subtitle", "compose", "export"]
```

Find the route `update_source_language` (around line 405). Its body currently writes `started = True` when seeing `s == "asr_normalize"`. Change that to `s == "asr_clean"`:

```python
    started = False
    for s in RESUMABLE_STEPS:
        if s == "asr_clean":
            started = True
        if started:
            store.set_step(task_id, s, "pending")
            store.set_step_message(task_id, s, "等待中...")

    omni_pipeline_runner.resume(task_id, "asr_clean", user_id=current_user.id)
```

(The `update_source_language` endpoint is omni-specific, so always restart from `asr_clean` when the user changes source language.)

- [ ] **Step 4: Smoke test pipeline-step list**

```bash
ssh -i C:/Users/admin/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 \
  'cd /opt/autovideosrt-test && git pull && systemctl restart autovideosrt-test && sleep 3 && \
   /opt/autovideosrt/venv/bin/python -c "
from appcore.runtime_omni import OmniTranslateRunner
from appcore.events import EventBus
runner = OmniTranslateRunner(bus=EventBus(), user_id=1)
steps = runner._get_pipeline_steps(\"x\", \"/tmp/x.mp4\", \"/tmp\")
print([s[0] for s in steps])
"'
```

Expected output (one line):
```
['extract', 'asr', 'asr_clean', 'voice_match', 'alignment', 'translate', 'tts', 'subtitle', 'compose', 'export']
```

- [ ] **Step 5: Commit**

```bash
git add appcore/runtime_omni.py web/routes/omni_translate.py
git commit -m "feat(omni): replace asr_normalize with asr_clean step (no English mid-translate)"
```

---

## Task 10: omni — override `_step_translate` to use source-language input

**Files:**
- Modify: `appcore/runtime_omni.py` (add `_step_translate` override)

- [ ] **Step 1: Override `_step_translate` to call source-language path**

In `appcore/runtime_omni.py`, append to `OmniTranslateRunner` class:

```python
    def _step_translate(self, task_id: str) -> None:
        """omni: translate directly from source-language transcript to target language.

        Differs from MultiTranslateRunner._step_translate in two ways:
        1. source_full_text is built from the source-language utterances/script_segments
           (multi reads utterances_en which omni no longer produces).
        2. The base_translation system prompt is augmented with INPUT NOTICE explaining
           that input may be ASR-noisy, to suppress fabrication.
        """
        import json as _json
        import os as _os

        from appcore.events import EVT_TRANSLATE_RESULT
        from appcore.runtime import (
            _build_review_segments,
            _llm_request_payload,
            _llm_response_payload,
            _log_translate_billing,
            _save_json,
            _resolve_translate_provider,
        )
        from pipeline.localization import build_source_full_text_zh, count_words
        from pipeline.translate import generate_localized_translation, get_model_display_name
        from web.preview_artifacts import build_asr_artifact, build_translate_artifact

        task = task_state.get(task_id)
        task_dir = task["task_dir"]
        if self._complete_original_video_passthrough(
            task_id, task.get("video_path") or "", task_dir,
        ):
            return
        lang = self._resolve_target_lang(task)
        source_language = task.get("source_language") or "zh"

        provider = _resolve_translate_provider(self.user_id)
        _model_tag = f"{provider} · {get_model_display_name(provider, self.user_id)}"
        self._set_step(task_id, "translate", "running",
                       f"正在从 {source_language.upper()} 直译为 {lang.upper()}...",
                       model_tag=_model_tag)

        script_segments = task.get("script_segments", []) or []
        # build_source_full_text_zh just joins script_segments[*].text — language-agnostic
        source_full_text = build_source_full_text_zh(script_segments)
        task_state.update(task_id, source_full_text_zh=source_full_text)
        _save_json(task_dir, "source_full_text.json", {"full_text": source_full_text,
                                                        "language": source_language})

        # Source-anchored system prompt: vanilla base_translation + INPUT NOTICE
        base_prompt = self._build_system_prompt(lang)
        notice = (
            f"\n\nINPUT NOTICE: The source script provided below is in "
            f"{source_language.upper()}. It came from automatic speech recognition "
            f"of the original video and may contain transcription artifacts. "
            f"Treat it as the source of truth for content; do NOT invent details "
            f"that are not implied by it. If a segment is unintelligible, keep "
            f"your version brief instead of fabricating context."
        )
        system_prompt = base_prompt + notice

        localized_translation = generate_localized_translation(
            source_full_text, script_segments, variant="normal",
            custom_system_prompt=system_prompt,
            provider=provider, user_id=self.user_id,
        )
        initial_messages = localized_translation.pop("_messages", None)
        if initial_messages:
            _save_json(task_dir, "localized_translate_messages.json", {
                "phase": "initial_translate",
                "source_language": source_language,
                "target_language": lang,
                "messages": initial_messages,
            })

        variants = dict(task.get("variants", {}))
        variant_state = dict(variants.get("normal", {}))
        variant_state["localized_translation"] = localized_translation
        variants["normal"] = variant_state
        _save_json(task_dir, "localized_translation.normal.json", localized_translation)

        review_segments = _build_review_segments(script_segments, localized_translation)
        requires_confirmation = bool(task.get("interactive_review"))
        task_state.update(
            task_id,
            source_full_text_zh=source_full_text,
            localized_translation=localized_translation,
            variants=variants,
            segments=review_segments,
            _segments_confirmed=not requires_confirmation,
        )
        task_state.set_artifact(task_id, "asr",
                                 build_asr_artifact(task.get("utterances", []),
                                                    source_full_text,
                                                    source_language=source_language))
        task_state.set_artifact(task_id, "translate",
                                 build_translate_artifact(source_full_text,
                                                          localized_translation,
                                                          source_language=source_language,
                                                          target_language=lang))
        _save_json(task_dir, "localized_translation.json", localized_translation)

        usage = localized_translation.get("_usage") or {}
        _log_translate_billing(
            user_id=self.user_id, project_id=task_id,
            use_case_code="video_translate.localize",
            provider=provider,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            success=True,
            request_payload=_llm_request_payload(
                localized_translation, provider, "video_translate.localize",
                messages=initial_messages,
            ),
            response_payload=_llm_response_payload(localized_translation),
        )

        if requires_confirmation:
            task_state.set_current_review_step(task_id, "translate")
            self._set_step(task_id, "translate", "waiting",
                           f"{lang.upper()} 翻译已生成，等待人工确认")
        else:
            task_state.set_current_review_step(task_id, "")
            self._set_step(task_id, "translate", "done",
                           f"{source_language.upper()} → {lang.upper()} 直译完成")

        self._emit(task_id, EVT_TRANSLATE_RESULT, {
            "source_full_text_zh": source_full_text,
            "localized_translation": localized_translation,
            "segments": review_segments,
            "requires_confirmation": requires_confirmation,
        })
```

- [ ] **Step 2: Smoke test (server-side dry call)**

```bash
ssh -i C:/Users/admin/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 \
  'cd /opt/autovideosrt-test && git pull && systemctl restart autovideosrt-test && sleep 3 && \
   /opt/autovideosrt/venv/bin/python -c "
from appcore.runtime_omni import OmniTranslateRunner
import inspect
print(\"_step_translate defined here:\", inspect.getsourcefile(OmniTranslateRunner._step_translate))
"'
```

Expected: prints path to `runtime_omni.py` (not the parent file), confirming the override is active.

- [ ] **Step 3: Commit**

```bash
git add appcore/runtime_omni.py
git commit -m "feat(omni): translate directly from source language (no English mid-step)"
```

---

## Task 11: omni — rewrite messages with original-ASR anchor

**Files:**
- Modify: `appcore/runtime_omni.py` (add `OmniLocalizationAdapter` and `_get_localization_module` override)
- Modify: `appcore/runtime_multi.py:74-119` only if `_PromptLocalizationAdapter` needs to expose hooks (verify; may need no change).

- [ ] **Step 1: Add `OmniLocalizationAdapter` subclass**

In `appcore/runtime_omni.py`, before the `OmniTranslateRunner` class definition, add:

```python
import json as _json_anchor
from appcore.llm_prompt_configs import resolve_prompt_config as _resolve_prompt_anchor
from appcore.runtime_multi import _PromptLocalizationAdapter as _BaseAdapter
from pipeline.localization import build_tts_segments as _build_tts_segments
from pipeline.localization import validate_tts_script as _validate_tts_script


class OmniLocalizationAdapter(_BaseAdapter):
    """omni-flavored adapter: rewrite messages carry the original ASR transcript."""

    def __init__(self, lang: str, source_language: str, original_asr_text: str):
        super().__init__(lang)
        self.source_language = source_language
        self.original_asr_text = original_asr_text
        self.__name__ = f"omni_translate.localization.{lang}"

    def build_localized_rewrite_messages(
        self,
        source_full_text: str,
        prev_localized_translation: dict,
        target_words: int,
        direction: str,
        source_language: str = "zh",
        feedback_notes: str | None = None,
    ) -> list[dict]:
        config = _resolve_prompt_anchor("base_rewrite", self.lang)
        prompt = config["content"].replace(
            "{target_words}", str(target_words)
        ).replace("{direction}", direction)

        src_label = {
            "zh": "Chinese", "en": "English", "es": "Spanish", "pt": "Portuguese",
            "fr": "French", "it": "Italian", "ja": "Japanese", "de": "German",
            "nl": "Dutch", "sv": "Swedish", "fi": "Finnish",
        }.get(self.source_language, self.source_language)

        user_content = (
            f"ORIGINAL VIDEO TRANSCRIPT ({src_label}, ground truth — what the video actually says):\n"
            f"{self.original_asr_text}\n\n"
            f"INITIAL LOCALIZATION (target language, written from the transcript above):\n"
            f"{_json_anchor.dumps(prev_localized_translation, ensure_ascii=False, indent=2)}\n\n"
            f"REWRITE TASK:\n"
            f"Rewrite the initial localization to {direction} to ~{target_words} words. "
            f"STAY ANCHORED in the original transcript. Do NOT fabricate details that "
            f"are not in the transcript above."
        )
        if feedback_notes:
            user_content += f"\n\n{feedback_notes}"

        return [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_content},
        ]
```

- [ ] **Step 2: Override `_get_localization_module` in OmniTranslateRunner**

In `OmniTranslateRunner`, append:

```python
    def _get_localization_module(self, task: dict):
        lang = self._resolve_target_lang(task)
        source_language = task.get("source_language") or "zh"
        utterances = task.get("utterances") or []
        original_asr_text = " ".join(
            (u.get("text") or "").strip() for u in utterances if u.get("text")
        ).strip()
        return OmniLocalizationAdapter(
            lang=lang,
            source_language=source_language,
            original_asr_text=original_asr_text,
        )
```

- [ ] **Step 3: Smoke test**

```bash
ssh -i C:/Users/admin/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 \
  'cd /opt/autovideosrt-test && git pull && systemctl restart autovideosrt-test && sleep 3 && \
   /opt/autovideosrt/venv/bin/python -c "
from appcore.runtime_omni import OmniLocalizationAdapter
adapter = OmniLocalizationAdapter(lang=\"en\", source_language=\"es\",
    original_asr_text=\"Hola amigos, hoy hablamos de\")
msgs = adapter.build_localized_rewrite_messages(
    source_full_text=\"Hola amigos\",
    prev_localized_translation={\"full_text\":\"Hi friends\"},
    target_words=80, direction=\"expand\", source_language=\"es\",
)
print(msgs[1][\"content\"][:200])
"'
```

Expected: user content starts with `"ORIGINAL VIDEO TRANSCRIPT (Spanish, ground truth"`.

- [ ] **Step 4: Commit**

```bash
git add appcore/runtime_omni.py
git commit -m "feat(omni): rewrite messages anchored to original ASR transcript"
```

---

## Task 12: multi — inject ASR purification inside `asr_normalize`

**Files:**
- Modify: `pipeline/asr_normalize.py:237-315` (`run_asr_normalize`) and `:326-388` (`run_user_specified`)

- [ ] **Step 1: Modify `run_asr_normalize` to insert purification before `translate_to_en`**

In `pipeline/asr_normalize.py`, find `run_asr_normalize`. After the `detect_result, detect_tokens = detect_language(...)` line, but before the route dispatch (`if lang == "other"`), insert:

```python
    # === Same-language ASR purification (multi keeps utterances_en mid-step) ===
    purify_artifact: dict[str, Any] = {"performed": False}
    if lang in {"zh", "en", "es", "pt", "fr", "it", "ja", "de", "nl", "sv", "fi"}:
        from pipeline import asr_clean as _asr_clean
        purify_result = _asr_clean.purify_utterances(
            utterances, language=lang, task_id=task_id, user_id=user_id,
        )
        purify_artifact = {
            "performed": True,
            "language": lang,
            "cleaned": purify_result["cleaned"],
            "fallback_used": purify_result["fallback_used"],
            "model_used": purify_result["model_used"],
            "validation_errors": purify_result["validation_errors"],
        }
        if purify_result["cleaned"]:
            utterances = purify_result["utterances"]
    # ===========================================================================
```

Now find where `artifact` is built (the big `artifact: dict[str, Any] = { ... }` literal). Add `"asr_clean": purify_artifact` to that dict alongside `"tokens"`.

- [ ] **Step 2: Mirror change inside `run_user_specified`**

In the same file, locate `run_user_specified`. After `route = _USER_SPECIFIED_ROUTES[source_language]`, but before `utterances_en: list[dict] | None = None`, insert the same purification block (with `lang = source_language` instead of `lang = detect_result["language"]`):

```python
    # === Same-language ASR purification ===
    purify_artifact: dict[str, Any] = {"performed": False}
    if source_language in {"zh", "en", "es", "pt", "fr", "it", "ja", "de", "nl", "sv", "fi"}:
        from pipeline import asr_clean as _asr_clean
        purify_result = _asr_clean.purify_utterances(
            utterances, language=source_language, task_id=task_id, user_id=user_id,
        )
        purify_artifact = {
            "performed": True,
            "language": source_language,
            "cleaned": purify_result["cleaned"],
            "fallback_used": purify_result["fallback_used"],
            "model_used": purify_result["model_used"],
            "validation_errors": purify_result["validation_errors"],
        }
        if purify_result["cleaned"]:
            utterances = purify_result["utterances"]
    # ======================================
```

Add `"asr_clean": purify_artifact` to its `artifact` dict literal as well.

- [ ] **Step 3: Add a test that mocks both LLM endpoints**

In `tests/test_asr_normalize.py` (create if missing — see existing tests for fixture patterns), add:

```python
"""Smoke test that ASR purification is invoked inside run_asr_normalize."""
from unittest.mock import patch

from pipeline import asr_normalize


def test_run_user_specified_calls_purify():
    utts = [{"index": 0, "start": 0, "end": 1, "text": "Hola"}]
    with patch("pipeline.asr_clean.purify_utterances",
               return_value={"utterances": utts, "cleaned": True,
                             "fallback_used": False, "model_used": "test",
                             "validation_errors": [], "raw_response_primary": "",
                             "raw_response_fallback": None,
                             "usage": {"primary": {}, "fallback": {}}}) as p, \
         patch("pipeline.asr_normalize.translate_to_en",
               return_value=([{"index": 0, "start": 0, "end": 1, "text": "Hello"}], {})):
        artifact = asr_normalize.run_user_specified(
            task_id="t-1", user_id=1, utterances=utts, source_language="es",
        )
    p.assert_called_once()
    assert artifact["asr_clean"]["performed"] is True
    assert artifact["asr_clean"]["cleaned"] is True
```

- [ ] **Step 4: Run test**

```bash
python -m pytest tests/test_asr_normalize.py -v 2>&1 | tail -20
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add pipeline/asr_normalize.py tests/test_asr_normalize.py
git commit -m "feat(multi): inject ASR purification before translate_to_en"
```

---

## Task 13: multi — add source_language to upload route

**Files:**
- Modify: `web/routes/multi_translate.py` (upload_and_start route)
- Modify: `tests/test_multi_translate_routes.py` (coverage)

- [ ] **Step 1: Add source_language form handling**

Open `web/routes/multi_translate.py`. Find the `upload_and_start` (or equivalent — check for `start` route that handles `request.files["video"]`). Mirror the structure of `web/routes/omni_translate.py.upload_and_start`:

```python
ALLOWED_SOURCE_LANGUAGES = ("", "zh", "en", "es", "pt", "fr", "it", "ja", "de", "nl", "sv", "fi")

# Inside upload_and_start:
raw_source_language = (request.form.get("source_language") or "").strip()
if raw_source_language not in ALLOWED_SOURCE_LANGUAGES:
    return jsonify({"error": f"source_language must be one of {list(ALLOWED_SOURCE_LANGUAGES)}"}), 400
user_specified_source_language = bool(raw_source_language)
source_language = raw_source_language or "zh"
```

Pass these into `store.update(task_id, ..., source_language=source_language, user_specified_source_language=user_specified_source_language)`.

If multi route has no upload-and-start equivalent (e.g. uses TOS bootstrap), apply the same field handling at whichever route creates the task record.

- [ ] **Step 2: Test coverage**

In `tests/test_multi_translate_routes.py`, add:

```python
@pytest.mark.parametrize("lang", ["es", "fr", "ja", "de"])
def test_multi_upload_accepts_source_language(client, login_user, lang, tiny_video_file):
    resp = client.post(
        "/api/multi-translate/start",
        data={
            "video": (tiny_video_file, "test.mp4"),
            "target_lang": "en",
            "source_language": lang,
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code in (201, 200), resp.get_json()


def test_multi_upload_rejects_unsupported_source_language(client, login_user, tiny_video_file):
    resp = client.post(
        "/api/multi-translate/start",
        data={
            "video": (tiny_video_file, "test.mp4"),
            "target_lang": "en",
            "source_language": "ru",
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
```

(Adjust route URL based on actual code — multi may use `/api/multi-translate/start` or similar.)

- [ ] **Step 3: Run tests**

```bash
python -m pytest tests/test_multi_translate_routes.py -v 2>&1 | tail -20
```

Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add web/routes/multi_translate.py tests/test_multi_translate_routes.py
git commit -m "feat(multi): add source_language form field"
```

---

## Task 14: Front-end — source-language `<select>` in create modals

**Files:**
- Modify: `web/templates/omni_translate_list.html` (create modal)
- Modify: `web/templates/multi_translate_list.html` (create modal)

- [ ] **Step 1: Add select to omni create modal**

Open `web/templates/omni_translate_list.html`. Find the create-modal form. After the existing `target_lang` `<select>`, insert:

```html
<div class="form-row">
  <label for="omni-source-language">源语言（可选）</label>
  <select id="omni-source-language" name="source_language" class="form-select">
    <option value="">自动检测</option>
    <option value="zh">中文</option>
    <option value="en">English</option>
    <option value="es">Español</option>
    <option value="pt">Português</option>
    <option value="fr">Français</option>
    <option value="it">Italiano</option>
    <option value="de">Deutsch</option>
    <option value="ja">日本語</option>
    <option value="nl">Nederlands</option>
    <option value="sv">Svenska</option>
    <option value="fi">Suomi</option>
  </select>
  <small class="form-hint">不确定时选「自动检测」即可。明确选择会跳过两层 LLM 检测，路由更精准。</small>
</div>
```

The form's submit handler (search the `<script>` block) already builds a FormData object that picks up all named fields, so no JS change should be needed. Verify by reading the existing handler.

Also add a one-line banner above the modal title:

```html
<div class="modal-banner experimental">
  实验通道：源语言直翻链路。问题反馈 → @owner。线上业务请用「多语视频翻译」。
</div>
```

(Style this in the existing CSS file matching `--warning-bg` token.)

- [ ] **Step 2: Add select to multi create modal**

Repeat in `web/templates/multi_translate_list.html`. Same `<select>` block (skip the experimental banner).

- [ ] **Step 3: Manual smoke test**

After deploy, open both list pages. Open the create modal. Verify:
- The new `<select>` exists with 12 options ("Auto-detect" first).
- omni shows the experimental banner; multi does not.
- Submitting without choosing (defaults to "Auto-detect") still creates the task successfully.
- Submitting with `Español` selected creates task with `source_language=es` in `state_json`.

(Verification on test server using existing browser session.)

- [ ] **Step 4: Commit**

```bash
git add web/templates/omni_translate_list.html web/templates/multi_translate_list.html
git commit -m "feat(ui): source-language select in create modals (omni + multi)"
```

---

## Task 15: Front-end — quality assessment card

**Files:**
- Modify: `web/templates/omni_translate_detail.html` (add card markup)
- Modify: `web/templates/multi_translate_detail.html` (add card markup)
- Create: `web/static/js/quality_assessment_card.js` (shared JS)
- Create: `web/static/css/quality_assessment_card.css` (shared CSS)

- [ ] **Step 1: Create shared JS module**

Create `web/static/js/quality_assessment_card.js`:

```javascript
// Translation Quality Assessment card.
// Initialise via: QualityAssessmentCard.init({ taskId, projectType, isAdmin })

window.QualityAssessmentCard = (function () {
  const VERDICT_CLASS = {
    recommend: "verdict-recommend",
    usable_with_minor_issues: "verdict-usable",
    needs_review: "verdict-needs-review",
    recommend_redo: "verdict-redo",
  };
  const VERDICT_LABEL = {
    recommend: "建议采用",
    usable_with_minor_issues: "可用 (有小瑕疵)",
    needs_review: "需要复核",
    recommend_redo: "建议重做",
  };

  function init({ taskId, projectType, isAdmin }) {
    const root = document.getElementById("quality-assessment-card");
    if (!root) return;
    root.dataset.taskId = taskId;
    root.dataset.projectType = projectType;
    root.dataset.isAdmin = isAdmin ? "1" : "0";
    refresh(root);
    setInterval(() => refresh(root), 8000);  // poll every 8s
    const btn = root.querySelector("[data-action='rerun']");
    if (btn) btn.addEventListener("click", () => triggerRun(root));
  }

  async function refresh(root) {
    const { taskId, projectType } = root.dataset;
    const apiBase = projectType === "omni_translate" ? "/api/omni-translate" : "/api/multi-translate";
    try {
      const resp = await fetch(`${apiBase}/${taskId}/quality-assessments`);
      if (!resp.ok) return;
      const data = await resp.json();
      render(root, data.assessments || []);
    } catch (err) { /* swallow */ }
  }

  async function triggerRun(root) {
    const { taskId, projectType } = root.dataset;
    const apiBase = projectType === "omni_translate" ? "/api/omni-translate" : "/api/multi-translate";
    const resp = await fetch(`${apiBase}/${taskId}/quality-assessments/run`, { method: "POST" });
    if (resp.status === 409) {
      alert("评估已经在跑");
      return;
    }
    if (!resp.ok) {
      alert("触发失败：" + (await resp.text()));
      return;
    }
    refresh(root);
  }

  function render(root, list) {
    const isAdmin = root.dataset.isAdmin === "1";
    const latest = list[0];
    const body = root.querySelector(".qa-body");
    if (!latest) {
      body.innerHTML = `<div class="qa-empty">尚无评估记录${isAdmin ? "（点「重跑」生成）" : ""}</div>`;
      return;
    }
    if (latest.status === "pending" || latest.status === "running") {
      body.innerHTML = `<div class="qa-loading">评估中… (run #${latest.run_id})</div>`;
      return;
    }
    if (latest.status === "failed") {
      body.innerHTML = `
        <div class="qa-failed">
          <div class="qa-error-text">评估失败：${escapeHtml(latest.error_text || "")}</div>
        </div>`;
      return;
    }
    const ts = latest.translation_score || 0;
    const ttsS = latest.tts_score || 0;
    const verdictClass = VERDICT_CLASS[latest.verdict] || "";
    const verdictText = VERDICT_LABEL[latest.verdict] || latest.verdict || "";
    body.innerHTML = `
      <div class="qa-scores">
        <div class="qa-ring qa-ring-translation" style="--score:${ts}">
          <div class="qa-ring-inner"><div class="qa-ring-num">${ts}</div><div class="qa-ring-label">翻译质量</div></div>
        </div>
        <div class="qa-ring qa-ring-tts" style="--score:${ttsS}">
          <div class="qa-ring-inner"><div class="qa-ring-num">${ttsS}</div><div class="qa-ring-label">TTS 还原度</div></div>
        </div>
      </div>
      <div class="qa-verdict ${verdictClass}">${verdictText}</div>
      <div class="qa-reason">${escapeHtml(latest.verdict_reason || "")}</div>
      ${renderDimensions("翻译细分", latest.translation_dimensions)}
      ${renderDimensions("TTS 细分", latest.tts_dimensions)}
      ${renderList("翻译问题", latest.translation_issues, "qa-issues")}
      ${renderList("翻译亮点", latest.translation_highlights, "qa-highlights")}
      ${renderList("TTS 问题", latest.tts_issues, "qa-issues")}
      ${renderList("TTS 亮点", latest.tts_highlights, "qa-highlights")}
      ${list.length > 1 ? `<div class="qa-history">历史评估 ${list.length} 次（最新 run #${latest.run_id}）</div>` : ""}
    `;
  }

  function renderDimensions(title, dims) {
    if (!dims || typeof dims !== "object") return "";
    const items = Object.entries(dims).map(([k, v]) =>
      `<li><span class="qa-dim-label">${k}</span><span class="qa-dim-bar"><span class="qa-dim-fill" style="width:${v}%"></span></span><span class="qa-dim-value">${v}</span></li>`
    ).join("");
    return `<div class="qa-dimensions"><div class="qa-dim-title">${title}</div><ul>${items}</ul></div>`;
  }

  function renderList(title, items, className) {
    if (!items || !items.length) return "";
    const lis = items.slice(0, 3).map(s => `<li>${escapeHtml(s)}</li>`).join("");
    return `<div class="${className}"><div class="qa-list-title">${title}</div><ul>${lis}</ul></div>`;
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[c]);
  }

  return { init };
})();
```

- [ ] **Step 2: Create shared CSS**

Create `web/static/css/quality_assessment_card.css`:

```css
/* Translation Quality Assessment card - Ocean Blue tokens */
.qa-card {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: var(--space-6);
  margin-top: var(--space-6);
}
.qa-card-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: var(--space-4); }
.qa-card-title { font-size: var(--text-md); font-weight: 600; color: var(--fg); }
.qa-rerun-btn {
  height: 28px; padding: 0 var(--space-3);
  border: 1px solid var(--border-strong); background: var(--bg);
  border-radius: var(--radius); font-size: var(--text-xs); cursor: pointer;
}
.qa-rerun-btn:hover { background: var(--bg-muted); }

.qa-empty, .qa-loading { color: var(--fg-muted); padding: var(--space-4); text-align: center; }
.qa-failed { color: var(--danger-fg); background: var(--danger-bg); padding: var(--space-4); border-radius: var(--radius-md); }

.qa-scores { display: flex; gap: var(--space-6); justify-content: center; margin: var(--space-4) 0; }
.qa-ring {
  width: 120px; height: 120px; border-radius: 50%;
  background: conic-gradient(var(--accent) calc(var(--score) * 1%), var(--bg-muted) 0);
  display: flex; align-items: center; justify-content: center;
}
.qa-ring-inner {
  width: 96px; height: 96px; border-radius: 50%;
  background: var(--bg);
  display: flex; flex-direction: column; align-items: center; justify-content: center;
}
.qa-ring-num { font-size: 28px; font-weight: 700; color: var(--fg); font-family: var(--font-mono); }
.qa-ring-label { font-size: var(--text-xs); color: var(--fg-muted); margin-top: 2px; }

.qa-verdict {
  display: inline-block; padding: 4px var(--space-3);
  border-radius: var(--radius-md); font-size: var(--text-sm);
  margin-top: var(--space-3);
}
.verdict-recommend { background: var(--success-bg); color: var(--success-fg); }
.verdict-usable    { background: var(--info-bg);    color: var(--info); }
.verdict-needs-review { background: var(--warning-bg); color: var(--warning-fg); }
.verdict-redo      { background: var(--danger-bg);  color: var(--danger-fg); }

.qa-reason { color: var(--fg-muted); font-size: var(--text-sm); margin-top: var(--space-2); }

.qa-dimensions { margin-top: var(--space-4); }
.qa-dim-title { font-size: var(--text-xs); color: var(--fg-muted); margin-bottom: var(--space-2); }
.qa-dimensions ul { list-style: none; padding: 0; margin: 0; }
.qa-dimensions li { display: grid; grid-template-columns: 140px 1fr 40px; align-items: center; gap: var(--space-2); padding: var(--space-1) 0; }
.qa-dim-label { font-size: var(--text-xs); color: var(--fg); }
.qa-dim-bar { height: 6px; background: var(--bg-muted); border-radius: var(--radius-full); overflow: hidden; }
.qa-dim-fill { display: block; height: 100%; background: var(--accent); border-radius: var(--radius-full); }
.qa-dim-value { font-size: var(--text-xs); color: var(--fg-muted); text-align: right; font-family: var(--font-mono); }

.qa-issues, .qa-highlights { margin-top: var(--space-3); }
.qa-list-title { font-size: var(--text-xs); color: var(--fg-muted); margin-bottom: var(--space-1); }
.qa-issues ul, .qa-highlights ul { padding-left: var(--space-5); margin: 0; font-size: var(--text-sm); color: var(--fg); }
.qa-history { margin-top: var(--space-4); font-size: var(--text-xs); color: var(--fg-subtle); }
```

- [ ] **Step 3: Insert card into omni detail template**

In `web/templates/omni_translate_detail.html`, near the bottom of the main detail column (before the closing main container), insert:

```html
<div id="quality-assessment-card" class="qa-card">
  <div class="qa-card-header">
    <div class="qa-card-title">翻译质量评估</div>
    {% if current_user.is_admin %}
    <button type="button" class="qa-rerun-btn" data-action="rerun">重跑评估</button>
    {% endif %}
  </div>
  <div class="qa-body"></div>
</div>
<link rel="stylesheet" href="{{ url_for('static', filename='css/quality_assessment_card.css') }}">
<script src="{{ url_for('static', filename='js/quality_assessment_card.js') }}"></script>
<script>
  QualityAssessmentCard.init({
    taskId: {{ project.id | tojson }},
    projectType: "omni_translate",
    isAdmin: {{ (current_user.is_admin | default(false)) | tojson }},
  });
</script>
```

- [ ] **Step 4: Insert card into multi detail template**

Same block inside `web/templates/multi_translate_detail.html`, with `projectType: "multi_translate"`.

- [ ] **Step 5: Manual smoke test (server)**

After deploy:
- Open a recent omni task detail page → card appears with "尚无评估记录" if no row yet.
- Open a task that just finished subtitle → card flips to "评估中" then to scores within ~10-20 s (Gemini Flash latency).
- Admin user sees "重跑评估" button; non-admin doesn't.

- [ ] **Step 6: Commit**

```bash
git add web/static/js/quality_assessment_card.js \
        web/static/css/quality_assessment_card.css \
        web/templates/omni_translate_detail.html \
        web/templates/multi_translate_detail.html
git commit -m "feat(ui): translation quality assessment card (omni + multi)"
```

---

## Task 16: Server-side smoke test for the whole pipeline

**Files:**
- None — verification only.

- [ ] **Step 1: Push branch to test server and run pytest**

```bash
cd .worktrees/translation-pipeline-overhaul
git push -u origin feature/translation-pipeline-overhaul
ssh -i C:/Users/admin/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 \
  'cd /opt/autovideosrt-test && git fetch origin feature/translation-pipeline-overhaul && \
   git checkout feature/translation-pipeline-overhaul && \
   systemctl restart autovideosrt-test && sleep 5 && \
   /opt/autovideosrt/venv/bin/python -m pytest \
     tests/test_asr_clean.py \
     tests/test_translation_quality.py \
     tests/test_quality_assessment_service.py \
     tests/test_asr_normalize.py \
     tests/test_omni_translate_routes.py \
     tests/test_multi_translate_routes.py \
     -q 2>&1 | tail -25'
```

Expected: all green; specifically the new tests added in tasks 3, 4, 5, 12, 8, 13.

- [ ] **Step 2: End-to-end manual run on test server**

Use the existing Spanish test video that produced the omni `723e7a3d…` and multi `562030b7…` tasks.

Upload twice:
1. To omni with `source_language=Auto-detect` → expect LID detects `es`, `_step_asr_clean` runs, translate goes `es → en`, rewrite messages contain Spanish anchor, quality card shows scores.
2. To multi with `source_language=Auto-detect` → expect `asr_normalize` runs purify then translates to en, downstream behavior identical to before this change except the noise is reduced. Quality card also shows scores.

Compare the two:
- Convergence rounds (omni should be ≤ multi for noisy sources)
- Quality assessment translation_score (omni should be ≥ multi)
- Final localized text fidelity (omni should refer to original-video content, multi may still drift)

- [ ] **Step 3: Document any surprises**

If integration testing reveals issues, file follow-up tasks. If clean, this plan is done.

- [ ] **Step 4: Final commit (if any tweaks needed during smoke test)**

```bash
git add -A
git commit -m "fix(translation-pipeline): integration test fixes"  # only if needed
```

---

## Task 17: Merge to master

**Files:**
- None — git operations only.

- [ ] **Step 1: Verify branch state**

```bash
cd .worktrees/translation-pipeline-overhaul
git log master..feature/translation-pipeline-overhaul --oneline
git status
```

Expected: clean tree, ~16 commits ahead of master.

- [ ] **Step 2: Merge to master**

Merge mode: explicit merge commit (`--no-ff`) so the feature is grouped on master.

```bash
cd "G:/Code/AutoVideoSrtLocal"  # main worktree
git checkout master
git merge --no-ff feature/translation-pipeline-overhaul \
  -m "Merge branch 'feature/translation-pipeline-overhaul'"
```

- [ ] **Step 3: Deploy to production server**

```bash
ssh -i C:/Users/admin/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 \
  'cd /opt/autovideosrt && git pull && systemctl restart autovideosrt'
```

Expected: migration auto-applies, server back up.

- [ ] **Step 4: Production verification**

```bash
ssh -i C:/Users/admin/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 \
  'cd /opt/autovideosrt && set -a && source /opt/autovideosrt/.env && set +a && \
   /opt/autovideosrt/venv/bin/python -c "
from appcore.db import query
print(query(\"SHOW TABLES LIKE %s\", (\"translation_quality_assessments\",)))
"'
```

Expected: table listed.

- [ ] **Step 5: Cleanup worktree**

```bash
cd "G:/Code/AutoVideoSrtLocal"
git worktree remove .worktrees/translation-pipeline-overhaul
git branch -d feature/translation-pipeline-overhaul
```

---

## Self-review checklist

After completing all tasks, verify:

- [ ] Spec section 4 (purification) → covered by Tasks 3 + 12
- [ ] Spec section 5 (omni source-language track) → covered by Tasks 8 + 9 + 10 + 11
- [ ] Spec section 6 (multi conservative track) → covered by Tasks 12 + 13
- [ ] Spec section 7 (assessment card) → covered by Tasks 1 + 2 + 4 + 5 + 6 + 7 + 15
- [ ] Spec section 9 (testing plan) → covered by Tasks 3, 4, 5, 8, 12, 13, 16
- [ ] Each task has bite-sized 2-5min steps with exact code or commands
- [ ] No "TBD" or vague placeholders
- [ ] Type names consistent across tasks (`OmniLocalizationAdapter`, `purify_utterances`, `assess`, `trigger_assessment`)
