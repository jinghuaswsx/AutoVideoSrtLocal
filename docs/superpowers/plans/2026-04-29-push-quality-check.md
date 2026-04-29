# Push Quality Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one-shot LLM quality checks for push-management copy, cover image, and video before admins push materials.

**Architecture:** Introduce a focused `appcore.push_quality_checks` module that owns fingerprints, DB persistence, LLM calls, and scheduler scans. Existing push routes serialize the latest result into the list and payload APIs, while the frontend renders a compact review panel in the push modal.

**Tech Stack:** Flask routes, PyMySQL helpers through `appcore.db`, APScheduler, `appcore.llm_client`, ffmpeg for 5-second clips, pytest.

---

## File Map

- Create `appcore/push_quality_checks.py`: DB schema helper, fingerprinting, one-shot reuse logic, three LLM checks, 5-second clip creation, public result serialization.
- Create `appcore/push_quality_check_scheduler.py`: APScheduler entrypoint and small-batch scanner.
- Create `db/migrations/2026_04_29_push_quality_checks.sql`: production table DDL.
- Modify `appcore/scheduler.py`: register scheduler job.
- Modify `appcore/scheduled_tasks.py`: expose the task in Scheduled Tasks management.
- Modify `web/routes/pushes.py`: include quality result in list/payload APIs and add manual retry endpoint.
- Modify `web/static/pushes.js`: render top-of-modal quality panel and call retry endpoint.
- Modify `web/static/pushes.css`: Ocean Blue styling for quality cards.
- Add tests in `tests/test_push_quality_checks.py`, `tests/test_push_quality_check_scheduler.py`, and targeted route tests.

---

### Task 1: Persistence And Fingerprints

**Files:**
- Create: `appcore/push_quality_checks.py`
- Create: `tests/test_push_quality_checks.py`
- Create: `db/migrations/2026_04_29_push_quality_checks.sql`

- [ ] **Step 1: Write failing tests for fingerprints and one-shot lookup**

Add tests that assert:

```python
def test_build_fingerprints_changes_when_copy_changes(monkeypatch):
    from appcore import push_quality_checks as qc
    item = {"id": 7, "product_id": 3, "lang": "de", "object_key": "v1.mp4", "cover_object_key": "c1.jpg"}
    product = {"id": 3, "name": "Demo"}
    monkeypatch.setattr(qc.pushes, "resolve_localized_text_payload", lambda item: {
        "title": "Titel", "message": "Hallo", "description": "Beschreibung", "lang": "德语",
    })
    first = qc.build_fingerprints(item, product)
    monkeypatch.setattr(qc.pushes, "resolve_localized_text_payload", lambda item: {
        "title": "Titel", "message": "Neu", "description": "Beschreibung", "lang": "德语",
    })
    second = qc.build_fingerprints(item, product)
    assert first.copy_fingerprint != second.copy_fingerprint
    assert first.cover_fingerprint == second.cover_fingerprint
    assert first.video_fingerprint == second.video_fingerprint
```

```python
def test_find_reusable_auto_result_returns_existing_same_fingerprint(monkeypatch):
    from appcore import push_quality_checks as qc
    captured = {}
    monkeypatch.setattr(qc, "ensure_table", lambda: None)
    monkeypatch.setattr(qc, "query_one", lambda sql, args: captured.setdefault("args", args) or {
        "id": 11, "status": "failed", "summary": "bad", "failed_reasons": "[\"文案混入英文\"]",
        "copy_result_json": "{}", "cover_result_json": "{}", "video_result_json": "{}",
        "provider": "openrouter", "model": qc.MODEL,
        "started_at": None, "finished_at": None, "created_at": None, "updated_at": None,
    })
    result = qc.find_reusable_auto_result(9, "copy", "cover", "video")
    assert result["id"] == 11
    assert captured["args"][:5] == (9, "copy", "cover", "video", "auto")
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_push_quality_checks.py::test_build_fingerprints_changes_when_copy_changes tests/test_push_quality_checks.py::test_find_reusable_auto_result_returns_existing_same_fingerprint -q`

