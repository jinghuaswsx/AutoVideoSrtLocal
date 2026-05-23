# 明空视频卡片 AI 精细评估自动任务 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 自动为明空视频素材库 Top500 和昨天消耗全部 Top100 视频卡片补齐 AI 精细评估结果，并复用现有弹窗/入库建议结果。

**Architecture:** 新增一个独立 APScheduler tick service，候选只读本地 `mingkong_material_*` 归档表，按 `material_key` 写入轻量自动评估记录表做一次性去重。每张卡片复用 `FineAiEvaluationService.create_external_link_run()` 和 `run_evaluation()`，因此最终结果仍落在 `ai_evaluation_runs` / `ai_country_evaluations`，前端读取同一份结果。

**Tech Stack:** Python 3.12, Flask, APScheduler, MySQL-compatible SQL migrations, pytest.

---

## File Map

- Create: `db/migrations/2026_05_23_mingkong_fine_ai_auto_evaluations.sql`  
  自动评估记录表，按 `material_key` 唯一去重。
- Create: `appcore/mingkong_fine_ai_auto_evaluation.py`  
  候选查询、单例接管、逐卡片执行、自动评估记录更新。
- Create: `appcore/mingkong_fine_ai_auto_evaluation_scheduler.py`  
  APScheduler 注册入口。
- Modify: `appcore/scheduled_tasks.py`  
  登记 Web 后台可见定时任务。
- Modify: `appcore/scheduler.py`  
  注册新 APScheduler module。
- Modify: `appcore/fine_ai_evaluation_service.py`  
  国家等待默认改 0，并给单国家评估增加一次自动重试。
- Modify: `appcore/mingkong_materials.py`  
  给未入库明空卡片按商品链接 + 视频路径附加最新外链精细评估结果。
- Test: `tests/test_mingkong_fine_ai_auto_evaluation.py`  
  覆盖候选优先级、Top100 全量、单例、10 条限制、执行记录。
- Test: `tests/test_mingkong_materials_scheduler.py`  
  覆盖任务登记和 scheduler 注册。
- Test: `tests/test_fine_ai_evaluation_pipeline.py`  
  覆盖国家失败自动重试。
- Test: `tests/test_db_migration_mingkong_fine_ai_auto_evaluation.py`  
  覆盖迁移表结构和索引。

## Task 1: Migration And Registry Tests

**Files:**
- Create: `tests/test_db_migration_mingkong_fine_ai_auto_evaluation.py`
- Modify: `tests/test_mingkong_materials_scheduler.py`

- [ ] **Step 1: Write failing migration smoke test**

```python
from pathlib import Path

MIGRATION = Path("db/migrations/2026_05_23_mingkong_fine_ai_auto_evaluations.sql")

def test_mingkong_fine_ai_auto_evaluation_migration_declares_table_and_indexes():
    body = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS mingkong_fine_ai_auto_evaluations" in body
    assert "material_key CHAR(64) NOT NULL" in body
    assert "UNIQUE KEY uk_mk_fine_ai_auto_material (material_key)" in body
    assert "KEY idx_mk_fine_ai_auto_status (status, updated_at)" in body
    assert "KEY idx_mk_fine_ai_auto_eval_run (evaluation_run_id)" in body
```

- [ ] **Step 2: Write failing scheduler registry test**

Append to `tests/test_mingkong_materials_scheduler.py`:

```python
def test_mingkong_fine_ai_auto_evaluation_registered():
    from appcore import scheduled_tasks

    task = scheduled_tasks.get_task_definition("mingkong_fine_ai_auto_evaluation_tick")
    definitions = {item["code"]: item for item in scheduled_tasks.task_definitions()}
    enriched = definitions["mingkong_fine_ai_auto_evaluation_tick"]

    assert task["code"] == "mingkong_fine_ai_auto_evaluation_tick"
    assert task["source_type"] == "apscheduler"
    assert task["source_ref"] == "mingkong_fine_ai_auto_evaluation_tick"
    assert task["runner"] == "appcore.mingkong_fine_ai_auto_evaluation_scheduler.tick_once"
    assert task["log_table"] == "scheduled_task_runs"
    assert "10 分钟" in task["schedule"]
    assert "2026-05-23-mingkong-fine-ai-auto-evaluation-design.md" in task["description"]
    assert enriched["control_strategy"] == "apscheduler"
    assert enriched["log_source"] == "db:scheduled_task_runs"


def test_mingkong_fine_ai_auto_evaluation_scheduler_registered_in_app_scheduler():
    source = (Path(__file__).resolve().parents[1] / "appcore" / "scheduler.py").read_text(encoding="utf-8")

    assert "mingkong_fine_ai_auto_evaluation_scheduler" in source
    assert "mingkong_fine_ai_auto_evaluation_scheduler.register(_scheduler)" in source
```

