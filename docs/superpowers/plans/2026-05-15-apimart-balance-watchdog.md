# APIMART Balance Watchdog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an hourly server-side APIMART balance watchdog that compares remote APIMART balance consumption with local AI billing records and sends existing Feishu scheduled-task alerts on anomalies.

**Architecture:** Create `appcore.apimart_balance_watchdog` as a focused service module. It queries APIMART balances, finds the prior successful watchdog snapshot from `scheduled_task_runs`, aggregates local APIMART usage from `usage_logs`, evaluates anomaly rules, and owns scheduled-run logging. Register it in `appcore.scheduled_tasks` and `appcore.scheduler`.

**Tech Stack:** Python 3.12, requests, MySQL-backed appcore DB helpers, APScheduler, existing Feishu scheduled-task alerts.

---

### Task 1: Watchdog Core

**Files:**
- Create: `appcore/apimart_balance_watchdog.py`
- Test: `tests/test_apimart_balance_watchdog.py`

- [ ] **Step 1: Write failing tests**

Create tests for balance parsing, previous snapshot lookup, local APIMART cost aggregation, normal run success, low-balance failure, and unexplained remote usage failure.

- [ ] **Step 2: Verify tests fail**

Run: `pytest tests/test_apimart_balance_watchdog.py -q`
Expected: import or attribute failures because the module does not exist yet.

- [ ] **Step 3: Implement minimal module**

Implement:
- `fetch_balance_snapshot()`
- `latest_success_snapshot()`
- `local_apimart_usage_usd()`
- `evaluate_snapshot()`
- `run_scheduled_check()`
- `register(scheduler)`

Use constants: `TASK_CODE='apimart_balance_watchdog'`, `USD_TO_CNY=Decimal('7.2')`, low balance `20`, gap `1`, ratio `0.20`.

- [ ] **Step 4: Verify tests pass**

Run: `pytest tests/test_apimart_balance_watchdog.py -q`
Expected: all tests pass.

### Task 2: Scheduler Registry

**Files:**
- Modify: `appcore/scheduled_tasks.py`
- Modify: `appcore/scheduler.py`
- Test: `tests/test_appcore_scheduled_tasks.py`
- Test: `tests/test_apimart_balance_watchdog.py`

- [ ] **Step 1: Write failing registry tests**

Add assertions that `task_definitions()` includes `apimart_balance_watchdog` with `source_type='apscheduler'`, hourly schedule, `scheduled_task_runs` log table, and runner `appcore.apimart_balance_watchdog.run_scheduled_check`. Add a scheduler-registration test using a fake scheduler.

- [ ] **Step 2: Verify registry tests fail**

Run: `pytest tests/test_appcore_scheduled_tasks.py::test_task_definitions_include_apimart_balance_watchdog tests/test_apimart_balance_watchdog.py::test_register_adds_hourly_controlled_job -q`
Expected: missing task definition / missing registration.

- [ ] **Step 3: Register task**

Add `TASK_DEFINITIONS['apimart_balance_watchdog']` and call `apimart_balance_watchdog.register(_scheduler)` inside `appcore.scheduler.get_scheduler()`.

- [ ] **Step 4: Verify registry tests pass**

Run: `pytest tests/test_appcore_scheduled_tasks.py::test_task_definitions_include_apimart_balance_watchdog tests/test_apimart_balance_watchdog.py::test_register_adds_hourly_controlled_job -q`
Expected: pass.

### Task 3: Final Verification and Deployment

**Files:**
- No new source files unless tests reveal a focused fix.

- [ ] **Step 1: Run focused tests**

Run: `pytest tests/test_apimart_balance_watchdog.py tests/test_appcore_scheduled_tasks.py tests/test_scheduler_shutdown.py -q`
Expected: pass.

- [ ] **Step 2: Compile changed Python files**

Run: `python -m py_compile appcore/apimart_balance_watchdog.py appcore/scheduled_tasks.py appcore/scheduler.py`
Expected: exit 0.

- [ ] **Step 3: Deploy only after user-approved server update**

Push branch or merge through the project release path, then update `/opt/autovideosrt` on the server and restart the web service only when the user explicitly approves the server deployment/restart.

- [ ] **Step 4: Manual server smoke**

Run `run_scheduled_check()` once on the server using the production venv. Confirm `scheduled_task_runs` contains the new task row and APIMART returns balance data.