Expected: FAIL because `appcore.push_quality_checks` does not exist.

- [ ] **Step 3: Implement table DDL, dataclass, fingerprint helpers, and reusable lookup**

Implement `ensure_table()`, `QualityFingerprints`, `build_fingerprints()`, `find_reusable_auto_result()`, and `_normalize_row()`.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `pytest tests/test_push_quality_checks.py::test_build_fingerprints_changes_when_copy_changes tests/test_push_quality_checks.py::test_find_reusable_auto_result_returns_existing_same_fingerprint -q`

Expected: PASS.

### Task 2: One-Shot Evaluation Service

**Files:**
- Modify: `appcore/push_quality_checks.py`
- Modify: `tests/test_push_quality_checks.py`

- [ ] **Step 1: Write failing tests for auto no-retry and manual retry**

Add tests that monkeypatch `run_three_checks()` and DB writes:

```python
def test_evaluate_item_auto_reuses_existing_without_llm(monkeypatch):
    from appcore import push_quality_checks as qc
    monkeypatch.setattr(qc.medias, "get_item", lambda item_id: {"id": item_id, "product_id": 1, "lang": "de"})
    monkeypatch.setattr(qc.medias, "get_product", lambda product_id: {"id": product_id})
    monkeypatch.setattr(qc, "build_fingerprints", lambda item, product: qc.QualityFingerprints("c", "i", "v"))
    monkeypatch.setattr(qc, "find_reusable_auto_result", lambda item_id, c, i, v: {"id": 5, "status": "passed"})
    monkeypatch.setattr(qc, "run_three_checks", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call LLM")))
    assert qc.evaluate_item(9, source="auto")["id"] == 5
```

```python
def test_evaluate_item_manual_ignores_auto_reuse(monkeypatch):
    from appcore import push_quality_checks as qc
    calls = []
    monkeypatch.setattr(qc.medias, "get_item", lambda item_id: {"id": item_id, "product_id": 1, "lang": "de"})
    monkeypatch.setattr(qc.medias, "get_product", lambda product_id: {"id": product_id})
    monkeypatch.setattr(qc, "build_fingerprints", lambda item, product: qc.QualityFingerprints("c", "i", "v"))
    monkeypatch.setattr(qc, "find_reusable_auto_result", lambda item_id, c, i, v: {"id": 5, "status": "passed"})
    monkeypatch.setattr(qc, "_record_running", lambda *a, **k: 22)
    monkeypatch.setattr(qc, "_record_finish", lambda check_id, result: calls.append((check_id, result)) or {"id": check_id, **result})
    monkeypatch.setattr(qc, "run_three_checks", lambda item, product, fp: {
        "status": "passed", "summary": "ok", "failed_reasons": [],
        "copy_result": {"status": "passed"}, "cover_result": {"status": "passed"}, "video_result": {"status": "passed"},
    })
    result = qc.evaluate_item(9, source="manual")
    assert result["id"] == 22
    assert calls
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_push_quality_checks.py::test_evaluate_item_auto_reuses_existing_without_llm tests/test_push_quality_checks.py::test_evaluate_item_manual_ignores_auto_reuse -q`

Expected: FAIL because `evaluate_item()` is missing.

- [ ] **Step 3: Implement evaluation orchestration**

Implement `evaluate_item(item_id, source="auto")`, `_record_running()`, `_record_finish()`, and `aggregate_status()`.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `pytest tests/test_push_quality_checks.py::test_evaluate_item_auto_reuses_existing_without_llm tests/test_push_quality_checks.py::test_evaluate_item_manual_ignores_auto_reuse -q`

Expected: PASS.

### Task 3: LLM Calls And 5-Second Video Clip

**Files:**
- Modify: `appcore/push_quality_checks.py`
- Modify: `tests/test_push_quality_checks.py`

- [ ] **Step 1: Write failing tests for model overrides and clip duration**