- [ ] **Step 3: Run red tests**

Run: `pytest tests/test_db_migration_mingkong_fine_ai_auto_evaluation.py tests/test_mingkong_materials_scheduler.py -q`

Expected: migration file missing and task definition missing failures.

- [ ] **Step 4: Add migration and scheduler registry**

Create migration with table and indexes. Add task definition to `appcore/scheduled_tasks.py`. Create scheduler wrapper:

```python
from __future__ import annotations

from appcore import mingkong_fine_ai_auto_evaluation, scheduled_tasks

TASK_CODE = "mingkong_fine_ai_auto_evaluation_tick"

def tick_once(limit: int = 10) -> dict:
    return mingkong_fine_ai_auto_evaluation.tick_once(limit=limit)

def register(scheduler) -> None:
    scheduled_tasks.add_controlled_job(
        scheduler,
        TASK_CODE,
        tick_once,
        "interval",
        minutes=10,
        id=TASK_CODE,
        replace_existing=True,
        max_instances=1,
    )
```

Register this module in `appcore/scheduler.py`.

- [ ] **Step 5: Run green tests**

Run: `pytest tests/test_db_migration_mingkong_fine_ai_auto_evaluation.py tests/test_mingkong_materials_scheduler.py -q`

Expected: pass.

## Task 2: Candidate Queue And Singleton Service

**Files:**
- Create: `tests/test_mingkong_fine_ai_auto_evaluation.py`
- Create: `appcore/mingkong_fine_ai_auto_evaluation.py`

- [ ] **Step 1: Write failing service tests**

Test behaviors:

```python
def test_tick_prioritizes_top500_before_yesterday_top100(monkeypatch):
    # fake latest_running_run None, fake start_run id 101
    # top500 returns one runnable row, top100 must not be queried
    # fake runner marks row completed
    # assert source_bucket == "top500_90d_spend"

def test_tick_uses_all_yesterday_top100_after_top500_exhausted(monkeypatch):
    # top500 returns []
    # top100 returns two rows, including is_new_top100_entry False
    # assert both are accepted as yesterday_top100 candidates

def test_tick_limits_each_round_to_ten(monkeypatch):
    # top500 returns 12 rows
    # assert only 10 calls to create/run

def test_tick_skips_when_existing_run_younger_than_30_minutes(monkeypatch):
    # latest_running_run started_at now minus 60 seconds
    # assert summary skipped and no start_run

def test_tick_replaces_running_run_older_than_30_minutes(monkeypatch):
    # latest_running_run started_at older than 1800 seconds
    # assert finish_run called failed for old id and new start_run called
```

Use monkeypatch for DB helpers (`query`, `query_one`, `execute`) and a fake `FineAiEvaluationService`.

- [ ] **Step 2: Run red service tests**

Run: `pytest tests/test_mingkong_fine_ai_auto_evaluation.py -q`

Expected: import/module missing failures.

- [ ] **Step 3: Implement minimal service**

Implement:

- `tick_once(limit=10, stale_after_seconds=1800)`
- `_guard_singleton()`
- `_running_age_seconds(row)`
- `_fetch_top500_candidates()`
- `_fetch_yesterday_top100_candidates()`
- `_candidate_key(row)`
- `_upsert_running_record(row, scheduled_run_id, source_bucket, source_rank)`
- `_finish_record(material_key, status, evaluation_run_id="", error="")`
- `_run_candidate(row, scheduled_run_id, source_bucket, source_rank, service)`

The first implementation can use direct SQL helpers from `appcore.db` and injected function parameters for tests.

- [ ] **Step 4: Run green service tests**

Run: `pytest tests/test_mingkong_fine_ai_auto_evaluation.py -q`

Expected: pass.

## Task 3: Fine AI Country Retry And Zero Wait

**Files:**
- Modify: `tests/test_fine_ai_evaluation_pipeline.py`
- Modify: `appcore/fine_ai_evaluation_service.py`

- [ ] **Step 1: Write failing retry test**

Add a fake client that fails `FR` first, succeeds second:

```python
def test_country_failure_retries_once_then_continues():
    calls = []
    service = FineAiEvaluationService(
        repository=InMemoryEvaluationRepository(),
        gemini_client=FlakyCountryGeminiClient(calls, fail_once_country="FR"),
        product_snapshot_service=FakeProductSnapshotService(),
        asset_snapshot_service=FakeAssetSnapshotService(),
        country_retry_attempts=2,
    )

    run = service.create_run(123, countries=["DE", "FR", "IT"])
    result = service.run_evaluation(run["evaluation_run_id"])

    assert result["status"] == "completed"
    assert [call[0] for call in calls] == ["product_facts", "country:DE", "country:FR", "country:FR", "country:IT"]
    assert result["countries"]["FR"]["status"] == "completed"
    fr_step = next(step for step in result["progress"]["steps"] if step["key"] == "country_FR")
    assert any("第 1 次失败" in log["message"] for log in fr_step["logs"])
```