Add tests that assert `llm_client.invoke_chat` uses OpenRouter Gemini 3.1 Flash Lite and `_make_video_clip_5s()` calls ffmpeg with `-t 5`.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_push_quality_checks.py::test_copy_check_uses_openrouter_gemini_flash_lite tests/test_push_quality_checks.py::test_video_clip_uses_first_five_seconds -q`

Expected: FAIL because checks and clip helper are missing.

- [ ] **Step 3: Implement `check_copy()`, `check_cover()`, `check_video()`, and `_make_video_clip_5s()`**

Use `llm_client.invoke_chat` for copy and `llm_client.invoke_generate` for cover/video. Force `provider_override="openrouter"` and `model_override="google/gemini-3.1-flash-lite-preview"`.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `pytest tests/test_push_quality_checks.py::test_copy_check_uses_openrouter_gemini_flash_lite tests/test_push_quality_checks.py::test_video_clip_uses_first_five_seconds -q`

Expected: PASS.

### Task 4: Scheduler Registration

**Files:**
- Create: `appcore/push_quality_check_scheduler.py`
- Modify: `appcore/scheduler.py`
- Modify: `appcore/scheduled_tasks.py`
- Create: `tests/test_push_quality_check_scheduler.py`
- Modify: `tests/test_appcore_scheduled_tasks.py`

- [ ] **Step 1: Write failing scheduler tests**

Assert `register()` adds an interval job with id `push_quality_check_tick`, and `scheduled_tasks.task_definitions()` includes it.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_push_quality_check_scheduler.py tests/test_appcore_scheduled_tasks.py::test_task_definitions_include_push_quality_check -q`

Expected: FAIL because files/task definition are missing.

- [ ] **Step 3: Implement scheduler**

Add a 10-minute interval job. `tick_once(limit=5)` finds ready pending/failed items through `pushes.list_items_for_push(limit=None)`, filters by `compute_status()`, skips reusable auto results, and evaluates up to limit.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `pytest tests/test_push_quality_check_scheduler.py tests/test_appcore_scheduled_tasks.py::test_task_definitions_include_push_quality_check -q`

Expected: PASS.

### Task 5: Push Routes And UI

**Files:**
- Modify: `web/routes/pushes.py`
- Modify: `web/static/pushes.js`
- Modify: `web/static/pushes.css`
- Modify: `tests/test_pushes_routes.py`

- [ ] **Step 1: Write failing route tests**

Assert `/pushes/api/items/<id>/payload` includes `quality_check`, and POST `/pushes/api/items/<id>/quality-check/retry` returns manual evaluation result.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_pushes_routes.py::test_api_build_payload_includes_quality_check tests/test_pushes_routes.py::test_api_quality_check_retry_runs_manual_evaluation -q`

Expected: FAIL because route fields/endpoints are missing.

- [ ] **Step 3: Implement route serialization**

Import `appcore.push_quality_checks`, include `latest_for_item()` in `_serialize_row()` and `api_build_payload()`, and add admin POST retry endpoint.

- [ ] **Step 4: Implement modal UI**

In `openPushModal()`, insert a quality section above push content. Render three cards and a retry button. After retry, replace the section contents with returned results.

- [ ] **Step 5: Run route tests and static syntax checks**

Run: `pytest tests/test_pushes_routes.py::test_api_build_payload_includes_quality_check tests/test_pushes_routes.py::test_api_quality_check_retry_runs_manual_evaluation -q`

Expected: PASS.

Run: `node --check web/static/pushes.js`

Expected: PASS.

### Task 6: Focused Regression Suite

**Files:**
- No production edits unless tests reveal defects.

- [ ] **Step 1: Run focused tests**

Run: `pytest tests/test_push_quality_checks.py tests/test_push_quality_check_scheduler.py tests/test_appcore_scheduled_tasks.py tests/test_pushes_routes.py -q`

Expected: PASS, or document any pre-existing slow/DB timeout separately.

- [ ] **Step 2: Inspect diff**

Run: `git diff --stat` and `git diff --check`.

Expected: no whitespace errors, scoped changes only.