Add a static/default test:

```python
def test_production_fine_ai_country_request_interval_defaults_to_zero():
    from appcore import fine_ai_evaluation_service as mod
    assert mod.PRODUCTION_COUNTRY_REQUEST_INTERVAL_SECONDS == 0
```

- [ ] **Step 2: Run red pipeline tests**

Run: `pytest tests/test_fine_ai_evaluation_pipeline.py::test_country_failure_retries_once_then_continues tests/test_fine_ai_evaluation_pipeline.py::test_production_fine_ai_country_request_interval_defaults_to_zero -q`

Expected: constructor arg missing / constant still 30.

- [ ] **Step 3: Implement retry**

Add `country_retry_attempts: int = 2` to `FineAiEvaluationService.__init__`. In the country loop, wrap `generate_country_evaluation()` in an attempts loop. On non-final failure, append a progress log via `_mark_progress_step(..., status="running", message=f"{code} 第 {attempt} 次失败，准备重试：...")` and retry. On final failure, keep existing failed-country behavior. Set `PRODUCTION_COUNTRY_REQUEST_INTERVAL_SECONDS = 0`.

- [ ] **Step 4: Run green pipeline tests**

Run: `pytest tests/test_fine_ai_evaluation_pipeline.py::test_country_failure_retries_once_then_continues tests/test_fine_ai_evaluation_pipeline.py::test_pipeline_continues_when_one_country_fails tests/test_fine_ai_evaluation_pipeline.py::test_country_request_waits_between_countries_and_marks_progress tests/test_fine_ai_evaluation_pipeline.py::test_production_fine_ai_country_request_interval_defaults_to_zero -q`

Expected: pass; explicit 30-second wait test still passes because it injects interval.

## Task 4: Reuse Auto External Results In Mingkong Cards

**Files:**
- Modify: `tests/test_mingkong_fine_ai_auto_evaluation.py`
- Modify: `appcore/mingkong_materials.py`

- [ ] **Step 1: Write failing enrichment test**

Add a test that feeds an item with no local product but with `mk_product_link`, `product_url`, and `video_path`. Patch `query()` so `_fine_ai_status_by_external_cards()` sees a completed external run whose `metadata.external_product_link` and `metadata.external_card_video.path` match. Assert `item["product_ad_status"]["fine_ai_evaluation"]["evaluation_run_id"] == "eval_auto"`.

- [ ] **Step 2: Run red enrichment test**

Run: `pytest tests/test_mingkong_fine_ai_auto_evaluation.py::test_enrich_cards_reads_external_fine_ai_result_for_unimported_material -q`

Expected: missing helper / no result.

- [ ] **Step 3: Implement external fine AI lookup**

In `appcore/mingkong_materials.py`, add helper:

```python
def _fine_ai_status_by_external_cards(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    # build per-card keys from product link candidates and normalized video_path
    # query product_id=0 external_product_link runs
    # load countries from ai_country_evaluations
    # return by material_key/video_path
```

Call it from `_enrich_cached_ad_statuses()` and set `product_status["fine_ai_evaluation"]` when no product-id result exists.

- [ ] **Step 4: Run green enrichment test**

Run: `pytest tests/test_mingkong_fine_ai_auto_evaluation.py::test_enrich_cards_reads_external_fine_ai_result_for_unimported_material -q`

Expected: pass.

## Task 5: Focused Verification

**Files:**
- All touched files.

- [ ] **Step 1: Run focused pytest**

Run:

```bash
pytest \
  tests/test_db_migration_mingkong_fine_ai_auto_evaluation.py \
  tests/test_mingkong_materials_scheduler.py \
  tests/test_mingkong_fine_ai_auto_evaluation.py \
  tests/test_fine_ai_evaluation_pipeline.py \
  tests/test_fine_ai_gemini_client.py \
  tests/test_xuanpin_routes.py::test_xuanpin_mk_material_import_modal_shows_fine_ai_soft_advice \
  -q
```

Expected: pass.

- [ ] **Step 2: Static checks**

Run:

```bash
python -m compileall appcore/mingkong_fine_ai_auto_evaluation.py appcore/mingkong_fine_ai_auto_evaluation_scheduler.py appcore/fine_ai_evaluation_service.py appcore/mingkong_materials.py
git diff --check
```

Expected: both exit 0.

- [ ] **Step 3: Review requirement coverage**

Check:

- Top500 before Top100.
- Top100 means complete Top100.
- Limit 10 per tick.
- 30-minute singleton takeover.
- `material_key` once-only automation.
- Results land in existing Fine AI tables.
- Country retry once.
- Country wait default 0.
- Google Search remains false.
